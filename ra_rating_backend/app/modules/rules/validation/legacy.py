"""Structural rule validation (requirement §6, structural tier).

Scope, deliberately: everything checkable from the rule *itself* plus the
canonical metadata it references. Business-reference existence is checked here
too (it needs only a DB lookup); conflict detection across rules and coverage-
gap analysis need the whole rule set at once and land with the compiler in
Phase 2.

Every issue carries a ``path`` so the visual builder can highlight the exact
condition or action row rather than dumping a list of sentences at the author.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog import models as cm
from app.modules.rules.constants import (
    ACTION_BY_TYPE,
    ATTRIBUTE_BY_KEY,
    OPERATOR_ARITY,
    OPERATORS_BY_TYPE,
    REQUIRED_ACTION_FOR_TYPE,
    RULE_TYPE_STAGE,
    ActionType,
    DataType,
    Operator,
    ValidationSeverity,
)
from app.modules.rules.models import Rule
from app.modules.rules.schemas import ValidationIssue, ValidationReport

#: Canonical metadata slug → ORM model, for REFERENCE-typed values. Values are
#: entity *codes*, not ids, so a rule exported from staging imports into
#: production unchanged.
_REFERENCE_MODELS: dict[str, type] = {
    "products": cm.Product,
    "offers": cm.Offer,
    "tariff-plans": cm.TariffPlan,
    "destination-zones": cm.DestinationZone,
    "time-bands": cm.TimeBand,
    "rating-groups": cm.RatingGroup,
    "currencies": cm.Currency,
    "tax-rules": cm.TaxRule,
    "rounding-rules": cm.RoundingRule,
    "discounts": cm.DiscountDefinition,
    "bundles": cm.BundleDefinition,
    "promotions": cm.Promotion,
}


def _err(code: str, message: str, path: str = "", hint: str = "") -> ValidationIssue:
    return ValidationIssue(
        severity=ValidationSeverity.ERROR, code=code, message=message, path=path, hint=hint
    )


def _warn(code: str, message: str, path: str = "", hint: str = "") -> ValidationIssue:
    return ValidationIssue(
        severity=ValidationSeverity.WARNING, code=code, message=message, path=path, hint=hint
    )


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float | Decimal):
        return True
    try:
        Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return False
    return True


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool) or str(value).upper() in {"TRUE", "FALSE", "YES", "NO"}


def _is_datetime(value: Any) -> bool:
    if isinstance(value, datetime | date):
        return True
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    return True


def _value_matches_type(value: Any, data_type: str) -> bool:
    match data_type:
        case DataType.NUMBER:
            return _is_number(value)
        case DataType.BOOLEAN:
            return _is_bool(value)
        case DataType.DATETIME:
            return _is_datetime(value)
        case _:
            return isinstance(value, str | int | float) and str(value) != ""


async def _existing_codes(db: AsyncSession, slug: str, codes: set[str]) -> set[str]:
    model = _REFERENCE_MODELS.get(slug)
    if model is None or not codes:
        return set()
    rows = await db.execute(select(model.code).where(model.code.in_(codes)))
    return {r for (r,) in rows.all()}


async def validate_rule(db: AsyncSession, rule: Rule) -> ValidationReport:
    issues: list[ValidationIssue] = []

    # --- Header -------------------------------------------------------------
    if not rule.name.strip():
        issues.append(_err("name_required", "Rule name is required.", "name"))

    if rule.rule_type not in RULE_TYPE_STAGE:
        issues.append(
            _err("unknown_rule_type", f"Unknown rule type '{rule.rule_type}'.", "rule_type")
        )

    if rule.effective_to and rule.effective_from and rule.effective_to < rule.effective_from:
        issues.append(
            _err(
                "invalid_validity_window",
                "Effective-to date is before effective-from.",
                "effective_to",
                hint="Leave it blank for an open-ended rule.",
            )
        )
    if rule.effective_to and rule.effective_to < date.today():
        issues.append(
            _warn(
                "already_expired",
                "This rule's validity window has already closed.",
                "effective_to",
                hint="It will never be selected. Extend the date or retire the rule.",
            )
        )

    if rule.currency_code:
        known = await _existing_codes(db, "currencies", {rule.currency_code})
        if rule.currency_code not in known:
            issues.append(
                _err(
                    "unknown_currency",
                    f"Currency '{rule.currency_code}' is not in the catalogue.",
                    "currency_code",
                )
            )

    # --- Conditions ---------------------------------------------------------
    if not rule.conditions:
        issues.append(
            _warn(
                "no_conditions",
                "This rule has no conditions and will match every CDR of its service type.",
                "conditions",
                hint="That is valid for a global default rule — otherwise add a condition.",
            )
        )

    # Collect REFERENCE values per catalogue so existence is one query per
    # catalogue, not one per condition. A 50-condition rule would otherwise fire
    # 50 round trips on every keystroke-triggered validate.
    ref_lookups: dict[str, set[str]] = {}
    seen: set[tuple[str, str, str]] = set()

    for i, cond in enumerate(rule.conditions):
        path = f"conditions[{i}]"
        attr = ATTRIBUTE_BY_KEY.get(cond.attribute)
        if attr is None:
            issues.append(
                _err(
                    "unknown_attribute",
                    f"'{cond.attribute}' is not a known rating attribute.",
                    f"{path}.attribute",
                )
            )
            continue

        allowed = OPERATORS_BY_TYPE.get(attr.data_type, ())
        if cond.operator not in allowed:
            issues.append(
                _err(
                    "operator_not_allowed",
                    f"Operator '{cond.operator}' cannot be used with "
                    f"{attr.label} ({attr.data_type}).",
                    f"{path}.operator",
                    hint=f"Allowed: {', '.join(allowed)}.",
                )
            )
            continue

        low, high = OPERATOR_ARITY.get(cond.operator, (1, 1))
        n = len(cond.values or [])
        if n < low or (high is not None and n > high):
            expected = f"{low}" if high == low else f"{low}-{high or 'many'}"
            issues.append(
                _err(
                    "wrong_value_count",
                    f"'{cond.operator}' expects {expected} value(s), got {n}.",
                    f"{path}.values",
                )
            )
            continue

        for j, value in enumerate(cond.values or []):
            if not _value_matches_type(value, attr.data_type):
                issues.append(
                    _err(
                        "value_type_mismatch",
                        f"'{value}' is not a valid {attr.data_type.lower()} for {attr.label}.",
                        f"{path}.values[{j}]",
                    )
                )
            elif attr.data_type == DataType.ENUM and str(value).upper() not in attr.values:
                issues.append(
                    _err(
                        "value_not_in_enum",
                        f"'{value}' is not a valid {attr.label}.",
                        f"{path}.values[{j}]",
                        hint=f"Allowed: {', '.join(attr.values)}.",
                    )
                )
            elif attr.data_type == DataType.REFERENCE and attr.reference:
                ref_lookups.setdefault(attr.reference, set()).add(str(value))

        if (
            cond.operator == Operator.BETWEEN
            and len(cond.values or []) == 2
            and _is_number(cond.values[0])
            and _is_number(cond.values[1])
            and Decimal(str(cond.values[0])) > Decimal(str(cond.values[1]))
        ):
            issues.append(
                    _err(
                        "between_bounds_reversed",
                        "BETWEEN lower bound is greater than the upper bound.",
                        f"{path}.values",
                    )
                )

        # Same attribute + operator twice in one group is either a typo or an
        # unsatisfiable AND (service_type = VOICE AND service_type = SMS).
        signature = (str(cond.group_index), cond.attribute, cond.operator)
        if signature in seen:
            issues.append(
                _warn(
                    "duplicate_condition",
                    f"{attr.label} is constrained twice with the same operator in this group.",
                    f"{path}.attribute",
                    hint="Use IN with multiple values instead of repeating the attribute.",
                )
            )
        seen.add(signature)

    for slug, codes in ref_lookups.items():
        found = await _existing_codes(db, slug, codes)
        for missing in sorted(codes - found):
            issues.append(
                _err(
                    "unknown_reference",
                    f"'{missing}' does not exist in the {slug.replace('-', ' ')} catalogue.",
                    "conditions",
                    hint="Create it under Metadata Catalogue, or correct the value.",
                )
            )

    # --- Actions ------------------------------------------------------------
    if not rule.actions:
        issues.append(
            _err(
                "no_actions",
                "A rule must define at least one action.",
                "actions",
                hint="Without an action the rule matches CDRs but changes nothing.",
            )
        )

    action_types = {a.action_type for a in rule.actions}
    required = REQUIRED_ACTION_FOR_TYPE.get(rule.rule_type, ())
    if required and not action_types & set(required):
        issues.append(
            _err(
                "missing_required_action",
                f"A {rule.rule_type} rule must include one of: {', '.join(required)}.",
                "actions",
            )
        )

    action_refs: dict[str, set[str]] = {}
    for i, action in enumerate(rule.actions):
        path = f"actions[{i}]"
        spec = ACTION_BY_TYPE.get(action.action_type)
        if spec is None:
            issues.append(
                _err(
                    "unknown_action",
                    f"'{action.action_type}' is not a known action.",
                    f"{path}.action_type",
                )
            )
            continue

        params = action.params or {}
        known_params = {p.key for p in spec.params}
        for extra in sorted(set(params) - known_params):
            issues.append(
                _warn(
                    "unknown_action_param",
                    f"'{extra}' is not a parameter of {spec.label} and will be ignored.",
                    f"{path}.params.{extra}",
                )
            )

        for p in spec.params:
            value = params.get(p.key)
            missing = value is None or value == ""
            if p.required and missing:
                issues.append(
                    _err(
                        "missing_action_param",
                        f"{spec.label} requires '{p.label}'.",
                        f"{path}.params.{p.key}",
                    )
                )
                continue
            if missing:
                continue
            if not _value_matches_type(value, p.data_type):
                issues.append(
                    _err(
                        "action_param_type",
                        f"'{p.label}' must be a {p.data_type.lower()}.",
                        f"{path}.params.{p.key}",
                    )
                )
            elif p.data_type == DataType.ENUM and str(value).upper() not in p.values:
                issues.append(
                    _err(
                        "action_param_enum",
                        f"'{value}' is not a valid {p.label}.",
                        f"{path}.params.{p.key}",
                        hint=f"Allowed: {', '.join(p.values)}.",
                    )
                )
            elif p.data_type == DataType.REFERENCE and p.reference:
                action_refs.setdefault(p.reference, set()).add(str(value))
            elif p.data_type == DataType.NUMBER and _is_number(value) and Decimal(str(value)) < 0:
                issues.append(
                    _err(
                        "negative_amount",
                        f"'{p.label}' cannot be negative.",
                        f"{path}.params.{p.key}",
                    )
                )

        issues.extend(_action_semantics(spec.type, params, path))

    for slug, codes in action_refs.items():
        found = await _existing_codes(db, slug, codes)
        for missing in sorted(codes - found):
            issues.append(
                _err(
                    "unknown_reference",
                    f"'{missing}' does not exist in the {slug.replace('-', ' ')} catalogue.",
                    "actions",
                )
            )

    # A rule whose actions all sit at a different stage than its type implies is
    # almost always a mis-typed rule; it would compile into the wrong pass.
    expected_stage = RULE_TYPE_STAGE.get(rule.rule_type)
    if expected_stage and rule.actions:
        stages = {
            ACTION_BY_TYPE[a.action_type].stage
            for a in rule.actions
            if a.action_type in ACTION_BY_TYPE
        }
        if stages and expected_stage not in stages:
            issues.append(
                _warn(
                    "stage_mismatch",
                    f"This is a {rule.rule_type} rule but none of its actions run at the "
                    f"{expected_stage} stage.",
                    "rule_type",
                    hint="The rule type decides where the rule runs in the charging sequence.",
                )
            )

    errors = sum(1 for i in issues if i.severity == ValidationSeverity.ERROR)
    warnings = sum(1 for i in issues if i.severity == ValidationSeverity.WARNING)
    return ValidationReport(
        valid=errors == 0,
        checked_at=datetime.now(UTC),
        error_count=errors,
        warning_count=warnings,
        issues=issues,
    )


def _action_semantics(action_type: str, params: dict[str, Any], path: str) -> list[ValidationIssue]:
    """Cross-parameter rules that a per-field check can't express."""
    out: list[ValidationIssue] = []

    if action_type == ActionType.APPLY_DISCOUNT:
        given = [k for k in ("discount", "percentage", "amount") if params.get(k) not in (None, "")]
        if not given:
            out.append(
                _err(
                    "discount_underspecified",
                    "Set a catalogue discount, a percentage, or a fixed amount.",
                    f"{path}.params",
                )
            )
        elif len(given) > 1:
            out.append(
                _err(
                    "discount_overspecified",
                    "Only one of discount / percentage / amount may be set "
                    f"(got {', '.join(given)}).",
                    f"{path}.params",
                )
            )
        pct = params.get("percentage")
        if pct not in (None, "") and _is_number(pct) and not (0 <= Decimal(str(pct)) <= 100):
            out.append(
                _err("discount_percentage_range", "Percentage must be between 0 and 100.",
                     f"{path}.params.percentage")
            )

    elif action_type == ActionType.ADD_SURCHARGE:
        given = [k for k in ("percentage", "amount") if params.get(k) not in (None, "")]
        if not given:
            out.append(
                _err("surcharge_underspecified", "Set a surcharge percentage or amount.",
                     f"{path}.params")
            )
        elif len(given) > 1:
            out.append(
                _err("surcharge_overspecified",
                     "Set either a percentage or an amount, not both.", f"{path}.params")
            )

    elif action_type == ActionType.APPLY_ROUNDING:
        if not params.get("rounding_rule") and not params.get("mode"):
            out.append(
                _err("rounding_underspecified",
                     "Choose a catalogue rounding rule, or set a mode and decimals.",
                     f"{path}.params")
            )

    elif action_type == ActionType.SET_RATE:
        per = params.get("per_units")
        if per not in (None, "") and _is_number(per) and Decimal(str(per)) <= 0:
            out.append(
                _err("per_units_positive", "'Per units' must be greater than zero.",
                     f"{path}.params.per_units")
            )

    elif action_type == ActionType.SET_PULSE:
        initial = params.get("initial_seconds")
        if initial not in (None, "") and _is_number(initial) and Decimal(str(initial)) <= 0:
            out.append(
                _err("pulse_positive", "Initial pulse must be greater than zero.",
                     f"{path}.params.initial_seconds")
            )

    return out
