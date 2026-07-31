"""Isolated execution and scheduling for the mirror assurance query."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionFactory
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.modules.mirror.engine import mirror_session
from app.modules.mirror_assurance.models import (
    MirrorAssuranceResult,
    MirrorAssuranceRun,
    MirrorAssuranceSchedule,
)

log = get_logger("mirror.assurance")
QUERY = Path(__file__).with_name("rating_assurance.sql").read_text(encoding="utf-8")
ACTIVE = {"QUEUED", "RUNNING"}
RESULT_BATCH_SIZE = 500
SCHEDULE_ID = "rating-assurance"
UNDEFINED_TABLE = "42P01"


def initial_stages() -> list[dict[str, Any]]:
    return [
        {"key": "READ_MIRROR", "label": "Read mirror data", "status": "PENDING", "detail": ""},
        {"key": "RECONCILE", "label": "Calculate assurance", "status": "PENDING", "detail": ""},
        {"key": "STORE_RESULTS", "label": "Store results", "status": "PENDING", "detail": ""},
    ]


def _patch_stage(run: MirrorAssuranceRun, key: str, **fields: Any) -> None:
    stages = [dict(stage) for stage in (run.stages or [])]
    for stage in stages:
        if stage["key"] == key:
            stage.update(fields)
    run.stages = stages


async def get_schedule(db: AsyncSession, *, lock: bool = False) -> MirrorAssuranceSchedule:
    stmt = select(MirrorAssuranceSchedule).where(MirrorAssuranceSchedule.id == SCHEDULE_ID)
    if lock:
        stmt = stmt.with_for_update()
    schedule = (await db.execute(stmt)).scalar_one_or_none()
    if schedule is None:
        schedule = MirrorAssuranceSchedule(id=SCHEDULE_ID, enabled=False)
        db.add(schedule)
        await db.flush()
        await db.refresh(schedule)
    return schedule


async def update_schedule(
    db: AsyncSession,
    *,
    enabled: bool,
    interval_minutes: int,
    window_hours: int,
    tolerance: Decimal,
    actor_id: str,
    actor_name: str,
) -> MirrorAssuranceSchedule:
    schedule = await get_schedule(db, lock=True)
    schedule.enabled = enabled
    schedule.interval_minutes = interval_minutes
    schedule.window_hours = window_hours
    schedule.tolerance = tolerance
    schedule.updated_by = actor_id
    schedule.updated_by_name = actor_name
    schedule.next_run_at = (
        datetime.now(UTC) + timedelta(minutes=interval_minutes) if enabled else None
    )
    await db.flush()
    await db.refresh(schedule)
    return schedule


async def create_run(
    db: AsyncSession,
    *,
    window_start: datetime,
    window_end: datetime,
    tolerance: Decimal,
    trigger: str,
    actor_id: str | None,
    actor_name: str | None,
) -> MirrorAssuranceRun:
    if not settings.mirror_enabled:
        raise ValidationFailedError(
            "Mirror rating assurance is unavailable because MIRROR_ENABLED is false."
        )
    if window_end <= window_start:
        raise ValidationFailedError("The assurance window end must be after its start.")
    # The singleton schedule row doubles as a short advisory lock. It closes the
    # race where two API workers both observe zero active runs and enqueue one.
    await get_schedule(db, lock=True)
    active = await db.scalar(
        select(func.count()).select_from(MirrorAssuranceRun).where(
            MirrorAssuranceRun.status.in_(ACTIVE)
        )
    )
    if active:
        raise ConflictError("A mirror rating-assurance run is already in progress.")
    run = MirrorAssuranceRun(
        trigger=trigger,
        status="QUEUED",
        window_start=window_start,
        window_end=window_end,
        tolerance=tolerance,
        stages=initial_stages(),
        triggered_by=actor_id,
        triggered_by_name=actor_name,
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


def launch(run_id: str) -> None:
    task = asyncio.create_task(execute(run_id))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


_BACKGROUND: set[asyncio.Task[Any]] = set()


async def _mark_started(run_id: str) -> MirrorAssuranceRun | None:
    async with SessionFactory() as db:
        run = await db.get(MirrorAssuranceRun, run_id)
        if run is None:
            return None
        run.status = "RUNNING"
        run.started_at = datetime.now(UTC)
        run.error = None
        _patch_stage(run, "READ_MIRROR", status="RUNNING", started_at=run.started_at.isoformat())
        await db.commit()
        return run


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


async def _store_batch(run_id: str, start: int, payloads: list[dict[str, Any]]) -> None:
    rows = [
        {
            "run_id": run_id,
            "ordinal": start + offset,
            "event_id": str(payload.get("event_id") or ""),
            "service_type": payload.get("service_type"),
            "reconciliation_status": str(payload.get("reconciliation_status") or "ERROR"),
            "event_time": payload.get("event_time"),
            "payload": _json_value(payload),
        }
        for offset, payload in enumerate(payloads)
    ]
    async with SessionFactory() as db:
        await db.execute(insert(MirrorAssuranceResult), rows)
        await db.commit()


async def execute(run_id: str) -> None:
    run = await _mark_started(run_id)
    if run is None:
        return

    counts: dict[str, int] = {}
    total_variance = Decimal(0)
    ordinal = 1
    batch: list[dict[str, Any]] = []
    try:
        async with mirror_session() as mirror:
            # This job may read the mirror but can never mutate it. The supplied
            # INSERT/CREATE wrapper is not part of QUERY, and PostgreSQL enforces
            # the boundary for the whole transaction as a second line of defence.
            await mirror.execute(text("SET TRANSACTION READ ONLY"))
            await mirror.execute(text("SET LOCAL statement_timeout = '30min'"))
            stream = await mirror.stream(
                text(QUERY),
                {
                    "window_start": run.window_start,
                    "window_end": run.window_end,
                    "reconciliation_tolerance": run.tolerance,
                },
            )
            async for row in stream.mappings():
                payload = dict(row)
                status = str(payload.get("reconciliation_status") or "ERROR")
                counts[status] = counts.get(status, 0) + 1
                variance = payload.get("charge_variance")
                if variance is not None:
                    total_variance += Decimal(str(variance))
                batch.append(payload)
                if len(batch) >= RESULT_BATCH_SIZE:
                    await _store_batch(run_id, ordinal, batch)
                    ordinal += len(batch)
                    batch = []
            if batch:
                await _store_batch(run_id, ordinal, batch)
                ordinal += len(batch)

        row_count = ordinal - 1
        async with SessionFactory() as db:
            current = await db.get(MirrorAssuranceRun, run_id)
            if current is None:
                return
            now = datetime.now(UTC)
            _patch_stage(
                current, "READ_MIRROR", status="COMPLETED", finished_at=now.isoformat(),
                detail=f"Read {row_count} events from the mirror.",
            )
            _patch_stage(
                current, "RECONCILE", status="COMPLETED", finished_at=now.isoformat(),
                detail=f"Calculated {row_count} expected charges.",
            )
            _patch_stage(
                current, "STORE_RESULTS", status="COMPLETED", finished_at=now.isoformat(),
                detail=f"Stored {row_count} result rows.",
            )
            current.status = "COMPLETED"
            current.row_count = row_count
            current.matched_count = counts.get("MATCHED", 0)
            current.undercharged_count = counts.get("UNDERCHARGED", 0)
            current.overcharged_count = counts.get("OVERCHARGED", 0)
            current.exception_count = row_count - current.matched_count
            current.total_variance = total_variance
            current.summary = {"by_status": counts}
            current.finished_at = now
            await db.commit()
        log.info("mirror_assurance_completed", run_id=run_id, rows=row_count)
    except Exception as exc:
        log.exception("mirror_assurance_failed", run_id=run_id)
        async with SessionFactory() as db:
            current = await db.get(MirrorAssuranceRun, run_id)
            if current is None:
                return
            current.status = "FAILED"
            current.error = str(exc)
            current.finished_at = datetime.now(UTC)
            for stage in current.stages or []:
                if stage["status"] == "RUNNING":
                    stage["status"] = "FAILED"
                    stage["error"] = str(exc)
                elif stage["status"] == "PENDING":
                    stage["status"] = "SKIPPED"
            current.stages = list(current.stages)
            await db.commit()


async def get_run(db: AsyncSession, run_id: str) -> MirrorAssuranceRun:
    run = await db.get(MirrorAssuranceRun, run_id)
    if run is None:
        raise NotFoundError("Mirror assurance run not found.")
    return run


async def results(
    db: AsyncSession, run_id: str, *, limit: int, offset: int, status: str | None
) -> tuple[int, list[MirrorAssuranceResult]]:
    await get_run(db, run_id)
    where = [MirrorAssuranceResult.run_id == run_id]
    if status:
        where.append(MirrorAssuranceResult.reconciliation_status == status.upper())
    total = await db.scalar(select(func.count()).select_from(MirrorAssuranceResult).where(*where))
    stmt = (
        select(MirrorAssuranceResult)
        .where(*where)
        .order_by(MirrorAssuranceResult.ordinal)
        .limit(limit)
        .offset(offset)
    )
    return int(total or 0), list((await db.execute(stmt)).scalars().all())


async def delete_run(db: AsyncSession, run_id: str) -> None:
    run = await get_run(db, run_id)
    if run.status in ACTIVE:
        raise ConflictError("A running assurance job cannot be deleted.")
    await db.execute(delete(MirrorAssuranceRun).where(MirrorAssuranceRun.id == run_id))


async def _claim_due() -> str | None:
    now = datetime.now(UTC)
    async with SessionFactory() as db:
        schedule = await get_schedule(db, lock=True)
        if not schedule.enabled or not schedule.next_run_at or schedule.next_run_at > now:
            await db.commit()
            return None
        active = await db.scalar(
            select(func.count()).select_from(MirrorAssuranceRun).where(
                MirrorAssuranceRun.status.in_(ACTIVE)
            )
        )
        schedule.next_run_at = now + timedelta(minutes=schedule.interval_minutes)
        if active:
            await db.commit()
            return None
        run = await create_run(
            db,
            window_start=now - timedelta(hours=schedule.window_hours),
            window_end=now,
            tolerance=schedule.tolerance,
            trigger="SCHEDULED",
            actor_id=schedule.updated_by,
            actor_name=schedule.updated_by_name or "Scheduler",
        )
        schedule.last_run_at = now
        await db.commit()
        return run.id


def _missing_control_tables(exc: Exception) -> bool:
    """Recognize Postgres' undefined-table error through SQLAlchemy's wrappers."""
    current: BaseException | None = exc
    while current is not None:
        code = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if code == UNDEFINED_TABLE:
            return True
        current = current.__cause__ or current.__context__
    return False


async def _scheduler_loop() -> None:
    migration_warning_emitted = False
    while True:
        delay = 30
        try:
            run_id = await _claim_due()
            if run_id:
                launch(run_id)
            migration_warning_emitted = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if _missing_control_tables(exc):
                if not migration_warning_emitted:
                    log.warning(
                        "mirror_assurance_scheduler_waiting_for_migration",
                        required_revision="0016",
                        hint="Run `alembic upgrade head`; other APIs remain available.",
                    )
                migration_warning_emitted = True
                delay = 300
            else:
                log.exception("mirror_assurance_scheduler_failed")
        await asyncio.sleep(delay)


_SCHEDULER: asyncio.Task[Any] | None = None


def start_scheduler() -> None:
    global _SCHEDULER
    if _SCHEDULER is None or _SCHEDULER.done():
        _SCHEDULER = asyncio.create_task(_scheduler_loop())


async def stop_scheduler() -> None:
    global _SCHEDULER
    if _SCHEDULER is not None:
        _SCHEDULER.cancel()
        with suppress(asyncio.CancelledError):
            await _SCHEDULER
    _SCHEDULER = None
