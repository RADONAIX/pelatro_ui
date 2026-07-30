"""Rule-set validation, compilation and snapshot management API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.rbac import RatingPermKey
from app.modules.compiler import schemas as s
from app.modules.compiler import service as svc
from app.modules.compiler.models import ExecutableRule, RuleSnapshot

router = APIRouter(tags=["snapshots"])

_view = require(RatingPermKey.SNAPSHOTS, "view")
SnapshotPublisher = principal_with(RatingPermKey.SNAPSHOTS, "edit")


@router.post(
    "/rule-validation",
    response_model=s.RuleSetValidationReport,
    summary="Validate a whole rule set: structural, conflict and coverage",
    dependencies=[Depends(_view)],
)
async def validate_rule_set(
    db: DbSession, payload: s.RuleSetValidationRequest
) -> s.RuleSetValidationReport:
    return await svc.validate_rule_set(
        db, rule_set_id=payload.rule_set_id, include_coverage=payload.include_coverage
    )


@router.post(
    "/rule-snapshots",
    response_model=s.SnapshotDetail,
    status_code=201,
    summary="Compile the approved rules into an immutable snapshot",
)
async def compile_snapshot(
    db: DbSession, payload: s.CompileRequest, principal: SnapshotPublisher
) -> RuleSnapshot:
    return await svc.compile_snapshot(
        db,
        name=payload.name,
        description=payload.description,
        rule_set_id=payload.rule_set_id,
        force=payload.force,
        actor_id=principal.id,
        actor_name=principal.full_name,
    )


@router.get(
    "/rule-snapshots",
    response_model=list[s.SnapshotSummary],
    summary="Published snapshots, newest first",
    dependencies=[Depends(_view)],
)
async def list_snapshots(db: DbSession, page: PageParams) -> list[RuleSnapshot]:
    stmt = (
        select(RuleSnapshot)
        .order_by(RuleSnapshot.version.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get(
    "/rule-snapshots/stats",
    summary="Which snapshot the engine is using",
    dependencies=[Depends(_view)],
)
async def stats(db: DbSession) -> dict:
    return await svc.snapshot_stats(db)


@router.get(
    "/rule-snapshots/active",
    response_model=s.SnapshotDetail | None,
    summary="The snapshot rating runs resolve against",
    dependencies=[Depends(_view)],
)
async def get_active(db: DbSession) -> RuleSnapshot | None:
    return await svc.active_snapshot(db)


@router.get(
    "/rule-snapshots/diff",
    response_model=s.SnapshotDiff,
    summary="Compare two snapshots rule by rule",
    dependencies=[Depends(_view)],
)
async def compare(db: DbSession, from_id: str = Query(...), to_id: str = Query(...)):
    return await svc.diff(db, from_id, to_id)


@router.get(
    "/rule-snapshots/{snapshot_id}",
    response_model=s.SnapshotDetail,
    summary="Snapshot detail, stats and issues",
    dependencies=[Depends(_view)],
)
async def get_snapshot(db: DbSession, snapshot_id: str) -> RuleSnapshot:
    return await svc.get_snapshot(db, snapshot_id)


@router.get(
    "/rule-snapshots/{snapshot_id}/rules",
    response_model=list[s.ExecutableRuleRead],
    summary="The executable rules a snapshot contains",
    dependencies=[Depends(_view)],
)
async def snapshot_rules(
    db: DbSession,
    snapshot_id: str,
    page: PageParams,
    execution_stage: str | None = Query(None),
    service_type: str | None = Query(None),
) -> list[ExecutableRule]:
    await svc.get_snapshot(db, snapshot_id)
    stmt = select(ExecutableRule).where(ExecutableRule.snapshot_id == snapshot_id)
    if execution_stage:
        stmt = stmt.where(ExecutableRule.execution_stage == execution_stage)
    if service_type:
        stmt = stmt.where(ExecutableRule.service_type == service_type)
    stmt = (
        stmt.order_by(
            ExecutableRule.stage_order,
            ExecutableRule.specificity.desc(),
            ExecutableRule.priority.desc(),
        )
        .limit(page.limit)
        .offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post(
    "/rule-snapshots/{snapshot_id}/activate",
    response_model=s.SnapshotDetail,
    summary="Make this the snapshot the engine uses",
)
async def activate(
    db: DbSession, snapshot_id: str, payload: s.ActivateRequest, principal: SnapshotPublisher
) -> RuleSnapshot:
    del payload
    return await svc.activate(db, snapshot_id, actor_id=principal.id)


@router.post(
    "/rule-snapshots/rollback",
    response_model=s.SnapshotDetail,
    summary="Re-activate the previously active snapshot",
)
async def rollback(db: DbSession, principal: SnapshotPublisher) -> RuleSnapshot:
    return await svc.rollback(db, actor_id=principal.id)
