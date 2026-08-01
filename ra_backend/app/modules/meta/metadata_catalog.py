"""Read-only table and column metadata for assurance rule authoring."""

from __future__ import annotations

from typing import NamedTuple

from pydantic import BaseModel

from app.core.config import settings
from app.core.errors import NotFoundError
from app.integrations import ra_postgres


class TableMetadata(BaseModel):
    id: str
    database_name: str
    schema_name: str
    table_name: str
    label: str


class ColumnMetadata(BaseModel):
    name: str
    data_type: str
    ordinal_position: int


# Clients select an assurance application, not arbitrary databases.
#
# A source narrows in three steps, each optional:
#   ``schemas=None`` exposes every non-system schema in the allow-listed
#   database — used for rafms_rating, whose catalogue is intentionally spread
#   across ingestion, assurance, canonical-rating and source schemas.
#   ``tables=None`` exposes every table in those schemas.
# Naming tables narrows a schema to the handful an assurance actually authors
# against, for a schema that holds far more than that.
class MetadataSource(NamedTuple):
    database: str
    schemas: tuple[str, ...] | None
    #: Qualified "schema.table" names. None = every table in `schemas`.
    tables: tuple[str, ...] | None


#: Policy entry, same shape but with the database left as a marker that
#: _resolve_database turns into a real name.
class _SourcePolicy(NamedTuple):
    database: str | None
    schemas: tuple[str, ...] | None
    tables: tuple[str, ...] | None = None


# Which sources each assurance authors rules against.
#
#   Rating            -> two canonical rating tables, in the rating database.
#   Usage             -> MSC and IN, both in the rating database.
#   Billing / Charging-> SDP and MSC. NOTE these are in DIFFERENT databases:
#                        the SDP tables live in rafms, msc_schema only exists in
#                        rafms_rating (rafms_rating.sdp_schema is empty). Both
#                        are offered because both are legitimately in scope, but
#                        a single rule cannot pair one with the other — the
#                        reconciliation runs as one server-side join, which
#                        cannot span databases, and the compiler says so rather
#                        than failing at execution.
#
# An assurance with no entry here falls back to DEFAULT_SOURCES rather than
# erroring: every scope's Rule Explorer must be able to list tables, even one
# whose source systems have not been assigned yet.
_ASSURANCE_SOURCES: dict[str, tuple[_SourcePolicy, ...]] = {
    # Rating authors against the canonical rating model, but only two of its
    # tables: rating_reconciliation holds what rating concluded per event and
    # tariff_master what it should have charged, which is the pairing a rating
    # rule needs. The other twenty-two objects in canonical_rating are the
    # reference data behind those two, not things a control compares.
    #
    # Usage keeps the switch/IN feeds below — it asks whether the network and
    # the IN saw the same call, a question about the sources rather than about
    # what rating made of them.
    "rating": (
        _SourcePolicy(
            None,
            ("canonical_rating","in_schema"),
            ("canonical_rating.rating_reconciliation", "canonical_rating.tariff_master", "canonical_rating.network_rating_output","in_schema.in_voice"),
        ),
    ),
    "usage": (_SourcePolicy(None, ("msc_schema", "in_schema")),),
    # Billing and Charging author against the AIR and SDP source schemas in the
    # RATING database. Both in one database, so a rule can pair them — unlike
    # the earlier msc/sdp split, where the two sides sat in different databases
    # and no rule could join them.
    "billing": (_SourcePolicy(None, ("air_schema", "sdp_schema")),),
    "charging": (_SourcePolicy(None, ("air_schema", "sdp_schema")),),
}

#: Used by any assurance without an explicit mapping above.
DEFAULT_SOURCES: tuple[_SourcePolicy, ...] = (
    _SourcePolicy(None, ("msc_schema", "in_schema", "air_schema")),
)


def _resolve_database(marker: str | None) -> str:
    """`None` means the rating database, `"__ra_pg__"` the reporting one.

    Indirected through markers so the table above reads as schema policy and the
    actual database names stay a matter of configuration.
    """
    return settings.ra_pg_name if marker == "__ra_pg__" else settings.ra_rating_pg_name


def sources_for(assurance: str) -> tuple[MetadataSource, ...]:
    configured = _ASSURANCE_SOURCES.get(assurance.lower(), DEFAULT_SOURCES)
    return tuple(
        MetadataSource(_resolve_database(policy.database), policy.schemas, policy.tables)
        for policy in configured
    )


def _permits(source: MetadataSource, schema_name: str, table_name: str | None) -> bool:
    """Whether this source covers the schema — and the table, when one is named.

    `table_name=None` answers the weaker question "is this schema in scope",
    which is all a caller that hasn't got a table can ask.
    """
    if source.schemas is not None and schema_name not in source.schemas:
        return False
    if source.tables is None or table_name is None:
        return True
    return f"{schema_name}.{table_name}" in source.tables


def source_for_schema(
    assurance: str,
    schema_name: str,
    database_name: str | None = None,
    table_name: str | None = None,
) -> str:
    """The database a table lives in, or raise if it is out of scope.

    Pass `table_name` wherever it is known: without it a schema-level match is
    the most that can be checked, which would let a caller read a table the
    policy names no allow-list entry for.
    """
    if database_name is not None:
        for source in sources_for(assurance):
            if source.database == database_name and _permits(source, schema_name, table_name):
                return source.database
        raise NotFoundError("Table is outside the selected assurance scope.")

    # Backwards compatibility for rules saved before table IDs included the
    # database name. Prefer an explicitly configured schema over a wildcard
    # source when names overlap (for example air_schema exists in both DBs).
    for source in sources_for(assurance):
        if source.schemas is not None and _permits(source, schema_name, table_name):
            return source.database
    for source in sources_for(assurance):
        if source.schemas is None and _permits(source, schema_name, table_name):
            return source.database
    raise NotFoundError("Table is outside the selected assurance scope.")


# ---------------------------------------------------------------------------
# File logs.
#
# The per-file processing logs for each stream and side. Single-table rules
# (Sequence, Duplicate) are authored against these rather than against the
# assurance's source schemas: a sequence check asks "did every file arrive",
# which is a question about the file log, not about the records inside.
#
# Deliberately an explicit allow-list, and deliberately NOT scoped by assurance:
# the same four logs are the answer for every assurance, and a fixed list means
# the columns endpoint can serve them without an assurance to validate against.
# ---------------------------------------------------------------------------
FILE_LOG_TABLES: tuple[tuple[str, str, str], ...] = (
    ("air_schema", "air_raw_file_log", "AIR Raw"),
    ("air_schema", "air_processed_file_log", "AIR Processed"),
    ("air_schema", "air_processed", "AIR Processed"),
    ("sdp_schema", "sdp_raw_file_log", "SDP Raw"),
    ("sdp_schema", "sdp_processed_file_log", "SDP Processed"),
)


def file_log_database() -> str:
    """File logs live in the reporting database, whatever the assurance."""
    return settings.ra_pg_name


def is_file_log(schema_name: str, table_name: str) -> bool:
    return any(s == schema_name and t == table_name for s, t, _ in FILE_LOG_TABLES)


async def list_file_logs() -> list[TableMetadata]:
    """The four file logs, filtered to the ones that actually exist."""
    database = file_log_database()
    rows = await ra_postgres.query_database(
        database,
        """
        SELECT table_schema AS schema_name, table_name
        FROM information_schema.tables
        WHERE (table_schema, table_name) IN (
            SELECT * FROM unnest(CAST(:schemas AS text[]), CAST(:tables AS text[]))
        )
        """,
        {
            "schemas": [s for s, _, _ in FILE_LOG_TABLES],
            "tables": [t for _, t, _ in FILE_LOG_TABLES],
        },
    )
    present = {(r["schema_name"], r["table_name"]) for r in rows}
    return [
        TableMetadata(
            id=f"{database}:{schema}.{table}",
            database_name=database,
            schema_name=schema,
            table_name=table,
            label=f"{label} — {schema}.{table}",
        )
        for schema, table, label in FILE_LOG_TABLES
        if (schema, table) in present
    ]


async def list_file_log_columns(schema_name: str, table_name: str) -> list[ColumnMetadata]:
    if not is_file_log(schema_name, table_name):
        raise NotFoundError("Not a file log table.")
    rows = await ra_postgres.query_database(
        file_log_database(),
        """
        SELECT column_name AS name, data_type, ordinal_position
        FROM information_schema.columns
        WHERE table_schema = :schema_name AND table_name = :table_name
        ORDER BY ordinal_position
        """,
        {"schema_name": schema_name, "table_name": table_name},
    )
    if not rows:
        raise NotFoundError("Table not found.")
    return [ColumnMetadata.model_validate(row) for row in rows]


async def list_tables(assurance: str) -> list[TableMetadata]:
    tables: list[TableMetadata] = []
    for source in sources_for(assurance):
        params: dict[str, list[str]] = {}
        if source.schemas is None:
            filters = """
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
              AND table_schema NOT LIKE 'pg_toast%'
            """
        else:
            filters = "AND table_schema = ANY(CAST(:schemas AS text[]))"
            params["schemas"] = list(source.schemas)

        # Named tables narrow the schema filter further. Still filtered in SQL
        # rather than in Python so a schema holding thousands of tables isn't
        # fetched whole to return two of them.
        if source.tables is not None:
            filters += """
              AND table_schema || '.' || table_name = ANY(CAST(:tables AS text[]))
            """
            params["tables"] = list(source.tables)

        rows = await ra_postgres.query_database(
            source.database,
            f"""
            SELECT table_schema AS schema_name, table_name
            FROM information_schema.tables
            WHERE table_type IN ('BASE TABLE', 'VIEW', 'FOREIGN', 'FOREIGN TABLE')
              {filters}
            ORDER BY table_schema, table_name
            """,
            params or None,
        )
        tables.extend(
            TableMetadata(
                # Database-qualified IDs remain unique when the two databases
                # contain the same schema/table name.
                id=f"{source.database}:{row['schema_name']}.{row['table_name']}",
                database_name=source.database,
                schema_name=row["schema_name"],
                table_name=row["table_name"],
                label=f"{row['schema_name']}.{row['table_name']}",
            )
            for row in rows
        )
    return sorted(tables, key=lambda table: (table.database_name, table.label))


async def list_columns(
    assurance: str,
    schema_name: str,
    table_name: str,
    database_name: str | None = None,
) -> list[ColumnMetadata]:
    database_name = source_for_schema(assurance, schema_name, database_name, table_name)
    rows = await ra_postgres.query_database(
        database_name,
        """
        SELECT column_name AS name, data_type, ordinal_position
        FROM information_schema.columns
        WHERE table_schema = :schema_name AND table_name = :table_name
        ORDER BY ordinal_position
        """,
        {"schema_name": schema_name, "table_name": table_name},
    )
    if not rows:
        raise NotFoundError("Table not found.")
    return [ColumnMetadata.model_validate(row) for row in rows]
