"""Database-free tests for the assurance metadata catalogue."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.errors import NotFoundError
from app.modules.meta import metadata_catalog


@pytest.mark.asyncio
async def test_billing_discovers_only_rating_air_and_sdp_tables(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_query(database_name, sql, params=None):
        calls.append((database_name, sql, params))
        return [
            {"schema_name": "air_schema", "table_name": "air_processed_aa"},
            {"schema_name": "sdp_schema", "table_name": "sdp_processed_aa"},
        ]

    monkeypatch.setattr(metadata_catalog.ra_postgres, "query_database", fake_query)

    tables = await metadata_catalog.list_tables("billing")

    assert {table.id for table in tables} == {
        f"{settings.ra_rating_pg_name}:air_schema.air_processed_aa",
        f"{settings.ra_rating_pg_name}:sdp_schema.sdp_processed_aa",
    }
    assert [call[0] for call in calls] == [settings.ra_rating_pg_name]
    rating_call = next(call for call in calls if call[0] == settings.ra_rating_pg_name)
    assert rating_call[2] == {"schemas": ["air_schema", "sdp_schema"]}
    assert "table_schema = ANY" in rating_call[1]


@pytest.mark.asyncio
async def test_columns_use_database_from_qualified_table_id(monkeypatch):
    seen: dict[str, object] = {}

    async def fake_query(database_name, sql, params=None):
        seen.update(database=database_name, params=params)
        return [{"name": "event_id", "data_type": "bigint", "ordinal_position": 1}]

    monkeypatch.setattr(metadata_catalog.ra_postgres, "query_database", fake_query)

    columns = await metadata_catalog.list_columns(
        "billing",
        "air_schema",
        "air_processed_aa",
        database_name=settings.ra_rating_pg_name,
    )

    assert seen["database"] == settings.ra_rating_pg_name
    assert columns[0].name == "event_id"


def test_unapproved_database_is_rejected():
    with pytest.raises(NotFoundError, match="outside the selected assurance scope"):
        metadata_catalog.source_for_schema("billing", "public", "untrusted_database")
