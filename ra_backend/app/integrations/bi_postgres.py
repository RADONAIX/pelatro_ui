"""Read-only access to the BI Postgres database (`rafms` / `bi_reports`).

A dedicated async engine, separate from both the app DB and ``ra_postgres``
(which targets ``rafms_db``). The ``bi_reports`` schema holds the pre-computed
report materialized views (e.g. ``air_file_seq_mv``). Read-only by convention.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import settings
from app.core.errors import UpstreamUnavailableError
from app.core.logging import get_logger
from app.integrations import pg_engines

log = get_logger("bi_postgres")


def _get_engine() -> AsyncEngine:
    # Same host/creds as ra_pg, different database (rafms). URL-keyed shared pool.
    return pg_engines.async_engine(pg_engines.async_url(
        settings.ra_pg_host, settings.ra_pg_port, settings.ra_bi_pg_name,
        settings.ra_pg_user, settings.ra_pg_password,
    ))


async def close_bi_postgres() -> None:
    # Engines are shared + closed once via pg_engines.close_async_engines().
    return None


async def query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not settings.ra_pg_enabled:
        raise UpstreamUnavailableError("BI Postgres integration is disabled.")
    try:
        async with _get_engine().connect() as conn:
            result = await conn.execute(text(sql), params or {})
            return [dict(row) for row in result.mappings().all()]
    except Exception as exc:  # noqa: BLE001
        log.warning("bi_pg_query_failed", error=str(exc))
        raise UpstreamUnavailableError(
            "BI Postgres query failed.", details={"reason": str(exc)}
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
