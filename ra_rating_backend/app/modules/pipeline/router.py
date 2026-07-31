"""Pipeline API: upload, monitor, retry.

The upload returns immediately with a run id. Everything after that is polled —
which is what makes a 1-crore batch a normal operation rather than a timeout.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.errors import ValidationFailedError
from app.core.rbac import RatingPermKey
from app.modules.pipeline import service as svc
from app.modules.pipeline.constants import STAGES
from app.modules.pipeline.models import PipelineRun

router = APIRouter(tags=["pipeline"])

_view = require(RatingPermKey.RUNS, "view")
PipelineOperator = principal_with(RatingPermKey.RUNS, "edit")

MAX_UPLOAD_BYTES = 128 * 1024 * 1024


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StageState(BaseModel):
    key: str
    label: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float | None = None
    records_in: int = 0
    records_out: int = 0
    records_failed: int = 0
    detail: str = ""
    error: str | None = None


class RunRead(Base):
    id: str
    filename: str
    source_system: str
    cdr_type: str
    status: str
    current_stage: str | None
    stages: list[StageState]
    batch_id: str | None
    rating_run_id: str | None
    snapshot_version: int | None
    total_records: int
    exception_count: int
    error: str | None
    summary: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    triggered_by_name: str | None
    connector_id: str | None
    created_at: datetime


class EventRead(Base):
    id: str
    stage: str | None
    level: str
    message: str
    created_at: datetime


class RunDetail(RunRead):
    events: list[EventRead] = Field(default_factory=list)


@router.get(
    "/pipeline/stages",
    summary="The stages a run goes through",
    dependencies=[Depends(_view)],
)
async def stage_catalog() -> list[dict[str, str]]:
    """Drives the pipeline monitor's stage rail, and documents the DAG."""
    return [
        {
            "key": s.key,
            "label": s.label,
            "description": s.description,
            "input_label": s.input_label,
            "output_label": s.output_label,
        }
        for s in STAGES
    ]


@router.post(
    "/pipeline/runs",
    response_model=RunRead,
    status_code=202,
    summary="Upload CDRs and start the pipeline (returns immediately)",
)
async def start_run(
    db: DbSession,
    principal: PipelineOperator,
    file: UploadFile = File(...),
    cdr_type: str = Form(..., description="MSC | SMS | DATA | TAP"),
    source_system: str = Form(...),
) -> PipelineRun:
    data = await file.read()
    if not data:
        raise ValidationFailedError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValidationFailedError(
            f"The file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            details={"hint": "Split it, or land it through a connector."},
        )

    run = await svc.create_run(
        db,
        filename=file.filename or "upload",
        data=data,
        cdr_type=cdr_type,
        source_system=source_system,
        actor_id=principal.id,
        actor_name=principal.full_name,
    )
    # Committed before the background task starts, so the worker's own session
    # can see the row.
    await db.commit()
    svc.launch(run.id)
    return run


@router.get(
    "/pipeline/runs",
    response_model=list[RunRead],
    summary="Pipeline run monitor",
    dependencies=[Depends(_view)],
)
async def list_runs(
    db: DbSession, page: PageParams, status: str | None = Query(None)
) -> list[PipelineRun]:
    stmt = select(PipelineRun)
    if status:
        stmt = stmt.where(PipelineRun.status == status.upper())
    stmt = (
        stmt.order_by(PipelineRun.created_at.desc()).limit(page.limit).offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get(
    "/pipeline/runs/{run_id}",
    response_model=RunDetail,
    summary="Run detail with per-stage progress and its event log",
    dependencies=[Depends(_view)],
)
async def get_run(db: DbSession, run_id: str) -> RunDetail:
    run = await svc.get_run(db, run_id)
    detail = RunDetail.model_validate(run)
    detail.events = [EventRead.model_validate(e) for e in await svc.events(db, run_id)]
    return detail


@router.post(
    "/pipeline/runs/{run_id}/retry",
    response_model=RunRead,
    status_code=202,
    summary="Re-run a stage and everything after it",
)
async def retry(
    db: DbSession,
    run_id: str,
    principal: PipelineOperator,
    stage: str = Query(..., description="Stage key to restart from"),
) -> PipelineRun:
    del principal
    key = stage.upper()
    run = await svc.retry_from(db, run_id, key)
    # Committed before the worker starts, so its own session sees the reset.
    await db.commit()
    svc.launch_from(run.id, key)
    return run
