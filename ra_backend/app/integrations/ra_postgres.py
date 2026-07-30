"""Read-only access to ra-platform's Postgres (rafms) — file_log / batches.

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
    if not settings.ra_pg_enabled:
        raise UpstreamUnavailableError("ra-platform Postgres integration is disabled.")
    try:
        async with _get_engine().connect() as conn:
            result = await conn.execute(text(sql), params or {})
            return [dict(row) for row in result.mappings().all()]
    except Exception as exc:  # noqa: BLE001
        log.warning("ra_pg_query_failed", error=str(exc))
        raise UpstreamUnavailableError(
            "ra-platform Postgres query failed.", details={"reason": str(exc)}
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
