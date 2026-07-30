"""Rule authoring API."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession, PageParams, principal_with, require
from app.core.errors import NotFoundError, PermissionDeniedError
from app.core.rbac import RatingPermKey
from app.modules.rules import schemas as s
from app.modules.rules import service as svc
from app.modules.rules.constants import RuleStatus
from app.modules.rules.models import Rule, RuleAuditEntry, RuleSet, RuleTemplate

router = APIRouter(tags=["rules"])

_view = require(RatingPermKey.RULES, "view")
_edit = require(RatingPermKey.RULES, "edit")
#: Enforces rules:edit AND injects the principal (needed for created_by/audit).
RuleEditor = principal_with(RatingPermKey.RULES, "edit")


def _summary(rule: Rule) -> s.RuleSummary:
    data = s.RuleSummary.model_validate(rule)
    data.condition_count = len(rule.conditions)
    data.action_count = len(rule.actions)
    data.has_errors = bool(rule.last_validation and rule.last_validation.get("valid") is False)
    return data


def _detail(rule: Rule) -> s.RuleDetail:
    data = s.RuleDetail.model_validate(rule)
    data.condition_count = len(rule.conditions)
    data.action_count = len(rule.actions)
    data.has_errors = bool(rule.last_validation and rule.last_validation.get("valid") is False)
    return data


# --- Rule sets --------------------------------------------------------------


@router.get(
    "/rule-sets",
    response_model=list[s.RuleSetRead],
    summary="List rule sets",
    dependencies=[Depends(_view)],
)
async def list_rule_sets(db: DbSession) -> list[s.RuleSetRead]:
    counts = dict(
        (await db.execute(select(Rule.rule_set_id, func.count()).group_by(Rule.rule_set_id))).all()
    )
    rows = (await db.execute(select(RuleSet).order_by(RuleSet.code))).scalars().all()
    out = []
    for rs in rows:
        item = s.RuleSetRead.model_validate(rs)
        item.rule_count = int(counts.get(rs.id, 0))
        out.append(item)
    return out


@router.post(
    "/rule-sets",
    response_model=s.RuleSetRead,
    status_code=201,
    summary="Create a rule set",
)
async def create_rule_set(
    db: DbSession, payload: s.RuleSetCreate, principal: RuleEditor
) -> s.RuleSetRead:
    rs = RuleSet(**payload.model_dump(), created_by=principal.id)
    db.add(rs)
    await db.flush()
    await db.refresh(rs)
    return s.RuleSetRead.model_validate(rs)


# --- Rules ------------------------------------------------------------------


@router.get(
    "/rules",
    response_model=s.RuleListResponse,
    summary="Rule catalogue",
    dependencies=[Depends(_view)],
)
async def list_rules(
    db: DbSession,
    page: PageParams,
    search: str | None = Query(None, description="Matches name, key or description"),
    status: str | None = Query(None),
    service_type: str | None = Query(None),
    rule_type: str | None = Query(None),
    product_id: str | None = Query(None),
    rule_set_id: str | None = Query(None),
    source_system: str | None = Query(None),
    effective_on: date | None = Query(
        None, description="Only rules whose validity window covers this date"
    ),
    latest_only: bool = Query(
        True, description="One row per logical rule (its highest version)"
    ),
    sort: str = Query("updated_at", pattern="^(updated_at|created_at|name|priority|specificity)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
) -> s.RuleListResponse:
    stmt = svc.build_list_query(
        search=search, status=status, service_type=service_type, rule_type=rule_type,
        product_id=product_id, rule_set_id=rule_set_id, source_system=source_system,
        effective_on=effective_on, latest_only=latest_only,
    )
    total = int(
        (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    column = getattr(Rule, sort)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    rows = (await db.execute(stmt.limit(page.limit).offset(page.offset))).scalars().all()
    return s.RuleListResponse(
        items=[_summary(r) for r in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.get(
    "/rules/stats",
    response_model=s.RuleStats,
    summary="Rule catalogue KPIs",
    dependencies=[Depends(_view)],
)
async def rule_stats(db: DbSession) -> s.RuleStats:
    return s.RuleStats(**await svc.stats(db))


@router.post(
    "/rules",
    response_model=s.RuleDetail,
    status_code=201,
    summary="Create a rule (draft)",
)
async def create_rule(
    db: DbSession, payload: s.RuleCreate, principal: RuleEditor
) -> s.RuleDetail:
    rule = await svc.create_rule(
        db, payload, actor_id=principal.id, actor_name=principal.full_name
    )
    return _detail(rule)


@router.get(
    "/rules/{rule_id}",
    response_model=s.RuleDetail,
    summary="Get a rule with its conditions and actions",
    dependencies=[Depends(_view)],
)
async def get_rule(db: DbSession, rule_id: str) -> s.RuleDetail:
    return _detail(await svc.get_rule(db, rule_id))


@router.patch(
    "/rules/{rule_id}",
    response_model=s.RuleDetail,
    summary="Edit a draft rule",
)
async def update_rule(
    db: DbSession, rule_id: str, payload: s.RuleUpdate, principal: RuleEditor
) -> s.RuleDetail:
    rule = await svc.update_rule(
        db, rule_id, payload, actor_id=principal.id, actor_name=principal.full_name
    )
    return _detail(rule)


@router.delete(
    "/rules/{rule_id}",
    status_code=204,
    summary="Delete a first-version draft",
    dependencies=[Depends(_edit)],
)
async def delete_rule(db: DbSession, rule_id: str) -> None:
    await svc.delete_draft(db, rule_id)


@router.post(
    "/rules/{rule_id}/validate",
    response_model=s.ValidationReport,
    summary="Run structural validation",
    dependencies=[Depends(_view)],
)
async def validate_rule(db: DbSession, rule_id: str) -> s.ValidationReport:
    rule = await svc.get_rule(db, rule_id)
    return await svc.validate_and_cache(db, rule)


@router.post(
    "/rules/{rule_id}/status",
    response_model=s.RuleDetail,
    summary="Move a rule through its lifecycle",
)
async def change_status(
    db: DbSession, rule_id: str, payload: s.StatusChange, principal: CurrentUser
) -> s.RuleDetail:
    # Maker-checker: authoring rights are enough to validate or submit, but
    # approving requires the separate approvals permission — an analyst must not
    # be able to sign off their own rule.
    needed = (
        RatingPermKey.APPROVALS
        if payload.status in (RuleStatus.APPROVED, RuleStatus.REVIEWED)
        else RatingPermKey.RULES
    )
    if not principal.can(needed, "edit"):
        raise PermissionDeniedError(
            f"Role '{principal.role}' lacks 'edit' on '{needed.value}'."
        )

    rule = await svc.change_status(
        db, rule_id, str(payload.status), comment=payload.comment,
        actor_id=principal.id, actor_name=principal.full_name,
    )
    return _detail(rule)


@router.post(
    "/rules/{rule_id}/versions",
    response_model=s.RuleDetail,
    status_code=201,
    summary="Cut a new draft version of a rule",
)
async def create_version(
    db: DbSession, rule_id: str, payload: s.NewVersionRequest,
    principal: RuleEditor,
) -> s.RuleDetail:
    rule = await svc.new_version(
        db, rule_id, change_comment=payload.change_comment, name=payload.name,
        actor_id=principal.id, actor_name=principal.full_name,
    )
    return _detail(rule)


@router.post(
    "/rules/{rule_id}/clone",
    response_model=s.RuleDetail,
    status_code=201,
    summary="Copy a rule into a new logical rule",
)
async def clone_rule(
    db: DbSession, rule_id: str, payload: s.CloneRequest,
    principal: RuleEditor,
) -> s.RuleDetail:
    rule = await svc.clone_rule(
        db, rule_id, rule_key=payload.rule_key, name=payload.name,
        actor_id=principal.id, actor_name=principal.full_name,
    )
    return _detail(rule)


@router.get(
    "/rules/{rule_id}/versions",
    response_model=list[s.RuleSummary],
    summary="Every version of this logical rule, newest first",
    dependencies=[Depends(_view)],
)
async def list_versions(db: DbSession, rule_id: str) -> list[s.RuleSummary]:
    rule = await svc.get_rule(db, rule_id)
    return [_summary(r) for r in await svc.versions_of(db, rule.rule_key)]


@router.get(
    "/rules/{rule_id}/audit",
    response_model=list[s.AuditEntryRead],
    summary="Change history for this logical rule (all versions)",
    dependencies=[Depends(_view)],
)
async def rule_audit(db: DbSession, rule_id: str, limit: int = Query(100, ge=1, le=500)):
    rule = await svc.get_rule(db, rule_id)
    stmt = (
        select(RuleAuditEntry)
        .where(RuleAuditEntry.rule_key == rule.rule_key)
        .order_by(RuleAuditEntry.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


# --- Templates --------------------------------------------------------------


@router.get(
    "/rule-templates",
    response_model=list[s.TemplateRead],
    summary="Starting points for 'Create from template'",
    dependencies=[Depends(_view)],
)
async def list_templates(
    db: DbSession, service_type: str | None = Query(None)
) -> list[RuleTemplate]:
    stmt = select(RuleTemplate).order_by(RuleTemplate.name)
    if service_type:
        stmt = stmt.where(RuleTemplate.service_type == service_type)
    return list((await db.execute(stmt)).scalars().all())


@router.get(
    "/rule-templates/{code}",
    response_model=s.TemplateRead,
    summary="Get one template",
    dependencies=[Depends(_view)],
)
async def get_template(db: DbSession, code: str) -> RuleTemplate:
    tpl = (
        await db.execute(select(RuleTemplate).where(RuleTemplate.code == code))
    ).scalar_one_or_none()
    if tpl is None:
        raise NotFoundError(f"Template '{code}' was not found.")
    return tpl
