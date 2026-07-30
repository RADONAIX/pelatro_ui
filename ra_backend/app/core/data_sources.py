"""Data-source (stream) registry — the plug-and-play engine.

A "data stream" is AIR / SDP / MSC / CCN / … . Each stream's schema and table
names follow a naming convention keyed by the stream key, so adding a new
CONVENTIONAL source is just adding its key to ``DATA_STREAMS`` in the env — no
code changes. A source that deviates (odd schema, non-standard tables) adds a
block to the optional ``DATA_SOURCES_FILE`` YAML.

This module is the ENGINE (convention + loader); you do NOT edit it per source.
``_BUILTIN`` only carries shape quirks for the streams that already ship today
(e.g. AIR's reconciliation uses a unified ``record_type`` while SDP's is split);
edit it only when an EXISTING stream's table shape changes.

Phase 1: all streams share the default ra_pg / bi_pg / clickhouse connections.
Cross-DB streams (a source on a different Postgres) are Phase 2.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("data_sources")


@dataclass(frozen=True)
class StreamPg:
    """A stream's own Postgres connection (split-DB mode).

    Present only when ``STREAM_{KEY}_PG_HOST`` is set for the stream; otherwise
    the stream uses the shared ``ra_pg``/``bi_pg`` connection (consolidated mode).
    ``name`` is the pipeline-logs DB (ra_pg) and ``bi_name`` the BI-reports DB
    (bi_pg) on that same host — both default to the shared DB names."""

    host: str
    port: int
    name: str
    bi_name: str
    user: str
    password: str


def _stream_pg(key: str) -> StreamPg | None:
    """Per-stream Postgres override from flat env vars, or None (=shared conn).

    Activated by ``STREAM_{KEY}_PG_HOST``; every other field falls back to the
    shared ``RA_PG_*`` value, so a stream that only differs by host needs a
    single env var. e.g. STREAM_AIR_PG_HOST=10.200.37.133."""
    prefix = f"STREAM_{key.upper()}_PG_"
    host = os.environ.get(prefix + "HOST", "").strip()
    if not host:
        return None

    def g(suffix: str, default: str) -> str:
        return os.environ.get(prefix + suffix, "").strip() or default

    return StreamPg(
        host=host,
        port=int(g("PORT", str(settings.ra_pg_port))),
        name=g("NAME", settings.ra_pg_name),
        bi_name=g("BI_NAME", settings.ra_bi_pg_name),
        user=g("USER", settings.ra_pg_user),
        # password may legitimately be empty; don't strip a real trailing space
        password=os.environ.get(prefix + "PASSWORD") or settings.ra_pg_password,
    )

# Shape quirks for streams shipping today. A new CONVENTIONAL source needs NO
# entry here; a deviating one uses the YAML instead. Only touch this for an
# existing stream whose tables change shape.
_BUILTIN: dict[str, dict[str, Any]] = {
    # AIR reconciliation now carries split raw_/proc_record_type (like SDP), so it
    # uses the convention default coalesce(raw_record_type, proc_record_type).
    "air": {},
    # SDP raw file log splits csv/db-loading, and its file_seq_check carries the
    # extra file_type + event_type columns (AIR's does not).
    "sdp": {"raw_file_variant": "omit", "file_seq_event_typed": True},
    "msc": {},  # ready to enable via DATA_STREAMS=air,sdp,msc
}


@dataclass(frozen=True)
class DataStream:
    key: str  # e.g. "air" — the table prefix + lowercase id
    label: str  # e.g. "AIR" — the source label shown in reports/UI
    schema: str  # e.g. "air_schema" — pipeline logs + report_batch_log
    bi_schema: str  # e.g. "bi_reports" — file seq/exception/summary reports
    # Reconciliation record_type expression (AIR: "record_type";
    # split streams: "coalesce(raw_record_type, proc_record_type)").
    recon_record_type: str
    raw_file_variant: str  # "refill" | "omit" — which raw file-log projection
    tables: dict[str, str]  # concrete table names (convention, overridable)
    # file_seq_check has the extra file_type + event_type columns (SDP); AIR/others
    # don't, so they NULL-pad those two to keep the consolidated UNION shape.
    file_seq_event_typed: bool = False
    # Own Postgres connection (split-DB mode); None = shared ra_pg/bi_pg.
    pg: StreamPg | None = None

    def t(self, name: str) -> str:
        """Concrete table name for a logical table key (e.g. 'file_seq_check')."""
        return self.tables[name]


def _convention(key: str) -> dict[str, Any]:
    """Everything about a stream derived purely from its key."""
    return {
        "label": key.upper(),
        "schema": f"{key}_schema",
        "bi_schema": settings.ra_bi_pg_schema,  # "bi_reports" (env-overridable)
        "recon_record_type": "coalesce(raw_record_type, proc_record_type)",
        "raw_file_variant": "refill",
        "file_seq_event_typed": False,
        "tables": {
            "record_seq_raw": f"{key}_raw_record_sequence_check",
            "record_seq_processed": f"{key}_processed_record_sequence_check",
            "file_seq_check": f"{key}_file_seq_check",
            "file_exception": f"{key}_file_exception_report",
            "file_summary": f"{key}_file_summary",
            "reconciliation": f"{key}_reconciliation",
            "batch_log_raw": f"{key}_raw_batch_log",
            "batch_log_processed": f"{key}_processed_batch_log",
            "file_log_raw": f"{key}_raw_file_log",
            "file_log_processed": f"{key}_processed_file_log",
        },
    }


def _yaml_overrides() -> dict[str, dict[str, Any]]:
    path = settings.data_sources_file.strip()
    if not path:
        return {}
    try:
        import yaml

        data = yaml.safe_load(Path(path).read_text()) or {}
        return data.get("streams", {}) or {}
    except Exception as exc:  # noqa: BLE001 — a bad/missing file must not crash startup
        log.error("data_sources_file_load_failed", path=path, error=str(exc))
        return {}


def _build(key: str, override: dict[str, Any]) -> DataStream:
    spec = _convention(key)
    # Apply built-in quirks, then YAML overrides (YAML wins). `tables` merges.
    for src in (_BUILTIN.get(key, {}), override):
        for k, v in (src or {}).items():
            if k == "tables" and isinstance(v, dict):
                spec["tables"] = {**spec["tables"], **v}
            else:
                spec[k] = v
    return DataStream(
        key=key,
        label=spec["label"],
        schema=spec["schema"],
        bi_schema=spec["bi_schema"],
        recon_record_type=spec["recon_record_type"],
        raw_file_variant=spec["raw_file_variant"],
        file_seq_event_typed=spec["file_seq_event_typed"],
        tables=spec["tables"],
        pg=_stream_pg(key),
    )


@functools.lru_cache(maxsize=1)
def enabled_streams() -> list[DataStream]:
    """Ordered list of enabled streams from DATA_STREAMS (+ optional YAML)."""
    keys = [k.strip().lower() for k in settings.data_streams.split(",") if k.strip()]
    overrides = _yaml_overrides()
    streams = [_build(k, overrides.get(k, {})) for k in keys]
    log.info("data_streams_loaded", streams=[s.key for s in streams])
    return streams
