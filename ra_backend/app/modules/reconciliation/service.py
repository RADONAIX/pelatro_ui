"""Orchestration: compile -> persist -> create table -> execute -> publish report.

This is the seam the rest of the app calls. The Rule Explorer's create/update
path calls ``compile_and_run``; the scheduler calls ``run_stored``; the report
service calls ``list_reports`` and ``read_report``.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.modules.reconciliation import compiler, engine, repository, row_rules, sql_builder
from app.modules.reconciliation.plan import (
    ALL_STATUSES,
    DUPLICATE_STATUSES,
    KIND_DUPLICATE,
    KIND_RECONCILIATION,
    KIND_SEQUENCE,
    KIND_THRESHOLD,
    ROW_KINDS,
    SEQUENCE_STATUSES,
    THRESHOLD_STATUSES,
)

log = get_logger("recon.service")

#: Rule categories that compile to something executable, and the generator each
#: one selects. Any other category is stored by the Rule Explorer and ignored
#: here — it has no compiled form yet.
COMPILED_CATEGORIES: dict[str, str] = {
    "reconciliation": KIND_RECONCILIATION,
    "sequence": KIND_SEQUENCE,
    "duplicate": KIND_DUPLICATE,
    "threshold": KIND_THRESHOLD,
}


def kind_for(category: str | None) -> str | None:
    return COMPILED_CATEGORIES.get((category or "").strip().lower())


def is_reconciliation(category: str | None) -> bool:
    """True when the category compiles to an executable rule of any kind.

    Kept under its old name because the Rule Explorer calls it to decide whether
    to compile at all, and that question is now "is this compilable", not "is
    this specifically a reconciliation".
    """
    return kind_for(category) is not None


async def compile_and_run(
    *,
    rule: dict[str, Any],
    trigger: str = "create",
    triggered_by: str = "",
    execute_now: bool = True,
) -> dict[str, Any]:
    """Turn a saved rule into a live reconciliation.

    Compilation and the first run are separate outcomes on purpose. A rule that
    compiles but whose first load fails is still a valid definition: it keeps
    its report, its metadata and its schedule, and the failure is on the
    execution record where an operator can see it. Only a compile error means
    there is nothing to schedule.
    """
    kind = kind_for(rule.get("category")) or KIND_RECONCILIATION
    common = {
        "rule_id": rule["id"],
        "assurance_id": rule["appId"],
        "rule_name": rule["name"],
        "frequency": rule.get("frequency") or "Daily",
        "severity": rule.get("severity") or "medium",
        # Carried onto the definition so the scheduler knows WHEN to run and a
        # finished run knows whether to raise a case, without reading back the
        # authored rule.
        "execution_time": rule.get("executionTime") or "00:00",
        "case_routing": rule.get("caseRouting"),
    }
    if kind == KIND_RECONCILIATION:
        plan = await compiler.compile_rule(
            comparison=rule.get("comparison") or {},
            params=rule.get("params") or {},
            **common,
        )
    elif kind in ROW_KINDS:
        plan = await compiler.compile_row_rule(
            kind=kind, params=rule.get("params") or {}, **common
        )
    else:
        plan = await compiler.compile_sequence_rule(
            kind=kind, params=rule.get("params") or {}, **common
        )

    # Generate once and store what was generated, so the scheduler re-runs
    # byte-identical SQL and an operator can read exactly what executed.
    plan = _with_sql(plan)
    await repository.upsert_definition(plan)

    if not execute_now:
        return {
            "ruleId": plan.rule_id,
            "reportKey": plan.report_key,
            "executed": False,
            "reason": "Rule is a draft — it runs when you set it Active.",
        }

    result = await engine.execute(plan, trigger=trigger, triggered_by=triggered_by)
    return {
        "ruleId": plan.rule_id,
        "reportKey": plan.report_key,
        "reportTitle": plan.report_title,
        "outputTable": plan.output_qualified,
        "executed": True,
        **result,
    }


def _with_sql(plan):
    from dataclasses import replace

    return replace(
        plan,
        ddl=sql_builder.build_ddl(plan),
        insert_sql=sql_builder.build_insert(plan),
    )


async def run_stored(rule_id: str, *, trigger: str, triggered_by: str = "") -> dict[str, Any]:
    """Re-run an already-compiled rule from its stored metadata."""
    definition = await repository.get_definition(rule_id)
    plan = repository.definition_to_plan(definition)
    return await engine.execute(plan, trigger=trigger, triggered_by=triggered_by)


async def remove(rule_id: str, *, drop_table: bool = True) -> None:
    """Forget a reconciliation when its rule is deleted.

    The definition row cascades from assurance_rule, but the generated table is
    in another database and has no foreign key to cascade through — it has to be
    dropped explicitly or it is orphaned for good.
    """
    try:
        definition = await repository.get_definition(rule_id)
    except NotFoundError:
        return
    if drop_table:
        plan = repository.definition_to_plan(definition)
        try:
            await engine.drop_output_table(plan)
        except Exception as exc:  # noqa: BLE001
            # A rule must still be deletable when its output table is already
            # gone or unreachable.
            log.warning("recon_drop_failed", rule_id=rule_id, error=str(exc))
    await repository.delete_definition(rule_id)


# --- report service ---------------------------------------------------------


def _describe(definition: dict[str, Any]) -> str:
    """What this report is, in the terms of its own kind — a sequence check is
    not "reconciles A against B", and saying so would misread the screen."""
    kind = definition.get("kind") or KIND_RECONCILIATION
    rule_id = definition["rule_id"]
    left = f"{definition['left_schema']}.{definition['left_table']}"

    if kind == KIND_RECONCILIATION:
        right = f"{definition['right_schema']}.{definition['right_table']}"
        return (
            f"Reconciles {left} against {right} on {len(definition['join_keys'])} key(s), "
            f"comparing {len(definition['metrics'])} metric(s). Generated from rule {rule_id}."
        )

    options = definition.get("options") or {}
    column = options.get("column", "?")
    within = (
        f"within {options['partitionColumn']}"
        if options.get("partitionColumn")
        else "within each series"
    )
    if kind == KIND_DUPLICATE:
        return (
            f"Flags repeated values of {left}.{column} {within}. "
            f"Generated from rule {rule_id}."
        )
    return (
        f"Checks the sequence carried by {left}.{column} {within} and flags gaps "
        f"and duplicates. Generated from rule {rule_id}."
    )


def _report_descriptor(definition: dict[str, Any]) -> dict[str, Any]:
    """One entry in the Reports menu. Shaped like the static catalog's entries
    so the UI renders both through the same component."""
    return {
        "key": definition["report_key"],
        "ruleId": definition["rule_id"],
        "title": definition["report_title"],
        "group": "Reconciliation",
        "assurance": definition["assurance_id"],
        "available": definition["status"] == "Ready",
        "status": definition["status"],
        "kind": definition.get("kind") or KIND_RECONCILIATION,
        "description": _describe(definition),
        "outputTable": f"{definition['output_schema']}.{definition['output_table']}",
        "frequency": definition["frequency"],
        "lastRunAt": definition["last_run_at"],
        "nextRunAt": definition["next_run_at"],
        "lastError": definition["last_error"],
    }


async def list_reports(*, assurance_id: str | None = None) -> list[dict[str, Any]]:
    """Every reconciliation report, newest first. This is what makes the Reports
    menu dynamic: one entry per compiled rule, no hardcoded list."""
    definitions = await repository.list_definitions(assurance_id=assurance_id)
    return [_report_descriptor(d) for d in definitions]


async def read_report(
    report_key: str,
    *,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """A page of the latest successful execution.

    Reading the latest *succeeded* execution rather than the latest attempt is
    what keeps a failed overnight run from blanking a report that has perfectly
    good data from yesterday.
    """
    definition = await repository.get_by_report_key(report_key)
    plan = repository.definition_to_plan(definition)
    kind = definition.get("kind") or KIND_RECONCILIATION

    # The statuses THIS kind can produce. Validating against the reconciliation
    # four regardless silently dropped the filter for a sequence rule, so
    # ?status=GAP returned every row instead of the gaps.
    allowed = {
        KIND_RECONCILIATION: ALL_STATUSES,
        KIND_DUPLICATE: DUPLICATE_STATUSES,
        # A threshold rule labels its rows with its own name, so the filter has
        # to offer that label rather than the generic constant.
        KIND_THRESHOLD: (row_rules.threshold_status(plan).replace("''", "'"),),
    }.get(kind, SEQUENCE_STATUSES)

    latest = await repository.latest_succeeded_execution(definition["rule_id"])
    if latest is None:
        return {
            "key": report_key,
            "title": definition["report_title"],
            "ruleId": definition["rule_id"],
            "kind": kind,
            "statuses": list(allowed),
            "columns": [*plan.business_columns, "status"],
            "rows": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "executionId": None,
            "executedAt": None,
            "note": definition["last_error"]
            or "This rule has not completed a run yet.",
        }

    if status and status not in allowed:
        raise ValidationFailedError(
            f"Unknown status '{status}' for this report.",
            details={"allowed": list(allowed)},
        )

    page = await engine.fetch_results(
        plan,
        execution_id=str(latest["execution_id"]),
        status=status,
        limit=limit,
        offset=offset,
    )
    return {
        "key": report_key,
        "title": definition["report_title"],
        "ruleId": definition["rule_id"],
        "kind": kind,
        # The status vocabulary differs by kind, and the view renders filter
        # chips from it — so it is served with the page rather than assumed.
        "statuses": list(allowed),
        "executionId": str(latest["execution_id"]),
        "executedAt": latest["started_at"],
        "statusFilter": status,
        **page,
    }


async def report_summary(report_key: str) -> dict[str, Any]:
    """Headline counts for the latest successful execution."""
    definition = await repository.get_by_report_key(report_key)
    executions = await repository.list_executions(definition["rule_id"], limit=1)
    latest = executions[0] if executions else None
    return {
        "key": report_key,
        "ruleId": definition["rule_id"],
        "title": definition["report_title"],
        "status": definition["status"],
        "lastRunAt": definition["last_run_at"],
        "nextRunAt": definition["next_run_at"],
        "outputTable": f"{definition['output_schema']}.{definition['output_table']}",
        "generatedSql": definition["generated_sql"],
        "latestExecution": latest,
    }


def output_schema_default() -> str:
    return settings.recon_output_schema


# --- execution-centric reports ----------------------------------------------


async def read_execution_report(
    execution_id: str, *, limit: int = 100, offset: int = 0
) -> dict[str, Any]:
    """One execution's summary plus a page of its rows.

    Rows come from the inlined JSONB when the report was small enough to store
    that way, and straight from the rule's generated table otherwise — the
    caller cannot tell the difference, which is the point.
    """
    report = await repository.get_report(execution_id)
    definition = await repository.get_definition(report["rule_id"])
    plan = repository.definition_to_plan(definition)

    summary = {
        "executionId": str(report["execution_id"]),
        "reportId": report["id"],
        "ruleId": report["rule_id"],
        "ruleName": definition["report_title"],
        "kind": definition.get("kind") or KIND_RECONCILIATION,
        "status": report["status"],
        "executionStart": report["started_at"],
        "executionEnd": report["ended_at"],
        "durationMs": report["duration_ms"],
        "rowsScanned": report["rows_scanned"],
        "rowsReturned": report["rows_returned"],
        "reportCount": report["report_count"],
        "caseCreated": bool(report["case_raised"]),
        "caseReference": report["case_reference"],
        "triggeredBy": report["triggered_by"],
        "trigger": report["trigger_source"],
        "outputTable": report["output_table"],
        "columns": report["columns"],
    }
    if limit == 0:
        return {**summary, "rows": [], "total": report["report_count"]}

    inline = report["report_json"]
    if inline is not None:
        return {
            **summary,
            "rows": inline[offset : offset + limit],
            "total": len(inline),
        }

    page = await engine.fetch_results(
        plan, execution_id=execution_id, limit=limit, offset=offset
    )
    return {**summary, "rows": page["rows"], "total": page["total"]}


async def stream_report_csv(execution_id: str, *, with_bom: bool = False):
    """The whole report as CSV, yielded in chunks.

    Never materialises the report: rows arrive from the database a page at a
    time and each page is turned into CSV text and released. Memory is flat
    whatever the row count.
    """
    report = await repository.get_report(execution_id)
    definition = await repository.get_definition(report["rule_id"])
    plan = repository.definition_to_plan(definition)
    columns: list[str] = list(report["columns"] or [])

    def to_row(values: list[Any]) -> str:
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\n").writerow(values)
        return buffer.getvalue()

    if with_bom:
        # Excel assumes the system codepage without it, and mangles anything
        # non-ASCII in a filename or a node id.
        yield "﻿"
    yield to_row(columns)

    inline = report["report_json"]
    if inline is not None:
        for row in inline:
            yield to_row([row.get(c) for c in columns])
        return

    offset = 0
    page_size = settings.recon_download_page_rows
    while True:
        page = await engine.fetch_results(
            plan, execution_id=execution_id, limit=page_size, offset=offset
        )
        rows = page["rows"]
        if not rows:
            break
        for row in rows:
            yield to_row([row.get(c) for c in columns])
        if len(rows) < page_size:
            break
        offset += page_size
