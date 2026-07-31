"""CDR ingestion and batch monitoring API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.errors import ValidationFailedError
from app.core.rbac import RatingPermKey
from app.modules.cdr import service as svc
from app.modules.cdr.models import CdrBatch, CdrEnriched, CdrLanding
from app.modules.cdr.normalize import CDR_PROFILES

router = APIRouter(tags=["cdr"])

_view = require(RatingPermKey.RUNS, "view")
CdrLoader = principal_with(RatingPermKey.RUNS, "edit")

MAX_UPLOAD_BYTES = 128 * 1024 * 1024


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BatchSummary(Base):
    id: str
    filename: str
    source_system: str
    cdr_type: str
    event_date: date | None
    status: str
    total_records: int
    loaded_records: int
    duplicate_records: int
    rejected_records: int
    enriched_records: int
    control_total: float | None
    summary: dict[str, Any]
    created_at: datetime


class EnrichedRead(Base):
    id: str
    cdr_id: str
    subscriber_id: str | None
    msisdn: str | None
    service_type: str
    event_timestamp: datetime
    event_date: date
    duration_seconds: float | None
    usage_volume: float | None
    calling_number: str | None
    called_number: str | None
    actual_charge: float | None
    currency: str | None
    product_code: str | None
    account_type: str | None
    destination_zone: str | None
    on_net: bool | None
    time_band: str | None
    roaming: bool | None
    context_key: str | None
    context_hash: str | None
    quality_status: str
    quality_detail: str | None


class LandingRead(Base):
    id: str
    row_number: int
    payload: dict[str, Any]
    status: str
    error: str | None


@router.get("/cdr-profiles", summary="Supported CDR formats", dependencies=[Depends(_view)])
async def cdr_profiles() -> list[dict[str, Any]]:
    """The source layouts the normalizer understands, and the columns it reads."""
    return [
        {
            "code": p.code,
            "label": p.label,
            "service_type": p.service_type,
            "required": list(p.required),
            "identity": list(p.identity),
            "notes": p.notes,
            "fields": {k: list(v) for k, v in p.fields.items()},
        }
        for p in CDR_PROFILES.values()
    ]


@router.post(
    "/cdr-batches",
    response_model=BatchSummary,
    status_code=201,
    summary="Ingest a CDR file: land, normalize and enrich",
)
async def ingest_batch(
    db: DbSession,
    principal: CdrLoader,
    file: UploadFile = File(...),
    cdr_type: str = Form(..., description="MSC | SMS | DATA | TAP"),
    source_system: str = Form(...),
) -> CdrBatch:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValidationFailedError(
            f"The file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            details={"hint": "Split it, or land it via the streaming path."},
        )
    return await svc.ingest(
        db,
        filename=file.filename or "upload",
        data=data,
        cdr_type=cdr_type,
        source_system=source_system,
        actor_id=principal.id,
    )


@router.get(
    "/cdr-batches",
    response_model=list[BatchSummary],
    summary="Batch monitor",
    dependencies=[Depends(_view)],
)
async def list_batches(db: DbSession, page: PageParams) -> list[CdrBatch]:
    stmt = (
        select(CdrBatch)
        .order_by(CdrBatch.created_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get(
    "/cdr-batches/{batch_id}",
    response_model=BatchSummary,
    summary="Batch detail",
    dependencies=[Depends(_view)],
)
async def get_batch(db: DbSession, batch_id: str) -> CdrBatch:
    return await svc.get_batch(db, batch_id)


@router.get(
    "/cdr-batches/{batch_id}/quality",
    summary="Data-quality breakdown",
    dependencies=[Depends(_view)],
)
async def quality(db: DbSession, batch_id: str) -> list[dict[str, Any]]:
    await svc.get_batch(db, batch_id)
    return await svc.data_quality(db, batch_id)


@router.get(
    "/cdr-batches/{batch_id}/contexts",
    summary="Most frequent rating contexts in this batch",
    dependencies=[Depends(_view)],
)
async def contexts(
    db: DbSession, batch_id: str, limit: int = Query(50, ge=1, le=500)
) -> list[dict[str, Any]]:
    await svc.get_batch(db, batch_id)
    return await svc.context_frequency(db, batch_id, limit)


@router.get(
    "/cdr-batches/{batch_id}/records",
    response_model=list[LandingRead],
    summary="Landed records — filter to DUPLICATE or REJECTED to investigate",
    dependencies=[Depends(_view)],
)
async def landing_records(
    db: DbSession,
    batch_id: str,
    page: PageParams,
    status: str | None = Query(None),
) -> list[CdrLanding]:
    await svc.get_batch(db, batch_id)
    stmt = select(CdrLanding).where(CdrLanding.batch_id == batch_id)
    if status:
        stmt = stmt.where(CdrLanding.status == status.upper())
    stmt = stmt.order_by(CdrLanding.row_number).limit(page.limit).offset(page.offset)
    return list((await db.execute(stmt)).scalars().all())


@router.get(
    "/cdrs",
    response_model=list[EnrichedRead],
    summary="Enriched CDRs — the CDR investigation surface",
    dependencies=[Depends(_view)],
)
async def list_cdrs(
    db: DbSession,
    page: PageParams,
    batch_id: str | None = Query(None),
    msisdn: str | None = Query(None),
    cdr_id: str | None = Query(None),
    quality_status: str | None = Query(None),
    service_type: str | None = Query(None),
) -> list[CdrEnriched]:
    stmt = select(CdrEnriched)
    if batch_id:
        stmt = stmt.where(CdrEnriched.batch_id == batch_id)
    if msisdn:
        stmt = stmt.where(CdrEnriched.msisdn == msisdn)
    if cdr_id:
        stmt = stmt.where(CdrEnriched.cdr_id == cdr_id)
    if quality_status:
        stmt = stmt.where(CdrEnriched.quality_status == quality_status.upper())
    if service_type:
        stmt = stmt.where(CdrEnriched.service_type == service_type.upper())
    stmt = (
        stmt.order_by(CdrEnriched.event_timestamp.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get(
    "/cdrs/{cdr_enriched_id}",
    response_model=EnrichedRead,
    summary="One enriched CDR",
    dependencies=[Depends(_view)],
)
async def get_cdr(db: DbSession, cdr_enriched_id: str) -> CdrEnriched:
    row = await db.get(CdrEnriched, cdr_enriched_id)
    if row is None:
        from app.core.errors import NotFoundError

        raise NotFoundError(f"CDR '{cdr_enriched_id}' was not found.")
    return row
