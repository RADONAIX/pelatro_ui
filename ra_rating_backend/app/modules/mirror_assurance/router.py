"""API for manual/scheduled rating assurance over the mirror database."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.rbac import RatingPermKey
from app.modules.mirror_assurance import service as svc
from app.modules.mirror_assurance.models import MirrorAssuranceRun, MirrorAssuranceSchedule

router = APIRouter(prefix="/mirror-assurance", tags=["mirror-assurance"])
_view = require(RatingPermKey.RUNS, "view")
AssuranceOperator = principal_with(RatingPermKey.RUNS, "edit")


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StageRead(Base):
    key: str
    label: str
    status: str
    detail: str = ""
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class RunRead(Base):
    id: str
    trigger: str
    status: str
    window_start: datetime
    window_end: datetime
    tolerance: Decimal
    stages: list[StageRead]
    row_count: int
    matched_count: int
    undercharged_count: int
    overcharged_count: int
    exception_count: int
    total_variance: Decimal
    summary: dict[str, Any]
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    triggered_by_name: str | None
    created_at: datetime


class RunCreate(BaseModel):
    window_start: datetime
    window_end: datetime
    tolerance: Decimal = Field(default=Decimal("0.01"), ge=0)


class ScheduleRead(Base):
    id: str
    enabled: bool
    interval_minutes: int
    window_hours: int
    tolerance: Decimal
    next_run_at: datetime | None
    last_run_at: datetime | None
    updated_by_name: str | None


class ScheduleUpdate(BaseModel):
    enabled: bool
    interval_minutes: int = Field(ge=5, le=43_200)
    window_hours: int = Field(ge=1, le=8_760)
    tolerance: Decimal = Field(default=Decimal("0.01"), ge=0)


class ResultRead(Base):
    ordinal: int
    event_id: str
    service_type: str | None
    reconciliation_status: str
    event_time: datetime | None
    payload: dict[str, Any]


class ResultPage(BaseModel):
    total: int
    limit: int
    offset: int
    rows: list[ResultRead]


@router.get("/schedule", response_model=ScheduleRead, dependencies=[Depends(_view)])
async def schedule(db: DbSession) -> MirrorAssuranceSchedule:
    return await svc.get_schedule(db)


@router.put("/schedule", response_model=ScheduleRead)
async def save_schedule(
    db: DbSession, payload: ScheduleUpdate, principal: AssuranceOperator
) -> MirrorAssuranceSchedule:
    return await svc.update_schedule(
        db,
        **payload.model_dump(),
        actor_id=principal.id,
        actor_name=principal.full_name,
    )


@router.post("/runs", response_model=RunRead, status_code=202)
async def start_run(
    db: DbSession, payload: RunCreate, principal: AssuranceOperator
) -> MirrorAssuranceRun:
    run = await svc.create_run(
        db,
        **payload.model_dump(),
        trigger="MANUAL",
        actor_id=principal.id,
        actor_name=principal.full_name,
    )
    await db.commit()
    svc.launch(run.id)
    return run


@router.get("/runs", response_model=list[RunRead], dependencies=[Depends(_view)])
async def runs(db: DbSession, page: PageParams) -> list[MirrorAssuranceRun]:
    stmt = (
        select(MirrorAssuranceRun)
        .order_by(MirrorAssuranceRun.created_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get("/runs/{run_id}", response_model=RunRead, dependencies=[Depends(_view)])
async def run(db: DbSession, run_id: str) -> MirrorAssuranceRun:
    return await svc.get_run(db, run_id)


@router.get(
    "/runs/{run_id}/results", response_model=ResultPage, dependencies=[Depends(_view)]
)
async def result_rows(
    db: DbSession,
    run_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
) -> ResultPage:
    total, rows = await svc.results(db, run_id, limit=limit, offset=offset, status=status)
    return ResultPage(
        total=total,
        limit=limit,
        offset=offset,
        rows=[ResultRead.model_validate(row) for row in rows],
    )


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(db: DbSession, run_id: str, principal: AssuranceOperator) -> None:
    del principal
    await svc.delete_run(db, run_id)
