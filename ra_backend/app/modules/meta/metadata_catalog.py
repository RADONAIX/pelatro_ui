"""Read-only table and column metadata for assurance rule authoring."""

from __future__ import annotations

from pydantic import BaseModel

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationFailedError
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


def sources_for(assurance: str) -> tuple[MetadataSource, ...]:
    sources = {
        # Billing rules are authored only against the AIR/SDP source schemas in
        # the dedicated rating database. Pipeline logs from rafms and unrelated
        # rafms_rating schemas must not appear in this selector.
        "billing": (
            (settings.ra_rating_pg_name, ("air_schema", "sdp_schema")),
        ),
        "charging": ((settings.ra_pg_name, ("air_schema", "sdp_schema")),),
        "rating": ((settings.ra_rating_pg_name, None),),
    }.get(assurance.lower())
    if sources is None:
        raise ValidationFailedError(
            "Database metadata is not configured for this assurance scope."
        )
    return sources


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
