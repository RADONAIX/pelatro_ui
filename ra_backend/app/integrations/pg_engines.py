"""URL-keyed Postgres engine caches (async + sync).

Every RA/BI Postgres engine — the shared ``ra_pg``/``bi_pg`` connections and any
per-stream override connections (split-DB mode) — is created through here and
cached by URL. Two callers asking for the same URL share one pool, so the
consolidated (single-DB) deployment keeps exactly one pool per database while
split mode adds one pool per distinct host/db.

Async engines back the API/reporting reads; sync (psycopg2) engines back the
Celery export worker's server-side-cursor streaming.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings

_async_cache: dict[str, AsyncEngine] = {}
_sync_cache: dict[str, Engine] = {}


def _pool_kwargs() -> dict:
    # Read-only, low-concurrency pools shared with the app DB — keep small and
    # env-tunable (RA_PG_POOL_SIZE / RA_PG_MAX_OVERFLOW / RA_PG_POOL_RECYCLE).
    return {
        "pool_pre_ping": True,
        "pool_size": settings.ra_pg_pool_size,
        "max_overflow": settings.ra_pg_max_overflow,
        "pool_recycle": settings.ra_pg_pool_recycle,
    }


def async_url(host: str, port: int, dbname: str, user: str, password: str) -> str:
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{dbname}"


def sync_url(host: str, port: int, dbname: str, user: str, password: str) -> str:
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"


def async_engine(url: str) -> AsyncEngine:
    eng = _async_cache.get(url)
    if eng is None:
        eng = create_async_engine(url, **_pool_kwargs())
        _async_cache[url] = eng
    return eng


def sync_engine(url: str) -> Engine:
    eng = _sync_cache.get(url)
    if eng is None:
        eng = create_engine(url, pool_pre_ping=True, future=True)
        _sync_cache[url] = eng
    return eng


async def close_async_engines() -> None:
    for eng in list(_async_cache.values()):
        await eng.dispose()
    _async_cache.clear()
