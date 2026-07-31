"""Synchronous, chunked readers for the Celery export worker.

Both report sources (BI Postgres `rafms`, ClickHouse) normally materialise the
full result set. For million-row exports the worker must stream instead:
 - Postgres: a SQLAlchemy server-side cursor (`yield_per`) over the BI DSN.
 - ClickHouse: `clickhouse_connect.query_row_block_stream` (block-by-block).

Each stream generator yields the COLUMN LIST first, then successive row-chunks
(lists of lists). Param syntax matches what reporting.service builds: `:name`
for Postgres (SQLAlchemy text), `{name:Type}` for ClickHouse.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import clickhouse_connect
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.integrations import pg_engines
from app.modules.reporting.sources import PgTarget


def _ra_engine() -> Engine:
    """Sync psycopg2 engine to the shared ra-platform pipeline DB (rafms_app)."""
    return pg_engines.sync_engine(pg_engines.sync_url(
        settings.ra_pg_host, settings.ra_pg_port, settings.ra_pg_name,
        settings.ra_pg_user, settings.ra_pg_password,
    ))


def _bi_engine() -> Engine:
    """Sync psycopg2 engine to the shared BI Postgres database (rafms)."""
    return pg_engines.sync_engine(pg_engines.sync_url(
        settings.ra_pg_host, settings.ra_pg_port, settings.ra_bi_pg_name,
        settings.ra_pg_user, settings.ra_pg_password,
    ))


def _pg_sync_engine(source: str, target: PgTarget | None) -> Engine:
    """The sync engine for a query group: its per-stream override (split mode) or
    the shared ra_pg/bi_pg engine (consolidated mode)."""
    if target is not None:
        return pg_engines.sync_engine(target.sync_url())
    return _ra_engine() if source == "ra_pg" else _bi_engine()


def _ch_client():
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
        connect_timeout=5,
    )


# --- One-shot (count / KPIs — single small row) ----------------------------
def query_one(
    source: str, sql: str, params: dict[str, Any] | None = None,
    target: PgTarget | None = None,
) -> dict[str, Any] | None:
    if source in ("bi_pg", "ra_pg"):
        with _pg_sync_engine(source, target).connect() as conn:
            row = conn.execute(text(sql), params or {}).mappings().first()
            return dict(row) if row else None
    if source == "clickhouse":
        qr = _ch_client().query(
            sql, parameters=params or {}, settings=settings.clickhouse_query_settings
        )
        if not qr.result_rows:
            return None
        return dict(zip(qr.column_names, qr.result_rows[0], strict=False))
    raise ValueError(f"Unknown source: {source}")


def count_with_timeout(
    source: str, sql: str, params: dict[str, Any] | None, timeout_seconds: float,
    target: PgTarget | None = None,
) -> int | None:
    """Exact row count for a progress bar, without letting the count block the
    export. ClickHouse counts are cheap → always exact. Postgres counts over a
    filtered billion-row set can take minutes → run under a ``statement_timeout``
    and return ``None`` on timeout so the caller streams with an unknown total
    (indeterminate progress) instead of sitting at 0%."""
    if source == "clickhouse":
        row = query_one(source, sql, params)
        return int((row or {}).get("n") or 0)
    if source in ("bi_pg", "ra_pg"):
        with _pg_sync_engine(source, target).connect() as conn:
            conn.execute(text(f"SET statement_timeout = {int(timeout_seconds * 1000)}"))
            try:
                row = conn.execute(text(sql), params or {}).mappings().first()
                return int((row or {}).get("n") or 0)
            except DBAPIError:
                return None  # statement_timeout fired → unknown total
    raise ValueError(f"Unknown source: {source}")


# --- Streaming (export rows) -----------------------------------------------
def stream_rows(
    source: str, sql: str, params: dict[str, Any] | None, chunk: int,
    target: PgTarget | None = None,
) -> Iterator[Any]:
    """First yield = column list; subsequent yields = chunks (list[list])."""
    if source in ("bi_pg", "ra_pg"):
        yield from _pg_stream(sql, params, chunk, _pg_sync_engine(source, target))
    elif source == "clickhouse":
        yield from _ch_stream(sql, params, chunk)
    else:
        raise ValueError(f"Unknown source: {source}")


def _pg_stream(
    sql: str, params: dict[str, Any] | None, chunk: int, engine: Engine
) -> Iterator[Any]:
    conn = engine.connect()
    try:
        result = conn.execution_options(yield_per=chunk).execute(text(sql), params or {})
        yield list(result.keys())
        for part in result.partitions():
            yield [list(r) for r in part]
    finally:
        conn.close()


def _ch_stream(sql: str, params: dict[str, Any] | None, chunk: int) -> Iterator[Any]:
    client = _ch_client()
    with client.query_row_block_stream(
        sql, parameters=params or {},
        settings={"max_block_size": chunk, **settings.clickhouse_stream_settings},
    ) as stream:
        cols = getattr(stream, "source", None)
        yield list(getattr(cols, "column_names", None) or getattr(stream, "column_names", []))
        for block in stream:
            yield [list(r) for r in block]
