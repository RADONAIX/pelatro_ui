"""Read normalized rows back into a :class:`CanonicalDraft`.

Exists for one reason above all others: it makes losslessness *testable*. A writer
you cannot reverse is a writer whose bugs you find in production, six months later,
when someone asks why the stored rate does not match the vendor's document.

Also the basis of the R4 parity job (canonical rows → draft → compiled output,
compared against the legacy path) and of the diff view (two versions → two drafts →
field-level differences).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rules.canonical.draft import (
    CanonicalDraft,
    DraftAction,
    DraftBehaviour,
    DraftCondition,
    DraftConditionGroup,
    DraftDependency,
    DraftFallback,
    DraftParameter,
    DraftTargets,
    DraftValidity,
    FieldProvenance,
    Provenance,
)
from app.modules.rules.canonical.graph import RuleDependency, RuleFallback
from app.modules.rules.canonical.lineage import RuleSourceLineage
from app.modules.rules.canonical.logic import (
    RuleActionRow,
    RuleConditionGroup,
    RuleConditionRow,
    RuleParameter,
)
from app.modules.rules.canonical.lookups import (
    RuleConflictGroupRow,
    RuleStackingPolicyRow,
)
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.canonical.valuetypes import TypedValue
from app.modules.rules.constants import Operator
from app.modules.rules.vocabulary.values import NUMERIC_VALUE_TYPES, ValueType

_NULLARY = frozenset({Operator.EXISTS, Operator.NOT_EXISTS})
_MULTI = frozenset({Operator.IN, Operator.NOT_IN, Operator.BETWEEN})


def _raw_values(row: RuleConditionRow) -> tuple:
    """Recover the value list a condition was written from.

    Numeric values come back as ``Decimal`` from the numeric column rather than
    being re-parsed from the text form, so a round-trip cannot introduce the very
    float that the codec exists to prevent.
    """
    if row.operator_code in _NULLARY:
        return ()
    if row.operator_code in _MULTI:
        elements = list(row.comparison_values or [])
        if row.comparison_value_type in NUMERIC_VALUE_TYPES or (
            row.comparison_value_numeric is not None
        ):
            return tuple(Decimal(str(e)) for e in elements)
        return tuple(elements)
    if row.comparison_value_numeric is not None:
        return (row.comparison_value_numeric,)
    if row.comparison_value_type == ValueType.BOOLEAN:
        return (row.comparison_value == "true",)
    return (row.comparison_value,)


def typed_from_condition(row: RuleConditionRow) -> TypedValue:
    return TypedValue(
        value_type=row.comparison_value_type,
        text=row.comparison_value,
        numeric=row.comparison_value_numeric,
        elements=tuple(row.comparison_values or ()),
        currency_code=row.currency_code,
        unit_code=row.unit_code,
        resolved_ref_id=row.resolved_ref_id,
    )


def _param_raw(row: RuleParameter) -> object:
    if row.parameter_value_numeric is not None:
        return row.parameter_value_numeric
    if row.parameter_value_type == ValueType.BOOLEAN:
        return row.parameter_value == "true"
    return row.parameter_value


async def read_draft(db: AsyncSession, rule_version_id: str) -> CanonicalDraft:
    """Reconstruct the draft one version was written from."""
    version = await db.get(CanonicalRuleVersion, rule_version_id)
    if version is None:
        raise LookupError(f"Rule version '{rule_version_id}' was not found.")
    rule = await db.get(CanonicalRule, version.rule_id)
    if rule is None:  # pragma: no cover - FK guarantees it
        raise LookupError(f"Rule '{version.rule_id}' was not found.")

    rule_type_code = await _code_of_rule_type(db, rule.rule_type_id)
    policy = await db.get(RuleStackingPolicyRow, version.stacking_policy_id)
    conflict = (
        await db.get(RuleConflictGroupRow, version.conflict_group_id)
        if version.conflict_group_id
        else None
    )

    groups = (
        await db.execute(
            select(RuleConditionGroup)
            .where(RuleConditionGroup.rule_version_id == rule_version_id)
            .order_by(RuleConditionGroup.sequence_number)
        )
    ).scalars().all()
    conditions = (
        await db.execute(
            select(RuleConditionRow)
            .where(RuleConditionRow.rule_version_id == rule_version_id)
            .order_by(RuleConditionRow.sequence_number)
        )
    ).scalars().all()

    by_group: dict[str, list[RuleConditionRow]] = {}
    for row in conditions:
        by_group.setdefault(row.condition_group_id, []).append(row)
    children: dict[str | None, list[RuleConditionGroup]] = {}
    for group in groups:
        children.setdefault(group.parent_group_id, []).append(group)

    def build_group(group: RuleConditionGroup) -> DraftConditionGroup:
        return DraftConditionGroup(
            logic=group.group_logic,
            negated=group.negated_flag,
            label=group.label,
            sequence=group.sequence_number,
            conditions=tuple(
                DraftCondition(
                    attribute=row.attribute_name,
                    operator=row.operator_code,
                    values=_raw_values(row),
                    negated=row.negated_flag,
                    unit=row.unit_code,
                    currency=row.currency_code,
                    sequence=row.sequence_number,
                )
                for row in sorted(
                    by_group.get(group.condition_group_id, ()),
                    key=lambda r: r.sequence_number,
                )
            ),
            children=tuple(
                build_group(child)
                for child in sorted(
                    children.get(group.condition_group_id, ()),
                    key=lambda g: g.sequence_number,
                )
            ),
        )

    roots = children.get(None, [])
    root = build_group(roots[0]) if roots else DraftConditionGroup()

    action_rows = (
        await db.execute(
            select(RuleActionRow)
            .where(RuleActionRow.rule_version_id == rule_version_id)
            .order_by(RuleActionRow.execution_sequence)
        )
    ).scalars().all()
    param_rows = (
        await db.execute(
            select(RuleParameter)
            .where(RuleParameter.rule_version_id == rule_version_id)
            .order_by(RuleParameter.parameter_name, RuleParameter.sequence_number)
        )
    ).scalars().all()

    params_by_action: dict[str | None, list[RuleParameter]] = {}
    for row in param_rows:
        params_by_action.setdefault(row.rule_action_id, []).append(row)

    actions = tuple(
        DraftAction(
            action_type=row.action_type,
            target_attribute=row.target_attribute,
            sequence=row.execution_sequence,
            parameters=tuple(
                DraftParameter(
                    name=p.parameter_name,
                    raw=_param_raw(p),
                    value_type=p.parameter_value_type,
                    currency=p.currency_code,
                    unit=p.unit_code,
                    sequence=p.sequence_number,
                )
                for p in params_by_action.get(row.rule_action_id, ())
            ),
        )
        for row in action_rows
    )

    rule_parameters = tuple(
        DraftParameter(
            name=p.parameter_name,
            raw=_param_raw(p),
            value_type=p.parameter_value_type,
            currency=p.currency_code,
            unit=p.unit_code,
            sequence=p.sequence_number,
        )
        for p in params_by_action.get(None, ())
    )

    dependencies = tuple(
        DraftDependency(
            depends_on_rule_key=await_key,
            dependency_type=dep_type,
            notes=notes,
        )
        for await_key, dep_type, notes in (
            await db.execute(
                select(
                    CanonicalRule.rule_key,
                    RuleDependency.dependency_type,
                    RuleDependency.notes,
                )
                .join(CanonicalRule, CanonicalRule.rule_id == RuleDependency.depends_on_rule_id)
                .where(RuleDependency.rule_id == rule.rule_id)
                .order_by(CanonicalRule.rule_key)
            )
        ).all()
    )

    fallbacks = tuple(
        DraftFallback(fallback_rule_key=key, level=level, scope=scope, reason=reason)
        for key, level, scope, reason in (
            await db.execute(
                select(
                    CanonicalRule.rule_key,
                    RuleFallback.fallback_level,
                    RuleFallback.fallback_scope,
                    RuleFallback.reason,
                )
                .join(CanonicalRule, CanonicalRule.rule_id == RuleFallback.fallback_rule_id)
                .where(RuleFallback.rule_id == rule.rule_id)
                .order_by(RuleFallback.fallback_level)
            )
        ).all()
    )

    lineage = (
        await db.execute(
            select(RuleSourceLineage)
            .where(RuleSourceLineage.rule_version_id == rule_version_id)
            .order_by(RuleSourceLineage.imported_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    provenance = Provenance()
    if lineage is not None:
        provenance = Provenance(
            source_system_code=None,
            external_ref=lineage.external_ref,
            external_version=lineage.external_version,
            raw_hash=lineage.raw_hash,
            batch_id=lineage.batch_id,
            record_id=lineage.record_id,
            fields={
                name: FieldProvenance(
                    source_field=entry.get("source_field", ""),
                    raw=entry.get("raw"),
                    transform=entry.get("transform", ""),
                )
                for name, entry in (lineage.field_provenance or {}).items()
                if isinstance(entry, dict)
            },
            applied_aliases=dict(lineage.applied_aliases or {}),
            unmapped=dict(lineage.unmapped_fields or {}),
        )

    projection_targets = (version.canonical_json or {}).get("targets", {})

    return CanonicalDraft(
        rule_key=rule.rule_key,
        rule_name=rule.rule_name,
        description=rule.description,
        charging_mode=rule.charging_mode,
        rule_type_code=rule_type_code,
        service_type=rule.service_type,
        root_group=root,
        actions=actions,
        parameters=rule_parameters,
        behaviour=DraftBehaviour(
            priority=version.priority,
            stacking_policy=policy.code if policy else "EXCLUSIVE",
            conflict_group=conflict.code if conflict else None,
            fallback_policy=version.fallback_policy,
            stop_processing=version.stop_processing,
            execution_mode=version.execution_mode,
            condition_logic=version.condition_logic,
        ),
        targets=DraftTargets(
            product=projection_targets.get("product"),
            offer=projection_targets.get("offer"),
            tariff_plan=projection_targets.get("tariff_plan"),
        ),
        validity=DraftValidity(
            effective_from=version.effective_from,
            effective_to=version.effective_to,
            currency_code=version.currency_code,
        ),
        dependencies=dependencies,
        fallbacks=fallbacks,
        provenance=provenance,
        owner=rule.owner,
        change_reason=version.change_reason,
    )


async def _code_of_rule_type(db: AsyncSession, rule_type_id: str) -> str:
    from app.modules.rules.canonical.lookups import RuleTypeRow

    row = await db.get(RuleTypeRow, rule_type_id)
    return row.code if row else ""
