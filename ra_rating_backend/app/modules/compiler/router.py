"""Rule-set validation, compilation and snapshot management API."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select

from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.rbac import RatingPermKey
from app.modules.compiler import schemas as s
from app.modules.compiler import service as svc
from app.modules.compiler import snapshot_detail as detail
from app.modules.compiler import snapshot_export as export
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


# --- Snapshot Details ---------------------------------------------------------
# Additive. Every endpoint above is untouched, and nothing here writes: a
# snapshot is immutable once compiled, and a details screen that could alter one
# would undermine the only property that makes it worth trusting.


@router.get(
    "/rule-snapshots/{snapshot_id}/diff",
    response_model=s.SnapshotDiff,
    summary="What changed since the previous snapshot",
    dependencies=[Depends(_view)],
)
async def diff_with_previous(db: DbSession, snapshot_id: str) -> s.SnapshotDiff:
    """The comparison the screen actually wants, without having to know what it
    is comparing against.

    `/rule-snapshots/diff?from_id=&to_id=` already exists and stays: it answers
    "compare any two". This answers "what changed since the last one", which is
    the question asked ninety-nine times out of a hundred and which a client
    could only ask before by first fetching the list and working out the
    predecessor itself.
    """
    snapshot = await svc.get_snapshot(db, snapshot_id)
    previous = await detail.previous_snapshot(db, snapshot)
    if previous is None:
        # The first snapshot has no predecessor. Reported as a diff against
        # nothing rather than as an error: "everything in it is new" is a true
        # and useful answer, and a 404 here would be the screen's problem to
        # special-case rather than ours.
        return s.SnapshotDiff(
            from_snapshot=0, to_snapshot=snapshot.version,
            added=snapshot.rule_count, removed=0, changed=0, unchanged=0,
            identical=False, entries=[],
        )
    return await svc.diff(db, previous.id, snapshot.id)


@router.get(
    "/rule-snapshots/{snapshot_id}/report",
    response_model=s.CompileReportRead,
    summary="Compile and validation report — is this safe to activate",
    dependencies=[Depends(_view)],
)
async def compile_report(db: DbSession, snapshot_id: str) -> s.CompileReportRead:
    snapshot = await svc.get_snapshot(db, snapshot_id)
    report = detail.compile_report(snapshot)
    return s.CompileReportRead(
        snapshot_id=report.snapshot_id,
        version=report.version,
        status=report.status,
        checksum=report.checksum,
        rule_count=report.rule_count,
        compiled_by=report.compiled_by,
        compiled_at=report.compiled_at,
        forced=report.forced,
        error_count=report.error_count,
        warning_count=report.warning_count,
        safe_to_activate=report.safe_to_activate,
        grouped_issues=report.grouped_issues,
        issues=report.issues,
        stats=report.stats,
    )


@router.get(
    "/rule-snapshots/{snapshot_id}/execution-order",
    response_model=list[s.StageGroupRead],
    summary="The rules grouped by stage, in the order the engine walks them",
    dependencies=[Depends(_view)],
)
async def execution_order(
    db: DbSession,
    snapshot_id: str,
    sample: int = Query(10, ge=1, le=100, description="Rules shown per stage"),
) -> list[s.StageGroupRead]:
    """Includes the stages this snapshot has *no* rules for.

    An absence is as informative as a presence: a pipeline with nothing at
    ROUNDING rounds nothing, and that is invisible in a list of what is there.
    """
    await svc.get_snapshot(db, snapshot_id)
    groups = await detail.execution_order(db, snapshot_id, sample=sample)
    return [
        s.StageGroupRead(
            stage=g.stage, stage_order=g.stage_order,
            rule_count=g.rule_count, rules=g.rules,
        )
        for g in groups
    ]


@router.get(
    "/rule-snapshots/{snapshot_id}/impact",
    response_model=s.ImpactRead,
    summary="What activating this snapshot would touch",
    dependencies=[Depends(_view)],
)
async def impact(
    db: DbSession,
    snapshot_id: str,
    window_days: int = Query(
        detail.DEFAULT_IMPACT_DAYS, ge=1, le=365,
        description="How far back to measure traffic.",
    ),
    changed_only: bool = Query(
        True,
        description="Measure only the rules that differ from the previous "
                    "snapshot — 'how much traffic does this change move', "
                    "rather than 'how big is this tariff'.",
    ),
) -> s.ImpactRead:
    """Two halves, answering different questions.

    **Reach** is what the rules target, derived exactly from the compiled
    dimensions and true whether or not anything has been rated. **Traffic** is
    what those rules have actually priced, drawn from rating results — so it is
    measured rather than estimated, and it is honest about having nothing to
    measure when the platform has not rated anything yet.
    """
    result = await detail.impact(
        db, snapshot_id, window_days=window_days, changed_only=changed_only
    )
    return s.ImpactRead(
        snapshot_id=result.snapshot_id,
        version=result.version,
        rule_count=result.rule_count,
        reach=[
            s.ReachRead(
                dimension=r.dimension, label=r.label,
                values=r.values, wildcard_rules=r.wildcard_rules,
            )
            for r in result.reach
        ],
        traffic=(
            # Field by field rather than `vars()`: these are slotted dataclasses,
            # which have no __dict__, and `asdict` would deep-copy the rule list
            # for nothing.
            s.TrafficImpactRead(
                window_days=result.traffic.window_days,
                from_date=result.traffic.from_date,
                to_date=result.traffic.to_date,
                has_traffic=result.traffic.has_traffic,
                rated_events=result.traffic.rated_events,
                affected_events=result.traffic.affected_events,
                distinct_subscribers=result.traffic.distinct_subscribers,
                affected_charge=result.traffic.affected_charge,
                currency=result.traffic.currency,
                top_rules=result.traffic.top_rules,
            )
            if result.traffic is not None
            else None
        ),
        changed_rule_keys=result.changed_rule_keys,
        compared_with_version=result.compared_with_version,
        note=result.note,
    )


@router.get(
    "/rule-snapshots/{snapshot_id}/export",
    summary="Download the compiled snapshot as CSV, JSON or XML",
    dependencies=[Depends(_view)],
)
async def export_snapshot(
    db: DbSession,
    snapshot_id: str,
    format: str = Query("csv", pattern="^(csv|json|xml)$"),
) -> Response:
    """The *compiled* form, not the authored one.

    Exporting what an author typed would produce a file that reads like the rules
    and does not say what will happen. The whole reason to export a snapshot is
    to be able to hand somebody the answer to "what was rating on the 14th".
    """
    snapshot = await svc.get_snapshot(db, snapshot_id)
    if format == "json":
        payload = await export.to_json(db, snapshot)
        return Response(
            content=json.dumps(payload, indent=2, default=str),
            media_type="application/json",
            headers=_attachment(export.filename(snapshot, "json")),
        )
    if format == "xml":
        return Response(
            content=await export.to_xml(db, snapshot),
            media_type="application/xml",
            headers=_attachment(export.filename(snapshot, "xml")),
        )
    return Response(
        content=await export.to_csv(db, snapshot),
        media_type="text/csv",
        headers=_attachment(export.filename(snapshot, "csv")),
    )


def _attachment(name: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{name}"'}
