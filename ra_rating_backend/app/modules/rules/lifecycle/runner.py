"""Executing a bulk lifecycle run outside the request that asked for it.

The runner deliberately contains no lifecycle logic. It resolves the selector,
calls the same ``service.validate`` / ``approve`` / ``revert`` / ``activate`` the
synchronous endpoints call, and writes the result to the run row. If the two
paths could disagree about what "approve" means, the job would be a second
implementation of the most consequential operation in the product — and the one
nobody watches while it runs.

Two things are worth stating about the failure modes:

**A crashed run must not look like a slow one.** The row carries a heartbeat,
written when the run starts and when it finishes. A RUNNING row whose heartbeat
is old belongs to a process that is gone, and `stale()` says so rather than
leaving an operator to guess.

**A refusal is not an error.** ``ValidationFailedError`` means the operation ran
and declined — unapproved rules, a validation wall — and the blocked report says
what to fix. That lands as REJECTED. Anything else is our bug and lands as ERROR,
with the message kept, because a job that fails silently at 3am is a job that
gets discovered by a billing dispute.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionFactory
from app.core.errors import AppError, ValidationFailedError
from app.core.logging import get_logger
from app.modules.rules.ingest import resolver
from app.modules.rules.lifecycle import service as svc
from app.modules.rules.lifecycle.models import BulkRunStatus, RuleBulkRun
from app.modules.rules.lifecycle.selectors import Selector, resolve
from app.modules.tenancy.context import scoped

logger = get_logger(__name__)

#: A RUNNING row untouched for longer than this belongs to a dead process.
#: Generous, because a genuine 50,000-rule approval is slow and calling it dead
#: while it works would be worse than waiting.
STALE_AFTER = timedelta(minutes=30)

OPERATIONS: tuple[str, ...] = (
    "validate", "approve", "revert", "activate", "delete",
)

_BACKGROUND: set[asyncio.Task] = set()


#: The session factory jobs run against. A module-level indirection rather than a
#: direct import so a test can bind a run to its own connection — the default
#: factory holds the application engine, which belongs to the application's event
#: loop, and a job driven from anywhere else would talk to a closed one.
def _default_factory():
    return SessionFactory()


def launch(run_id: str) -> None:
    """Start the run in the background, holding the task reference.

    Without the reference the event loop may collect the task mid-run, which
    leaves the row RUNNING forever with no process behind it — the one failure
    mode this module is built to make impossible.
    """
    task = asyncio.create_task(execute(run_id))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


def stale(run: RuleBulkRun, *, now: datetime | None = None) -> bool:
    """Whether a RUNNING row's process is gone."""
    if run.status != BulkRunStatus.RUNNING:
        return False
    beat = run.heartbeat_at or run.started_at
    if beat is None:
        return True
    if beat.tzinfo is None:
        beat = beat.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - beat > STALE_AFTER


async def execute(run_id: str, *, factory=None) -> None:
    """Run one job, in its own session.

    Its own session because the request that queued it has already committed and
    gone. Its own transaction because a job that half-commits is the thing the
    atomic flag exists to prevent, and the flag is only honoured if the whole
    operation shares one transaction.
    """
    async with (factory or _default_factory)() as db:
        run = await db.get(RuleBulkRun, run_id)
        if run is None:
            logger.error("bulk_run.missing", run_id=run_id)
            return
        if run.status != BulkRunStatus.PENDING:
            # Already claimed — a duplicate launch, or a restart racing itself.
            return

        tenant_id = run.tenant_id
        run.status = BulkRunStatus.RUNNING
        run.started_at = run.heartbeat_at = datetime.now(UTC)
        await db.commit()

        try:
            async with scoped(db, tenant_id):
                result = await _run(db, run, tenant_id)
            _record(run, result)
            await db.commit()
            logger.info(
                "bulk_run.finished", run_id=run_id, operation=run.operation,
                status=run.status, applied=run.applied, total=run.total,
            )
        except ValidationFailedError as exc:
            await db.rollback()
            await _fail(db, run_id, BulkRunStatus.REJECTED, exc)
        except AppError as exc:
            await db.rollback()
            await _fail(db, run_id, BulkRunStatus.REJECTED, exc)
        except Exception as exc:  # the row must record everything
            await db.rollback()
            logger.exception("bulk_run.error", run_id=run_id)
            await _fail(db, run_id, BulkRunStatus.ERROR, exc)


async def _run(
    db: AsyncSession, run: RuleBulkRun, tenant_id: str
) -> svc.BulkResult:
    selector = Selector(
        batch_id=run.selector.get("batch_id"),
        rule_set_id=run.selector.get("rule_set_id"),
        filters=run.selector.get("filters"),
        rule_keys=(
            tuple(run.selector["rule_keys"])
            if run.selector.get("rule_keys")
            else None
        ),
    )
    rules = await resolve(db, selector, tenant_id=tenant_id)

    # The count is knowable now and the operation may take minutes, so publish it
    # before starting rather than after: a progress bar that reads 0/0 until it
    # reads 5000/5000 is not a progress bar.
    run.total = len(rules)
    run.heartbeat_at = datetime.now(UTC)
    await db.flush()

    described = selector.describe()
    if run.operation == "validate":
        cache = await resolver.build(db, tenant_id, with_rule_index=False)
        return await svc.validate(
            db, rules, cache, selector=described, tenant_id=tenant_id,
            dry_run=run.dry_run,
        )
    if run.operation == "approve":
        return await svc.approve(
            db, rules, selector=described, actor_id=run.actor_id or "",
            actor_name=run.actor_name or "", comment=run.comment,
            atomic=run.atomic, enforce_maker_checker=await _maker_checker(db, tenant_id),
            dry_run=run.dry_run,
        )
    if run.operation == "revert":
        return await svc.revert(
            db, rules, selector=described, actor_id=run.actor_id or "",
            actor_name=run.actor_name or "", comment=run.comment,
            dry_run=run.dry_run,
        )
    if run.operation == "delete":
        return await svc.delete(
            db, rules, selector=described, actor_id=run.actor_id or "",
            actor_name=run.actor_name or "", comment=run.comment,
            atomic=run.atomic, dry_run=run.dry_run,
        )
    if run.operation == "activate":
        return await svc.activate(
            db, rules, selector=described,
            rule_set_id=await _rule_set_for(db, selector, rules),
            actor_id=run.actor_id or "", actor_name=run.actor_name or "",
            comment=run.comment, force=run.force, dry_run=run.dry_run,
        )
    raise ValidationFailedError(
        f"'{run.operation}' is not a bulk operation.",
        details={"allowed": list(OPERATIONS)},
    )


async def _rule_set_for(
    db: AsyncSession, selector: Selector, rules: list[Any]
) -> str | None:
    """Which rule set a snapshot would be compiled from.

    Explicit when the caller selected one. Otherwise derived from what the
    selection actually matched, and only if that is unambiguous — compiling a
    selection that spans two rule sets would quietly snapshot one of them.
    """
    if selector.rule_set_id:
        return selector.rule_set_id

    from app.modules.rules.canonical.sets import RuleSetMember

    ids = {r.rule_id for r in rules}
    if not ids:
        return None
    found = set(
        (
            await db.execute(
                select(RuleSetMember.rule_set_id).where(
                    RuleSetMember.rule_id.in_(ids)
                )
            )
        )
        .scalars()
        .all()
    )
    if len(found) == 1:
        return found.pop()
    if not found:
        raise ValidationFailedError(
            "These rules do not belong to a rule set, so there is nothing to "
            "compile into a snapshot.",
            details={"hint": "Import created a rule set, or add them to one first."},
        )
    raise ValidationFailedError(
        f"This selection spans {len(found)} rule sets; a snapshot is compiled "
        f"from one.",
        details={"rule_set_ids": sorted(found), "hint": "Select by rule_set_id."},
    )


def _record(run: RuleBulkRun, result: svc.BulkResult) -> None:
    run.status = BulkRunStatus.SUCCEEDED
    run.processed = len(result.outcomes)
    run.applied = result.applied
    run.counts = result.counts
    run.blocked = result.blocked()
    run.snapshot = result.snapshot
    run.touched_live_pricing = result.touched_live_pricing
    run.finished_at = run.heartbeat_at = datetime.now(UTC)


async def _fail(
    db: AsyncSession, run_id: str, status: str, exc: Exception
) -> None:
    """Record the failure in a fresh transaction.

    Fresh because the one the operation used has been rolled back, and writing
    the reason for a failure into the transaction that failed would roll the
    explanation back along with it.
    """
    run = await db.get(RuleBulkRun, run_id)
    if run is None:
        return
    run.status = status
    run.error = str(exc)
    details = getattr(exc, "details", None)
    run.error_details = details if isinstance(details, dict) else None
    run.finished_at = run.heartbeat_at = datetime.now(UTC)
    await db.commit()


async def _maker_checker(db: AsyncSession, tenant_id: str) -> bool:
    from app.modules.tenancy.models import Tenant

    tenant = await db.get(Tenant, tenant_id)
    return bool((getattr(tenant, "settings", None) or {}).get(
        "enforce_maker_checker", True
    ))
