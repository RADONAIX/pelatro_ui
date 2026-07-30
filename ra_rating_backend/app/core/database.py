"""Async SQLAlchemy engines for the Rating Assurance service.

Two engines, deliberately asymmetric:

``engine``           read/write, resolves to the ``rating`` schema. This service
                     owns every table in it.
``identity_engine``  READ-ONLY, resolves to ``administration``. Opened with
                     ``default_transaction_read_only=on`` so Postgres itself
                     rejects any write — the existing backend's data cannot be
                     mutated from here even by a coding mistake.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings

# Same convention as the existing service → clean, reversible Alembic diffs.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(AsyncAttrs, DeclarativeBase):
    # schema is NOT pinned on the metadata: the connection's search_path points
    # at `rating`, which keeps table names unqualified in queries and lets a
    # deployment relocate the schema with one env var.
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Adds created_at / updated_at to a model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


engine = create_async_engine(
    settings.rating_database_url,
    echo=settings.rating_db_echo,
    pool_size=settings.rating_db_pool_size,
    max_overflow=settings.rating_db_max_overflow,
    pool_pre_ping=True,
    connect_args={"server_settings": {"search_path": settings.rating_db_schema}},
)

SessionFactory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


#: Session parameters for the identity connection. Exposed as a module constant
#: so the isolation guarantee is assertable in a test rather than buried in a
#: call site. ``default_transaction_read_only`` is the guarantee: any
#: INSERT/UPDATE/DELETE fails with "cannot execute ... in a read-only
#: transaction" before it reaches a table in the administration schema.
IDENTITY_SERVER_SETTINGS: dict[str, str] = {
    "search_path": settings.identity_db_schema,
    "default_transaction_read_only": "on",
}

identity_engine = create_async_engine(
    settings.identity_database_url,
    echo=False,
    pool_size=settings.identity_db_pool_size,
    max_overflow=settings.identity_db_max_overflow,
    pool_pre_ping=True,
    connect_args={"server_settings": IDENTITY_SERVER_SETTINGS},
)

IdentitySessionFactory = async_sessionmaker(
    bind=identity_engine, expire_on_commit=False, autoflush=False
)


#: Read-only session parameters for an external source database (MSC switch
#: records, OCS charges). Same guarantee as the identity bridge: the operator's
#: own systems are physically unwritable from this service, enforced by Postgres
#: rather than by convention.
def _source_server_settings(schema: str) -> dict[str, str]:
    return {"search_path": schema, "default_transaction_read_only": "on"}


#: Engines are created lazily so a deployment with no configured source never
#: opens a pool — and, more importantly, an unreachable source host cannot stop
#: the service from starting.
_source_engines: dict[str, tuple[Any, async_sessionmaker[AsyncSession]]] = {}


def _source_factory(
    name: str, url: str, schema: str, pool_size: int, max_overflow: int
) -> async_sessionmaker[AsyncSession]:
    cached = _source_engines.get(name)
    if cached is None:
        source_engine = create_async_engine(
            url,
            echo=False,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            connect_args={"server_settings": _source_server_settings(schema)},
        )
        cached = (
            source_engine,
            async_sessionmaker(bind=source_engine, expire_on_commit=False, autoflush=False),
        )
        _source_engines[name] = cached
    return cached[1]


def msc_source_factory() -> async_sessionmaker[AsyncSession]:
    """Read-only sessions on the MSC CDR landing database."""
    return _source_factory(
        "msc",
        settings.msc_source_url,
        settings.msc_source_schema,
        settings.msc_source_pool_size,
        settings.msc_source_max_overflow,
    )


def ocs_source_factory() -> async_sessionmaker[AsyncSession]:
    """Read-only sessions on the OCS / IN charge database."""
    return _source_factory(
        "ocs",
        settings.ocs_source_url,
        settings.ocs_source_schema,
        settings.ocs_source_pool_size,
        settings.ocs_source_max_overflow,
    )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a transactional session on the rating schema."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engines() -> None:
    await engine.dispose()
    await identity_engine.dispose()
    for source_engine, _ in _source_engines.values():
        await source_engine.dispose()
    _source_engines.clear()
