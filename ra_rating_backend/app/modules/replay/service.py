"""Execute a replay: rewind, re-rate, compare.

The sequence matters:

1. **Rewind** the original run's bundle consumption. Rating is stateful now —
   re-rating the same CDRs without rewinding would draw every call from the
   allowance a second time and manufacture leakage that only the replay
   created.
2. **Re-rate** the same batch as a ``REPLAY``-type run against the chosen
   snapshot (usually one containing the fix under test).
3. **Compare** and store the delta once, while both runs are intact. The
   number that matters is ``recovered_amount``: original undercharge minus
   replay undercharge, kept signed so a fix that made things worse shows as
   negative instead of being clamped into silence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.modules.balances.engine import rewind_run
from app.modules.rating import service as rating_svc
from app.modules.rating.constants import ExceptionStatus, RunStatus
from app.modules.rating.models import RatingException, RatingRun
from app.modules.replay.models import ReplayRun

log = get_logger("replay")


def _run_summary(run: RatingRun) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "snapshot_version": run.snapshot_version,
        "rated_cdrs": run.rated_cdrs,
        "matched": run.matched_count,
        "expected_revenue": float(run.expected_revenue or 0),
        "billed_revenue": float(run.billed_revenue or 0),
        "undercharge": float(run.undercharge_total or 0),
        "overcharge": float(run.overcharge_total or 0),
        "exceptions": run.exception_count,
        "by_status": (run.stats or {}).get("by_status", {}),
    }


async def execute_replay(
    db: AsyncSession,
    *,
    source_run_id: str,
    snapshot_id: str | None,
    exception_id: str | None,
    actor_id: str,
    actor_name: str,
) -> ReplayRun:
    original = await db.get(RatingRun, source_run_id)
    if original is None:
        raise NotFoundError(f"Rating run '{source_run_id}' was not found.")
    if original.status != RunStatus.COMPLETED.value:
        raise ValidationFailedError(
            "Only a completed run can be replayed.",
            details={"status": original.status},
        )

    replay = ReplayRun(
        source_run_id=original.id,
        snapshot_id=snapshot_id or original.snapshot_id,
        exception_id=exception_id,
        triggered_by=actor_id,
        triggered_by_name=actor_name,
    )
    db.add(replay)
    await db.flush()

    try:
        # 1. Undo the original run's stateful footprint.
        rewound = await rewind_run(db, original.id)

        # 2. Re-rate the same batch against the fix.
        new_run = await rating_svc.start_run(
            db,
            batch_id=original.batch_id,
            actor_id=actor_id,
            actor_name=actor_name,
            snapshot_id=snapshot_id,
            run_type="REPLAY",
            replay_of_run_id=original.id,
        )
        new_run = await rating_svc.execute_run(db, new_run)

        # 3. The delta, stored while both runs are intact.
        recovered = float(original.undercharge_total or 0) - float(
            new_run.undercharge_total or 0
        )
        overcharge_delta = float(original.overcharge_total or 0) - float(
            new_run.overcharge_total or 0
        )
        replay.new_run_id = new_run.id
        replay.snapshot_id = new_run.snapshot_id
        replay.snapshot_version = new_run.snapshot_version
        replay.recovered_amount = recovered
        replay.status = (
            "COMPLETED" if new_run.status == RunStatus.COMPLETED.value else "FAILED"
        )
        replay.error = new_run.error
        replay.comparison = {
            "before": _run_summary(original),
            "after": _run_summary(new_run),
            "recovered_undercharge": round(recovered, 6),
            "overcharge_reduction": round(overcharge_delta, 6),
            "exceptions_before": original.exception_count,
            "exceptions_after": new_run.exception_count,
            "ledger_entries_rewound": rewound,
        }
        replay.finished_at = datetime.now(UTC)

        # 4. If launched from an exception, record the recovery on the case
        #    and move it to REPROCESSED where its lifecycle allows.
        if exception_id:
            case = await db.get(RatingException, exception_id)
            if case is not None:
                case.recovered_amount = max(0.0, recovered)
                if (
                    replay.status == "COMPLETED"
                    and case.status == ExceptionStatus.RESOLVED.value
                ):
                    case.status = ExceptionStatus.REPROCESSED.value

        await db.flush()
        log.info(
            "replay_completed",
            source_run=original.id,
            new_run=new_run.id,
            recovered=recovered,
        )
        return replay
    except Exception as exc:
        replay.status = "FAILED"
        replay.error = str(exc)
        replay.finished_at = datetime.now(UTC)
        await db.flush()
        raise


async def list_replays(db: AsyncSession, limit: int, offset: int) -> list[ReplayRun]:
    rows = (
        await db.execute(
            select(ReplayRun)
            .order_by(ReplayRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return list(rows)


async def recovery_total(db: AsyncSession) -> dict[str, Any]:
    row = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(ReplayRun.recovered_amount), 0),
            ).where(ReplayRun.status == "COMPLETED")
        )
    ).one()
    return {"completed_replays": int(row[0]), "total_recovered": float(row[1] or 0)}
