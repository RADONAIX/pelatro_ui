"""Per-connection query grouping for consolidated vs split-DB reporting.

A consolidated report UNIONs every stream in one query on one connection. When
streams live on different Postgres servers (split-DB mode, ``STREAM_{KEY}_PG_*``),
that single UNION can't span servers, so the report is executed once per
*connection group* and the results merged.

This module is the connection layer: it maps a stream to its ``PgTarget`` (its
own override or the shared ra_pg/bi_pg) and groups a report's streams by target.
The SQL for each group is assembled by the caller (it owns the per-stream arms).
Both the async API path and the sync export worker import ``PgTarget`` so they
resolve the same connection two ways (asyncpg vs psycopg2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.data_sources import DataStream
from app.integrations import pg_engines


@dataclass(frozen=True)
class PgTarget:
    """A resolved Postgres connection (host/db/creds), engine-agnostic so both
    the async and sync sides can build their own engine from it."""

    host: str
    port: int
    dbname: str
    user: str
    password: str

    def key(self) -> str:
        return f"{self.host}:{self.port}/{self.dbname}:{self.user}"

    def async_url(self) -> str:
        return pg_engines.async_url(self.host, self.port, self.dbname, self.user, self.password)

    def sync_url(self) -> str:
        return pg_engines.sync_url(self.host, self.port, self.dbname, self.user, self.password)


def _shared_target(kind: str) -> PgTarget:
    dbname = settings.ra_pg_name if kind == "ra" else settings.ra_bi_pg_name
    return PgTarget(
        host=settings.ra_pg_host, port=settings.ra_pg_port, dbname=dbname,
        user=settings.ra_pg_user, password=settings.ra_pg_password,
    )


def pg_target(stream: DataStream, kind: str) -> PgTarget:
    """The Postgres target for a stream's ``kind`` tables ('ra' = pipeline logs,
    'bi' = BI-reports matviews): its own override if set, else the shared conn."""
    if stream.pg is None:
        return _shared_target(kind)
    dbname = stream.pg.name if kind == "ra" else stream.pg.bi_name
    return PgTarget(
        host=stream.pg.host, port=stream.pg.port, dbname=dbname,
        user=stream.pg.user, password=stream.pg.password,
    )


@dataclass
class QueryGroup:
    """One report execution unit: the SQL for a set of streams that share a
    connection. ``target`` is None for ClickHouse (uses the CH client)."""

    source: str  # 'ra_pg' | 'bi_pg' | 'clickhouse'
    target: PgTarget | None
    sql: str
    params: dict[str, Any] = field(default_factory=dict)
    streams: tuple[str, ...] = ()  # stream keys in this group (for logging)


_KIND_FOR_SOURCE = {"ra_pg": "ra", "bi_pg": "bi"}


def group_streams(streams: list[DataStream], source: str) -> list[list[DataStream]]:
    """Partition a report's streams by the Postgres connection they resolve to,
    preserving order. ClickHouse never splits (single server) → one group.

    Consolidated mode → one group (every stream shares the target); split mode →
    one group per distinct target."""
    if source not in _KIND_FOR_SOURCE:
        return [list(streams)]
    kind = _KIND_FOR_SOURCE[source]
    groups: dict[str, list[DataStream]] = {}
    order: list[str] = []
    for s in streams:
        k = pg_target(s, kind).key()
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(s)
    return [groups[k] for k in order]


def target_for_group(group_streams: list[DataStream], source: str) -> PgTarget | None:
    """The connection every stream in a group shares (None for ClickHouse)."""
    kind = _KIND_FOR_SOURCE.get(source)
    if kind is None:
        return None
    return pg_target(group_streams[0], kind)
