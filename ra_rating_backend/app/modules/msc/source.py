"""Streaming reader over the MSC landing table.

Two constraints shape everything here.

**Read-only.** The session comes from a pool opened with
``default_transaction_read_only=on``, so a stray INSERT against the operator's
switch archive fails in Postgres rather than succeeding quietly.

**Never load the batch into memory.** At 10 lakh rows a ``SELECT *`` materialised
into Python is not slow, it is impossible. Rows are pulled in keyset-paged
windows: ``WHERE id > :cursor ORDER BY id LIMIT :size``. Keyset rather than
OFFSET, because OFFSET makes the database re-scan and discard every row it
already returned — page 100 of a 5,000-row window costs 500,000 discarded rows,
so an ``OFFSET`` pager degrades quadratically over exactly the volumes this has
to survive.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import msc_source_factory
from app.core.errors import UpstreamUnavailableError
from app.core.logging import get_logger
from app.modules.msc.constants import SOURCE_COLUMNS

log = get_logger("msc.source")

#: Identifiers are interpolated into SQL (a table name cannot be a bind
#: parameter), so they are validated rather than trusted. The values come from
#: settings rather than from a request, but a typo that produced valid SQL would
#: be far worse than one that fails loudly.
_SAFE_IDENTIFIER = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _identifier(value: str, *, what: str) -> str:
    text_value = str(value).strip()
    if not text_value or set(text_value) - _SAFE_IDENTIFIER:
        raise ValueError(f"{what} '{value}' is not a valid SQL identifier.")
    return text_value


@dataclass
class SourcePage:
    rows: list[dict[str, Any]]
    #: The highest source id in this page — the next page's exclusive lower
    #: bound, and the value written back to the cursor once the page commits.
    last_id: int


class MscSourceReader:
    """Keyset-paged, read-only access to one MSC table."""

    def __init__(self, table: str, *, schema: str | None = None, fetch_size: int | None = None):
        self.table = _identifier(table, what="MSC source table")
        self.schema = _identifier(
            schema or settings.msc_source_schema, what="MSC source schema"
        )
        self.fetch_size = fetch_size or settings.msc_source_fetch_size
        self._columns = ", ".join(_identifier(c, what="column") for c in SOURCE_COLUMNS)

    @property
    def qualified_name(self) -> str:
        return f'"{self.schema}"."{self.table}"'

    async def count(self, *, after_id: int = 0) -> int:
        statement = text(
            f"SELECT count(*) FROM {self.qualified_name} WHERE id > :after_id"
        )
        async with msc_source_factory()() as session:
            try:
                return int((await session.execute(statement, {"after_id": after_id})).scalar_one())
            except SQLAlchemyError as exc:
                raise UpstreamUnavailableError(
                    f"The MSC source table {self.qualified_name} could not be read.",
                    details={"error": str(exc)},
                ) from exc

    async def pages(
        self, *, after_id: int = 0, limit: int | None = None
    ) -> AsyncIterator[SourcePage]:
        """Yield successive pages of raw rows, oldest first.

        ``limit`` caps the total rows across all pages, so a caller can ask for
        "the next 10,000" without knowing how they divide into pages.
        """
        statement = text(
            f"SELECT {self._columns} FROM {self.qualified_name} "
            "WHERE id > :after_id ORDER BY id LIMIT :page_size"
        ).bindparams(bindparam("after_id"), bindparam("page_size"))

        cursor = after_id
        remaining = limit
        async with msc_source_factory()() as session:
            while remaining is None or remaining > 0:
                page_size = self.fetch_size
                if remaining is not None:
                    page_size = min(page_size, remaining)

                try:
                    result = await session.execute(
                        statement, {"after_id": cursor, "page_size": page_size}
                    )
                except SQLAlchemyError as exc:
                    raise UpstreamUnavailableError(
                        f"The MSC source table {self.qualified_name} could not be read.",
                        details={"error": str(exc), "after_id": cursor},
                    ) from exc

                rows = [dict(row) for row in result.mappings().all()]
                if not rows:
                    return

                cursor = int(rows[-1]["id"])
                if remaining is not None:
                    remaining -= len(rows)
                log.debug(
                    "msc_source_page",
                    table=self.table,
                    rows=len(rows),
                    last_id=cursor,
                )
                yield SourcePage(rows=rows, last_id=cursor)

                # A short page means the table is exhausted; asking again would
                # be one wasted round trip per empty poll.
                if len(rows) < page_size:
                    return


async def probe() -> dict[str, Any]:
    """Check the source is reachable and report what is there.

    Used by the health endpoint and before a long batch, so a misconfigured
    source fails in a second with a clear message rather than halfway through a
    run with a connection error.
    """
    if not settings.msc_source_enabled:
        return {"enabled": False, "tables": []}

    tables: list[dict[str, Any]] = []
    for name in settings.msc_source_table_list:
        reader = MscSourceReader(name)
        try:
            tables.append(
                {"table": name, "reachable": True, "record_count": await reader.count()}
            )
        except UpstreamUnavailableError as exc:
            tables.append({"table": name, "reachable": False, "error": exc.message})
    return {
        "enabled": True,
        "host": settings.msc_source_host,
        "database": settings.msc_source_name,
        "schema": settings.msc_source_schema,
        "tables": tables,
    }
