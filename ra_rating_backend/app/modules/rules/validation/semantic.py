"""Semantic checks — the draft against the catalogue and against sense.

Two things happen here that the structural tier cannot do. Reference existence
needs the catalogue, and cross-parameter coherence needs to look at several
parameters of one action at once ("a discount is a catalogue entry, or a
percentage, or an amount — exactly one of the three").

Reference resolution goes through the batch's :class:`ResolutionCache`, never a
query per value. A fifty-condition rule validated on every keystroke would
otherwise fire fifty round trips, and a 40,000-rule import would fire two
million.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.modules.rules.canonical.draft import CanonicalDraft
from app.modules.rules.ingest.resolver import PENDING_CATALOGUES, ResolutionCache
from app.modules.rules.validation.issues import Issue, error, warn
from app.modules.rules.vocabulary.actions import ACTION_BY_CODE
from app.modules.rules.vocabulary.attributes import CANONICAL_ATTRIBUTE_BY_KEY
from app.modules.rules.vocabulary.values import ValueType


async def check(draft: CanonicalDraft, cache: ResolutionCache) -> list[Issue]:
    """Resolve the draft's references against the catalogue, then sanity-check it.

    ``cache.load_catalogues`` is idempotent and per-run, so calling this for each
    draft in a batch still costs one query per catalogue for the whole batch.
    """
    wanted = draft.reference_codes()
    missing = await cache.load_catalogues(wanted) if wanted else {}

    issues: list[Issue] = []
    for slug, codes in sorted(missing.items()):
        issues.append(
            error(
                "catalogue_unavailable" if slug in PENDING_CATALOGUES
                else "unknown_reference",
                cache.describe_missing(slug, codes),
                _path_for(draft, slug),
                hint="Create it under Metadata Catalogue, or correct the value.",
            )
        )

    issues.extend(_currency_present(draft))
    for index, action in enumerate(draft.actions):
        issues.extend(_action_coherence(action, f"actions[{index}]"))
    return issues


def _path_for(draft: CanonicalDraft, slug: str) -> str:
    """Point the author at the first place this catalogue is mentioned."""
    if slug in {"products", "offers", "tariff-plans"}:
        return f"targets.{slug.rstrip('s').replace('-', '_')}"
    for index, cond in enumerate(draft.conditions):
        attr = CANONICAL_ATTRIBUTE_BY_KEY.get(cond.attribute)
        if attr is not None and attr.reference == slug:
            return f"conditions[{index}].values"
    for index, action in enumerate(draft.actions):
        spec = ACTION_BY_CODE.get(action.action_type)
        if spec is None:
            continue
        for param in spec.params:
            if param.reference == slug:
                return f"actions[{index}].params.{param.key}"
    return ""


def _currency_present(draft: CanonicalDraft) -> list[Issue]:
    """A monetary action with no currency anywhere is the classic import defect.

    The codec already refuses to store MONEY without one, so this exists to give
    the *rule-level* fix — set the version currency once — rather than one
    "currency required" per parameter on a rule with eight rate tiers.
    """
    if draft.validity.currency_code:
        return []
    for index, action in enumerate(draft.actions):
        spec = ACTION_BY_CODE.get(action.action_type)
        if spec is None:
            continue
        monetary = {p.key for p in spec.params if p.value_type == ValueType.MONEY}
        for param in action.parameters:
            if param.name in monetary and not param.currency:
                return [
                    error(
                        "currency_missing",
                        f"{spec.label} sets a monetary value but neither the "
                        "parameter nor the rule states a currency.",
                        f"actions[{index}].params.{param.name}",
                        hint="Set the rule's currency once, in Step 1 — a money "
                             "amount whose currency has to be guessed is how "
                             "cent-level drift starts.",
                    )
                ]
    return []


def _number(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _exactly_one(action, keys: tuple[str, ...]) -> list[str]:
    supplied = {p.name: p.raw for p in action.parameters}
    return [k for k in keys if supplied.get(k) not in (None, "")]


def _action_coherence(action, path: str) -> list[Issue]:
    """Cross-parameter rules a per-field check cannot express."""
    issues: list[Issue] = []
    supplied = {p.name: p.raw for p in action.parameters}

    match action.action_type:
        case "APPLY_DISCOUNT":
            given = _exactly_one(action, ("discount", "percentage", "amount"))
            if not given:
                issues.append(
                    error(
                        "discount_underspecified",
                        "Set a catalogue discount, a percentage, or a fixed amount.",
                        f"{path}.params",
                    )
                )
            elif len(given) > 1:
                issues.append(
                    error(
                        "discount_overspecified",
                        "Only one of discount / percentage / amount may be set "
                        f"(got {', '.join(given)}).",
                        f"{path}.params",
                        hint="Two of them set is ambiguous, and which one wins would "
                             "depend on evaluation order.",
                    )
                )
            pct = _number(supplied.get("percentage"))
            if pct is not None and not 0 <= pct <= 100:
                issues.append(
                    error(
                        "discount_percentage_range",
                        "A discount percentage must be between 0 and 100.",
                        f"{path}.params.percentage",
                    )
                )

        case "ADD_SURCHARGE":
            given = _exactly_one(action, ("percentage", "amount"))
            if not given:
                issues.append(
                    error("surcharge_underspecified",
                          "Set a surcharge percentage or amount.", f"{path}.params")
                )
            elif len(given) > 1:
                issues.append(
                    error("surcharge_overspecified",
                          "Set either a percentage or an amount, not both.",
                          f"{path}.params")
                )

        case "APPLY_TAX":
            given = _exactly_one(action, ("tax_rule", "rate_percent"))
            if not given:
                issues.append(
                    error(
                        "tax_underspecified",
                        "Set a catalogue tax rule, or an inline rate percentage.",
                        f"{path}.params",
                        hint="A catalogue rule is preferable — change the rate once "
                             "and every rule that references it follows.",
                    )
                )
            elif len(given) > 1:
                issues.append(
                    error(
                        "tax_overspecified",
                        "Set either a catalogue tax rule or an inline rate, not both "
                        f"(got {', '.join(given)}).",
                        f"{path}.params",
                        hint="Two rates for one tax is a disagreement nobody can "
                             "resolve at rating time.",
                    )
                )
            rate = _number(supplied.get("rate_percent"))
            if rate is not None and not 0 <= rate <= 100:
                issues.append(
                    error(
                        "tax_percentage_range",
                        "A tax rate must be between 0 and 100 percent.",
                        f"{path}.params.rate_percent",
                    )
                )

        case "APPLY_ROUNDING":
            if not supplied.get("rounding_rule") and not supplied.get("mode"):
                decimals = supplied.get("decimals")
                issues.append(
                    error(
                        "rounding_underspecified",
                        (
                            f"Rounding to {decimals} decimal places was given, but not "
                            "how to round."
                            if decimals not in (None, "")
                            else "Rounding needs either a catalogue rule or a mode."
                        ),
                        f"{path}.params.mode",
                        hint="Add a Rounding Mode column (HALF_UP, HALF_EVEN, "
                             "CEILING, FLOOR or TRUNCATE), or name a catalogue "
                             "rounding rule. It is deliberately not defaulted: "
                             "HALF_UP versus HALF_EVEN is a penny per invoice.",
                    )
                )

        case "SET_RATE":
            per = _number(supplied.get("per_units"))
            if per is not None and per <= 0:
                issues.append(
                    error("per_units_positive",
                          "'Per units' must be greater than zero — a rate divided by "
                          "zero units has no meaning.",
                          f"{path}.params.per_units")
                )

        case "SET_PULSE":
            initial = _number(supplied.get("initial_seconds"))
            if initial is not None and initial <= 0:
                issues.append(
                    error("pulse_positive",
                          "The initial pulse must be greater than zero.",
                          f"{path}.params.initial_seconds")
                )

        # --- Prepaid ------------------------------------------------------
        case "RESERVE_BALANCE":
            validity = _number(supplied.get("validity_seconds"))
            if validity is not None and validity <= 0:
                issues.append(
                    error("reservation_validity_positive",
                          "A reservation with no validity period is released the "
                          "instant it is taken.",
                          f"{path}.params.validity_seconds")
                )

        case "ALLOW_NEGATIVE_BALANCE":
            limit = _number(supplied.get("limit_amount"))
            if limit is not None and limit <= 0:
                issues.append(
                    warn("negative_limit_zero",
                         "A negative-balance limit of zero allows no overdraft at "
                         "all, which makes this rule a no-op.",
                         f"{path}.params.limit_amount")
                )

        # --- Postpaid -----------------------------------------------------
        case "CHECK_CREDIT_LIMIT":
            pct = _number(supplied.get("warning_threshold_pct"))
            if pct is not None and not 0 < pct <= 100:
                issues.append(
                    error("credit_threshold_range",
                          "The warning threshold must be a percentage above 0 and "
                          "at most 100.",
                          f"{path}.params.warning_threshold_pct")
                )

        case "ADD_LATE_FEE":
            if not _exactly_one(action, ("amount", "percentage")):
                issues.append(
                    error("late_fee_underspecified",
                          "A late fee needs an amount, a percentage, or a profile "
                          "that supplies one.",
                          f"{path}.params")
                )

    return issues
