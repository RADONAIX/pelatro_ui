"""A canonical rule version, shaped so the existing compiler can read it.

This is the bridge R4 turns on. It exists so the cut-over is a change of *source*
rather than a rewrite of the compiler: ``compile_rule`` keeps reading exactly the
attributes it reads today, and stops caring whether they came from
``rating.rules`` or from ``ra_rule.rule_version``.

Why that matters more than it looks. The parity gate the plan requires — "legacy
row → compiled output must equal canonical row → compiled output, byte-identical,
for 100% of rules" — is only meaningful if *the same compiler* produces both
sides. A parity check that compares the old compiler against a new one proves the
two implementations agree, which is a different and much weaker claim than the
one anybody cares about: that the migration changed nothing.

The view is deliberately read-only and deliberately dumb. Every field is either
copied or looked up in a map the caller loaded once; nothing is derived, because
a derivation here is a place the two sides could differ for a reason that has
nothing to do with the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.modules.rules.canonical.logic import (
    RuleActionRow,
    RuleConditionGroup,
    RuleConditionRow,
    RuleParameter,
)
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.vocabulary.values import NUMERIC_VALUE_TYPES, ValueType


@dataclass(slots=True)
class ConditionView:
    """What ``_compile_conditions`` reads off one predicate."""

    attribute: str
    operator: str
    values: list[Any]
    negate: bool
    group_index: int
    sequence: int


@dataclass(slots=True)
class ActionView:
    """What ``_compile_actions`` reads off one action."""

    action_type: str
    params: dict[str, Any]
    sequence: int


@dataclass(slots=True)
class RuleView:
    """A canonical rule version wearing the legacy row's interface.

    Attribute-for-attribute what ``compiler.compile_rule`` touches. Adding a
    field to the compiler without adding it here fails loudly at compile time
    rather than producing a snapshot with a silently missing dimension.
    """

    id: str
    rule_key: str
    version: int
    name: str
    rule_type: str
    execution_stage: str
    service_type: str
    priority: int
    specificity: int
    stacking_policy: str
    conflict_group: str | None
    condition_logic: str
    effective_from: date
    effective_to: date | None
    currency_code: str | None
    product_id: str | None
    offer_id: str | None
    tariff_plan_id: str | None
    conditions: list[ConditionView] = field(default_factory=list)
    actions: list[ActionView] = field(default_factory=list)


def build(
    rule: CanonicalRule,
    version: CanonicalRuleVersion,
    groups: list[RuleConditionGroup],
    conditions: list[RuleConditionRow],
    actions: list[RuleActionRow],
    parameters: list[RuleParameter],
    *,
    stage_code: str,
    rule_type_code: str,
    stacking_codes: dict[str, str],
    conflict_codes: dict[str, str] | None = None,
) -> RuleView:
    """Assemble the view from rows already loaded — no queries here.

    The caller loads a whole snapshot's worth of rows in a handful of statements
    and calls this per rule. Querying inside would put the per-row round trips
    the canonical model exists to remove straight back into the compile path.
    """
    group_index = _group_indexes(groups, conditions)
    params_by_action: dict[str | None, list[RuleParameter]] = {}
    for parameter in parameters:
        params_by_action.setdefault(parameter.rule_action_id, []).append(parameter)

    return RuleView(
        # The *version* id, not the rule id: a rating result references the exact
        # text that produced it, and the version is what carries that text.
        id=version.rule_version_id,
        rule_key=rule.rule_key,
        version=version.version_number,
        name=rule.rule_name,
        rule_type=rule_type_code,
        execution_stage=stage_code,
        service_type=rule.service_type,
        priority=version.priority,
        specificity=version.specificity_score,
        # Looked up in maps the caller loaded, not through a lazy relationship:
        # a lazy load inside a compile is a query per rule, and a *missing* lazy
        # load silently substitutes a default, which would show up as a parity
        # difference nobody could explain.
        stacking_policy=stacking_codes[version.stacking_policy_id],
        conflict_group=(conflict_codes or {}).get(version.conflict_group_id or ""),
        condition_logic=version.condition_logic,
        effective_from=version.effective_from,
        effective_to=version.effective_to,
        currency_code=version.currency_code,
        product_id=version.product_id,
        offer_id=version.offer_id,
        tariff_plan_id=version.tariff_plan_id,
        conditions=[
            _condition(row, group_index)
            for row in sorted(
                conditions, key=lambda c: (group_index.get(c.condition_group_id, 0),
                                           c.sequence_number)
            )
        ],
        actions=[
            _action(row, params_by_action.get(row.rule_action_id, []))
            for row in sorted(actions, key=lambda a: a.execution_sequence)
        ],
    )


def _group_indexes(
    groups: list[RuleConditionGroup], conditions: list[RuleConditionRow]
) -> dict[str, int]:
    """Flatten the condition tree back to the compiler's ``group_index``.

    The compiler's lookup key is flat, and always was: two levels is what every
    tariff we modelled needs. A deeper tree is not lost — it is in
    ``canonical_json`` and in the normalized rows — but the compiled form numbers
    the leaves it can act on, which is exactly the information the legacy model
    carried.

    **Only groups that hold conditions get a number**, in depth-first order. A
    rule imported from the legacy model with two bracketed groups has a root that
    holds nothing and exists solely to carry the OR between them; numbering that
    empty root 0 would shift its children to 1 and 2, and the compiled predicates
    would carry group indexes one higher than the rule they came from. That
    difference is invisible in the rule detail view and changes which predicates
    the engine ANDs together — which is precisely the class of silent divergence
    the parity gate exists to catch, and did.
    """
    ordered = sorted(groups, key=lambda g: (g.parent_group_id or "", g.sequence_number))
    populated = {c.condition_group_id for c in conditions}
    children_of: dict[str | None, list[RuleConditionGroup]] = {}
    for group in ordered:
        children_of.setdefault(group.parent_group_id, []).append(group)

    index: dict[str, int] = {}
    counter = 0

    def walk(group: RuleConditionGroup) -> None:
        nonlocal counter
        if group.condition_group_id in populated:
            index[group.condition_group_id] = counter
            counter += 1
        for child in children_of.get(group.condition_group_id, []):
            walk(child)

    for root in children_of.get(None, []):
        walk(root)
    return index


def _condition(row: RuleConditionRow, group_index: dict[str, int]) -> ConditionView:
    return ConditionView(
        attribute=row.attribute_name,
        operator=row.operator_code,
        values=_values(row),
        negate=row.negated_flag,
        group_index=group_index.get(row.condition_group_id, 0),
        sequence=row.sequence_number,
    )


def _values(row: RuleConditionRow) -> list[Any]:
    """Rebuild the legacy value list from the typed columns.

    Numbers come back as ``Decimal`` rather than the text form: the compiler
    normalises them itself, and handing it a string where the legacy row handed
    it a number would show up as a parity difference that is an artefact of this
    view rather than of the migration.
    """
    if row.comparison_values:
        return [_scalar(v, row.comparison_value_type) for v in row.comparison_values]
    if not row.comparison_value:
        return []
    return [_scalar(row.comparison_value, row.comparison_value_type)]


def _scalar(value: Any, value_type: str) -> Any:
    if value_type in NUMERIC_VALUE_TYPES:
        try:
            return Decimal(str(value))
        except (ArithmeticError, ValueError, TypeError):
            return value
    if value_type == ValueType.BOOLEAN:
        return str(value).strip().lower() == "true"
    return value


def _action(row: RuleActionRow, parameters: list[RuleParameter]) -> ActionView:
    """Rebuild the legacy ``params`` dict from the parameter rows.

    Note the direction of travel: ``params`` JSONB stopped being truth in R2 and
    is reconstructed here for one consumer. When the compiler reads
    ``canonical_json`` directly, this function goes.
    """
    params: dict[str, Any] = {}
    for parameter in sorted(parameters, key=lambda p: (p.parameter_name,
                                                       p.sequence_number)):
        params[parameter.parameter_name] = _parameter_value(parameter)
    return ActionView(
        action_type=row.action_type,
        params=params,
        sequence=row.execution_sequence,
    )


def _parameter_value(parameter: RuleParameter) -> Any:
    if parameter.parameter_value_numeric is not None:
        return parameter.parameter_value_numeric
    if parameter.parameter_value_type == ValueType.BOOLEAN:
        return parameter.parameter_value.strip().lower() == "true"
    return parameter.parameter_value
