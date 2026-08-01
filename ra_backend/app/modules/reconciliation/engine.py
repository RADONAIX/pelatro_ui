"""Execution Engine — creates the output table, runs the reconciliation, records it.

One run is:

    DDL (idempotent) -> indexes -> INSERT ... SELECT -> per-status counts
    -> prune superseded executions -> ANALYZE

The INSERT and the prune share a transaction, so a run either publishes a
complete execution or leaves the previous one untouched. There is no window in
which the report shows half a reconciliation.

The DDL runs in its own transaction first: CREATE TABLE IF NOT EXISTS is
idempotent and wants to survive a failed load, so the next attempt does not
start by rebuilding the table.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.core.config import settings
from app.core.errors import ConflictError, UpstreamUnavailableError
from app.core.logging import get_logger
from app.integrations import pg_engines
from app.modules.reconciliation import cases, repository, sql_builder
from app.modules.reconciliation.plan import SYSTEM_COLUMNS, ReconPlan

log = get_logger("recon.engine")


def _engine(database: str):
    return pg_engines.async_engine(
        pg_engines.async_url(
            settings.ra_pg_host,
            settings.ra_pg_port,
            database,
            settings.ra_pg_user,
            settings.ra_pg_password,
        )
    )


@asynccontextmanager
async def _rule_lock(plan: ReconPlan):
    """Serialise runs of ONE rule across every process.

    The scheduler tick and an operator's Run now can land on the same rule at
    the same moment, and a run may DROP and recreate the output table when the
    rule's shape changed — concurrently with another run inserting into it. A
    Postgres advisory lock keyed on the rule id makes that impossible without a
    lock table of our own, and it is released even if this process dies, which a
    row-flag would not be.

    Different rules do not contend: the key is derived from the rule id.
    """
    engine = _engine(plan.source_database)
    conn = await engine.connect()
    await conn.execution_options(isolation_level="AUTOCOMMIT")
    try:
        acquired = await conn.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:rule_id)::bigint)"),
            {"rule_id": f"recon:{plan.rule_id}"},
        )
        if not acquired.scalar():
            raise ConflictError(
                "This reconciliation is already running.",
                details={"ruleId": plan.rule_id},
            )
        try:
            yield
        finally:
            await conn.execute(
                text("SELECT pg_advisory_unlock(hashtext(:rule_id)::bigint)"),
                {"rule_id": f"recon:{plan.rule_id}"},
            )
    finally:
        await conn.close()


async def ensure_output_table(plan: ReconPlan) -> None:
    """Create the output table and its indexes, rebuilding it if the rule changed.

    CREATE TABLE IF NOT EXISTS alone is not enough. Editing a rule's metrics or
    keys changes the output's shape, and against an existing table that is
    silently wrong in both directions: a removed metric leaves a column nothing
    populates, and an added one makes the INSERT fail on a column that does not
    exist. So the existing shape is compared with the plan's and the table is
    rebuilt when they differ.

    Dropping is safe because every row is derived — the next statement in this
    same run repopulates it from source. Rows from the previous shape are not
    worth migrating: they answer a different comparison than the rule now asks.
    """
    engine = _engine(plan.source_database)
    expected = [*plan.business_columns, *SYSTEM_COLUMNS]

    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{plan.output_schema}"'))

        result = await conn.execute(
            text(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = :schema AND table_name = :table
                ORDER BY ordinal_position
                """
            ),
            {"schema": plan.output_schema, "table": plan.output_table},
        )
        actual = [row[0] for row in result]

        if actual and actual != expected:
            log.info(
                "recon_table_rebuilt",
                rule_id=plan.rule_id,
                table=plan.output_qualified,
                added=sorted(set(expected) - set(actual)),
                removed=sorted(set(actual) - set(expected)),
            )
            await conn.execute(
                text(f'DROP TABLE "{plan.output_schema}"."{plan.output_table}"')
            )

        await conn.execute(text(plan.ddl or sql_builder.build_ddl(plan)))
        for statement in sql_builder.build_indexes(plan):
            await conn.execute(text(statement))

    log.info("recon_table_ready", rule_id=plan.rule_id, table=plan.output_qualified)


async def execute(
    plan: ReconPlan,
    *,
    trigger: str = "manual",
    triggered_by: str = "",
) -> dict[str, Any]:
    """Run the reconciliation once. Returns the execution record.

    Errors are recorded and re-raised: the caller decides whether a failure is
    fatal (a manual run reports it) or tolerable (the scheduler logs and moves
    to the next rule), but the audit row is written either way.
    """
    execution_id = str(uuid.uuid4())
    started = time.perf_counter()
    await repository.start_execution(
        execution_id=execution_id,
        rule_id=plan.rule_id,
        trigger=trigger,
        triggered_by=triggered_by,
    )
    log.info(
        "recon_execution_started",
        rule_id=plan.rule_id,
        execution_id=execution_id,
        trigger=trigger,
        output=plan.output_qualified,
    )

    try:
        async with _rule_lock(plan):
            await ensure_output_table(plan)
            counts = await _load(plan, execution_id)
        duration_ms = int((time.perf_counter() - started) * 1000)
        await repository.finish_execution(
            execution_id=execution_id, counts=counts, duration_ms=duration_ms
        )
        await repository.mark_ready(
            plan.rule_id,
            execution_id=execution_id,
            frequency=plan.frequency,
            execution_time=plan.execution_time,
        )
        log.info(
            "recon_execution_succeeded",
            rule_id=plan.rule_id,
            execution_id=execution_id,
            duration_ms=duration_ms,
            **{k.lower(): v for k, v in counts.items()},
        )

        # After the results are published, never before: a case that points at
        # an execution the report cannot show yet would be a lie. Best-effort —
        # see cases.raise_case_if_breached.
        case = await cases.raise_case_if_breached(
            plan, counts, execution_id=execution_id
        )

        return {
            "executionId": execution_id,
            "status": "Succeeded",
            "durationMs": duration_ms,
            "counts": counts,
            **({"case": case} if case is not None else {}),
        }
    except ConflictError:
        # Another run holds the lock. That is not a failure OF the rule, so the
        # definition keeps its Ready status and its schedule; only this attempt
        # is abandoned.
        await repository.fail_execution(
            execution_id=execution_id,
            error="Skipped: another execution of this rule was already running.",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        log.info("recon_execution_skipped", rule_id=plan.rule_id, execution_id=execution_id)
        raise
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.perf_counter() - started) * 1000)
        message = str(exc)
        await repository.fail_execution(
            execution_id=execution_id, error=message, duration_ms=duration_ms
        )
        await repository.mark_failed(
            plan.rule_id,
            error=message,
            frequency=plan.frequency,
            execution_time=plan.execution_time,
        )
        log.warning(
            "recon_execution_failed",
            rule_id=plan.rule_id,
            execution_id=execution_id,
            duration_ms=duration_ms,
            error=message,
        )
        raise UpstreamUnavailableError(
            "Reconciliation failed.",
            details={"ruleId": plan.rule_id, "executionId": execution_id, "reason": message},
        ) from exc


async def _load(plan: ReconPlan, execution_id: str) -> dict[str, int]:
    """The INSERT, the counts and the prune, in one transaction."""
    engine = _engine(plan.source_database)
    insert_sql = plan.insert_sql or sql_builder.build_insert(plan)

    async with engine.begin() as conn:
        if settings.recon_statement_timeout_seconds > 0:
            # Per-transaction, so a runaway reconciliation cannot pin a
            # connection indefinitely. SET LOCAL reverts on commit.
            await conn.execute(
                text(
                    "SET LOCAL statement_timeout = "
                    f"'{int(settings.recon_statement_timeout_seconds)}s'"
                )
            )

        await conn.execute(
            text(insert_sql),
            {
                "execution_id": execution_id,
                "rule_id": plan.rule_id,
                "execution_time": datetime.now(timezone.utc),
            },
        )

        summary = await conn.execute(
            text(sql_builder.build_summary(plan)), {"execution_id": execution_id}
        )
        counts: dict[str, int] = {row["status"]: int(row["n"]) for row in summary.mappings()}
        counts["total"] = sum(counts.values())

        keep = await repository.retained_execution_ids(
            plan.rule_id, settings.recon_keep_executions
        )
        # The execution being written is not committed yet, so it is not in the
        # retained list read above — add it or the prune deletes what just ran.
        if execution_id not in keep:
            keep.append(execution_id)
        await conn.execute(text(sql_builder.build_prune(plan)), {"keep": keep})

    # Outside the transaction: ANALYZE takes its own locks and is a planner
    # hint, not part of the atomic publish.
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text(f'ANALYZE "{plan.output_schema}"."{plan.output_table}"')
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("recon_analyze_failed", rule_id=plan.rule_id, error=str(exc))

    return counts


async def fetch_results(
    plan: ReconPlan,
    *,
    execution_id: str,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """One page of a reconciliation, plus the total for the same filter.

    Two statements, never per-row queries: the count and the page. Both hit the
    (execution_id, status) index.
    """
    engine = _engine(plan.source_database)
    params: dict[str, Any] = {"execution_id": execution_id}
    if status:
        params["status"] = status

    async with engine.connect() as conn:
        total_result = await conn.execute(
            text(sql_builder.build_results_count(plan, status=status)), params
        )
        total = int(total_result.scalar() or 0)

        page_result = await conn.execute(
            text(sql_builder.build_results_query(plan, status=status)),
            {**params, "limit": limit, "offset": offset},
        )
        rows = [dict(r) for r in page_result.mappings()]

    return {
        "columns": [*plan.business_columns, "status"],
        "rows": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def drop_output_table(plan: ReconPlan) -> None:
    """Used when a reconciliation rule is deleted."""
    engine = _engine(plan.source_database)
    async with engine.begin() as conn:
        await conn.execute(
            text(f'DROP TABLE IF EXISTS "{plan.output_schema}"."{plan.output_table}"')
        )
    log.info("recon_table_dropped", rule_id=plan.rule_id, table=plan.output_qualified)
