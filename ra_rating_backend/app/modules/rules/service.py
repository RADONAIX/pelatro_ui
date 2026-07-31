"""Rule authoring, versioning and lifecycle logic."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, RuleStateError, ValidationFailedError
from app.modules.mirror import hooks as mirror_hooks
from app.modules.rules import validation
from app.modules.rules.constants import (
    ALLOWED_TRANSITIONS,
    ATTRIBUTE_BY_KEY,
    EDITABLE_STATUSES,
    RULE_TYPE_STAGE,
    Operator,
    RuleStatus,
)
from app.modules.rules.models import (
    Rule,
    RuleAction,
    RuleAuditEntry,
    RuleCondition,
    RuleSet,
)
from app.modules.rules.schemas import ActionIn, ConditionIn, RuleCreate, RuleUpdate

_SLUG_RE = re.compile(r"[^A-Z0-9]+")

#: Fields copied verbatim when cutting a new version or cloning a rule.
_CARRIED_FIELDS = (
    "name", "description", "rule_type", "execution_stage", "category", "service_type",
    "rule_set_id", "product_id", "offer_id", "tariff_plan_id", "priority",
    "stacking_policy", "conflict_group", "condition_logic", "effective_from",
    "effective_to", "currency_code", "owner", "source_system", "attributes",
)


def derive_rule_key(name: str) -> str:
    """A stable, human-readable key from a rule name (``Peak On-net`` → ``PEAK_ON_NET``)."""
    key = _SLUG_RE.sub("_", name.strip().upper()).strip("_")
    return (key or "RULE")[:72]


def compute_specificity(conditions: list[RuleCondition] | list[ConditionIn]) -> int:
    """Score how narrowly a rule is targeted.

    Higher = more specific = wins a tie against a broader rule. Equality-style
    operators bind a dimension tightly and score full weight; set membership and
    ranges bind it loosely and score less. Existence checks bind nothing.

    This is what lets the requirement's 4-level fallback (exact → product →
    service → global default) fall out of the data instead of being hand-coded:
    a rule that pins product AND destination AND time band naturally outranks a
    service-wide default without anyone tuning priorities.
    """
    score = 0
    for cond in conditions:
        attr = ATTRIBUTE_BY_KEY.get(cond.attribute)
        if attr is None:
            continue
        match cond.operator:
            case Operator.EQUALS | Operator.STARTS_WITH:
                score += attr.specificity
            case Operator.IN:
                # Diminishing: an IN over 20 zones is barely narrower than "any".
                n = max(1, len(cond.values or []))
                score += max(1, attr.specificity // min(n, 5))
            case (
                Operator.BETWEEN
                | Operator.GREATER_THAN
                | Operator.LESS_THAN
                | Operator.GREATER_OR_EQUAL
                | Operator.LESS_OR_EQUAL
                | Operator.CONTAINS
            ):
                score += attr.specificity // 2
            case Operator.NOT_EQUALS | Operator.NOT_IN:
                score += 1
            case _:
                continue
    return score


# --- Reads ------------------------------------------------------------------


async def get_rule(db: AsyncSession, rule_id: str) -> Rule:
    rule = await db.get(Rule, rule_id)
    if rule is None:
        raise NotFoundError(f"Rule '{rule_id}' was not found.")
    return rule


async def latest_version(db: AsyncSession, rule_key: str) -> Rule | None:
    stmt = (
        select(Rule)
        .where(Rule.rule_key == rule_key)
        .order_by(Rule.version.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def versions_of(db: AsyncSession, rule_key: str) -> list[Rule]:
    stmt = select(Rule).where(Rule.rule_key == rule_key).order_by(Rule.version.desc())
    return list((await db.execute(stmt)).scalars().all())


def build_list_query(
    *,
    search: str | None = None,
    status: str | None = None,
    service_type: str | None = None,
    rule_type: str | None = None,
    product_id: str | None = None,
    rule_set_id: str | None = None,
    source_system: str | None = None,
    effective_on: date | None = None,
    latest_only: bool = True,
) -> Select:
    stmt = select(Rule)
    if search:
        needle = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Rule.name).like(needle),
                func.lower(Rule.rule_key).like(needle),
                func.lower(Rule.description).like(needle),
            )
        )
    if status:
        stmt = stmt.where(Rule.status == status)
    if service_type:
        stmt = stmt.where(Rule.service_type == service_type)
    if rule_type:
        stmt = stmt.where(Rule.rule_type == rule_type)
    if product_id:
        stmt = stmt.where(Rule.product_id == product_id)
    if rule_set_id:
        stmt = stmt.where(Rule.rule_set_id == rule_set_id)
    if source_system:
        stmt = stmt.where(Rule.source_system == source_system)
    if effective_on:
        stmt = stmt.where(
            Rule.effective_from <= effective_on,
            or_(Rule.effective_to.is_(None), Rule.effective_to >= effective_on),
        )
    if latest_only:
        # One row per logical rule: the highest version. A grouped subquery
        # joined back (rather than a window function) so it composes with every
        # filter above and stays index-friendly as the estate grows past 10k.
        latest = (
            select(Rule.rule_key, func.max(Rule.version).label("v"))
            .group_by(Rule.rule_key)
            .subquery()
        )
        stmt = stmt.join(
            latest,
            (Rule.rule_key == latest.c.rule_key) & (Rule.version == latest.c.v),
        )
    return stmt


def _carry(source: Rule) -> dict[str, Any]:
    """Copy the carried fields, deep-copying the mutable ones so two rule rows
    never end up sharing one JSONB dict."""
    carried = {f: getattr(source, f) for f in _CARRIED_FIELDS}
    carried["attributes"] = dict(source.attributes or {})
    return carried


# --- Writes -----------------------------------------------------------------


def _apply_children(
    rule: Rule,
    conditions: list[ConditionIn] | None,
    actions: list[ActionIn] | None,
) -> None:
    if conditions is not None:
        if len(conditions) > settings.max_conditions_per_rule:
            raise ValidationFailedError(
                f"A rule may have at most {settings.max_conditions_per_rule} conditions."
            )
        rule.conditions = [
            RuleCondition(
                sequence=i,
                group_index=c.group_index,
                attribute=c.attribute,
                operator=str(c.operator),
                values=list(c.values or []),
                negate=c.negate,
            )
            for i, c in enumerate(conditions)
        ]
    if actions is not None:
        if len(actions) > settings.max_actions_per_rule:
            raise ValidationFailedError(
                f"A rule may have at most {settings.max_actions_per_rule} actions."
            )
        rule.actions = [
            RuleAction(sequence=i, action_type=str(a.action_type), params=dict(a.params or {}))
            for i, a in enumerate(actions)
        ]


async def record_audit(
    db: AsyncSession,
    rule: Rule,
    *,
    action: str,
    actor_id: str | None,
    actor_name: str | None,
    from_status: str | None = None,
    to_status: str | None = None,
    comment: str = "",
    diff: dict[str, Any] | None = None,
) -> None:
    db.add(
        RuleAuditEntry(
            rule_key=rule.rule_key,
            rule_id=rule.id,
            version=rule.version,
            action=action,
            from_status=from_status,
            to_status=to_status,
            actor_id=actor_id,
            actor_name=actor_name,
            comment=comment,
            diff=diff or {},
        )
    )


async def create_rule(
    db: AsyncSession, payload: RuleCreate, *, actor_id: str, actor_name: str
) -> Rule:
    rule_key = payload.rule_key or derive_rule_key(payload.name)
    if await latest_version(db, rule_key) is not None:
        raise ConflictError(
            f"Rule key '{rule_key}' already exists.",
            details={
                "rule_key": rule_key,
                "hint": "Create a new version of the existing rule, or choose another key.",
            },
        )
    if payload.rule_set_id and await db.get(RuleSet, payload.rule_set_id) is None:
        raise NotFoundError(f"Rule set '{payload.rule_set_id}' was not found.")

    rule = Rule(
        rule_key=rule_key,
        version=1,
        name=payload.name,
        description=payload.description,
        rule_type=str(payload.rule_type),
        execution_stage=RULE_TYPE_STAGE[payload.rule_type],
        category=payload.category,
        service_type=str(payload.service_type),
        rule_set_id=payload.rule_set_id,
        product_id=payload.product_id,
        offer_id=payload.offer_id,
        tariff_plan_id=payload.tariff_plan_id,
        priority=payload.priority,
        stacking_policy=str(payload.stacking_policy),
        conflict_group=payload.conflict_group,
        condition_logic=str(payload.condition_logic),
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        currency_code=payload.currency_code,
        owner=payload.owner,
        source_system=payload.source_system,
        change_comment=payload.change_comment,
        attributes=payload.attributes,
        status=RuleStatus.DRAFT.value,
        created_by=actor_id,
    )
    _apply_children(rule, payload.conditions, payload.actions)
    rule.specificity = compute_specificity(rule.conditions)
    db.add(rule)
    await db.flush()
    await record_audit(
        db, rule, action="created", actor_id=actor_id, actor_name=actor_name,
        to_status=rule.status, comment=payload.change_comment,
    )
    await db.refresh(rule)
    mirror_hooks.record_legacy_rule(db, rule.id)
    return rule


async def update_rule(
    db: AsyncSession, rule_id: str, payload: RuleUpdate, *, actor_id: str, actor_name: str
) -> Rule:
    rule = await get_rule(db, rule_id)
    if rule.status not in EDITABLE_STATUSES:
        raise RuleStateError(
            f"A rule in '{rule.status}' cannot be edited.",
            details={
                "status": rule.status,
                "hint": "Create a new version instead — approved rules are immutable.",
            },
        )

    data = payload.model_dump(exclude_unset=True, exclude={"conditions", "actions"})
    diff: dict[str, Any] = {}
    for key, value in data.items():
        current = getattr(rule, key, None)
        new = str(value) if hasattr(value, "value") else value
        if current != new:
            diff[key] = {"from": _jsonable(current), "to": _jsonable(new)}
            setattr(rule, key, new)

    if "rule_type" in data:
        rule.execution_stage = RULE_TYPE_STAGE[rule.rule_type]

    if payload.conditions is not None or payload.actions is not None:
        _apply_children(rule, payload.conditions, payload.actions)
        diff["structure"] = {"from": "modified", "to": "modified"}

    rule.specificity = compute_specificity(rule.conditions)
    # Any edit invalidates the cached validation verdict — showing a stale green
    # tick next to changed logic is worse than showing nothing.
    rule.last_validation = None
    if rule.status == RuleStatus.VALIDATED:
        rule.status = RuleStatus.DRAFT.value

    await db.flush()
    if diff:
        await record_audit(
            db, rule, action="updated", actor_id=actor_id, actor_name=actor_name,
            comment=payload.change_comment or "", diff=diff,
        )
    await db.refresh(rule)
    mirror_hooks.record_legacy_rule(db, rule.id)
    return rule


async def new_version(
    db: AsyncSession, rule_id: str, *, change_comment: str, name: str | None,
    actor_id: str, actor_name: str,
) -> Rule:
    """Cut version N+1 as a fresh DRAFT, leaving the source row untouched.

    The source is NOT superseded here — that happens when the new version is
    published (Phase 2). Until then the live rule keeps rating.
    """
    source = await get_rule(db, rule_id)
    newest = await latest_version(db, source.rule_key)
    assert newest is not None
    if newest.status in EDITABLE_STATUSES and newest.id != source.id:
        raise ConflictError(
            f"Version {newest.version} of this rule is still a draft.",
            details={"rule_id": newest.id, "hint": "Finish or discard that draft first."},
        )

    draft = Rule(
        rule_key=source.rule_key,
        version=newest.version + 1,
        supersedes_id=source.id,
        status=RuleStatus.DRAFT.value,
        created_by=actor_id,
        change_comment=change_comment,
        **_carry(source),
    )
    if name:
        draft.name = name
    draft.conditions = [
        RuleCondition(
            sequence=c.sequence, group_index=c.group_index, attribute=c.attribute,
            operator=c.operator, values=list(c.values or []), negate=c.negate,
        )
        for c in source.conditions
    ]
    draft.actions = [
        RuleAction(sequence=a.sequence, action_type=a.action_type, params=dict(a.params or {}))
        for a in source.actions
    ]
    draft.specificity = source.specificity
    db.add(draft)
    await db.flush()
    await record_audit(
        db, draft, action="version_created", actor_id=actor_id, actor_name=actor_name,
        to_status=draft.status, comment=change_comment,
        diff={"from_version": source.version, "to_version": draft.version},
    )
    await db.refresh(draft)
    mirror_hooks.record_legacy_rule(db, draft.id)
    return draft


async def clone_rule(
    db: AsyncSession, rule_id: str, *, rule_key: str | None, name: str,
    actor_id: str, actor_name: str,
) -> Rule:
    """Copy a rule into a brand-new logical rule at version 1."""
    source = await get_rule(db, rule_id)
    key = rule_key or derive_rule_key(name)
    if await latest_version(db, key) is not None:
        raise ConflictError(f"Rule key '{key}' already exists.", details={"rule_key": key})

    carried = _carry(source)
    carried["name"] = name
    clone = Rule(
        rule_key=key,
        version=1,
        status=RuleStatus.DRAFT.value,
        created_by=actor_id,
        change_comment=f"Cloned from {source.rule_key} v{source.version}",
        **carried,
    )
    clone.conditions = [
        RuleCondition(
            sequence=c.sequence, group_index=c.group_index, attribute=c.attribute,
            operator=c.operator, values=list(c.values or []), negate=c.negate,
        )
        for c in source.conditions
    ]
    clone.actions = [
        RuleAction(sequence=a.sequence, action_type=a.action_type, params=dict(a.params or {}))
        for a in source.actions
    ]
    clone.specificity = source.specificity
    db.add(clone)
    await db.flush()
    await record_audit(
        db, clone, action="cloned", actor_id=actor_id, actor_name=actor_name,
        to_status=clone.status, comment=clone.change_comment,
        diff={"source_rule_key": source.rule_key, "source_version": source.version},
    )
    await db.refresh(clone)
    mirror_hooks.record_legacy_rule(db, clone.id)
    return clone


async def validate_and_cache(db: AsyncSession, rule: Rule):
    """Run structural validation and cache the verdict on the rule row."""
    report = await validation.validate_rule(db, rule)
    rule.last_validation = {
        "valid": report.valid,
        "checked_at": report.checked_at.isoformat(),
        "error_count": report.error_count,
        "warning_count": report.warning_count,
    }
    await db.flush()
    return report


async def change_status(
    db: AsyncSession, rule_id: str, target: str, *, comment: str,
    actor_id: str, actor_name: str,
) -> Rule:
    rule = await get_rule(db, rule_id)
    allowed = ALLOWED_TRANSITIONS.get(rule.status, ())
    if target not in allowed:
        raise RuleStateError(
            f"Cannot move a rule from '{rule.status}' to '{target}'.",
            details={"from": rule.status, "allowed": list(allowed)},
        )

    # Promotion out of DRAFT is gated on a clean structural validation — this is
    # what stops a broken rule reaching the approval queue.
    if target == RuleStatus.VALIDATED:
        report = await validate_and_cache(db, rule)
        if not report.valid:
            raise ValidationFailedError(
                "The rule has validation errors and cannot be marked as validated.",
                details={
                    "error_count": report.error_count,
                    "issues": [i.model_dump() for i in report.issues if i.severity == "ERROR"],
                },
            )

    previous = rule.status
    rule.status = target
    now = datetime.now(UTC)
    if target == RuleStatus.REVIEWED:
        rule.submitted_by, rule.submitted_at = actor_id, now
    elif target == RuleStatus.APPROVED:
        rule.approved_by, rule.approved_at = actor_id, now
    elif target == RuleStatus.RETIRED:
        rule.retired_at = now

    await db.flush()
    await record_audit(
        db, rule, action=f"status_{target.lower()}", actor_id=actor_id, actor_name=actor_name,
        from_status=previous, to_status=target, comment=comment,
    )
    await db.refresh(rule)
    mirror_hooks.record_legacy_rule(db, rule.id)
    return rule


async def delete_draft(db: AsyncSession, rule_id: str) -> None:
    """Hard-delete — allowed for DRAFT v1 only.

    Anything that has ever been validated or versioned is retired instead, so a
    rule that might already appear in an audit trail is never made to vanish.
    """
    rule = await get_rule(db, rule_id)
    if rule.status != RuleStatus.DRAFT or rule.version != 1:
        raise RuleStateError(
            "Only a first-version draft can be deleted; retire the rule instead.",
            details={"status": rule.status, "version": rule.version},
        )
    # Captured before the delete: this is a hard delete, so after the commit
    # there is no row left to identify.
    mirror_hooks.record_legacy_rule_delete(db, rule.id)
    await db.delete(rule)


# --- Stats ------------------------------------------------------------------


async def stats(db: AsyncSession) -> dict[str, Any]:
    async def _grouped(column) -> dict[str, int]:
        rows = await db.execute(select(column, func.count()).group_by(column))
        return {str(k): int(v) for k, v in rows.all()}

    total = int((await db.execute(select(func.count()).select_from(Rule))).scalar_one())
    logical = int(
        (await db.execute(select(func.count(func.distinct(Rule.rule_key))))).scalar_one()
    )
    by_status = await _grouped(Rule.status)
    horizon = date.today() + timedelta(days=30)
    expiring = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Rule)
                .where(
                    Rule.effective_to.isnot(None),
                    Rule.effective_to <= horizon,
                    Rule.effective_to >= date.today(),
                    Rule.status.in_([RuleStatus.ACTIVE, RuleStatus.PUBLISHED]),
                )
            )
        ).scalar_one()
    )
    with_errors = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Rule)
                .where(Rule.last_validation["valid"].astext == "false")
            )
        ).scalar_one()
    )
    return {
        "total_rules": total,
        "logical_rules": logical,
        "by_status": by_status,
        "by_service_type": await _grouped(Rule.service_type),
        "by_rule_type": await _grouped(Rule.rule_type),
        "draft_count": by_status.get(RuleStatus.DRAFT.value, 0),
        "active_count": by_status.get(RuleStatus.ACTIVE.value, 0),
        "expiring_within_30_days": expiring,
        "rules_with_errors": with_errors,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, dict | list | str | int | float | bool) or value is None:
        return value
    return str(value)
