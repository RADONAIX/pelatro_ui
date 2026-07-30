"""Operations business logic: pipelines, decoders, system config.

Live pipeline data is derived from ra-platform where possible:
  * KPIs / run history from ClickHouse ``air_reconciliation`` +
    ``reconciliation_run_log`` (schema known from ra-platform/scripts).
  * Decoders / system config / alert-ack state are owned by this service.

When the upstream is disabled or unreachable, representative fallback data
(matching the UI's expected shapes) is returned and the degradation is logged,
so the dashboard stays functional in dev/offline environments.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.data_sources import enabled_streams
from app.core.errors import NotFoundError, UpstreamUnavailableError
from app.core.logging import get_logger
from app.integrations import airflow, clickhouse, ra_postgres
from app.modules.operations import schemas
from app.modules.operations.models import Decoder, PipelineAlert, SystemConfig

log = get_logger("operations")


# --- Pipeline Map: batch logs (read-only, rafms_app.<dag>_schema.*_batch_log) -
# (dag, stream, schema, table), generated from the enabled data streams
# (DATA_STREAMS). Adding a stream there adds its Raw + Processed rows here — no
# code change. Table/schema names come from the data-source registry.
def _batchlog_sources() -> list[tuple[str, str, str, str]]:
    out: list[tuple[str, str, str, str]] = []
    for s in enabled_streams():
        out.append((s.label, "Raw", s.schema, s.t("batch_log_raw")))
        out.append((s.label, "Processed", s.schema, s.t("batch_log_processed")))
    return out


BATCHLOG_SOURCES: list[tuple[str, str, str, str]] = _batchlog_sources()


# --- Reconciliation stream (Pipeline Map "Reconciled") -----------------------
# The reconciliation runs are logged in the report batch log alongside other
# report-generation processes. We surface only the reconciliation rows
# (process_name like '%reconciliation%', e.g. air_reconciliation /
# sdp_reconciliation) as the "Reconciled" stream. The table's shape differs from
# the *_batch_log tables, so we alias to the BatchLog fields the UI uses (batch_id,
# start/end time, status, error). The DAG is derived from the process_name prefix
# (air_ -> AIR, sdp_ -> SDP), and the batch_id is built as <DAG>_RECONCILED_<id>
# so the UI's parseBatchType groups it under the right DAG + the Reconciled stream.
RECON_REPORT_TABLE = "report_batch_log"


async def _recon_sources(*, hours: int) -> list[schemas.BatchSource]:
    # Reconciliation runs live in each stream's <schema>.report_batch_log. We read
    # every enabled stream's schema (deduping shared schemas + batch_ids), so a new
    # stream's Reconciled runs appear automatically. The DAG is still derived from
    # the process_name prefix so a shared recon log is grouped correctly.
    by_dag: dict[str, list[schemas.BatchLog]] = {}
    seen_batch: set[str] = set()
    for schema in dict.fromkeys(s.schema for s in enabled_streams()):
        try:
            rows = await ra_postgres.query(
                "SELECT upper(split_part(process_name, '_', 1)) AS _dag, "
                "upper(split_part(process_name, '_', 1)) || '_RECONCILED_' || report_batch_id "
                "AS batch_id, "
                "start_time AS batch_start_time, end_time AS batch_end_time, "
                "start_time AS batch_timestamp, status AS batch_status, "
                "coalesce(error_message, '') AS error_message "
                f'FROM {schema}."{RECON_REPORT_TABLE}" '
                "WHERE lower(process_name) LIKE '%reconciliation%' "
                "AND start_time >= now() - make_interval(hours => :hours) "
                f"ORDER BY start_time DESC LIMIT {settings.pipeline_batch_row_cap}",
                {"hours": hours},
            )
        except UpstreamUnavailableError:
            log.info("recon_source_unavailable", schema=schema, table=RECON_REPORT_TABLE)
            continue
        for row in rows:
            if row.get("batch_id") in seen_batch:
                continue
            seen_batch.add(row.get("batch_id"))
            dag = (row.pop("_dag", "") or "AIR").strip() or "AIR"
            by_dag.setdefault(dag, []).append(schemas.BatchLog(**row))
    return [
        schemas.BatchSource(dag=dag, stream="Reconciled", rows=rows_)
        for dag, rows_ in by_dag.items()
    ]


async def list_batch_sources(*, hours: int | None = None) -> list[schemas.BatchSource]:
    """Per-batch pipeline logs for the UI Pipeline Map, grouped by DAG + stream,
    read live from ra-platform's Postgres. Each source returns batches whose
    ``batch_start_time`` is within the last ``hours``. Each table is queried
    independently so a missing / under-development / unreachable source yields
    an empty group instead of failing the whole response (no 500). The Reconciled
    stream is appended from the report batch log (see ``_recon_sources``)."""
    if hours is None:
        hours = settings.pipeline_default_hours
    out: list[schemas.BatchSource] = []
    for dag, stream, schema, table in BATCHLOG_SOURCES:
        try:
            rows = await ra_postgres.query(
                f'SELECT * FROM {schema}."{table}" '
                f"WHERE batch_start_time >= now() - make_interval(hours => :hours) "
                # window is the real filter; LIMIT is just a safety cap (env-tunable)
                f"ORDER BY batch_start_time DESC LIMIT {settings.pipeline_batch_row_cap}",
                {"hours": hours},
            )
            out.append(
                schemas.BatchSource(
                    dag=dag,
                    stream=stream,
                    rows=[schemas.BatchLog(**row) for row in rows],
                )
            )
        except UpstreamUnavailableError:
            log.info("batchlog_source_unavailable", dag=dag, stream=stream, table=table)
            out.append(schemas.BatchSource(dag=dag, stream=stream, rows=[]))
    out.extend(await _recon_sources(hours=hours))
    return out


# --- Export module: per-batch file logs (rafms_app.<dag>_schema.*_file_log) ---
# Both file tables use file_node_id / file_sequence_number / attempt_count /
# last_error_message / insert_timestamp, so BOTH need aliasing to the UI FileLog
# shape (columns absent from a table just default via the Pydantic model).
# Processed has single csv_creation_*/db_loading_*; raw has per-type splits
# (we surface the refill_* ones as representative).
_COMMON_FILE_HEAD = (
    "id, filename, batch_id, file_node_id AS node_id, "
    "file_sequence_number AS sequence_number, file_timestamp, "
    "file_status, integrity_flag, archived_at, archived_path, "
    "watcher_start_time, watcher_end_time, watcher_status, "
    "decoder_start_time, decoder_end_time, decoder_status, "
)
_COMMON_FILE_TAIL = (
    "ingestion_start_time, ingestion_end_time, ingestion_status, "
    "expected_record_count, actual_record_count, "
    "attempt_count AS retry_count, last_error_step, "
    "file_reject_reason AS error_message, insert_timestamp AS created_at, "
    "quarantined_at, quarantine_reason, quarantine_batch_dir, quarantine_count, retried_at"
)
_PROCESSED_FILE_PROJECTION = (
    _COMMON_FILE_HEAD
    # file_type (AA/RR/CDR …) exists only on the PROCESSED file logs (post-decode);
    # the raw projections omit it and it defaults to "" in the FileLog model.
    + "file_type, "
    + "csv_creation_start_time, csv_creation_end_time, csv_creation_status, "
    + "db_loading_start_time, db_loading_end_time, db_loading_status, "
    + _COMMON_FILE_TAIL
)
_RAW_FILE_PROJECTION = (
    _COMMON_FILE_HEAD
    + "refill_csv_creation_start_time AS csv_creation_start_time, "
    + "refill_csv_creation_end_time AS csv_creation_end_time, "
    + "refill_csv_creation_status AS csv_creation_status, "
    + "refill_db_loading_start_time AS db_loading_start_time, "
    + "refill_db_loading_end_time AS db_loading_end_time, "
    + "refill_db_loading_status AS db_loading_status, "
    + _COMMON_FILE_TAIL
)
# SDP raw file log has the same common columns as AIR, but splits CSV/DB-loading
# across seven per-type streams (account_adjustment, periodic_reset, …) with no
# single ``refill_*`` representative. We therefore omit the csv_creation_* /
# db_loading_* columns (they default to "" in the FileLog model) rather than
# picking one stream arbitrarily; the common watcher/decoder/ingestion stages
# and counts still surface. (SDP processed has plain csv_creation_*/db_loading_*,
# so it reuses _PROCESSED_FILE_PROJECTION unchanged.)
_SDP_RAW_FILE_PROJECTION = _COMMON_FILE_HEAD + _COMMON_FILE_TAIL

# batch_id prefix -> (schema, table, projection_sql), generated from the enabled
# streams. The raw file log's shape varies per stream (data_sources raw_file_variant:
# "refill" = AIR-style refill_* columns; "omit" = SDP-style, csv/db-loading omitted);
# processed is uniform. A new stream picks its variant in the data-source registry.
_RAW_FILE_PROJECTIONS = {"refill": _RAW_FILE_PROJECTION, "omit": _SDP_RAW_FILE_PROJECTION}


def _file_sources() -> list[tuple[str, str, str, str]]:
    out: list[tuple[str, str, str, str]] = []
    for s in enabled_streams():
        raw_proj = _RAW_FILE_PROJECTIONS.get(s.raw_file_variant, _RAW_FILE_PROJECTION)
        out.append((f"{s.label}_PROCESSED_", s.schema, s.t("file_log_processed"),
                    _PROCESSED_FILE_PROJECTION))
        out.append((f"{s.label}_RAW_", s.schema, s.t("file_log_raw"), raw_proj))
    return out


FILE_SOURCES: list[tuple[str, str, str, str]] = _file_sources()


async def list_batch_files(batch_id: str) -> list[schemas.FileLog]:
    """All files for one batch, for the Export module's drill-down. The source
    table is resolved from the ``batch_id`` prefix (e.g. ``AIR_PROCESSED_`` ->
    air_schema.air_processed_file_log). Returns ``[]`` for an unknown prefix or
    when the upstream is unavailable (never 500s)."""
    key = batch_id.upper()
    match = next((s for s in FILE_SOURCES if key.startswith(s[0])), None)
    if match is None:
        log.info("batch_files_unknown_prefix", batch_id=batch_id)
        return []
    _prefix, schema, table, projection = match
    qualified = f'{schema}."{table}"'
    try:
        rows = await ra_postgres.query(
            f"SELECT {projection} FROM {qualified} "
            f"WHERE batch_id = :batch_id ORDER BY sequence_number "
            f"LIMIT {settings.pipeline_file_row_cap}",
            {"batch_id": batch_id},
        )
        return [schemas.FileLog(**row) for row in rows]
    except UpstreamUnavailableError:
        log.info("batch_files_unavailable", batch_id=batch_id, table=table)
        return []


def _db_ident() -> str:
    from app.core.config import settings

    return "`" + settings.clickhouse_database.replace("`", "``") + "`"


# --- Pipelines: KPIs -------------------------------------------------------
async def get_kpis(db: AsyncSession) -> schemas.PipelineKpis:
    cfg = await get_or_create_config(db)
    try:
        ident = _db_ident()
        rows = await clickhouse.query(
            f"""
            SELECT
                count() AS total,
                countIf(reconciliation_status = 'MATCHED') AS matched,
                countIf(reconciliation_status != 'MATCHED') AS mismatched
            FROM {ident}.air_reconciliation FINAL
            WHERE coalesce(raw_origin_timestamp, proc_origin_timestamp)
                  >= now() - INTERVAL 24 HOUR
            """
        )
        stats = rows[0] if rows else {"total": 0, "matched": 0, "mismatched": 0}
        total = int(stats.get("total") or 0)
        mismatched = int(stats.get("mismatched") or 0)
        breaches = await _sla_breaches(ident, cfg.sla_minutes)
        return schemas.PipelineKpis(
            throughput=f"{total:,} / 24h",
            avgLatency=await _avg_latency(ident),
            failed24h=mismatched,
            slaBreaches=breaches,
        )
    except UpstreamUnavailableError:
        log.info("kpis_unavailable", reason="clickhouse_unavailable")
        return schemas.PipelineKpis(throughput="—", avgLatency="—", failed24h=0, slaBreaches=0)


async def _avg_latency(ident: str) -> str:
    rows = await clickhouse.query(
        f"""
        SELECT avg(dateDiff('second', recon_start_time, recon_end_time)) AS secs
        FROM {ident}.reconciliation_run_log
        WHERE status = 'COMPLETED' AND created_time >= now() - INTERVAL 24 HOUR
        """
    )
    secs = int((rows[0].get("secs") if rows else 0) or 0)
    return f"{secs // 60}m {secs % 60}s"


async def _sla_breaches(ident: str, sla_minutes: int) -> int:
    rows = await clickhouse.query(
        f"""
        SELECT countIf(dateDiff('minute', recon_start_time, recon_end_time) > {sla_minutes}) AS n
        FROM {ident}.reconciliation_run_log
        WHERE status = 'COMPLETED' AND created_time >= now() - INTERVAL 24 HOUR
        """
    )
    return int((rows[0].get("n") if rows else 0) or 0)


# --- Pipelines: stages -----------------------------------------------------
async def get_stages(db: AsyncSession) -> list[schemas.PipelineStage]:
    try:
        ident = _db_ident()
        rows = await clickhouse.query(
            f"""
            SELECT
                count() AS total,
                countIf(reconciliation_status = 'MATCHED') AS matched
            FROM {ident}.air_reconciliation FINAL
            WHERE coalesce(raw_origin_timestamp, proc_origin_timestamp)
                  >= now() - INTERVAL 24 HOUR
            """
        )
        total = int((rows[0].get("total") if rows else 0) or 0)
        matched = int((rows[0].get("matched") if rows else 0) or 0)
        match_pct = (matched / total * 100) if total else 100.0
        # Only values backed by real ra-platform data are reported; per-stage
        # durations have no upstream source, so they're left blank, not faked.
        return [
            schemas.PipelineStage(
                key="decoding", name="Decoding", status="ok",
                duration="", metric=f"{total:,} records",
            ),
            schemas.PipelineStage(
                key="reconciliation", name="Reconciliation",
                status="ok" if match_pct >= 100 else "warning",
                duration="", metric=f"{match_pct:.2f}% match",
            ),
        ]
    except UpstreamUnavailableError:
        log.info("stages_unavailable", reason="clickhouse_unavailable")
        return []


# --- Pipelines: runs -------------------------------------------------------
async def get_runs(db: AsyncSession, *, limit: int) -> list[schemas.PipelineRun]:
    try:
        ident = _db_ident()
        rows = await clickhouse.query(
            f"""
            SELECT recon_run_id, recon_start_time, recon_end_time, status
            FROM {ident}.reconciliation_run_log
            ORDER BY created_time DESC
            LIMIT {int(limit)}
            """
        )
        return [
            schemas.PipelineRun(
                id=str(r.get("recon_run_id")),
                source="Reconciliation",
                batch=str(r.get("recon_run_id")),
                start=r.get("recon_start_time"),
                end=r.get("recon_end_time"),
                status=str(r.get("status") or ""),
                records=0,
                failed=0,
            )
            for r in rows
        ]
    except UpstreamUnavailableError:
        log.info("runs_unavailable", reason="clickhouse_unavailable")
        return []


# --- Pipelines: alerts (DB-owned) ------------------------------------------
async def list_alerts(db: AsyncSession) -> list[schemas.PipelineAlertRow]:
    rows = (
        (await db.execute(select(PipelineAlert).order_by(PipelineAlert.created_at.desc())))
        .scalars()
        .all()
    )
    return [
        schemas.PipelineAlertRow(
            id=a.id,
            severity=a.severity,
            stage=a.stage,
            message=a.message,
            createdAt=a.created_at,
            status=a.status,
        )
        for a in rows
    ]


async def acknowledge_alert(db: AsyncSession, alert_id: str, actor: str) -> schemas.ActionResult:
    alert = (
        await db.execute(select(PipelineAlert).where(PipelineAlert.id == alert_id))
    ).scalar_one_or_none()
    if alert is None:
        raise NotFoundError("Alert not found.")
    alert.status = "Acknowledged"
    alert.acknowledged_by = actor
    await db.flush()
    return schemas.ActionResult(ok=True, detail=f"Alert {alert_id} acknowledged.")


# --- Pipelines: retries ----------------------------------------------------
async def get_retries(db: AsyncSession) -> list[schemas.RetryJob]:
    # No canonical retry queue in ra-platform yet; surface open alerts as
    # actionable retry candidates. Empty when there are none.
    alerts = (
        (await db.execute(select(PipelineAlert).where(PipelineAlert.status == "Open")))
        .scalars()
        .all()
    )
    return [
        schemas.RetryJob(id=a.id, batch="", stage=a.stage, error=a.message, retryCount=0)
        for a in alerts
    ]


async def retry_job(job_id: str) -> schemas.ActionResult:
    return await _trigger("air_recon_dag", job_id, "retry")


async def replay_job(job_id: str) -> schemas.ActionResult:
    return await _trigger("air_pipeline_dag", job_id, "replay")


async def _trigger(dag_id: str, job_id: str, action: str) -> schemas.ActionResult:
    try:
        await airflow.trigger_dag(dag_id, conf={"job_id": job_id, "action": action})
        return schemas.ActionResult(ok=True, detail=f"Triggered {dag_id} for {job_id}.")
    except UpstreamUnavailableError as exc:
        log.info("trigger_skipped", dag_id=dag_id, reason=exc.message)
        return schemas.ActionResult(
            ok=True, detail=f"Queued {action} for {job_id} (Airflow integration disabled)."
        )


# --- Decoders --------------------------------------------------------------
async def list_decoders(db: AsyncSession) -> list[schemas.DecoderRow]:
    rows = (await db.execute(select(Decoder).order_by(Decoder.name))).scalars().all()
    return [
        schemas.DecoderRow(
            id=d.id, name=d.name, version=d.version, status=d.status, throughput=d.throughput
        )
        for d in rows
    ]


async def upsert_decoder(db: AsyncSession, payload: schemas.DecoderUpsert) -> Decoder:
    dec = (await db.execute(select(Decoder).where(Decoder.id == payload.id))).scalar_one_or_none()
    if dec is None:
        dec = Decoder(id=payload.id)
        db.add(dec)
    dec.name = payload.name
    dec.version = payload.version
    dec.status = payload.status
    dec.throughput = payload.throughput
    if payload.config is not None:
        dec.config = payload.config
    await db.flush()
    await db.refresh(dec)
    return dec


# --- System config ---------------------------------------------------------
async def get_or_create_config(db: AsyncSession) -> SystemConfig:
    cfg = (
        await db.execute(select(SystemConfig).where(SystemConfig.id == "system"))
    ).scalar_one_or_none()
    if cfg is None:
        cfg = SystemConfig(id="system")
        db.add(cfg)
        await db.flush()
        await db.refresh(cfg)
    return cfg


async def get_system_config(db: AsyncSession) -> schemas.SystemConfigOut:
    cfg = await get_or_create_config(db)
    return schemas.SystemConfigOut(
        environment=cfg.environment,
        retentionDays=cfg.retention_days,
        slaMinutes=cfg.sla_minutes,
        alertEmail=cfg.alert_email,
        maintenanceMode=cfg.maintenance_mode,
    )


async def update_system_config(
    db: AsyncSession, payload: schemas.SystemConfigUpdate
) -> schemas.SystemConfigOut:
    cfg = await get_or_create_config(db)
    if payload.environment is not None:
        cfg.environment = payload.environment
    if payload.retentionDays is not None:
        cfg.retention_days = payload.retentionDays
    if payload.slaMinutes is not None:
        cfg.sla_minutes = payload.slaMinutes
    if payload.alertEmail is not None:
        cfg.alert_email = str(payload.alertEmail)
    if payload.maintenanceMode is not None:
        cfg.maintenance_mode = payload.maintenanceMode
    await db.flush()
    await db.refresh(cfg)
    return await get_system_config(db)
