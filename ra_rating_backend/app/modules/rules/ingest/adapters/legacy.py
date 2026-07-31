"""``rating.rules`` → :class:`CanonicalDraft`.

The R4 backfill's normalize step. It is an *adapter* rather than a migration
script because a backfill that writes rows by its own rules, in its own shape,
produces an estate the maintaining code has never seen — and the divergence only
surfaces months later when someone asks why an imported rule and a backfilled one
behave differently. Running through the same kernel means the 40,000 rows we
migrate are indistinguishable from the ones we will import tomorrow.

Four things the legacy model does not say, which have to be decided here:

**Charging mode.** The legacy row has none. It is inferred from an
``account_type`` condition where there is one, and defaults to ``BOTH``. Every
inference is reported, because "we guessed POSTPAID on 1,400 rules" is a fact an
operator must see rather than discover.

**Nesting.** ``group_index`` plus one rule-level ``condition_logic`` is a flat
two-level structure. It maps exactly onto one root group whose logic is the
rule's, containing one AND child per distinct index — no information gained, none
lost.

**Value types.** The legacy model stores a bare JSONB array and re-infers the
type per CDR. The declared type comes from the attribute and action registries,
which is the whole reason those registries exist.

**Money.** This is the one that cannot be done perfectly. Legacy action params
hold JSON numbers, which arrive as Python floats — so ``0.1`` reaching us is
already ``0.1000000000000000055511151231257827``. ``Decimal(repr(x))`` recovers
the shortest decimal that round-trips to that float, which is the value a human
originally typed in every realistic case. Where it cannot be sure, the draft is
flagged rather than quietly carried forward: see :data:`SUSPECT_MONEY`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.modules.rules.canonical.draft import (
    CanonicalDraft,
    DraftAction,
    DraftBehaviour,
    DraftCondition,
    DraftConditionGroup,
    DraftParameter,
    DraftTargets,
    DraftValidity,
    FieldProvenance,
    Provenance,
)
from app.modules.rules.models import Rule
from app.modules.rules.vocabulary.actions import ACTION_BY_CODE
from app.modules.rules.vocabulary.aliases import resolve_action, resolve_rule_type
from app.modules.rules.vocabulary.attributes import CANONICAL_ATTRIBUTE_BY_KEY
from app.modules.rules.vocabulary.modes import ChargingMode, ExecutionMode
from app.modules.rules.vocabulary.values import ValueType

#: A monetary value whose shortest round-tripping decimal needs more digits than
#: this was almost certainly damaged by float storage before it reached us. Six
#: is the canonical scale; a legitimate rate never needs more.
_MAX_MONEY_DIGITS = 6

#: How the legacy ``account_type`` condition maps onto a rule's charging mode.
#: ``HYBRID`` describes a *subscriber* who holds both a balance and an account —
#: the rule that serves them is correct for either, which is ``BOTH``.
_MODE_FROM_ACCOUNT_TYPE: dict[str, str] = {
    "PREPAID": ChargingMode.PREPAID,
    "POSTPAID": ChargingMode.POSTPAID,
    "HYBRID": ChargingMode.BOTH,
    "ANY": ChargingMode.BOTH,
}


@dataclass(slots=True)
class Conversion:
    """One converted rule, plus everything the operator needs to audit the guess."""

    draft: CanonicalDraft
    #: Non-fatal observations: an inferred mode, a re-parsed amount, an alias
    #: applied. Reported per rule and summarised per batch, never swallowed.
    notes: list[str] = field(default_factory=list)
    #: Money values this adapter could not vouch for. A rule carrying any of
    #: these is backfilled *and* flagged for confirmation — the plan's G.6.
    suspect_money: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return bool(self.suspect_money)


def convert(
    rule: Rule,
    *,
    product_codes: dict[str, str] | None = None,
    offer_codes: dict[str, str] | None = None,
    tariff_plan_codes: dict[str, str] | None = None,
) -> Conversion:
    """Convert one legacy rule row into a canonical draft.

    The code maps are ``{id: code}``, loaded once for the whole backfill: a draft
    carries catalogue *codes*, and re-querying per rule would put 40,000 round
    trips back into exactly the loop R4 exists to take them out of.
    """
    notes: list[str] = []
    suspect: list[str] = []

    charging_mode = _infer_charging_mode(rule, notes)
    rule_type_code, type_alias = resolve_rule_type(rule.rule_type)
    if type_alias:
        notes.append(f"Rule type '{type_alias}' mapped to '{rule_type_code}'.")

    root_group = _condition_tree(rule)
    actions = tuple(
        _action(action, index, notes, suspect)
        for index, action in enumerate(sorted(rule.actions, key=lambda a: a.sequence))
    )

    draft = CanonicalDraft(
        # The key is carried across verbatim. A backfill that re-derived identity
        # would orphan every rating result that references the old one, which is
        # the one thing a migration of an assurance platform may never do.
        rule_key=rule.rule_key,
        rule_name=rule.name,
        description=rule.description or "",
        charging_mode=charging_mode,
        rule_type_code=rule_type_code,
        service_type=rule.service_type,
        root_group=root_group,
        actions=actions,
        behaviour=DraftBehaviour(
            priority=rule.priority,
            stacking_policy=rule.stacking_policy,
            # Older rows used NO/NONE as a sentinel for an absent conflict
            # group. Treating it as a real registry code quarantines an
            # otherwise valid rule during canonical backfill.
            conflict_group=(
                None
                if str(rule.conflict_group or "").strip().upper()
                in {"", "NO", "NONE", "N/A", "NULL"}
                else rule.conflict_group
            ),
            condition_logic=rule.condition_logic,
            # Every legacy rule is an offline recalculation rule; nothing in the
            # legacy vocabulary can express online session charging, so claiming
            # otherwise would be inventing a fact.
            execution_mode=ExecutionMode.BOTH,
        ),
        targets=DraftTargets(
            product=(product_codes or {}).get(rule.product_id or ""),
            offer=(offer_codes or {}).get(rule.offer_id or ""),
            tariff_plan=(tariff_plan_codes or {}).get(rule.tariff_plan_id or ""),
        ),
        validity=DraftValidity(
            effective_from=rule.effective_from,
            effective_to=rule.effective_to,
            currency_code=rule.currency_code,
        ),
        provenance=Provenance(
            channel="MIGRATION",
            # The legacy `source_system` is free text, not a registered code, so
            # it is preserved as provenance rather than resolved to a source
            # system id that may not exist.
            external_ref=rule.id,
            external_version=str(rule.version),
            fields={
                "rule_key": FieldProvenance("rules.rule_key", rule.rule_key, "verbatim"),
                "source_system": FieldProvenance(
                    "rules.source_system", rule.source_system, "verbatim"
                ),
            },
            unmapped=dict(rule.attributes or {}),
        ),
        owner=rule.owner,
        change_reason=f"Backfilled from rating.rules v{rule.version}.",
    )
    return Conversion(draft=draft, notes=notes, suspect_money=suspect)


# --- Charging mode ----------------------------------------------------------


def _infer_charging_mode(rule: Rule, notes: list[str]) -> str:
    """Read the mode off an ``account_type`` condition, or default to BOTH.

    Defaulting to ``BOTH`` rather than guessing is deliberate: a rule wrongly
    marked PREPAID stops applying to half the estate, whereas ``BOTH`` preserves
    exactly the behaviour the rule has today. It is also the only value that
    cannot be wrong, since every legacy rule uses common stages only.
    """
    for condition in rule.conditions:
        if condition.attribute != "account_type":
            continue
        values = [str(v).strip().upper() for v in (condition.values or [])]
        modes = {_MODE_FROM_ACCOUNT_TYPE.get(v) for v in values} - {None}
        if len(modes) == 1:
            mode = modes.pop()
            notes.append(
                f"Charging mode inferred as {mode} from the account_type condition."
            )
            return str(mode)
        if modes:
            notes.append(
                "The account_type condition names several account types, so the "
                "charging mode stays BOTH."
            )
        break
    return ChargingMode.BOTH


# --- Conditions -------------------------------------------------------------


def _condition_tree(rule: Rule) -> DraftConditionGroup:
    """Flat ``group_index`` + one rule-level logic → one nested tree.

    An exact, information-preserving mapping: conditions sharing an index were
    ANDed, and the groups were combined with the rule's ``condition_logic``. A
    single group needs no bracket at all, so it collapses into the root — which
    keeps the behaviour hash of a simple rule from depending on whether the
    author happened to use group 0.
    """
    by_index: dict[int, list[DraftCondition]] = defaultdict(list)
    for condition in sorted(rule.conditions, key=lambda c: c.sequence):
        by_index[condition.group_index].append(
            DraftCondition(
                attribute=condition.attribute,
                operator=condition.operator,
                values=tuple(condition.values or ()),
                negated=condition.negate,
                sequence=condition.sequence,
            )
        )

    if len(by_index) <= 1:
        only = next(iter(by_index.values()), [])
        return DraftConditionGroup(logic=rule.condition_logic, conditions=tuple(only))

    return DraftConditionGroup(
        logic=rule.condition_logic,
        children=tuple(
            DraftConditionGroup(
                logic="AND",
                sequence=position,
                label=f"Group {index}",
                conditions=tuple(conditions),
            )
            for position, (index, conditions) in enumerate(sorted(by_index.items()))
        ),
    )


# --- Actions and parameters -------------------------------------------------


def _action(
    action: Any, sequence: int, notes: list[str], suspect: list[str]
) -> DraftAction:
    code, alias = resolve_action(action.action_type)
    if alias:
        notes.append(f"Action '{alias}' mapped to '{code}'.")

    spec = ACTION_BY_CODE.get(code)
    declared = {p.key: p for p in spec.params} if spec else {}

    parameters: list[DraftParameter] = []
    for name, raw in sorted((action.params or {}).items()):
        if raw is None or raw == "":
            continue
        param_spec = declared.get(name)
        value_type = param_spec.value_type if param_spec else _infer_value_type(raw)
        value = raw
        if value_type == ValueType.MONEY:
            value, note = _money(raw, f"{code}.{name}")
            if note:
                suspect.append(note)
        parameters.append(
            DraftParameter(name=name, raw=value, value_type=value_type)
        )

    return DraftAction(
        action_type=code, parameters=tuple(parameters), sequence=sequence
    )


def _money(raw: Any, where: str) -> tuple[Decimal | Any, str | None]:
    """Recover the decimal a human typed from whatever the float became.

    ``repr`` of a Python float is the shortest string that round-trips to it, so
    a rate stored as 0.012345 comes back as exactly that rather than as
    0.012344999999999999. Where the shortest form still needs more precision than
    the canonical scale allows, the value is *reported* — carrying a rate we
    cannot vouch for into the money-exact model silently would defeat the point
    of building it.
    """
    try:
        value = Decimal(repr(raw)) if isinstance(raw, float) else Decimal(str(raw))
    except (ArithmeticError, ValueError, TypeError):
        return raw, f"{where}: '{raw}' is not a number."

    exponent = value.as_tuple().exponent
    digits = -int(exponent) if isinstance(exponent, int) and exponent < 0 else 0
    if digits > _MAX_MONEY_DIGITS:
        return value, (
            f"{where}: {raw!r} needs {digits} decimal places, more than the "
            f"canonical scale of {_MAX_MONEY_DIGITS}. It was stored as a float, so "
            "the original value cannot be recovered with certainty — confirm it "
            "against the source document."
        )
    return value, None


def _infer_value_type(raw: Any) -> str:
    """Last resort, for a parameter no action spec declares.

    Only reached for vendor extras the registry does not model. Everything the
    system actually charges on has a declared type, which is the point.
    """
    if isinstance(raw, bool):
        return ValueType.BOOLEAN
    if isinstance(raw, int | float | Decimal):
        return ValueType.NUMBER
    if isinstance(raw, list | tuple):
        return ValueType.LIST
    return ValueType.STRING


def known_attribute(key: str) -> bool:
    """Whether the canonical registry recognises a legacy attribute.

    Used by the backfill's pre-flight: an unknown attribute is a rule that cannot
    be converted, and finding all of them before writing anything is worth one
    extra pass over the estate.
    """
    return key in CANONICAL_ATTRIBUTE_BY_KEY
