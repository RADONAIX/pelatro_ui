"""Metadata Repository — recon_definition and recon_execution.

The only component that knows how compiled plans are stored. The engine, the
scheduler and the report service all go through here, so the storage shape can
change without touching them.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings
from app.core.errors import NotFoundError
from app.integrations import ra_postgres
from app.modules.reconciliation.sequence import SEQUENCE_COLUMNS
from app.modules.reconciliation.plan import (
    KIND_RECONCILIATION,
    Column,
    ColumnPair,
    ReconPlan,
    SequenceOptions,
    TableRef,
)

_DEFINITION_COLUMNS = """
    rule_id, assurance_id, source_database,
    left_schema, left_table, right_schema, right_table,
    join_keys, metrics, tolerance_pct, frequency, severity,
    output_schema, output_table, generated_ddl, generated_sql,
    report_key, report_title, status, last_error,
    last_run_at, next_run_at, last_execution_id, kind, options, created_at, updated_at
"""

# How often each frequency repeats. "Real-time" is deliberately the tightest
# poll the scheduler honours rather than a true stream — see scheduler.py.
_INTERVALS: dict[str, timedelta] = {
    "Real-time": timedelta(minutes=5),
    "Hourly": timedelta(hours=1),
    "Daily": timedelta(days=1),
    "Weekly": timedelta(weeks=1),
    "Cycle": timedelta(days=30),
}


def next_run_after(frequency: str, *, since: datetime | None = None) -> datetime:
    base = since or datetime.now(timezone.utc)
    return base + _INTERVALS.get(frequency, _INTERVALS["Daily"])


async def _query(sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    return await ra_postgres.query_database(settings.app_rules_db_name, sql, params)


async def _execute(sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    return await ra_postgres.execute_database(settings.app_rules_db_name, sql, params)


def _table() -> str:
    return f"{settings.app_rules_schema}.recon_definition"


def _executions() -> str:
    return f"{settings.app_rules_schema}.recon_execution"


# --- definitions ------------------------------------------------------------


async def upsert_definition(plan: ReconPlan) -> dict[str, Any]:
    """Store the compiled plan. Re-compiling a rule (an edit) overwrites its
    definition in place — the rule id is the identity, so an edited rule does
    not leave a second definition or a second report behind."""
    rows = await _execute(
        f"""
        INSERT INTO {_table()} (
            rule_id, assurance_id, source_database,
            left_schema, left_table, right_schema, right_table,
            join_keys, metrics, tolerance_pct, frequency, severity,
            output_schema, output_table, generated_ddl, generated_sql,
            report_key, report_title, status, next_run_at, kind, options
        ) VALUES (
            :rule_id, :assurance_id, :source_database,
            :left_schema, :left_table, :right_schema, :right_table,
            CAST(:join_keys AS jsonb), CAST(:metrics AS jsonb), :tolerance_pct,
            :frequency, :severity,
            :output_schema, :output_table, :generated_ddl, :generated_sql,
            :report_key, :report_title, 'Pending', :next_run_at,
            :kind, CAST(:options AS jsonb)
        )
        ON CONFLICT (rule_id) DO UPDATE SET
            assurance_id = EXCLUDED.assurance_id,
            source_database = EXCLUDED.source_database,
            left_schema = EXCLUDED.left_schema,
            left_table = EXCLUDED.left_table,
            right_schema = EXCLUDED.right_schema,
            right_table = EXCLUDED.right_table,
            join_keys = EXCLUDED.join_keys,
            metrics = EXCLUDED.metrics,
            tolerance_pct = EXCLUDED.tolerance_pct,
            frequency = EXCLUDED.frequency,
            severity = EXCLUDED.severity,
            output_schema = EXCLUDED.output_schema,
            output_table = EXCLUDED.output_table,
            generated_ddl = EXCLUDED.generated_ddl,
            generated_sql = EXCLUDED.generated_sql,
            report_title = EXCLUDED.report_title,
            next_run_at = EXCLUDED.next_run_at,
            kind = EXCLUDED.kind,
            options = EXCLUDED.options
        RETURNING {_DEFINITION_COLUMNS}
        """,
        {
            "rule_id": plan.rule_id,
            "assurance_id": plan.assurance_id,
            "source_database": plan.source_database,
            "left_schema": plan.left.schema,
            "left_table": plan.left.table,
            "right_schema": plan.right.schema,
            "right_table": plan.right.table,
            "join_keys": json.dumps(
                [
                    {
                        "left": p.left.name,
                        "right": p.right.name,
                        "outputLeft": p.output_left,
                        "outputRight": p.output_right,
                        "leftType": p.left.data_type,
                        "rightType": p.right.data_type,
                    }
                    for p in plan.keys
                ]
            ),
            "metrics": json.dumps(
                [
                    {
                        "left": p.left.name,
                        "right": p.right.name,
                        "outputLeft": p.output_left,
                        "outputRight": p.output_right,
                        "leftType": p.left.data_type,
                        "rightType": p.right.data_type,
                    }
                    for p in plan.metrics
                ]
            ),
            "tolerance_pct": plan.tolerance_pct or None,
            "frequency": plan.frequency,
            "severity": plan.severity,
            "output_schema": plan.output_schema,
            "output_table": plan.output_table,
            "generated_ddl": plan.ddl,
            "generated_sql": plan.insert_sql,
            "report_key": plan.report_key,
            "report_title": plan.report_title,
            "next_run_at": next_run_after(plan.frequency),
            "kind": plan.kind,
            "options": json.dumps(plan.sequence.as_dict() if plan.sequence else {}),
        },
    )
    return rows[0]


async def get_definition(rule_id: str) -> dict[str, Any]:
    rows = await _query(
        f"SELECT {_DEFINITION_COLUMNS} FROM {_table()} WHERE rule_id = :rule_id",
        {"rule_id": rule_id},
    )
    if not rows:
        raise NotFoundError(f"No reconciliation is defined for rule {rule_id}.")
    return rows[0]


async def get_by_report_key(report_key: str) -> dict[str, Any]:
    rows = await _query(
        f"SELECT {_DEFINITION_COLUMNS} FROM {_table()} WHERE report_key = :report_key",
        {"report_key": report_key},
    )
    if not rows:
        raise NotFoundError(f"No reconciliation report named {report_key}.")
    return rows[0]


async def list_definitions(*, assurance_id: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE assurance_id = :assurance_id" if assurance_id else ""
    return await _query(
        f"SELECT {_DEFINITION_COLUMNS} FROM {_table()} {where} ORDER BY created_at DESC",
        {"assurance_id": assurance_id} if assurance_id else None,
    )


async def list_due(*, limit: int = 20) -> list[dict[str, Any]]:
    """Rules whose next run is in the past. Ready only — a definition that has
    never compiled cleanly is not something the scheduler should retry blindly."""
    return await _query(
        f"""
        SELECT {_DEFINITION_COLUMNS} FROM {_table()}
        WHERE status = 'Ready' AND next_run_at IS NOT NULL AND next_run_at <= now()
        ORDER BY next_run_at
        LIMIT :limit
        """,
        {"limit": limit},
    )


async def mark_ready(rule_id: str, *, execution_id: str, frequency: str) -> None:
    await _execute(
        f"""
        UPDATE {_table()}
        SET status = 'Ready', last_error = NULL, last_run_at = now(),
            last_execution_id = CAST(:execution_id AS uuid), next_run_at = :next_run_at
        WHERE rule_id = :rule_id
        """,
        {
            "rule_id": rule_id,
            "execution_id": execution_id,
            "next_run_at": next_run_after(frequency),
        },
    )


async def mark_failed(rule_id: str, *, error: str, frequency: str) -> None:
    """A failed run still advances next_run_at: leaving it in the past would
    make the scheduler spin on the same broken rule every tick."""
    await _execute(
        f"""
        UPDATE {_table()}
        SET status = 'Failed', last_error = :error, last_run_at = now(),
            next_run_at = :next_run_at
        WHERE rule_id = :rule_id
        """,
        {
            "rule_id": rule_id,
            "error": error[:4000],
            "next_run_at": next_run_after(frequency),
        },
    )


async def delete_definition(rule_id: str) -> None:
    await _execute(f"DELETE FROM {_table()} WHERE rule_id = :rule_id", {"rule_id": rule_id})


# --- executions -------------------------------------------------------------


async def start_execution(
    *, execution_id: str, rule_id: str, trigger: str, triggered_by: str
) -> None:
    await _execute(
        f"""
        INSERT INTO {_executions()} (execution_id, rule_id, trigger_source, status, triggered_by)
        VALUES (CAST(:execution_id AS uuid), :rule_id, :trigger, 'Running', :triggered_by)
        """,
        {
            "execution_id": execution_id,
            "rule_id": rule_id,
            "trigger": trigger,
            "triggered_by": triggered_by,
        },
    )


async def finish_execution(
    *, execution_id: str, counts: dict[str, int], duration_ms: int
) -> None:
    await _execute(
        f"""
        UPDATE {_executions()}
        SET status = 'Succeeded', ended_at = now(), duration_ms = :duration_ms,
            rows_total = :total, rows_match = :match, rows_mismatch = :mismatch,
            rows_raw_missing = :raw_missing, rows_processed_missing = :processed_missing
        WHERE execution_id = CAST(:execution_id AS uuid)
        """,
        {
            "execution_id": execution_id,
            "duration_ms": duration_ms,
            "total": counts.get("total", 0),
            "match": counts.get("MATCH", 0),
            "mismatch": counts.get("MISMATCH", 0),
            "raw_missing": counts.get("RAW_MISSING", 0),
            "processed_missing": counts.get("PROCESSED_MISSING", 0),
        },
    )


async def fail_execution(*, execution_id: str, error: str, duration_ms: int) -> None:
    await _execute(
        f"""
        UPDATE {_executions()}
        SET status = 'Failed', ended_at = now(), duration_ms = :duration_ms, error = :error
        WHERE execution_id = CAST(:execution_id AS uuid)
        """,
        {"execution_id": execution_id, "error": error[:4000], "duration_ms": duration_ms},
    )


async def list_executions(rule_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    return await _query(
        f"""
        SELECT execution_id, rule_id, trigger_source, status, started_at, ended_at,
               duration_ms, rows_total, rows_match, rows_mismatch,
               rows_raw_missing, rows_processed_missing, error, triggered_by
        FROM {_executions()}
        WHERE rule_id = :rule_id
        ORDER BY started_at DESC
        LIMIT :limit
        """,
        {"rule_id": rule_id, "limit": limit},
    )


async def latest_succeeded_execution(rule_id: str) -> dict[str, Any] | None:
    rows = await _query(
        f"""
        SELECT execution_id, started_at, rows_total
        FROM {_executions()}
        WHERE rule_id = :rule_id AND status = 'Succeeded'
        ORDER BY started_at DESC
        LIMIT 1
        """,
        {"rule_id": rule_id},
    )
    return rows[0] if rows else None


async def retained_execution_ids(rule_id: str, keep: int) -> list[str]:
    rows = await _query(
        f"""
        SELECT execution_id FROM {_executions()}
        WHERE rule_id = :rule_id AND status = 'Succeeded'
        ORDER BY started_at DESC
        LIMIT :keep
        """,
        {"rule_id": rule_id, "keep": keep},
    )
    return [str(r["execution_id"]) for r in rows]


def definition_to_plan(row: dict[str, Any]) -> ReconPlan:
    """Rehydrate a plan from its stored definition.

    The scheduler runs off this: nothing is recompiled, so a scheduled run
    cannot drift from what was reviewed at creation. Column types come back
    from the stored metadata rather than a fresh catalog read, which also means
    a source column dropped underneath us surfaces as an execution error rather
    than a silently different plan.
    """

    def pairs(raw: list[dict[str, Any]]) -> list[ColumnPair]:
        return [
            ColumnPair(
                left=Column(
                    name=p["left"],
                    data_type=p.get("leftType", "text"),
                    numeric=_looks_numeric(p.get("leftType", "text")),
                ),
                right=Column(
                    name=p["right"],
                    data_type=p.get("rightType", "text"),
                    numeric=_looks_numeric(p.get("rightType", "text")),
                ),
                output_left=p.get("outputLeft") or p["left"],
                output_right=p.get("outputRight") or p["right"],
            )
            for p in raw
        ]

    keys = pairs(row["join_keys"])
    metrics = pairs(row["metrics"])

    # A single-table rule carries no pairs; its output shape is fixed and its
    # parameters live in `options`.
    kind = row.get("kind") or KIND_RECONCILIATION
    options = row.get("options") or {}
    sequence_options = (
        SequenceOptions.from_dict(options)
        if kind != KIND_RECONCILIATION and options.get("column")
        else None
    )
    if sequence_options is not None:
        business = list(SEQUENCE_COLUMNS)
    else:
        business = [c for p in keys for c in (p.output_left, p.output_right)]
        business += [c for p in metrics for c in (p.output_left, p.output_right)]

    return ReconPlan(
        rule_id=row["rule_id"],
        assurance_id=row["assurance_id"],
        rule_name=row["report_title"],
        left=TableRef(row["source_database"], row["left_schema"], row["left_table"]),
        right=TableRef(row["source_database"], row["right_schema"], row["right_table"]),
        keys=keys,
        metrics=metrics,
        kind=kind,
        sequence=sequence_options,
        tolerance_pct=float(row["tolerance_pct"] or 0),
        output_schema=row["output_schema"],
        output_table=row["output_table"],
        report_key=row["report_key"],
        report_title=row["report_title"],
        frequency=row["frequency"],
        severity=row["severity"],
        ddl=row["generated_ddl"],
        insert_sql=row["generated_sql"],
        business_columns=business,
    )


def _looks_numeric(data_type: str) -> bool:
    base = data_type.split("(")[0].strip().lower()
    return base in {
        "smallint", "integer", "bigint", "decimal", "numeric", "real",
        "double precision", "money",
    }
