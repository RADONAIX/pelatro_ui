"""Structural checks — everything answerable from the draft alone.

No database, no catalogue lookups, no other rules. That matters for two
reasons: the wizard can run this tier on every keystroke without a round trip,
and a 40,000-record import can run it before it has resolved a single code, so
a malformed batch fails in seconds rather than after twenty catalogue queries.

The checks mirror the legacy validator's structural tier — attribute exists,
operator is legal for its type, arity matches, required actions present,
parameters typed — restated against ``CanonicalDraft`` rather than the flat ORM
rows. Where the legacy version re-inferred a value's type from the value itself,
this one uses the declared type, which is the point of having declared it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.core.config import settings
from app.modules.rules.canonical.draft import CanonicalDraft, DraftConditionGroup
from app.modules.rules.canonical.valuetypes import ValueError_, encode
from app.modules.rules.constants import OPERATOR_ARITY, OPERATORS_BY_TYPE, Operator
from app.modules.rules.validation.issues import Issue, error, warn
from app.modules.rules.vocabulary.actions import ACTION_BY_CODE
from app.modules.rules.vocabulary.attributes import CANONICAL_ATTRIBUTE_BY_KEY
from app.modules.rules.vocabulary.types import RULE_TYPE_BY_CODE
from app.modules.rules.vocabulary.values import ValueType

#: Deeper than this and no author can read the rule back, while the compiler's
#: key expansion goes exponential. A policy judgement, so it lives here rather
#: than in a CHECK constraint.
MAX_GROUP_DEPTH = 4


def check(draft: CanonicalDraft) -> list[Issue]:
    return [
        *_header(draft),
        *_shape(draft),
        *_conditions(draft),
        *_actions(draft),
    ]


# --- Header -----------------------------------------------------------------


def _header(draft: CanonicalDraft) -> list[Issue]:
    issues: list[Issue] = []
    if not draft.rule_name.strip():
        issues.append(error("name_required", "A rule name is required.", "rule_name"))
    if not draft.service_type:
        issues.append(
            error("service_type_required", "A service type is required.", "service_type")
        )

    validity = draft.validity
    if validity.effective_to and validity.effective_to < validity.effective_from:
        issues.append(
            error(
                "invalid_validity_window",
                "The effective-to date is before the effective-from date.",
                "validity.effective_to",
                hint="Leave it blank for an open-ended rule.",
            )
        )
    elif validity.effective_to and validity.effective_to < date.today():
        issues.append(
            warn(
                "already_expired",
                "This rule's validity window has already closed, so it will never "
                "be selected.",
                "validity.effective_to",
                hint="Extend the date, or retire the rule instead of leaving it live.",
            )
        )
    return issues


def _shape(draft: CanonicalDraft) -> list[Issue]:
    issues: list[Issue] = []
    depth = draft.condition_depth
    if depth > MAX_GROUP_DEPTH:
        issues.append(
            error(
                "conditions_too_deep",
                f"Conditions are nested {depth} deep; the limit is {MAX_GROUP_DEPTH}.",
                "conditions",
                hint="Split the rule, or express the inner bracket as an IN list.",
            )
        )
    if len(draft.conditions) > settings.max_conditions_per_rule:
        issues.append(
            error(
                "too_many_conditions",
                f"A rule may have at most {settings.max_conditions_per_rule} "
                f"conditions; this one has {len(draft.conditions)}.",
                "conditions",
            )
        )
    if len(draft.actions) > settings.max_actions_per_rule:
        issues.append(
            error(
                "too_many_actions",
                f"A rule may have at most {settings.max_actions_per_rule} actions; "
                f"this one has {len(draft.actions)}.",
                "actions",
            )
        )
    if not draft.actions:
        issues.append(
            error(
                "no_actions",
                "A rule must define at least one action.",
                "actions",
                hint="Without one it matches CDRs and changes nothing, which is "
                     "indistinguishable from a broken rule.",
            )
        )
    if not draft.conditions:
        issues.append(
            warn(
                "no_conditions",
                "This rule has no conditions and will match every event of its "
                "service type.",
                "conditions",
                hint="That is correct for a global default — otherwise add a condition.",
            )
        )
    return issues


# --- Conditions -------------------------------------------------------------


def _conditions(draft: CanonicalDraft) -> list[Issue]:
    issues: list[Issue] = []
    index = 0
    for group_number, group in enumerate(draft.root_group.walk()):
        issues.extend(_group_conditions(group, group_number, index))
        index += len(group.conditions)
    return issues


def _group_conditions(
    group: DraftConditionGroup, group_number: int, offset: int
) -> list[Issue]:
    issues: list[Issue] = []
    # Same attribute constrained twice with the same operator inside one AND
    # group is either a typo or unsatisfiable (service = VOICE AND service = SMS).
    seen: set[tuple[str, str]] = set()

    for local, cond in enumerate(group.conditions):
        path = f"conditions[{offset + local}]"
        attr = CANONICAL_ATTRIBUTE_BY_KEY.get(cond.attribute)
        if attr is None:
            issues.append(
                error(
                    "unknown_attribute",
                    f"'{cond.attribute}' is not a known rating attribute.",
                    f"{path}.attribute",
                )
            )
            continue

        allowed = OPERATORS_BY_TYPE.get(attr.data_type, ())
        if allowed and cond.operator not in allowed:
            issues.append(
                error(
                    "operator_not_allowed",
                    f"'{cond.operator}' cannot be used with {attr.label} "
                    f"({attr.data_type}).",
                    f"{path}.operator",
                    hint=f"Allowed here: {', '.join(allowed)}.",
                )
            )
            continue

        low, high = OPERATOR_ARITY.get(cond.operator, (1, 1))
        count = len(cond.values)
        if count < low or (high is not None and count > high):
            expected = str(low) if high == low else f"{low}-{high or 'many'}"
            issues.append(
                error(
                    "wrong_value_count",
                    f"'{cond.operator}' expects {expected} value(s), got {count}.",
                    f"{path}.values",
                )
            )
            continue

        issues.extend(_condition_values(cond, attr, path))

        if cond.operator == Operator.BETWEEN and count == 2:
            issues.extend(_between_ordered(cond, path))

        signature = (cond.attribute, cond.operator)
        if signature in seen and group.logic == "AND":
            issues.append(
                warn(
                    "duplicate_condition",
                    f"{attr.label} is constrained twice with the same operator in "
                    "this group.",
                    f"{path}.attribute",
                    hint="Use IN with several values instead of repeating the "
                         "attribute — two ANDed equalities can never both hold.",
                )
            )
        seen.add(signature)

    del group_number
    return issues


def _condition_values(cond, attr, path: str) -> list[Issue]:
    """Type each value through the same codec the writer uses.

    Deliberately the codec rather than a parallel set of type predicates: a check
    that accepts a value the writer then rejects is worse than no check, because
    the author sees a clean validation and a failed save.
    """
    issues: list[Issue] = []
    for position, value in enumerate(cond.values):
        try:
            encode(
                value,
                attr.data_type,
                field=f"{path}.values[{position}]",
                allowed=tuple(attr.values or ()),
                unit=cond.unit,
                currency=cond.currency,
            )
        except ValueError_ as exc:
            issues.append(
                error(
                    "value_not_storable",
                    exc.message,
                    exc.field or f"{path}.values[{position}]",
                )
            )
    return issues


def _between_ordered(cond, path: str) -> list[Issue]:
    try:
        low, high = (Decimal(str(v)) for v in cond.values)
    except (ArithmeticError, ValueError):
        return []
    if low > high:
        return [
            error(
                "between_bounds_reversed",
                "The BETWEEN lower bound is greater than the upper bound, so the "
                "condition can never be true.",
                f"{path}.values",
            )
        ]
    return []


# --- Actions ----------------------------------------------------------------


def _actions(draft: CanonicalDraft) -> list[Issue]:
    issues: list[Issue] = []

    spec = RULE_TYPE_BY_CODE.get(draft.rule_type_code)
    if spec is not None and spec.required_action_types:
        present = set(draft.action_types)
        if not present & set(spec.required_action_types):
            issues.append(
                error(
                    "missing_required_action",
                    f"A {spec.name} rule must carry one of: "
                    f"{', '.join(spec.required_action_types)}.",
                    "actions",
                    hint="Without it the rule matches and produces no charge, which "
                         "reads as a zero rate rather than as a mistake.",
                )
            )

    for index, action in enumerate(draft.actions):
        path = f"actions[{index}]"
        action_spec = ACTION_BY_CODE.get(action.action_type)
        if action_spec is None:
            issues.append(
                error(
                    "unknown_action",
                    f"'{action.action_type}' is not a known action.",
                    f"{path}.action_type",
                )
            )
            continue

        declared = {p.key: p for p in action_spec.params}
        supplied = {p.name for p in action.parameters}

        for extra in sorted(supplied - set(declared)):
            issues.append(
                warn(
                    "unknown_action_param",
                    f"'{extra}' is not a parameter of {action_spec.label} and will "
                    "be ignored.",
                    f"{path}.params.{extra}",
                )
            )

        for param_spec in action_spec.params:
            if param_spec.required and param_spec.key not in supplied:
                issues.append(
                    error(
                        "missing_action_param",
                        f"{action_spec.label} requires '{param_spec.label}'.",
                        f"{path}.params.{param_spec.key}",
                    )
                )

        for param in action.parameters:
            issues.extend(
                _parameter(param, declared.get(param.name), draft, f"{path}.params")
            )

    return issues


def _parameter(param, declared, draft: CanonicalDraft, path: str) -> list[Issue]:
    if declared is None:
        return []
    field_path = f"{path}.{param.name}"
    issues: list[Issue] = []

    if declared.value_type != param.value_type and param.value_type != ValueType.STRING:
        issues.append(
            warn(
                "param_type_mismatch",
                f"'{declared.label}' is declared {declared.value_type} but was given "
                f"as {param.value_type}.",
                field_path,
            )
        )

    try:
        typed = encode(
            param.raw,
            declared.value_type,
            field=field_path,
            allowed=declared.values,
            currency=param.currency or draft.validity.currency_code,
            unit=param.unit,
        )
    except ValueError_ as exc:
        return [*issues, error("param_not_storable", exc.message, exc.field or field_path)]

    if typed.numeric is not None and typed.numeric < 0:
        issues.append(
            error(
                "negative_amount",
                f"'{declared.label}' cannot be negative.",
                field_path,
            )
        )
    return issues
