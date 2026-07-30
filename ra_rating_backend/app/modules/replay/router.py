"""Replay & recovery API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.errors import NotFoundError
from app.core.rbac import RatingPermKey
from app.modules.replay import service as svc
from app.modules.replay.models import ReplayRun

router = APIRouter(prefix="/replay", tags=["replay"])

_view = require(RatingPermKey.EXCEPTIONS, "view")
# Replay rewrites balance state and produces a new run — that is an
# exceptions-workflow power, not a read.
ReplayOperator = principal_with(RatingPermKey.EXCEPTIONS, "edit")


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ReplayRead(Base):
    id: str
    source_run_id: str
    new_run_id: str | None
    snapshot_id: str
    snapshot_version: int | None
    exception_id: str | None
    status: str
    error: str | None
    recovered_amount: float
    comparison: dict[str, Any]
    triggered_by_name: str | None
    created_at: datetime
    finished_at: datetime | None


class ReplayRequest(BaseModel):
    run_id: str
    #: Omit to replay against the currently active snapshot (the usual case:
    #: the fix has just been activated).
    snapshot_id: str | None = None
    #: Case this replay was launched from, for recovery tracking.
    exception_id: str | None = None


@router.get(
    "",
    response_model=list[ReplayRead],
    summary="Replay history, newest first",
    dependencies=[Depends(_view)],
)
async def list_replays(db: DbSession, page: PageParams) -> list[ReplayRun]:
    return await svc.list_replays(db, page.limit, page.offset)


@router.get(
    "/recovery",
    summary="Total revenue recovered across completed replays",
    dependencies=[Depends(_view)],
)
async def recovery(db: DbSession) -> dict:
    return await svc.recovery_total(db)


@router.get(
    "/{replay_id}",
    response_model=ReplayRead,
    summary="One replay with its before/after comparison",
    dependencies=[Depends(_view)],
)
async def get_replay(db: DbSession, replay_id: str) -> ReplayRun:
    row = await db.get(ReplayRun, replay_id)
    if row is None:
        raise NotFoundError(f"Replay '{replay_id}' was not found.")
    return row


@router.post(
    "",
    response_model=ReplayRead,
    summary="Rewind a run's consumption and re-rate it against a snapshot",
)
async def create_replay(
    db: DbSession,
    payload: ReplayRequest,
    principal: ReplayOperator,
) -> ReplayRun:
    return await svc.execute_replay(
        db,
        source_run_id=payload.run_id,
        snapshot_id=payload.snapshot_id,
        exception_id=payload.exception_id,
        actor_id=principal.id,
        actor_name=principal.full_name,
    )
