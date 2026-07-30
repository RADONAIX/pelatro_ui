"""Rating assurance execution API (§24).

All paths are relative to the service's API prefix, which is ``/api/rating``:

    POST /api/rating/execute                            run the batch
    GET  /api/rating/results/{usage_id}                 the verdict for one record
    GET  /api/rating/results/{usage_id}/explanation     the whole story, step by step
    GET  /api/rating/usage-exceptions                   what needs investigating
    POST /api/rating/usage-exceptions/{id}/retry        re-rate one failed record
    POST /api/rating/rerate                             re-rate named records
    POST /api/rating/msc/ingest                         pull new MSC records
    GET  /api/rating/msc/status                         how far the source has been read

Mounted alongside the existing rating router rather than replacing it: the
run/result/exception endpoints that already exist serve the batch-scoped history,
and these serve the usage-scoped assurance layer. The per-record exceptions live
under ``/usage-exceptions`` rather than ``/exceptions`` so their ids can never be
confused with the grouped investigations the existing endpoint serves.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.core.config import settings
from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.rbac import RatingPermKey
from app.modules.cdr.models import CdrEnriched
from app.modules.msc import service as msc_svc
from app.modules.rating import execution
from app.modules.rating.audit_models import (
    CalculationComponent,
    EvaluationStatus,
    RatingResultFinal,
    RatingStatus,
    RuleEvaluationAudit,
    UsageException,
    UsageExceptionStatus,
)

router = APIRouter(tags=["rating-assurance"])

_view_runs = require(RatingPermKey.RUNS, "view")
RunOperator = principal_with(RatingPermKey.RUNS, "edit")
_view_exceptions = require(RatingPermKey.EXCEPTIONS, "view")
ExceptionOwner = principal_with(RatingPermKey.EXCEPTIONS, "edit")


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Execute ----------------------------------------------------------------


class ExecuteRequest(Base):
    limit: int | None = Field(
        default=None,
        ge=1,
        description="Maximum records to rate. Defaults to MAXIMUM_RECORDS_PER_RUN.",
    )
    batch_size: int | None = Field(
        default=None, ge=1, le=50_000, description="Records per transaction."
    )
    reprocess: bool = Field(
        default=False,
        description=(
            "Re-rate records that were already rated. Safe to repeat: results "
            "are upserted on usage_id, never duplicated."
        ),
    )
    snapshot_id: str | None = Field(
        default=None, description="Rule snapshot to rate against. Defaults to the active one."
    )


class ExecuteResponse(Base):
    batch_id: str
    total_records: int
    processed_records: int
    successful_records: int
    exception_records: int
    status: str
    by_status: dict[str, int]
    by_enrichment: dict[str, int]
    distinct_contexts: int
    rules_loaded: int
    expected_total: str
    actual_total: str
    undercharge_total: str
    overcharge_total: str
    duration_ms: float
    error: str | None = None


@router.post(
    "/execute",
    response_model=ExecuteResponse,
    summary="Rate unprocessed usage and store the assurance verdict",
)
async def execute(
    payload: ExecuteRequest,
    db: DbSession,
    actor=Depends(RunOperator),
) -> ExecuteResponse:
    summary = await execution.execute_rating_batch(
        db,
        batch_size=payload.batch_size,
        limit=payload.limit,
        reprocess=payload.reprocess,
        snapshot_id=payload.snapshot_id,
        actor_id=getattr(actor, "user_id", None),
    )
    return ExecuteResponse(**summary.as_dict())


class RerateRequest(Base):
    usage_ids: list[str] = Field(min_length=1, max_length=10_000)


@router.post(
    "/rerate",
    response_model=ExecuteResponse,
    summary="Re-rate named usage records against the current rules",
)
async def rerate(
    payload: RerateRequest,
    db: DbSession,
    actor=Depends(RunOperator),
) -> ExecuteResponse:
    summary = await execution.execute_rating_batch(
        db,
        usage_ids=payload.usage_ids,
        reprocess=True,
        actor_id=getattr(actor, "user_id", None),
    )
    await execution.resolve_exceptions_for(db, payload.usage_ids)
    await db.commit()
    return ExecuteResponse(**summary.as_dict())


# --- Results ----------------------------------------------------------------


class ResultRead(Base):
    rating_result_id: str
    usage_id: str
    charge_component: str
    run_id: str | None
    subscriber_msisdn: str | None
    service_type: str | None
    call_direction: str | None
    event_date: date | None
    selected_base_rule_id: str | None
    selected_rule_ids: dict[str, Any]
    expected_charge: float
    actual_charge: float | None
    variance_amount: float
    absolute_variance: float
    variance_percentage: float | None
    currency: str | None
    rating_status: str
    enrichment_status: str | None
    actual_charge_status: str | None
    explanation: str
    created_at: datetime
    updated_at: datetime


@router.get(
    "/results",
    summary="Assurance results, filterable by status and date",
    dependencies=[Depends(_view_runs)],
)
async def list_results(
    db: DbSession,
    page: PageParams,
    status: str | None = Query(default=None),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    msisdn: str | None = Query(default=None),
) -> dict[str, Any]:
    conditions = []
    if status:
        conditions.append(RatingResultFinal.rating_status == status)
    if from_date:
        conditions.append(RatingResultFinal.event_date >= from_date)
    if to_date:
        conditions.append(RatingResultFinal.event_date <= to_date)
    if msisdn:
        conditions.append(RatingResultFinal.subscriber_msisdn == msisdn)

    total = int(
        (
            await db.execute(
                select(func.count()).select_from(RatingResultFinal).where(*conditions)
            )
        ).scalar_one()
    )
    rows = (
        await db.execute(
            select(RatingResultFinal)
            .where(*conditions)
            .order_by(RatingResultFinal.absolute_variance.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    ).scalars().all()
    return {
        "items": [ResultRead.model_validate(r).model_dump() for r in rows],
        "total": total,
        "limit": page.limit,
        "offset": page.offset,
    }


async def _result_or_404(db: DbSession, usage_id: str) -> RatingResultFinal:
    row = (
        await db.execute(
            select(RatingResultFinal).where(
                RatingResultFinal.usage_id == usage_id,
                RatingResultFinal.charge_component == "TOTAL",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"No rating result exists for usage '{usage_id}'.")
    return row


@router.get(
    "/results/{usage_id}",
    response_model=ResultRead,
    summary="The assurance verdict for one usage record",
    dependencies=[Depends(_view_runs)],
)
async def get_result(usage_id: str, db: DbSession) -> RatingResultFinal:
    return await _result_or_404(db, usage_id)


@router.get(
    "/results/{usage_id}/explanation",
    summary="Why this charge is what it is, step by step",
    dependencies=[Depends(_view_runs)],
)
async def explain(usage_id: str, db: DbSession) -> dict[str, Any]:
    """The full audit trail for one record (§24).

    Everything an analyst needs to either accept the verdict or find the defect,
    in one response: the usage as received, what it was enriched to, every rule
    that was considered and why it won or lost, each calculation step, and the
    comparison.
    """
    result = await _result_or_404(db, usage_id)

    usage = (
        await db.execute(select(CdrEnriched).where(CdrEnriched.usage_id == usage_id))
    ).scalar_one_or_none()

    evaluations = (
        await db.execute(
            select(RuleEvaluationAudit)
            .where(RuleEvaluationAudit.usage_id == usage_id)
            .order_by(
                RuleEvaluationAudit.rule_stage,
                RuleEvaluationAudit.priority.desc(),
                RuleEvaluationAudit.specificity_score.desc(),
            )
        )
    ).scalars().all()

    components = (
        await db.execute(
            select(CalculationComponent)
            .where(CalculationComponent.usage_id == usage_id)
            .order_by(CalculationComponent.sequence_number)
        )
    ).scalars().all()

    exceptions = (
        await db.execute(
            select(UsageException).where(UsageException.usage_id == usage_id)
        )
    ).scalars().all()

    def rules_with(*statuses: str) -> list[dict[str, Any]]:
        return [
            {
                "rule_id": e.rule_id,
                "rule_key": e.rule_key,
                "rule_name": e.rule_name,
                "stage": e.rule_stage,
                "status": e.evaluation_status,
                "reason": e.rejection_reason,
                "detail": e.detail,
                "priority": e.priority,
                "specificity": e.specificity_score,
                "version": e.rule_version,
                "exclusive_group": e.exclusive_group,
            }
            for e in evaluations
            if e.evaluation_status in statuses
        ]

    return {
        "usage_id": usage_id,
        "canonical_usage": (
            {
                "usage_id": usage.usage_id,
                "source_system": usage.source_system,
                "source_file": usage.source_file,
                "source_record_number": usage.source_record_number,
                "service_type": usage.service_type,
                "call_direction": usage.call_direction,
                "event_start_time": usage.event_timestamp,
                "event_end_time": usage.event_end_time,
                "duration_seconds": float(usage.duration_seconds)
                if usage.duration_seconds is not None
                else None,
                "subscriber_msisdn": usage.msisdn,
                "calling_number": usage.calling_number,
                "called_number": usage.called_number,
                "call_reference": usage.call_reference,
                "cell_id": usage.cell_id,
                "location_area_code": usage.location_area_code,
                "charged_party": usage.charged_party,
            }
            if usage
            else None
        ),
        "enrichment": (
            {
                "status": usage.enrichment_status,
                "error": usage.enrichment_error,
                "subscriber_type": usage.subscriber_type,
                "customer_segment": usage.customer_segment,
                # The list — one record, many groups, one result (§2).
                "subscriber_groups": list(usage.subscriber_groups or []),
                "product_code": usage.product_code,
                "tariff_plan_id": usage.tariff_plan_code,
                "network_relation": usage.network_relation,
                "destination_type": usage.destination_type,
                "destination_zone": usage.destination_zone,
                "origin_zone": usage.origin_zone,
                "time_band": usage.time_band,
                "day_type": usage.day_type,
                "roaming_flag": usage.roaming,
                "bundle_ids": list(usage.bundle_ids or []),
                "offer_ids": list(usage.offer_ids or []),
                "rating_context_key": usage.context_key,
            }
            if usage
            else None
        ),
        "candidate_rules": rules_with(
            EvaluationStatus.SELECTED,
            EvaluationStatus.MATCHED,
            EvaluationStatus.MATCHED_NOT_SELECTED,
            EvaluationStatus.REJECTED,
        ),
        "selected_rules": rules_with(EvaluationStatus.SELECTED),
        "rejected_rules": rules_with(
            EvaluationStatus.REJECTED, EvaluationStatus.MATCHED_NOT_SELECTED
        ),
        "calculation_steps": [
            {
                "sequence": c.sequence_number,
                "component_type": c.component_type,
                "rule_key": c.rule_key,
                "input_quantity": float(c.input_quantity)
                if c.input_quantity is not None
                else None,
                "input_amount": float(c.input_amount) if c.input_amount is not None else None,
                "adjustment_amount": float(c.adjustment_amount)
                if c.adjustment_amount is not None
                else None,
                "output_amount": float(c.output_amount)
                if c.output_amount is not None
                else None,
                "detail": c.calculation_detail,
            }
            for c in components
        ],
        "expected_charge": float(result.expected_charge),
        "actual_charge": float(result.actual_charge)
        if result.actual_charge is not None
        else None,
        "variance_amount": float(result.variance_amount),
        "variance_percentage": float(result.variance_percentage)
        if result.variance_percentage is not None
        else None,
        "currency": result.currency,
        "rating_status": result.rating_status,
        "actual_charge_status": result.actual_charge_status,
        "explanation": result.explanation,
        "exceptions": [
            {
                "exception_id": e.exception_id,
                "type": e.exception_type,
                "code": e.exception_code,
                "message": e.error_message,
                "status": e.status,
                "retry_count": e.retry_count,
            }
            for e in exceptions
        ],
    }


# --- Exceptions -------------------------------------------------------------


class UsageExceptionRead(Base):
    exception_id: str
    usage_id: str
    run_id: str | None
    exception_type: str
    exception_code: str
    error_message: str
    status: str
    retry_count: int
    created_at: datetime
    resolved_at: datetime | None


@router.get(
    "/usage-exceptions",
    summary="Per-record rating exceptions",
    dependencies=[Depends(_view_exceptions)],
)
async def list_usage_exceptions(
    db: DbSession,
    page: PageParams,
    status: str | None = Query(default=None),
    exception_type: str | None = Query(default=None),
    exception_code: str | None = Query(default=None),
) -> dict[str, Any]:
    conditions = []
    if status:
        conditions.append(UsageException.status == status)
    if exception_type:
        conditions.append(UsageException.exception_type == exception_type)
    if exception_code:
        conditions.append(UsageException.exception_code == exception_code)

    total = int(
        (
            await db.execute(
                select(func.count()).select_from(UsageException).where(*conditions)
            )
        ).scalar_one()
    )
    rows = (
        await db.execute(
            select(UsageException)
            .where(*conditions)
            .order_by(UsageException.created_at.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    ).scalars().all()
    return {
        "items": [UsageExceptionRead.model_validate(r).model_dump() for r in rows],
        "total": total,
        "limit": page.limit,
        "offset": page.offset,
    }


@router.post(
    "/usage-exceptions/{exception_id}/retry",
    summary="Re-rate the record behind one exception",
)
async def retry_exception(
    exception_id: str,
    db: DbSession,
    actor=Depends(ExceptionOwner),
) -> dict[str, Any]:
    row = (
        await db.execute(
            select(UsageException).where(UsageException.exception_id == exception_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Exception '{exception_id}' was not found.")

    if row.retry_count >= settings.maximum_retry_count:
        # Retrying a permanent data defect forever hides it behind noise. The
        # record needs the underlying reference data fixed, not another attempt.
        row.status = UsageExceptionStatus.EXHAUSTED
        await db.commit()
        raise ValidationFailedError(
            f"This exception has already been retried {row.retry_count} times.",
            details={
                "maximum_retry_count": settings.maximum_retry_count,
                "hint": "Fix the underlying reference data or rule, then re-rate.",
            },
        )

    row.retry_count += 1
    row.last_retry_at = datetime.now(tz=None).astimezone()
    row.status = UsageExceptionStatus.RETRYING

    summary = await execution.execute_rating_batch(
        db,
        usage_ids=[row.usage_id],
        reprocess=True,
        actor_id=getattr(actor, "user_id", None),
    )
    resolved = await execution.resolve_exceptions_for(db, [row.usage_id])
    await db.commit()

    return {
        "exception_id": exception_id,
        "usage_id": row.usage_id,
        "retry_count": row.retry_count,
        "resolved": bool(resolved),
        "batch": summary.as_dict(),
    }


# --- MSC source -------------------------------------------------------------


class IngestRequest(Base):
    source_system: str = Field(default="MSC01")
    tables: list[str] | None = None
    limit: int | None = Field(default=None, ge=1)
    from_start: bool = Field(
        default=False,
        description=(
            "Ignore the cursor and re-read from the beginning. Safe: writes are "
            "upserts on the deterministic usage_id."
        ),
    )


@router.post(
    "/msc/ingest",
    summary="Pull MSC records from the source and store them as canonical usage",
)
async def ingest_msc(
    payload: IngestRequest,
    db: DbSession,
    actor=Depends(RunOperator),
) -> dict[str, Any]:
    summary = await msc_svc.ingest(
        db,
        source_system=payload.source_system,
        tables=payload.tables,
        limit=payload.limit,
        from_start=payload.from_start,
        actor_id=getattr(actor, "user_id", None),
    )
    return summary.as_dict()


@router.get(
    "/msc/status",
    summary="Source reachability and how far each table has been read",
    dependencies=[Depends(_view_runs)],
)
async def msc_status(
    db: DbSession, source_system: str = Query(default="MSC01")
) -> dict[str, Any]:
    return await msc_svc.status(db, source_system)


@router.get(
    "/meta/assurance-statuses",
    summary="Assurance statuses and exception vocabulary for the UI",
    dependencies=[Depends(_view_runs)],
)
async def meta() -> dict[str, Any]:
    from app.modules.cdr.bulk_enrich import EnrichmentStatus
    from app.modules.rating.audit_models import ActualChargeStatus, RejectionReason

    return {
        "rating_statuses": [s.value for s in RatingStatus],
        "enrichment_statuses": [s.value for s in EnrichmentStatus],
        "evaluation_statuses": [s.value for s in EvaluationStatus],
        "rejection_reasons": [r.value for r in RejectionReason],
        "actual_charge_statuses": [s.value for s in ActualChargeStatus],
        "exception_statuses": [s.value for s in UsageExceptionStatus],
        "tolerances": {
            "absolute": settings.absolute_variance_tolerance,
            "percentage": settings.percentage_variance_tolerance,
        },
    }
