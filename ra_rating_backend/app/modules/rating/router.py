"""Rating runs, results, calculation traces and exceptions API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.errors import NotFoundError
from app.core.rbac import RatingPermKey
from app.modules.compiler.models import ExecutableRule
from app.modules.rating import service as svc
from app.modules.rating.constants import EXCEPTION_TRANSITIONS, AssuranceStatus, RootCause
from app.modules.rating.models import (
    ContextRuleMap,
    ExceptionComment,
    RatingException,
    RatingResult,
    RatingRun,
)

router = APIRouter(tags=["rating"])

_view_runs = require(RatingPermKey.RUNS, "view")
RunOperator = principal_with(RatingPermKey.RUNS, "edit")
_view_exceptions = require(RatingPermKey.EXCEPTIONS, "view")
ExceptionOwner = principal_with(RatingPermKey.EXCEPTIONS, "edit")


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RunRequest(Base):
    batch_id: str
    #: Omit to use the active snapshot — the normal case.
    snapshot_id: str | None = None


class RunRead(Base):
    id: str
    batch_id: str
    snapshot_id: str
    snapshot_version: int
    status: str
    run_type: str
    total_cdrs: int
    rated_cdrs: int
    distinct_contexts: int
    matched_count: int
    exception_count: int
    expected_revenue: float
    billed_revenue: float
    undercharge_total: float
    overcharge_total: float
    stats: dict[str, Any]
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    triggered_by_name: str | None
    created_at: datetime


class ResultRead(Base):
    id: str
    run_id: str
    cdr_enriched_id: str
    cdr_id: str
    context_hash: str | None
    subscriber_id: str | None
    msisdn: str | None
    service_type: str
    product_code: str | None
    destination_zone: str | None
    time_band: str | None
    event_date: date
    selected_rule_ids: dict[str, Any]
    billable_quantity: float | None
    billable_unit: str | None
    expected_base_charge: float
    expected_discount: float
    expected_tax: float
    expected_final_charge: float
    actual_charge: float | None
    variance: float
    currency: str | None
    bundle_code: str | None = None
    bundle_consumed: float = 0
    bundle_overflow: float = 0
    unpriced_quantity: float = 0
    status: str
    root_cause: str | None
    engine_version: str


class ResultDetail(ResultRead):
    trace: list[Any]
    #: Every rule that was considered for this CDR's context, and why it won or
    #: lost — "why was I charged this?" answered in one payload.
    candidates: list[Any] = Field(default_factory=list)
    context_key: str | None = None


class ExceptionRead(Base):
    id: str
    run_id: str
    group_key: str
    title: str
    assurance_status: str
    root_cause: str
    severity: str
    status: str
    service_type: str | None
    product_code: str | None
    destination_zone: str | None
    rule_key: str | None
    cdr_count: int
    subscriber_count: int
    revenue_impact: float
    expected_total: float
    actual_total: float
    first_event_date: date | None
    last_event_date: date | None
    sample_result_id: str | None
    probable_cause: str
    recommended_action: str
    assigned_to: str | None
    assigned_to_name: str | None
    resolution: str
    recovered_amount: float
    created_at: datetime


class CommentRead(Base):
    id: str
    kind: str
    body: str
    author_name: str | None
    created_at: datetime


class ExceptionDetail(ExceptionRead):
    details: dict[str, Any]
    comments: list[CommentRead] = Field(default_factory=list)


class TransitionRequest(Base):
    status: str
    comment: str = ""
    assigned_to: str | None = None
    assigned_to_name: str | None = None
    resolution: str | None = None


# --- Runs -------------------------------------------------------------------


@router.post(
    "/rating-runs",
    response_model=RunRead,
    status_code=201,
    summary="Rate a CDR batch against the active snapshot",
)
async def create_run(
    db: DbSession, payload: RunRequest, principal: RunOperator
) -> RatingRun:
    run = await svc.start_run(
        db,
        batch_id=payload.batch_id,
        snapshot_id=payload.snapshot_id,
        actor_id=principal.id,
        actor_name=principal.full_name,
    )
    return await svc.execute_run(db, run)


@router.get(
    "/rating-runs",
    response_model=list[RunRead],
    summary="Rating run monitor",
    dependencies=[Depends(_view_runs)],
)
async def list_runs(
    db: DbSession, page: PageParams, batch_id: str | None = Query(None)
) -> list[RatingRun]:
    stmt = select(RatingRun)
    if batch_id:
        stmt = stmt.where(RatingRun.batch_id == batch_id)
    stmt = (
        stmt.order_by(RatingRun.created_at.desc()).limit(page.limit).offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get(
    "/rating-runs/{run_id}",
    response_model=RunRead,
    summary="Run detail",
    dependencies=[Depends(_view_runs)],
)
async def get_run(db: DbSession, run_id: str) -> RatingRun:
    run = await db.get(RatingRun, run_id)
    if run is None:
        raise NotFoundError(f"Rating run '{run_id}' was not found.")
    return run


@router.get(
    "/rating-runs/{run_id}/summary",
    summary="Assurance KPIs for a run",
    dependencies=[Depends(_view_runs)],
)
async def run_summary(db: DbSession, run_id: str) -> dict[str, Any]:
    return await svc.run_summary(db, run_id)


@router.get(
    "/rating-runs/{run_id}/contexts",
    summary="Resolved rule set per rating context",
    dependencies=[Depends(_view_runs)],
)
async def run_contexts(
    db: DbSession, run_id: str, page: PageParams
) -> list[dict[str, Any]]:
    stmt = (
        select(ContextRuleMap)
        .where(ContextRuleMap.run_id == run_id)
        .order_by(ContextRuleMap.cdr_count.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "context_key": r.context_key,
            "context_hash": r.context_hash,
            "cdr_count": r.cdr_count,
            "candidate_count": r.candidate_count,
            "selected_rules": r.selected_rules,
            "candidates": r.candidates,
        }
        for r in rows
    ]


# --- Results ----------------------------------------------------------------


@router.get(
    "/rating-results",
    response_model=list[ResultRead],
    summary="Per-CDR rating results",
    dependencies=[Depends(_view_runs)],
)
async def list_results(
    db: DbSession,
    page: PageParams,
    run_id: str | None = Query(
        None, description="Omit to browse across every run — the record explorer."
    ),
    status: str | None = Query(None),
    product_code: str | None = Query(None),
    service_type: str | None = Query(None),
    root_cause: str | None = Query(None),
    msisdn: str | None = Query(None),
    cdr_id: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    min_variance: float | None = Query(None, description="Absolute variance floor"),
) -> list[RatingResult]:
    stmt = select(RatingResult)
    if run_id:
        stmt = stmt.where(RatingResult.run_id == run_id)
    if status:
        stmt = stmt.where(RatingResult.status == status.upper())
    if product_code:
        stmt = stmt.where(RatingResult.product_code == product_code.upper())
    if service_type:
        stmt = stmt.where(RatingResult.service_type == service_type.upper())
    if root_cause:
        stmt = stmt.where(RatingResult.root_cause == root_cause.upper())
    if msisdn:
        stmt = stmt.where(RatingResult.msisdn == msisdn)
    if cdr_id:
        stmt = stmt.where(RatingResult.cdr_id == cdr_id)
    if date_from:
        stmt = stmt.where(RatingResult.event_date >= date_from)
    if date_to:
        stmt = stmt.where(RatingResult.event_date <= date_to)
    if min_variance is not None:
        stmt = stmt.where(func.abs(RatingResult.variance) >= min_variance)
    stmt = (
        stmt.order_by(func.abs(RatingResult.variance).desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get(
    "/rating-results/{result_id}",
    response_model=ResultDetail,
    summary="One result with its full calculation trace and rule candidates",
    dependencies=[Depends(_view_runs)],
)
async def get_result(db: DbSession, result_id: str) -> ResultDetail:
    result = await db.get(RatingResult, result_id)
    if result is None:
        raise NotFoundError(f"Rating result '{result_id}' was not found.")

    detail = ResultDetail.model_validate(result)
    if result.context_hash:
        entry = (
            await db.execute(
                select(ContextRuleMap).where(
                    ContextRuleMap.run_id == result.run_id,
                    ContextRuleMap.context_hash == result.context_hash,
                )
            )
        ).scalar_one_or_none()
        if entry:
            detail.candidates = entry.candidates
            detail.context_key = entry.context_key
    return detail


@router.get(
    "/rating-results/{result_id}/rules",
    summary="The executable rules that produced this charge",
    dependencies=[Depends(_view_runs)],
)
async def result_rules(db: DbSession, result_id: str) -> list[dict[str, Any]]:
    result = await db.get(RatingResult, result_id)
    if result is None:
        raise NotFoundError(f"Rating result '{result_id}' was not found.")
    ids = list((result.selected_rule_ids or {}).values())
    if not ids:
        return []
    rows = (
        await db.execute(select(ExecutableRule).where(ExecutableRule.id.in_(ids)))
    ).scalars().all()
    return [
        {
            "stage": r.execution_stage,
            "stage_order": r.stage_order,
            "rule_key": r.rule_key,
            "rule_version": r.rule_version,
            "rule_name": r.rule_name,
            "specificity": r.specificity,
            "priority": r.priority,
            "signature": r.signature,
            "actions": r.actions,
        }
        for r in sorted(rows, key=lambda r: r.stage_order)
    ]


# --- Exceptions -------------------------------------------------------------


@router.get(
    "/exceptions",
    response_model=list[ExceptionRead],
    summary="Grouped exceptions, biggest revenue impact first",
    dependencies=[Depends(_view_exceptions)],
)
async def list_exceptions(
    db: DbSession,
    page: PageParams,
    run_id: str | None = Query(None),
    status: str | None = Query(None),
    severity: str | None = Query(None),
    root_cause: str | None = Query(None),
    product_code: str | None = Query(None),
) -> list[RatingException]:
    stmt = select(RatingException)
    if run_id:
        stmt = stmt.where(RatingException.run_id == run_id)
    if status:
        stmt = stmt.where(RatingException.status == status.upper())
    if severity:
        stmt = stmt.where(RatingException.severity == severity.upper())
    if root_cause:
        stmt = stmt.where(RatingException.root_cause == root_cause.upper())
    if product_code:
        stmt = stmt.where(RatingException.product_code == product_code.upper())
    stmt = (
        stmt.order_by(func.abs(RatingException.revenue_impact).desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get(
    "/exceptions/meta",
    summary="Statuses, transitions and root causes for the exception UI",
    dependencies=[Depends(_view_exceptions)],
)
async def exception_meta() -> dict[str, Any]:
    return {
        "assurance_statuses": [s.value for s in AssuranceStatus],
        "root_causes": [c.value for c in RootCause],
        "transitions": {k: list(v) for k, v in EXCEPTION_TRANSITIONS.items()},
    }


@router.get(
    "/exceptions/{exception_id}",
    response_model=ExceptionDetail,
    summary="Exception detail with its investigation timeline",
    dependencies=[Depends(_view_exceptions)],
)
async def get_exception(db: DbSession, exception_id: str) -> ExceptionDetail:
    row = await svc.get_exception(db, exception_id)
    comments = (
        await db.execute(
            select(ExceptionComment)
            .where(ExceptionComment.exception_id == exception_id)
            .order_by(ExceptionComment.created_at.desc())
        )
    ).scalars().all()
    detail = ExceptionDetail.model_validate(row)
    detail.comments = [CommentRead.model_validate(c) for c in comments]
    return detail


@router.get(
    "/exceptions/{exception_id}/results",
    response_model=list[ResultRead],
    summary="The CDRs behind an exception",
    dependencies=[Depends(_view_exceptions)],
)
async def exception_results(
    db: DbSession, exception_id: str, page: PageParams
) -> list[RatingResult]:
    row = await svc.get_exception(db, exception_id)
    parts = row.group_key.split("|")
    stmt = select(RatingResult).where(
        RatingResult.run_id == row.run_id,
        RatingResult.status == parts[0],
    )
    if row.product_code:
        stmt = stmt.where(RatingResult.product_code == row.product_code)
    if row.destination_zone:
        stmt = stmt.where(RatingResult.destination_zone == row.destination_zone)
    stmt = (
        stmt.order_by(func.abs(RatingResult.variance).desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post(
    "/exceptions/{exception_id}/transition",
    response_model=ExceptionDetail,
    summary="Move an exception through its lifecycle",
)
async def transition(
    db: DbSession,
    exception_id: str,
    payload: TransitionRequest,
    principal: ExceptionOwner,
) -> ExceptionDetail:
    row = await svc.transition_exception(
        db,
        exception_id,
        status=payload.status.upper(),
        comment=payload.comment,
        assigned_to=payload.assigned_to,
        assigned_to_name=payload.assigned_to_name,
        resolution=payload.resolution,
        actor_id=principal.id,
        actor_name=principal.full_name,
    )
    return await get_exception(db, row.id)


class CommentCreate(Base):
    body: str = Field(min_length=1, max_length=8000)


@router.post(
    "/exceptions/{exception_id}/comments",
    response_model=ExceptionDetail,
    summary="Add an investigation note to an exception",
)
async def add_comment(
    db: DbSession,
    exception_id: str,
    payload: CommentCreate,
    principal: ExceptionOwner,
) -> ExceptionDetail:
    row = await svc.get_exception(db, exception_id)
    db.add(
        ExceptionComment(
            exception_id=row.id,
            kind="COMMENT",
            body=payload.body,
            author_id=principal.id,
            author_name=principal.full_name,
        )
    )
    await db.flush()
    return await get_exception(db, row.id)


# --- Simulation -------------------------------------------------------------


class SimulationRequest(Base):
    service_type: str = "VOICE"
    event_timestamp: datetime | None = None
    duration_seconds: float | None = None
    usage_volume: float | None = None
    msisdn: str | None = None
    calling_number: str | None = None
    called_number: str | None = None
    #: Supply it to have the simulation compare as well as calculate.
    actual_charge: float | None = None
    currency: str | None = None
    snapshot_id: str | None = None
    #: Test a context directly, without inventing a subscriber that resolves to it.
    product_code: str | None = None
    destination_zone: str | None = None
    time_band: str | None = None
    account_type: str | None = None
    roaming: bool | None = None


class SnapshotComparisonRequest(SimulationRequest):
    from_snapshot_id: str
    to_snapshot_id: str


@router.post(
    "/rating-simulation",
    summary="Rate one CDR against a snapshot without writing anything",
    dependencies=[Depends(require(RatingPermKey.SIMULATION, "view"))],
)
async def simulate(db: DbSession, payload: SimulationRequest) -> dict[str, Any]:
    """Runs the real engine against the real snapshot — never an approximation."""
    from app.modules.rating import simulation

    return await simulation.simulate(db, **payload.model_dump())


@router.post(
    "/rating-simulation/compare",
    summary="Rate the same CDR against two snapshots — the pre-publish what-if",
    dependencies=[Depends(require(RatingPermKey.SIMULATION, "view"))],
)
async def compare(db: DbSession, payload: SnapshotComparisonRequest) -> dict[str, Any]:
    from app.modules.rating import simulation

    body = payload.model_dump()
    from_id = body.pop("from_snapshot_id")
    to_id = body.pop("to_snapshot_id")
    body.pop("snapshot_id", None)
    return await simulation.compare_snapshots(
        db, from_snapshot_id=from_id, to_snapshot_id=to_id, **body
    )
