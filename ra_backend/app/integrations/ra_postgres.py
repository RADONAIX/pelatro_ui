"""Read-only access to ra-platform's Postgres databases.

A dedicated async engine separate from the app database. Used to surface
file/batch processing status in the Pipelines view. Read-only by convention.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import settings
from app.core.errors import UpstreamUnavailableError
from app.core.logging import get_logger
from app.integrations import pg_engines

log = get_logger("ra_postgres")


def _get_engine() -> AsyncEngine:
    # URL-keyed shared pool (pg_engines), so this engine and the reporting split
    # queries to the same DB reuse one pool. Pool sizes are env-tunable
    # (RA_PG_POOL_SIZE / RA_PG_MAX_OVERFLOW / RA_PG_POOL_RECYCLE).
    return pg_engines.async_engine(pg_engines.async_url(
        settings.ra_pg_host, settings.ra_pg_port, settings.ra_pg_name,
        settings.ra_pg_user, settings.ra_pg_password,
    ))


async def close_ra_postgres() -> None:
    await pg_engines.close_async_engines()


async def query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return await query_database(settings.ra_pg_name, sql, params)


async def query_database(
    database_name: str,
    sql: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run a read-only query against a named database on the RA Postgres host."""
    if not settings.ra_pg_enabled:
        raise UpstreamUnavailableError("ra-platform Postgres integration is disabled.")
    try:
        engine = pg_engines.async_engine(
            pg_engines.async_url(
                settings.ra_pg_host,
                settings.ra_pg_port,
                database_name,
                settings.ra_pg_user,
                settings.ra_pg_password,
            )
        )
        async with engine.connect() as conn:
            result = await conn.execute(text(sql), params or {})
            return [dict(row) for row in result.mappings().all()]
    except Exception as exc:  # noqa: BLE001
        log.warning("ra_pg_query_failed", database=database_name, error=str(exc))
        raise UpstreamUnavailableError(
            "ra-platform Postgres query failed.",
            details={"database": database_name, "reason": str(exc)},
        ) from exc


async def execute_database(
    database_name: str,
    sql: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run a WRITING statement against a named database on the RA Postgres host.

    The read path above deliberately never commits. This is its counterpart, and
    the only place the app writes to this host — the Rule Explorer's authored
    rules (see settings.app_rules_db_name). Rows are returned so a caller can
    use RETURNING and get the stored row back in one round trip rather than
    writing and then re-reading it.
    """
    if not settings.ra_pg_enabled:
        raise UpstreamUnavailableError("ra-platform Postgres integration is disabled.")
    try:
        engine = pg_engines.async_engine(
            pg_engines.async_url(
                settings.ra_pg_host,
                settings.ra_pg_port,
                database_name,
                settings.ra_pg_user,
                settings.ra_pg_password,
            )
        )
        async with engine.begin() as conn:  # begin() => commits on clean exit
            result = await conn.execute(text(sql), params or {})
            if result.returns_rows:
                return [dict(row) for row in result.mappings().all()]
            return []
    except UpstreamUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("ra_pg_execute_failed", database=database_name, error=str(exc))
        raise UpstreamUnavailableError(
            "ra-platform Postgres write failed.",
            details={"database": database_name, "reason": str(exc)},
        ) from exc


async def ping() -> bool:
    if not settings.ra_pg_enabled:
        return False
    try:
        async with _get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
