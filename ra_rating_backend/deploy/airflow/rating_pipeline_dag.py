"""Airflow DAGs for the Rating Assurance platform.

These call the same stage functions the in-process orchestrator does — the task
boundaries here and the stage boundaries in ``app/modules/pipeline/constants.py``
are the same list, so a run behaves identically whichever launches it.

Deploy by copying this file into the Airflow DAGs folder with
``ra_rating_backend`` importable (same venv, or installed as a package).

    AIRFLOW_CONN / env required:
        RATING_DB_*, IDENTITY_DB_*, JWT_SECRET  — as in ra_rating_backend/.env
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from airflow import DAG
from airflow.operators.python import PythonOperator

DEFAULT_ARGS: dict[str, Any] = {
    "owner": "revenue-assurance",
    "depends_on_past": False,
    # Every stage is idempotent within its own transaction, so a retry re-runs
    # cleanly rather than half-applying.
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": True,
}


def _run(coro: Any) -> Any:
    """Airflow tasks are synchronous; the service layer is async."""
    return asyncio.run(coro)


# --- Rating pipeline --------------------------------------------------------


def _stage(stage_key: str):
    """Build the callable for one pipeline stage."""

    def _execute(**context: Any) -> str:
        from app.modules.pipeline.service import _run_stage

        run_id = context["dag_run"].conf.get("pipeline_run_id")
        if not run_id:
            raise ValueError("dag_run.conf must carry a pipeline_run_id.")
        ok = _run(_run_stage(run_id, stage_key))
        if not ok:
            # Fail the task so Airflow's retry and alerting apply; the stage's
            # own error is already recorded on the run.
            raise RuntimeError(f"Stage {stage_key} failed for run {run_id}.")
        return run_id

    return _execute


with DAG(
    dag_id="ra_rating_pipeline",
    description="CDR ingestion → normalization → enrichment → selection → rating → assurance",
    default_args=DEFAULT_ARGS,
    schedule=None,  # triggered per batch, with pipeline_run_id in conf
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=4,
    tags=["rating-assurance", "cdr"],
) as rating_pipeline:
    from app.modules.pipeline.constants import STAGES

    previous = None
    for spec in STAGES:
        task = PythonOperator(
            task_id=spec.key.lower(),
            python_callable=_stage(spec.key),
            doc=spec.description,
        )
        if previous is not None:
            previous >> task
        previous = task


# --- Rule import ------------------------------------------------------------


def _import_connector(**context: Any) -> dict[str, int]:
    """Pull a vendor's tariff export and reconcile it into canonical rules."""
    from app.core.database import SessionFactory
    from app.modules.connectors import service as connector_svc

    source_id = context["dag_run"].conf.get("source_id")
    if not source_id:
        raise ValueError("dag_run.conf must carry a source_id.")

    async def _go() -> dict[str, int]:
        async with SessionFactory() as db:
            source = await connector_svc.get_source(db, source_id)
            record = await connector_svc.run_import(
                db, source, uploaded=None, trigger="SCHEDULE",
                actor_id=None, actor_name="airflow",
            )
            await db.commit()
            return {
                "created": record.rules_created,
                "updated": record.rules_updated,
                "unchanged": record.rules_unchanged,
                "rejected": record.records_rejected,
            }

    return _run(_go())


def _validate_rule_set(**_: Any) -> dict[str, int]:
    from app.core.database import SessionFactory
    from app.modules.compiler import service as compiler_svc

    async def _go() -> dict[str, int]:
        async with SessionFactory() as db:
            report = await compiler_svc.validate_rule_set(db, rule_set_id=None)
            return {"errors": report.error_count, "warnings": report.warning_count}

    return _run(_go())


with DAG(
    dag_id="ra_rule_import",
    description="Vendor connector → canonical rules → validation summary",
    default_args=DEFAULT_ARGS,
    schedule=None,  # each connector's own cron triggers it with source_id
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["rating-assurance", "rules"],
) as rule_import:
    extract = PythonOperator(
        task_id="import_from_connector",
        python_callable=_import_connector,
        doc="Fetch the vendor export, map it to canonical, detect changes.",
    )
    validate = PythonOperator(
        task_id="validate_rule_set",
        python_callable=_validate_rule_set,
        doc="Structural, conflict and coverage validation across the estate.",
    )
    extract >> validate


# --- Rule publication -------------------------------------------------------


def _compile_snapshot(**context: Any) -> str:
    from app.core.database import SessionFactory
    from app.modules.compiler import service as compiler_svc

    conf = context["dag_run"].conf or {}

    async def _go() -> str:
        async with SessionFactory() as db:
            snapshot = await compiler_svc.compile_snapshot(
                db,
                name=conf.get("name") or f"Scheduled compile {context['ds']}",
                description="Compiled by the rule publication DAG.",
                rule_set_id=conf.get("rule_set_id"),
                force=False,
                actor_id=None,
                actor_name="airflow",
            )
            await db.commit()
            return snapshot.id

    return _run(_go())


def _activate_snapshot(**context: Any) -> int:
    from app.core.database import SessionFactory
    from app.modules.compiler import service as compiler_svc

    snapshot_id = context["ti"].xcom_pull(task_ids="compile_rules")

    async def _go() -> int:
        async with SessionFactory() as db:
            snapshot = await compiler_svc.activate(db, snapshot_id, actor_id=None)
            await db.commit()
            return snapshot.version

    return _run(_go())


with DAG(
    dag_id="ra_rule_publication",
    description="Validate → compile → activate an executable rule snapshot",
    default_args=DEFAULT_ARGS,
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["rating-assurance", "rules"],
) as rule_publication:
    check = PythonOperator(task_id="validate_snapshot", python_callable=_validate_rule_set)
    compile_task = PythonOperator(task_id="compile_rules", python_callable=_compile_snapshot)
    activate = PythonOperator(task_id="activate_snapshot", python_callable=_activate_snapshot)
    check >> compile_task >> activate
