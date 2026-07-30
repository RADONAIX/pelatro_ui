"""Canonical rule reads, and the mapping between the API shape and a draft.

Two responsibilities, kept apart from the router so both are testable without
HTTP:

**Inbound**, :func:`to_draft` turns a validated request body into a
``CanonicalDraft``. That is all it does — no persistence decisions, no ORM
objects. Everything after it is the kernel's, which is what makes the wizard
provably the same write path as a 40,000-row import rather than the same write
path by assertion.

**Outbound**, the read functions assemble the catalogue row and the detail view.
The list query is the one with a performance contract (§F.2: p95 under 200 ms
with all eight filters at 40,000 rules), so it reads indexed columns on
``rule``/``rule_version`` and joins the lookup tables — never a JSONB scan, which
is most of why the normalized model was worth migrating to.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.catalog import models as cm
from app.modules.rules.api import schemas as s
from app.modules.rules.canonical import specificity
from app.modules.rules.canonical.draft import (
    CanonicalDraft,
    DraftAction,
    DraftBehaviour,
    DraftCondition,
    DraftConditionGroup,
    DraftParameter,
    DraftTargets,
    DraftValidity,
    Provenance,
)
from app.modules.rules.canonical.lineage import RuleValidationIssue
from app.modules.rules.canonical.logic import (
    RuleActionRow,
    RuleConditionGroup,
    RuleConditionRow,
    RuleParameter,
)
from app.modules.rules.canonical.lookups import (
    RuleConflictGroupRow,
    RuleStackingPolicyRow,
    RuleStage,
    RuleTypeRow,
)
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.vocabulary.actions import ACTION_BY_CODE
from app.modules.rules.vocabulary.values import NUMERIC_VALUE_TYPES, ValueType

# --- Inbound ----------------------------------------------------------------


def to_draft(payload: s.RuleWrite, *, channel: str = "MANUAL") -> CanonicalDraft:
    """API body → the only shape the writer accepts.

    Deliberately total: every field the API offers maps onto a draft field, and
    a field the draft has no home for cannot be added to the API without someone
    noticing here. That is what stops the API and the storage model drifting.
    """
    return CanonicalDraft(
        rule_key=payload.rule_key,
        rule_name=payload.rule_name,
        description=payload.description,
        charging_mode=payload.charging_mode,
        rule_type_code=payload.rule_type_code,
        service_type=payload.service_type,
        root_group=_group(payload.conditions),
        actions=tuple(
            DraftAction(
                action_type=action.action_type,
                target_attribute=action.target_attribute,
                sequence=index if action.sequence == 0 else action.sequence,
                parameters=tuple(
                    DraftParameter(
                        name=p.name,
                        raw=p.value,
                        value_type=p.value_type,
                        currency=p.currency,
                        unit=p.unit,
                        sequence=p.sequence,
                    )
                    for p in action.parameters
                ),
            )
            for index, action in enumerate(payload.actions)
        ),
        behaviour=DraftBehaviour(
            priority=payload.behaviour.priority,
            stacking_policy=payload.behaviour.stacking_policy,
            conflict_group=payload.behaviour.conflict_group,
            fallback_policy=payload.behaviour.fallback_policy,
            stop_processing=payload.behaviour.stop_processing,
            execution_mode=payload.behaviour.execution_mode,
            condition_logic=payload.behaviour.condition_logic,
        ),
        targets=DraftTargets(
            product=payload.targets.product,
            offer=payload.targets.offer,
            tariff_plan=payload.targets.tariff_plan,
        ),
        validity=DraftValidity(
            effective_from=payload.validity.effective_from,
            effective_to=payload.validity.effective_to,
            currency_code=payload.validity.currency_code,
        ),
        set_codes=tuple(payload.set_codes),
        provenance=Provenance(channel=channel),
        owner=payload.owner,
        change_reason=payload.change_reason,
    )


def _group(group: s.ConditionGroupIn, sequence: int = 0) -> DraftConditionGroup:
    return DraftConditionGroup(
        logic=group.logic,
        negated=group.negated,
        label=group.label,
        sequence=sequence,
        conditions=tuple(
            DraftCondition(
                attribute=c.attribute,
                operator=c.operator,
                values=tuple(c.values),
                negated=c.negated,
                unit=c.unit,
                currency=c.currency,
                sequence=index,
            )
            for index, c in enumerate(group.conditions)
        ),
        children=tuple(
            _group(child, index) for index, child in enumerate(group.children)
        ),
    )


# --- Lookup caches ----------------------------------------------------------


async def lookup_maps(db: AsyncSession) -> dict[str, dict[str, Any]]:
    """Every id→code map a read needs, in four queries rather than four per row."""
    return {
        "stages": {
            r.rule_stage_id: r.code for r in (await db.execute(select(RuleStage))).scalars()
        },
        "types": {
            r.rule_type_id: r for r in (await db.execute(select(RuleTypeRow))).scalars()
        },
        "stacking": {
            r.stacking_policy_id: r.code
            for r in (await db.execute(select(RuleStackingPolicyRow))).scalars()
        },
        "conflicts": {
            r.conflict_group_id: r.code
            for r in (await db.execute(select(RuleConflictGroupRow))).scalars()
        },
    }


async def _catalogue_codes(db: AsyncSession, versions: list[CanonicalRuleVersion]) -> dict:
    """``{id: code}`` for the products, offers and plans this page references."""
    wanted = {
        "products": {v.product_id for v in versions if v.product_id},
        "offers": {v.offer_id for v in versions if v.offer_id},
        "tariff-plans": {v.tariff_plan_id for v in versions if v.tariff_plan_id},
    }
    models = {"products": cm.Product, "offers": cm.Offer, "tariff-plans": cm.TariffPlan}
    out: dict[str, dict[str, str]] = {}
    for slug, ids in wanted.items():
        if not ids:
            out[slug] = {}
            continue
        rows = (
            await db.execute(
                select(models[slug].id, models[slug].code).where(models[slug].id.in_(ids))
            )
        ).all()
        out[slug] = dict(rows)
    return out


# --- List / search ----------------------------------------------------------


def build_list_query(
    *,
    search: str | None = None,
    charging_mode: str | None = None,
    service_type: str | None = None,
    rule_type: str | None = None,
    status: str | None = None,
    product_id: str | None = None,
    source_system_id: str | None = None,
    snapshot_id: str | None = None,
    validation_state: str | None = None,
    execution_mode: str | None = None,
    effective_on: date | None = None,
    rule_set_id: str | None = None,
) -> Select:
    """One row per logical rule, joined to its current version.

    Every filter is a predicate on an indexed column of ``rule`` or
    ``rule_version``. That is the whole reason the normalized model was worth
    migrating to: the legacy equivalents of Snapshot and Validation would have
    been JSONB scans, and the catalogue's eight filters together would not have
    stayed inside the 200 ms budget at 40,000 rules.
    """
    stmt = (
        select(CanonicalRule, CanonicalRuleVersion)
        .join(
            CanonicalRuleVersion,
            CanonicalRuleVersion.rule_version_id == CanonicalRule.current_version_id,
            isouter=True,
        )
    )
    if search:
        needle = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(CanonicalRule.rule_name).like(needle),
                func.lower(CanonicalRule.rule_key).like(needle),
                func.lower(CanonicalRule.description).like(needle),
            )
        )
    if charging_mode:
        stmt = stmt.where(CanonicalRule.charging_mode == charging_mode)
    if service_type:
        stmt = stmt.where(CanonicalRule.service_type == service_type)
    if status:
        stmt = stmt.where(CanonicalRule.status == status)
    if source_system_id:
        stmt = stmt.where(CanonicalRule.source_system_id == source_system_id)
    if rule_type:
        stmt = stmt.where(
            CanonicalRule.rule_type_id.in_(
                select(RuleTypeRow.rule_type_id).where(RuleTypeRow.code == rule_type)
            )
        )
    if product_id:
        stmt = stmt.where(CanonicalRuleVersion.product_id == product_id)
    if snapshot_id:
        stmt = stmt.where(CanonicalRuleVersion.published_snapshot_id == snapshot_id)
    if validation_state:
        stmt = stmt.where(CanonicalRuleVersion.validation_state == validation_state)
    if execution_mode:
        stmt = stmt.where(CanonicalRuleVersion.execution_mode == execution_mode)
    if effective_on:
        stmt = stmt.where(
            and_(
                CanonicalRuleVersion.effective_from <= effective_on,
                or_(
                    CanonicalRuleVersion.effective_to.is_(None),
                    CanonicalRuleVersion.effective_to >= effective_on,
                ),
            )
        )
    if rule_set_id:
        from app.modules.rules.canonical.sets import RuleSetMember

        stmt = stmt.where(
            CanonicalRule.rule_id.in_(
                select(RuleSetMember.rule_id).where(
                    RuleSetMember.rule_set_id == rule_set_id
                )
            )
        )
    return stmt


async def summarise(
    db: AsyncSession,
    rows: list[tuple[CanonicalRule, CanonicalRuleVersion | None]],
    maps: dict[str, dict[str, Any]],
) -> list[s.RuleSummary]:
    """Catalogue rows, with the condition and action counts filled in.

    The counts come from two grouped queries over the whole page rather than a
    relationship load per row — the difference between one round trip and fifty
    on a page of fifty rules.
    """
    versions = [v for _, v in rows if v is not None]
    codes = await _catalogue_codes(db, versions)
    counts = await _child_counts(db, [v.rule_version_id for v in versions])
    sources = await _source_codes(db, [r.source_system_id for r, _ in rows])

    out: list[s.RuleSummary] = []
    for rule, version in rows:
        rule_type = maps["types"].get(rule.rule_type_id)
        counted = counts.get(version.rule_version_id if version else "", (0, 0))
        out.append(
            s.RuleSummary(
                rule_id=rule.rule_id,
                rule_key=rule.rule_key,
                rule_name=rule.rule_name,
                charging_mode=rule.charging_mode,
                rule_type_code=getattr(rule_type, "code", ""),
                rule_type_name=getattr(rule_type, "name", ""),
                stage_code=maps["stages"].get(rule.rule_stage_id, ""),
                service_type=rule.service_type,
                status=rule.status,
                owner=rule.owner,
                source=sources.get(rule.source_system_id or "", "MANUAL"),
                version_number=version.version_number if version else None,
                priority=version.priority if version else None,
                specificity_score=version.specificity_score if version else None,
                effective_from=version.effective_from if version else None,
                effective_to=version.effective_to if version else None,
                currency_code=version.currency_code if version else None,
                product_code=codes["products"].get(version.product_id or "") if version else None,
                offer_code=codes["offers"].get(version.offer_id or "") if version else None,
                tariff_plan_code=(
                    codes["tariff-plans"].get(version.tariff_plan_id or "")
                    if version
                    else None
                ),
                validation_state=version.validation_state if version else "UNKNOWN",
                published_snapshot_id=version.published_snapshot_id if version else None,
                condition_count=counted[0],
                action_count=counted[1],
                updated_at=rule.updated_at,
            )
        )
    return out


async def _child_counts(
    db: AsyncSession, version_ids: list[str]
) -> dict[str, tuple[int, int]]:
    if not version_ids:
        return {}
    conditions = dict(
        (
            await db.execute(
                select(RuleConditionRow.rule_version_id, func.count())
                .where(RuleConditionRow.rule_version_id.in_(version_ids))
                .group_by(RuleConditionRow.rule_version_id)
            )
        ).all()
    )
    actions = dict(
        (
            await db.execute(
                select(RuleActionRow.rule_version_id, func.count())
                .where(RuleActionRow.rule_version_id.in_(version_ids))
                .group_by(RuleActionRow.rule_version_id)
            )
        ).all()
    )
    return {
        vid: (int(conditions.get(vid, 0)), int(actions.get(vid, 0)))
        for vid in version_ids
    }


async def _source_codes(db: AsyncSession, ids: list[str | None]) -> dict[str, str]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    from app.modules.connectors.models import SourceSystem

    rows = (
        await db.execute(
            select(SourceSystem.id, SourceSystem.code).where(SourceSystem.id.in_(wanted))
        )
    ).all()
    return dict(rows)


# --- Detail -----------------------------------------------------------------


async def get_rule(db: AsyncSession, rule_id: str) -> CanonicalRule:
    rule = await db.get(CanonicalRule, rule_id)
    if rule is None:
        raise NotFoundError(f"Rule '{rule_id}' was not found.")
    return rule


async def detail(
    db: AsyncSession,
    rule: CanonicalRule,
    maps: dict[str, dict[str, Any]],
    *,
    version: CanonicalRuleVersion | None = None,
) -> s.RuleDetail:
    """The full rule, as the detail screen and the wizard's edit mode need it.

    The refresh is not defensive padding. A flush that updated the rule row
    expires its server-generated columns — ``updated_at`` carries an ``onupdate``
    — and reading an expired attribute under asyncio raises ``MissingGreenlet``
    rather than lazily loading, because implicit IO has nowhere to await. So a
    write followed by a render, which is exactly what every POST here does, must
    reload first. Scoped to instances that actually have unloaded attributes, so
    a plain read costs nothing extra.
    """
    from sqlalchemy import inspect as sa_inspect

    for instance in (rule, version):
        if instance is not None and sa_inspect(instance).unloaded:
            await db.refresh(instance)

    version = version or (
        await db.get(CanonicalRuleVersion, rule.current_version_id)
        if rule.current_version_id
        else None
    )
    summaries = await summarise(db, [(rule, version)], maps)
    base = summaries[0]
    data = s.RuleDetail(**base.model_dump())
    data.description = rule.description

    if version is None:
        return data

    version_id = version.rule_version_id
    data.rule_version_id = version_id
    data.execution_mode = version.execution_mode
    data.stacking_policy = maps["stacking"].get(version.stacking_policy_id, "EXCLUSIVE")
    data.conflict_group = maps["conflicts"].get(version.conflict_group_id or "")
    data.fallback_policy = version.fallback_policy
    data.stop_processing = version.stop_processing
    data.condition_logic = version.condition_logic
    data.behaviour_hash = version.behaviour_hash
    data.change_reason = version.change_reason
    data.extras = dict(version.extras or {})

    groups = await _rows(db, RuleConditionGroup, RuleConditionGroup.rule_version_id, version_id)
    conditions = await _rows(db, RuleConditionRow, RuleConditionRow.rule_version_id, version_id)
    actions = await _rows(db, RuleActionRow, RuleActionRow.rule_version_id, version_id)
    parameters = await _rows(db, RuleParameter, RuleParameter.rule_version_id, version_id)

    data.conditions = _condition_tree(groups, conditions)
    data.actions = _actions(actions, parameters)
    data.specificity = _specificity(data.conditions)
    data.issues = await _issues(db, version_id)
    data.versions = await _versions(db, rule.rule_id)
    return data


async def _rows(db: AsyncSession, model, column, value) -> list[Any]:
    return list((await db.execute(select(model).where(column == value))).scalars().all())


def _condition_tree(
    groups: list[RuleConditionGroup], conditions: list[RuleConditionRow]
) -> s.ConditionGroupOut | None:
    if not groups:
        return None
    by_group: dict[str, list[RuleConditionRow]] = {}
    for row in sorted(conditions, key=lambda c: c.sequence_number):
        by_group.setdefault(row.condition_group_id, []).append(row)

    children_of: dict[str | None, list[RuleConditionGroup]] = {}
    for group in sorted(groups, key=lambda g: g.sequence_number):
        children_of.setdefault(group.parent_group_id, []).append(group)

    def build(group: RuleConditionGroup) -> s.ConditionGroupOut:
        return s.ConditionGroupOut(
            logic=group.group_logic,
            negated=group.negated_flag,
            label=group.label,
            sequence=group.sequence_number,
            conditions=[
                s.ConditionOut(
                    attribute=row.attribute_name,
                    operator=row.operator_code,
                    values=_condition_values(row),
                    value_type=row.comparison_value_type,
                    negated=row.negated_flag,
                    unit_code=row.unit_code,
                    currency_code=row.currency_code,
                    resolved_ref_id=row.resolved_ref_id,
                    sequence=row.sequence_number,
                )
                for row in by_group.get(group.condition_group_id, [])
            ],
            children=[build(child) for child in children_of.get(group.condition_group_id, [])],
        )

    roots = children_of.get(None, [])
    return build(roots[0]) if roots else None


def _condition_values(row: RuleConditionRow) -> list[Any]:
    if row.comparison_values:
        return list(row.comparison_values)
    return [row.comparison_value] if row.comparison_value else []


def _actions(
    actions: list[RuleActionRow], parameters: list[RuleParameter]
) -> list[s.ActionOut]:
    by_action: dict[str | None, list[RuleParameter]] = {}
    for parameter in parameters:
        by_action.setdefault(parameter.rule_action_id, []).append(parameter)

    out: list[s.ActionOut] = []
    for row in sorted(actions, key=lambda a: a.execution_sequence):
        spec = ACTION_BY_CODE.get(row.action_type)
        out.append(
            s.ActionOut(
                action_type=row.action_type,
                label=getattr(spec, "label", row.action_type),
                stage_code=getattr(spec, "stage_code", ""),
                target_attribute=row.target_attribute,
                value=row.action_value,
                value_numeric=row.action_value_numeric,
                currency_code=row.currency_code,
                unit_code=row.unit_code,
                sequence=row.execution_sequence,
                parameters=[
                    s.ParameterOut(
                        name=p.parameter_name,
                        value=p.parameter_value,
                        value_type=p.parameter_value_type,
                        numeric=p.parameter_value_numeric,
                        currency_code=p.currency_code,
                        unit_code=p.unit_code,
                        resolved_ref_id=p.resolved_ref_id,
                        sequence=p.sequence_number,
                    )
                    for p in sorted(
                        by_action.get(row.rule_action_id, []),
                        key=lambda p: (p.parameter_name, p.sequence_number),
                    )
                ],
            )
        )
    return out


def _specificity(root: s.ConditionGroupOut | None) -> s.SpecificityOut:
    """The read-only score, with its working shown.

    Step 4 displays this instead of an editable field. An author-editable
    specificity beside an author-editable priority is two knobs for one job; the
    derivation is what replaces the knob — an author who disagrees with the score
    can see exactly which condition earned what, and change the condition.
    """
    if root is None:
        return s.SpecificityOut(score=0, derivation="No conditions — scores 0.")

    conditions = tuple(_flatten(root))
    explained = specificity.explain(
        DraftConditionGroup(
            conditions=tuple(
                DraftCondition(
                    attribute=c.attribute,
                    operator=c.operator,
                    values=tuple(c.values),
                    negated=c.negated,
                )
                for c in conditions
            )
        )
    )
    factors = [
        s.SpecificityFactor(
            attribute=part["attribute"],
            label=part["label"],
            operator=part["operator"],
            points=part["points"],
        )
        for part in explained["contributions"]
    ]
    derivation = (
        " + ".join(f"{f.label.lower()} {f.points}" for f in factors)
        + f" = {explained['total']}"
        if factors
        else "No scoring conditions — scores 0."
    )
    return s.SpecificityOut(
        score=explained["total"], factors=factors, derivation=derivation
    )


def _flatten(group: s.ConditionGroupOut):
    yield from group.conditions
    for child in group.children:
        yield from _flatten(child)


async def _issues(db: AsyncSession, version_id: str) -> list[s.IssueOut]:
    rows = (
        await db.execute(
            select(RuleValidationIssue)
            .where(RuleValidationIssue.rule_version_id == version_id)
            .order_by(RuleValidationIssue.severity, RuleValidationIssue.code)
        )
    ).scalars().all()
    return [
        s.IssueOut(
            severity=r.severity, code=r.code, message=r.message, path=r.path, hint=r.hint
        )
        for r in rows
    ]


async def _versions(db: AsyncSession, rule_id: str) -> list[s.VersionSummary]:
    rows = (
        await db.execute(
            select(CanonicalRuleVersion)
            .where(CanonicalRuleVersion.rule_id == rule_id)
            .order_by(CanonicalRuleVersion.version_number.desc())
        )
    ).scalars().all()
    return [s.VersionSummary.model_validate(r) for r in rows]


async def version_by_number(
    db: AsyncSession, rule_id: str, version_number: int
) -> CanonicalRuleVersion:
    version = (
        await db.execute(
            select(CanonicalRuleVersion).where(
                CanonicalRuleVersion.rule_id == rule_id,
                CanonicalRuleVersion.version_number == version_number,
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise NotFoundError(f"Version {version_number} of this rule was not found.")
    return version


def value_of(row: RuleParameter | RuleConditionRow) -> Any:
    """The stored value in its declared type, for a diff or an export."""
    value_type = getattr(row, "parameter_value_type", None) or getattr(
        row, "comparison_value_type", ValueType.STRING
    )
    numeric = getattr(row, "parameter_value_numeric", None) or getattr(
        row, "comparison_value_numeric", None
    )
    if value_type in NUMERIC_VALUE_TYPES and numeric is not None:
        return numeric
    return getattr(row, "parameter_value", None) or getattr(row, "comparison_value", "")
