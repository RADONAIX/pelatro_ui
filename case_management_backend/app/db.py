"""Database engine, session factory and the declarative Base.

PostgreSQL. Case management owns the `assurance` schema (settings.db_schema),
created on startup alongside its tables — the same schema-per-domain layout the
rest of this database already uses.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

SCHEMA = settings.db_schema


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


engine = create_engine(
    settings.sqlalchemy_url,
    echo=settings.sql_echo,
    # Verifies a pooled connection before handing it out, so a database restart
    # or an idle-timeout kill surfaces as a reconnect rather than a 500.
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle_seconds,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create the schema and any missing tables. Idempotent."""
    from app import models  # noqa: F401  (registers the mappers)

    with engine.begin() as conn:
        # An identifier can't be bound as a parameter; the value comes from
        # configuration, not from a request, and is quoted defensively.
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))

    Base.metadata.create_all(bind=engine)
