"""Pipeline orchestration.

The caller uploads a file and gets a run id back immediately; the stages run in
the background and the UI polls. A 1-crore batch cannot be a synchronous HTTP
request, and a request that times out mid-run looks exactly like data loss.

Each stage runs in **its own transaction**. That is deliberate: when enrichment
fails because a prefix is missing, ingestion and normalization stay committed,
so the fix is "load the prefix and re-run enrichment", not "re-upload 1 crore
rows". Restartability is the whole reason the stage boundaries exist.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionFactory
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.modules.pipeline.constants import (
    STAGE_BY_KEY,
    STAGE_ORDER,
    PipelineStatus,
    StageStatus,
    initial_stages,
)
from app.modules.pipeline.models import PipelineEvent, PipelineRun
from app.modules.pipeline.stages import STAGE_FUNCTIONS

log = get_logger("pipeline")


async def create_run(
    db: AsyncSession,
    *,
    filename: str,
    data: bytes,
    cdr_type: str,
    source_system: str,
    actor_id: str,
    actor_name: str,
    connector_id: str | None = None,
) -> PipelineRun:
    run = PipelineRun(
        filename=filename,
        source_system=source_system.upper(),
        cdr_type=cdr_type.upper(),
        status=PipelineStatus.QUEUED.value,
        stages=initial_stages(),
        payload=data,
        triggered_by=actor_id,
        triggered_by_name=actor_name,
        connector_id=connector_id,
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


def launch(run_id: str) -> None:
    """Start the run in the background.

    A fire-and-forget task, with the reference held so the event loop cannot
    garbage-collect it mid-run — an orphaned task would leave the run stuck in
    RUNNING with no way to tell why.
    """
    task = asyncio.create_task(execute(run_id))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


def launch_from(run_id: str, stage_key: str) -> None:
    """Re-run one stage and everything after it, in the background."""
    remaining = list(STAGE_ORDER[STAGE_ORDER.index(stage_key) :])
    task = asyncio.create_task(execute(run_id, only=remaining))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


_BACKGROUND: set[asyncio.Task] = set()


async def execute(run_id: str, *, only: list[str] | None = None) -> None:
    """Run the stages in order, each in its own transaction.

    ``only`` restarts a subset — the recovery path after a stage failed for a
    reason outside the data, such as missing reference data.
    """
    stages_to_run = only or list(STAGE_ORDER)

    async with SessionFactory() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            log.error("pipeline_run_missing", run_id=run_id)
            return
        run.status = PipelineStatus.RUNNING.value
        run.started_at = run.started_at or datetime.now(UTC)
        run.error = None
        await db.commit()

    failed = False
    for key in stages_to_run:
        ok = await _run_stage(run_id, key)
        if not ok:
            failed = True
            break

    async with SessionFactory() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            return
        stages = run.stages or []
        issues = sum(int(s.get("records_failed") or 0) for s in stages)
        if failed:
            run.status = PipelineStatus.FAILED.value
        elif issues:
            run.status = PipelineStatus.COMPLETED_WITH_ISSUES.value
        else:
            run.status = PipelineStatus.COMPLETED.value
        run.finished_at = datetime.now(UTC)
        run.current_stage = None
        await db.commit()
        log.info("pipeline_finished", run_id=run_id, status=run.status)


async def _run_stage(run_id: str, key: str) -> bool:
    """Execute one stage. Returns False when it failed."""
    spec = STAGE_BY_KEY[key]
    started = time.perf_counter()

    async with SessionFactory() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            return False
        _patch_stage(run, key, status=StageStatus.RUNNING, started_at=_iso())
        run.current_stage = key
        db.add(PipelineEvent(run_id=run_id, stage=key, message=f"{spec.label} started."))
        await db.commit()

    try:
        async with SessionFactory() as db:
            run = await db.get(PipelineRun, run_id)
            assert run is not None
            records_in, records_out, records_failed, detail = await STAGE_FUNCTIONS[key](db, run)
            _patch_stage(
                run,
                key,
                status=StageStatus.COMPLETED,
                finished_at=_iso(),
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                records_in=records_in,
                records_out=records_out,
                records_failed=records_failed,
                detail=detail,
            )
            db.add(
                PipelineEvent(
                    run_id=run_id,
                    stage=key,
                    message=f"{spec.label} completed — {detail}.",
                )
            )
            await db.commit()
        return True

    except Exception as exc:
        message = str(exc)
        log.error("pipeline_stage_failed", run_id=run_id, stage=key, error=message)
        async with SessionFactory() as db:
            run = await db.get(PipelineRun, run_id)
            if run is None:
                return False
            _patch_stage(
                run,
                key,
                status=StageStatus.FAILED,
                finished_at=_iso(),
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                error=message,
            )
            # Stages after the failure never ran; say so rather than leaving
            # them PENDING, which reads as "still to come".
            hit = False
            for entry in run.stages:
                if entry["key"] == key:
                    hit = True
                    continue
                if hit and entry["status"] == StageStatus.PENDING.value:
                    entry["status"] = StageStatus.SKIPPED.value
            run.stages = list(run.stages)
            run.error = f"{spec.label}: {message}"
            db.add(
                PipelineEvent(
                    run_id=run_id, stage=key, level="ERROR",
                    message=f"{spec.label} failed — {message}",
                )
            )
            await db.commit()
        return False


def _patch_stage(run: PipelineRun, key: str, **fields: Any) -> None:
    stages = [dict(s) for s in (run.stages or [])]
    for entry in stages:
        if entry["key"] == key:
            for name, value in fields.items():
                entry[name] = value.value if hasattr(value, "value") else value
    # Reassigned rather than mutated in place: SQLAlchemy does not track
    # mutations inside a JSONB list, so an in-place edit would never persist.
    run.stages = stages


def _iso() -> str:
    return datetime.now(UTC).isoformat()


# --- Reads ------------------------------------------------------------------


async def get_run(db: AsyncSession, run_id: str) -> PipelineRun:
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise NotFoundError(f"Pipeline run '{run_id}' was not found.")
    return run


async def events(db: AsyncSession, run_id: str, limit: int = 100) -> list[PipelineEvent]:
    stmt = (
        select(PipelineEvent)
        .where(PipelineEvent.run_id == run_id)
        .order_by(PipelineEvent.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def retry_from(db: AsyncSession, run_id: str, stage_key: str) -> PipelineRun:
    """Re-run a failed stage and everything after it.

    The stages before it stay committed, which is the point of the split — a
    missing prefix should cost an enrichment re-run, not a re-upload.
    """
    run = await get_run(db, run_id)
    if stage_key not in STAGE_ORDER:
        raise NotFoundError(f"Unknown stage '{stage_key}'.")
    if run.status == PipelineStatus.RUNNING.value:
        raise ConflictError("This run is still in progress.")
    if stage_key == STAGE_ORDER[0] and run.payload is None:
        raise ConflictError(
            "The uploaded file is no longer held for this run, so ingestion cannot be re-run.",
            details={"hint": "Re-upload the file as a new run."},
        )

    index = STAGE_ORDER.index(stage_key)
    remaining = list(STAGE_ORDER[index:])
    stages = [dict(s) for s in run.stages]
    for entry in stages:
        if entry["key"] in remaining:
            entry.update(
                status=StageStatus.PENDING.value,
                started_at=None,
                finished_at=None,
                duration_ms=None,
                records_in=0,
                records_out=0,
                records_failed=0,
                detail="",
                error=None,
            )
    run.stages = stages
    run.status = PipelineStatus.QUEUED.value
    run.error = None
    await db.flush()
    return run
