"""Recurring execution.

The loop reads due rules from recon_definition and re-runs their STORED SQL —
nothing is recompiled, so a scheduled run is the same statement that was
reviewed when the rule was created.

Failure of one rule never stops the loop or the tick: each rule is executed
inside its own try, its error lands on its own execution record, and
next_run_at is advanced so a permanently broken rule is retried on its normal
cadence instead of every tick.
"""

from __future__ import annotations

import asyncio

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.reconciliation import repository, service

log = get_logger("recon.scheduler")


async def run_due_once() -> int:
    """One tick. Returns how many rules ran. Safe to call directly in a test."""
    due = await repository.list_due(limit=settings.recon_scheduler_batch)
    if not due:
        return 0

    log.info("recon_scheduler_tick", due=len(due))
    ran = 0
    for definition in due:
        rule_id = definition["rule_id"]
        try:
            await service.run_stored(rule_id, trigger="schedule", triggered_by="scheduler")
            ran += 1
        except Exception as exc:  # noqa: BLE001
            # Already recorded on the execution row and the definition by the
            # engine; logged here so a scheduled failure is visible in the app
            # log too, then on to the next rule.
            log.warning("recon_scheduled_run_failed", rule_id=rule_id, error=str(exc))
    return ran


async def scheduler_loop() -> None:
    """Long-running task started from the app lifespan."""
    interval = max(10, settings.recon_scheduler_interval_seconds)
    log.info("recon_scheduler_started", interval_seconds=interval)
    while True:
        try:
            await run_due_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # The loop itself must never die — a bad tick is logged and the next
            # one happens on schedule.
            log.warning("recon_scheduler_tick_failed", error=str(exc))
        await asyncio.sleep(interval)
