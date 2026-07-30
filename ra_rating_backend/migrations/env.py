"""Alembic environment for the Rating Assurance service.

Targets the ``rating`` schema exclusively. This history is independent of
``ra_backend``'s — the two services never share a revision chain, so a
migration here can never be applied to the administration schema.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Side-effect import: populates Base.metadata for autogenerate.
import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.rating_database_url_sync)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to):
    # alembic's bookkeeping table sits in the search_path and would otherwise
    # show up as a spurious "removed table" in an autogenerate diff.
    if type_ == "table" and name == "alembic_version":
        return False

    # NEVER emit a drop for a table this service does not declare.
    #
    # The search_path is `rating, public`, and Alembic reflects everything
    # reachable through it as if it were the default schema. `public` on this
    # deployment holds another system's tables entirely — the AIR ingestion
    # pipeline's file_log, batch_log and quarantine_audit_log. Without this
    # guard autogenerate reflects them, fails to find them in Base.metadata,
    # and writes `op.drop_table` for each one into the upgrade path. Running
    # that migration would delete a different product's data.
    #
    # A shared database makes "reflected but undeclared" a normal, safe state,
    # not a diff to reconcile. The service owns what it declares and nothing
    # else.
    if reflected and compare_to is None:
        return False

    # The canonical model (`ra_rule` and, from R7, `ra_catalog` / `ra_bundle` /
    # `ra_subscriber`) lives outside the search_path. Autogenerate only reflects
    # the default schema, so it would see those tables as missing and try to
    # recreate them on every diff. Their migrations are hand-written; exclude
    # them here so an autogenerate run against `rating` stays trustworthy.
    schema = getattr(obj, "schema", None)
    return schema in (None, settings.rating_db_schema)


def run_migrations_offline() -> None:
    context.configure(
        url=settings.rating_database_url_sync,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    schema = settings.rating_db_schema
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Create the schema and make it the search_path so unqualified DDL and
        # the alembic_version table land inside it. Committed up front so
        # Alembic owns its own transaction (SQLAlchemy 2.0 autobegin would
        # otherwise leave one open that Alembic never commits).
        connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        connection.exec_driver_sql(f'SET search_path TO "{schema}", public')
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table_schema=schema,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
