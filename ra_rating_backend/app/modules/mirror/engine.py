"""The mirror's own engine — created lazily, disposed on shutdown.

Lazy for the same reason the MSC and OCS source engines are lazy
(`core/database.py`): a deployment with the mirror disabled must never open a
pool, and an unreachable mirror host must never stop the service from starting.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _engine, _factory
    if _factory is None:
        _engine = create_async_engine(
            settings.mirror_database_url,
            echo=False,
            pool_size=settings.mirror_db_pool_size,
            max_overflow=settings.mirror_db_max_overflow,
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": settings.mirror_db_schema}},
        )
        _factory = async_sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _factory


@asynccontextmanager
async def mirror_session() -> AsyncGenerator[AsyncSession, None]:
    """One transaction against the mirror. Commits on success, rolls back on error.

    The caller in ``hooks.py`` swallows whatever comes out; this only guarantees
    the mirror's *own* consistency, never the primary's.
    """
    async with session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None


def is_open() -> bool:
    """Whether an engine has actually been constructed. Asserted by the isolation
    test: with the feature disabled this must stay False for the process's life."""
    return _engine is not None
