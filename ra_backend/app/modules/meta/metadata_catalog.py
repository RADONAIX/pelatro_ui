"""Read-only table and column metadata for assurance rule authoring."""

from __future__ import annotations

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


# Clients select an assurance application, not arbitrary databases.  A source
# with ``schemas=None`` exposes every non-system schema in that allow-listed
# database.  This is used for rafms_rating because its catalogue is intentionally
# spread across ingestion, assurance, canonical-rating and source schemas.
MetadataSource = tuple[str, tuple[str, ...] | None]


# Which source schemas each assurance authors rules against.
#
#   Rating / Usage    -> MSC and IN, both in the rating database.
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
#: Policy entry: (database marker, schemas). The marker is resolved to a real
#: database name by _resolve_database.
_SourcePolicy = tuple[str | None, tuple[str, ...] | None]

_ASSURANCE_SOURCES: dict[str, tuple[_SourcePolicy, ...]] = {
    "rating": ((None, ("msc_schema", "in_schema")),),
    "usage": ((None, ("msc_schema", "in_schema")),),
    # Billing and Charging author against the AIR and SDP source schemas in the
    # RATING database. Both in one database, so a rule can pair them — unlike
    # the earlier msc/sdp split, where the two sides sat in different databases
    # and no rule could join them.
    "billing": ((None, ("air_schema", "sdp_schema")),),
    "charging": ((None, ("air_schema", "sdp_schema")),),
}

#: Used by any assurance without an explicit mapping above.
DEFAULT_SOURCES: tuple[_SourcePolicy, ...] = (
    (None, ("msc_schema", "in_schema", "air_schema")),
)


def _resolve_database(marker: str | None) -> str:
    """`None` means the rating database, `"__ra_pg__"` the reporting one.

    Indirected through markers so the table above reads as schema policy and the
    actual database names stay a matter of configuration.
    """
    return settings.ra_pg_name if marker == "__ra_pg__" else settings.ra_rating_pg_name


def sources_for(assurance: str) -> tuple[MetadataSource, ...]:
    configured = _ASSURANCE_SOURCES.get(assurance.lower(), DEFAULT_SOURCES)
    return tuple((_resolve_database(marker), schemas) for marker, schemas in configured)


def source_for_schema(
    assurance: str,
    schema_name: str,
    database_name: str | None = None,
) -> str:
    if database_name is not None:
        for allowed_database, schemas in sources_for(assurance):
            database_allowed = allowed_database == database_name
            schema_allowed = schemas is None or schema_name in schemas
            if database_allowed and schema_allowed:
                return allowed_database
        raise NotFoundError("Table is outside the selected assurance scope.")

    # Backwards compatibility for rules saved before table IDs included the
    # database name. Prefer an explicitly configured schema over a wildcard
    # source when names overlap (for example air_schema exists in both DBs).
    for database_name, schemas in sources_for(assurance):
        if schemas is not None and schema_name in schemas:
            return database_name
    for database_name, schemas in sources_for(assurance):
        if schemas is None:
            return database_name
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
    for database_name, schemas in sources_for(assurance):
        if schemas is None:
            schema_filter = """
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
              AND table_schema NOT LIKE 'pg_toast%'
            """
            params = None
        else:
            schema_filter = "AND table_schema = ANY(CAST(:schemas AS text[]))"
            params = {"schemas": list(schemas)}

        rows = await ra_postgres.query_database(
            database_name,
            f"""
            SELECT table_schema AS schema_name, table_name
            FROM information_schema.tables
            WHERE table_type IN ('BASE TABLE', 'VIEW', 'FOREIGN', 'FOREIGN TABLE')
              {schema_filter}
            ORDER BY table_schema, table_name
            """,
            params,
        )
        tables.extend(
            TableMetadata(
                # Database-qualified IDs remain unique when the two databases
                # contain the same schema/table name.
                id=f"{database_name}:{row['schema_name']}.{row['table_name']}",
                database_name=database_name,
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
    database_name = source_for_schema(assurance, schema_name, database_name)
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
