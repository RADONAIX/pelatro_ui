"""Orchestration: compile -> persist -> create table -> execute -> publish report.

This is the seam the rest of the app calls. The Rule Explorer's create/update
path calls ``compile_and_run``; the scheduler calls ``run_stored``; the report
service calls ``list_reports`` and ``read_report``.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.modules.reconciliation import compiler, engine, repository, sql_builder
from app.modules.reconciliation.plan import ALL_STATUSES

log = get_logger("recon.service")

#: The rule category that compiles to a reconciliation. Rules of any other
#: category are stored and ignored here.
RECONCILIATION_CATEGORY = "Reconciliation"


def is_reconciliation(category: str | None) -> bool:
    return (category or "").strip().lower() == RECONCILIATION_CATEGORY.lower()


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
    plan = await compiler.compile_rule(
        rule_id=rule["id"],
        assurance_id=rule["appId"],
        rule_name=rule["name"],
        comparison=rule.get("comparison") or {},
        params=rule.get("params") or {},
        frequency=rule.get("frequency") or "Daily",
        severity=rule.get("severity") or "medium",
    )

    # Generate once and store what was generated, so the scheduler re-runs
    # byte-identical SQL and an operator can read exactly what executed.
    plan = _with_sql(plan)
    await repository.upsert_definition(plan)

    if not execute_now:
        return {"ruleId": plan.rule_id, "reportKey": plan.report_key, "executed": False}

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
        "description": (
            f"Reconciles {definition['left_schema']}.{definition['left_table']} against "
            f"{definition['right_schema']}.{definition['right_table']} on "
            f"{len(definition['join_keys'])} key(s), comparing "
            f"{len(definition['metrics'])} metric(s). Generated from rule "
            f"{definition['rule_id']}."
        ),
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

    latest = await repository.latest_succeeded_execution(definition["rule_id"])
    if latest is None:
        return {
            "key": report_key,
            "title": definition["report_title"],
            "ruleId": definition["rule_id"],
            "columns": [*plan.business_columns, "status"],
            "rows": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "executionId": None,
            "executedAt": None,
            "note": definition["last_error"]
            or "This reconciliation has not completed a run yet.",
        }

    if status and status not in ALL_STATUSES:
        status = None

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
