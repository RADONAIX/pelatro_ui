"""RA report catalog — generated from the enabled data streams.

The consolidated reports (record-sequence / file-sequence / file-exception /
file-summary / report-batch-log) UNION every enabled stream; reconciliation is
one report per stream. Table names come from the data-source registry
(``core.data_sources``), so adding a stream to ``DATA_STREAMS`` adds it to every
report with NO code change. Each report carries its own ``columns`` (the filter
whitelist) and ``sample_by`` (stratified drill-down), which are fixed per report
type.

Phase 1: all streams share the ra_pg / bi_pg / clickhouse connections, so each
consolidated report is a single UNION query (identical to the previous hardcoded
SQL for AIR+SDP). Cross-DB streams are Phase 2 (query per connection + merge).
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.data_sources import DataStream, enabled_streams

_JOIN = " UNION ALL "

_RECON_COLUMNS = [
    "reconciliation_status", "record_type", "txn_id", "node_id", "subscriber_num",
    "raw_tran_amt", "proc_tran_amt", "raw_acc_balance", "proc_acc_balance",
    "filename", "created_time",
]

# Common projection head for the bi_pg file reports: source/stream are parsed
# from the data_source column (e.g. "air_raw" -> AIR / Raw).
_SRC_STREAM = (
    "SELECT split_part(data_source, '_', 1) AS source, "
    "initcap(split_part(data_source, '_', 2)) AS stream, "
)


def _assemble_pg(
    spec: dict[str, Any],
    streams: list[DataStream],
    detail_arms: dict[str, str],
    count_arms: dict[str, str],
) -> dict[str, Any]:
    """Attach per-stream arms + the consolidated detail/count SQL to a Postgres
    report. ``detail_sql``/``count_sql`` (all arms UNIONed) are the single-DB fast
    path; ``detail_arms``/``count_arms`` (keyed by stream) let the service execute
    per-connection and merge when streams live on different servers (split mode).
    ``detail_arms`` values preserve stream order, so the joined SQL is identical
    to the previous hand-built UNION."""
    spec["stream_objs"] = streams
    spec["detail_arms"] = detail_arms
    spec["count_arms"] = count_arms
    spec["detail_sql"] = f"SELECT * FROM ({_JOIN.join(detail_arms.values())}) AS _u"
    spec["count_sql"] = f"SELECT count(*) AS n FROM ({_JOIN.join(count_arms.values())}) AS _c"
    return spec


def _record_sequence_check(streams: list[DataStream]) -> dict[str, Any]:
    detail, count = [], []
    for s in streams:
        for stream_label, tbl in (
            ("Raw", s.t("record_seq_raw")),
            ("Processed", s.t("record_seq_processed")),
        ):
            detail.append(
                f"SELECT '{s.label}' AS source, '{stream_label}' AS stream, "
                "record_date, node_id, previous_filename, previous_sequence_number, "
                "previous_origin_timestamp, next_filename, next_sequence_number, "
                "next_origin_timestamp, missing_sequence_count "
                f"FROM {tbl}"
            )
            count.append(f"SELECT missing_sequence_count FROM {tbl}")
    return {
        "key": "record_sequence_check", "title": "Record Sequence Check", "group": "Files",
        "available": True, "source": "clickhouse",
        "count_sql": f"SELECT count(*) AS n FROM ({_JOIN.join(count)})",
        "detail_sql": f"SELECT * FROM ({_JOIN.join(detail)})",
        "order_by": "missing_sequence_count DESC",
        "date_column": "record_date",
        "kpi_agg": "count(*) AS rows, sum(missing_sequence_count) AS missing_records",
        "columns": ["source", "stream", "record_date", "node_id",
                    "previous_filename", "previous_sequence_number", "previous_origin_timestamp",
                    "next_filename", "next_sequence_number", "next_origin_timestamp",
                    "missing_sequence_count"],
        "sample_by": ["source", "stream"],
    }


def _file_sequence_check(streams: list[DataStream]) -> dict[str, Any]:
    def proj(s: DataStream) -> str:
        # SDP's file_seq_check carries file_type + event_type; AIR's does not, so
        # NULL-pad those two on non-typed streams to keep the UNION shape uniform.
        if s.file_seq_event_typed:
            ft, et = "file_type", "event_type"
        else:
            ft = "CAST(NULL AS text) AS file_type"
            et = "CAST(NULL AS text) AS event_type"
        return (_SRC_STREAM + f"file_date, file_node_id, {ft}, {et}, sequence_cycle, "
                "file_sequence, expected_file, actual_file, status, current_status, "
                "probable_node_restart_flag "
                f"FROM {s.bi_schema}.{s.t('file_seq_check')}")
    return _assemble_pg({
        "key": "file_sequence_check", "title": "File Sequence Check", "group": "Files",
        "available": True, "source": "bi_pg",
        "order_by": "file_date DESC",
        "date_column": "file_date",
        "kpi_agg": ("count(*) AS rows, "
                    "count(*) FILTER (WHERE status <> 'Present') AS missing_files"),
        "columns": ["source", "stream", "file_date", "file_node_id", "file_type",
                    "event_type", "sequence_cycle", "file_sequence", "expected_file",
                    "actual_file", "status", "current_status", "probable_node_restart_flag"],
        "sample_by": ["source", "stream", "status"],
    }, streams,
        {s.key: proj(s) for s in streams},
        {s.key: f"SELECT 1 FROM {s.bi_schema}.{s.t('file_seq_check')}" for s in streams},
    )


def _file_exception(streams: list[DataStream]) -> dict[str, Any]:
    def proj(s: DataStream) -> str:
        return (_SRC_STREAM + "batch_date, file_date, file_status, filename "
                f"FROM {s.bi_schema}.{s.t('file_exception')}")
    return _assemble_pg({
        "key": "file_exception", "title": "File Exception Report", "group": "Files",
        "available": True, "source": "bi_pg",
        "order_by": "file_date DESC",
        "date_column": "file_date",
        "kpi_agg": "count(*) AS rows",
        "columns": ["source", "stream", "batch_date", "file_date", "file_status", "filename"],
        "sample_by": ["source", "stream", "file_status"],
    }, streams,
        {s.key: proj(s) for s in streams},
        {s.key: f"SELECT 1 FROM {s.bi_schema}.{s.t('file_exception')}" for s in streams},
    )


def _file_summary(streams: list[DataStream]) -> dict[str, Any]:
    def proj(s: DataStream) -> str:
        return (_SRC_STREAM + "file_date, total_files_loaded, duplicate_file_count, "
                f"zero_kb_file_count, corrupt_file_count FROM {s.bi_schema}.{s.t('file_summary')}")
    return _assemble_pg({
        "key": "file_summary", "title": "File Summary Report", "group": "Files",
        "available": True, "source": "bi_pg",
        "order_by": "file_date DESC",
        "date_column": "file_date",
        "kpi_agg": ("count(*) AS rows, sum(total_files_loaded) AS files_loaded, "
                    "sum(duplicate_file_count) AS duplicates, sum(corrupt_file_count) AS corrupt"),
        "columns": ["source", "stream", "file_date", "total_files_loaded",
                    "duplicate_file_count", "zero_kb_file_count", "corrupt_file_count"],
        "sample_by": ["source", "stream"],
    }, streams,
        {s.key: proj(s) for s in streams},
        {s.key: f"SELECT 1 FROM {s.bi_schema}.{s.t('file_summary')}" for s in streams},
    )


def _report_batch_log(streams: list[DataStream]) -> dict[str, Any]:
    def proj(s: DataStream) -> str:
        return (f"SELECT '{s.label}' AS source, report_batch_id, process_name, start_time, "
                f"end_time, status, error_message FROM {s.schema}.report_batch_log")
    return _assemble_pg({
        "key": "report_batch_log", "title": "Report Batch Log", "group": "Operations",
        "available": True, "source": "bi_pg",
        "order_by": "start_time DESC",
        "date_column": "start_time",
        "kpi_agg": "count(*) AS rows",
        "columns": ["source", "report_batch_id", "process_name", "start_time", "end_time",
                    "status", "error_message"],
        "sample_by": ["source", "process_name", "status"],
    }, streams,
        {s.key: proj(s) for s in streams},
        {s.key: f"SELECT 1 FROM {s.schema}.report_batch_log" for s in streams},
    )


def _reconciliation(s: DataStream) -> dict[str, Any]:
    tbl = s.t("reconciliation")
    return {
        "key": f"{s.key}_reconciliation", "title": f"{s.label} Reconciliation Report",
        "group": "Reconciliation", "available": True, "source": "clickhouse",
        "count_sql": f"SELECT count() AS n FROM {tbl} WHERE reconciliation_status != 'MATCHED'",
        "detail_sql": (
            f"SELECT reconciliation_status, {s.recon_record_type} AS record_type, "
            "coalesce(raw_transaction_id, proc_transaction_id) AS txn_id, "
            "coalesce(raw_node_id, proc_node_id) AS node_id, "
            "coalesce(raw_subscriber_number, proc_subscriber_number) AS subscriber_num, "
            "raw_transaction_amount AS raw_tran_amt, proc_transaction_amount AS proc_tran_amt, "
            "raw_account_balance AS raw_acc_balance, proc_account_balance AS proc_acc_balance, "
            "coalesce(raw_filename, proc_filename) AS filename, "
            "coalesce(raw_origin_timestamp, proc_origin_timestamp) AS created_time "
            f"FROM {tbl} WHERE reconciliation_status != 'MATCHED'"
        ),
        "order_by": "created_time DESC",
        "date_column": "created_time",
        "kpi_agg": ("count(*) AS rows, "
                    "countIf(reconciliation_status = 'AMOUNT_MISMATCH') AS amount_mismatch, "
                    "countIf(reconciliation_status = 'RAW_ONLY') AS raw_only, "
                    "countIf(reconciliation_status = 'PROC_ONLY') AS proc_only"),
        "columns": list(_RECON_COLUMNS),
        "sample_by": ["reconciliation_status"],
    }


# Pipeline batches export columns. dag/stream are projected literals (real,
# filterable SQL columns) so the Pipeline Map export can filter by DAG/stream
# without the UI's batch_id parsing. All other columns exist in both the raw and
# processed batch-log tables.
_PIPELINE_BATCH_COLUMNS = [
    "batch_id", "batch_start_time", "batch_end_time", "dag", "stream", "batch_status",
    "total_files", "decode_complete_count", "decode_failed_count", "load_complete_count",
    "zero_kb_file_count", "duplicate_file_count", "corrupt_file_count",
]


def _pipeline_batches(streams: list[DataStream]) -> dict[str, Any]:
    """Pipeline & Job Monitor batch export (ra_pg ``*_batch_log`` tables).

    Not shown as a Reports card — it backs the Pipeline Map "Export CSV" via the
    Download Center (report key ``pipeline_batches``). Raw + Processed only;
    Reconciled is excluded (its report_batch_log has a different column shape)."""
    cols = ", ".join(
        c for c in _PIPELINE_BATCH_COLUMNS if c not in ("dag", "stream")
    )

    def proj(s: DataStream, stream_label: str, table_key: str) -> str:
        return (
            f"SELECT '{s.label}' AS dag, '{stream_label}' AS stream, {cols} "
            f'FROM {s.schema}."{s.t(table_key)}"'
        )

    # One arm per stream = its Raw + Processed UNIONed, so a stream's two tables
    # stay on the same connection when streams are split across servers.
    arms = {
        s.key: _JOIN.join([proj(s, "Raw", "batch_log_raw"),
                           proj(s, "Processed", "batch_log_processed")])
        for s in streams
    }
    return _assemble_pg({
        "key": "pipeline_batches", "title": "Pipeline Batches", "group": "Operations",
        "available": settings.ra_pg_enabled, "source": "ra_pg",
        "order_by": "batch_start_time DESC",
        "date_column": "batch_start_time",
        "kpi_agg": ("count(*) AS batches, sum(total_files) AS total_files, "
                    "sum(decode_failed_count) AS decode_failed"),
        "columns": list(_PIPELINE_BATCH_COLUMNS),
        "sample_by": ["dag", "stream"],
    }, streams, arms, arms)


# Pipeline files export columns (dag/stream are projected literals). Most are
# common to the raw + processed file logs; file_type / csv_creation_status /
# db_loading_status exist only on PROCESSED (AIR raw has refill_* equivalents,
# SDP raw has none → projected NULL) so every UNION arm has an identical shape.
_PIPELINE_FILE_COLUMNS = [
    "dag", "stream", "filename", "batch_id", "node_id", "sequence_number",
    "file_timestamp", "file_status", "integrity_flag", "file_type",
    "decoder_status", "csv_creation_status", "db_loading_status", "ingestion_status",
    "expected_record_count", "actual_record_count", "retry_count", "last_error_step",
    "error_message", "created_at",
]


def _pipeline_files(streams: list[DataStream]) -> dict[str, Any]:
    """Pipeline & Job Monitor file-log export (ra_pg ``*_file_log`` tables).

    Backs the Pipeline Map file-logs "Export CSV" via the Download Center (report
    key ``pipeline_files``). Raw + Processed per stream; per-stage columns absent
    from a given table are projected NULL so the UNION shape is uniform."""
    _common = (
        "filename, batch_id, file_node_id AS node_id, "
        # file_sequence_number is integer in one table, varchar in the rest → cast
        # to text so the UNION types match (it's an export/display column).
        "CAST(file_sequence_number AS text) AS sequence_number, file_timestamp, "
        "file_status, integrity_flag, {file_type}, decoder_status, "
        "{csv_status}, {db_status}, ingestion_status, "
        "expected_record_count, actual_record_count, "
        "attempt_count AS retry_count, last_error_step, "
        "file_reject_reason AS error_message, insert_timestamp AS created_at"
    )

    def proj(s: DataStream, stream_label: str, table_key: str, processed: bool) -> str:
        if processed:
            ft, csv_s, db_s = "file_type", "csv_creation_status", "db_loading_status"
        elif s.raw_file_variant == "refill":            # AIR raw → refill_* stages
            ft = "CAST(NULL AS text) AS file_type"
            csv_s = "refill_csv_creation_status AS csv_creation_status"
            db_s = "refill_db_loading_status AS db_loading_status"
        else:                                            # SDP raw ('omit') → no stages
            ft = "CAST(NULL AS text) AS file_type"
            csv_s = "CAST(NULL AS text) AS csv_creation_status"
            db_s = "CAST(NULL AS text) AS db_loading_status"
        body = _common.format(file_type=ft, csv_status=csv_s, db_status=db_s)
        return (f"SELECT '{s.label}' AS dag, '{stream_label}' AS stream, {body} "
                f'FROM {s.schema}."{s.t(table_key)}"')

    arms = {
        s.key: _JOIN.join([proj(s, "Raw", "file_log_raw", processed=False),
                           proj(s, "Processed", "file_log_processed", processed=True)])
        for s in streams
    }
    return _assemble_pg({
        "key": "pipeline_files", "title": "Pipeline Files", "group": "Operations",
        "available": settings.ra_pg_enabled, "source": "ra_pg",
        "order_by": "file_timestamp DESC",
        "date_column": "file_timestamp",
        "kpi_agg": ("count(*) AS files, sum(actual_record_count) AS records, "
                    "count(*) FILTER (WHERE file_status <> 'SUCCESS') AS not_success"),
        "columns": list(_PIPELINE_FILE_COLUMNS),
        "sample_by": ["dag", "stream"],
    }, streams, arms, arms)


def build_reports() -> list[dict[str, Any]]:
    """The full report catalog for the currently-enabled streams."""
    streams = enabled_streams()
    reports: list[dict[str, Any]] = [
        _record_sequence_check(streams),
        _file_sequence_check(streams),
        _file_exception(streams),
        _file_summary(streams),
        _report_batch_log(streams),
        _pipeline_batches(streams),
        _pipeline_files(streams),
    ]
    reports.extend(_reconciliation(s) for s in streams)
    return reports
