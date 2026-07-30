"""Reporting business logic: a registry-driven RA report catalog.

Each report is a spec with a live count query and a drill-down query against a
pre-computed source (BI Postgres matviews in ``rafms.bi_reports`` or ClickHouse
matviews in ``rafms``). The catalog badge shows the true total count; the
drill-down returns up to DETAIL_LIMIT rows; export returns the full set as CSV.
"""

from __future__ import annotations

import asyncio
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.core.config import settings
from app.core.errors import UpstreamUnavailableError
from app.core.logging import get_logger
from app.integrations import clickhouse, pg_engines
from app.modules.reporting import schemas, sources
from app.modules.reporting.catalog import build_reports
from app.modules.reporting.sources import QueryGroup

log = get_logger("reporting")

_UNION = " UNION ALL "

# Row caps now live in settings (env-overridable: REPORTS_DETAIL_LIMIT /
# REPORTS_EXPORT_LIMIT). Read them per use rather than at import so an env
# override is always honoured.

# Report registry. `{schema}` is filled from settings.ra_bi_pg_schema (BI PG).
# detail_sql is the base SELECT (with ORDER BY, NO limit); the limit is applied
# per use (DETAIL_LIMIT for drill-down, EXPORT_LIMIT for CSV).
# `date_column` (a column in detail_sql's output) enables bulk-export date-range
# filtering + KPIs (exports module). `kpi_agg` is the aggregate projection used
# for the KPI preview, evaluated over the (date-filtered) export rows.
REPORTS: list[dict[str, Any]] = build_reports()
_BY_KEY = {r["key"]: r for r in REPORTS}


def report_columns(key: str) -> list[str]:
    """Whitelisted output columns for a report (for filtered export). Empty when
    unknown — callers then apply date-range only."""
    r = _BY_KEY.get(key)
    return list(r.get("columns") or []) if r else []


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


async def _run_pg(target: sources.PgTarget, sql: str, params: dict[str, Any] | None):
    if not settings.ra_pg_enabled:
        raise UpstreamUnavailableError("ra-platform Postgres integration is disabled.")
    engine = pg_engines.async_engine(target.async_url())
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(sql), params or {})
            return [dict(row) for row in result.mappings().all()]
    except UpstreamUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("pg_query_failed", host=target.host, db=target.dbname, error=str(exc))
        raise UpstreamUnavailableError(
            "Postgres query failed.", details={"reason": str(exc)}
        ) from exc


async def _run_group(g: QueryGroup) -> list[dict[str, Any]]:
    """Execute one query group on its connection (ClickHouse client, or the
    shared / per-stream Postgres engine)."""
    if g.source == "clickhouse":
        return await clickhouse.query(g.sql, g.params)
    if g.target is None:
        raise UpstreamUnavailableError(f"Unknown report source: {g.source}")
    return await _run_pg(g.target, g.sql, g.params)


async def _run_group_timed(g: QueryGroup, timeout_s: float) -> list[dict[str, Any]] | None:
    """Run a group but give up after ``timeout_s`` (or if the source is down)."""
    try:
        return await asyncio.wait_for(_run_group(g), timeout_s)
    except (TimeoutError, UpstreamUnavailableError):
        return None


# --- Bulk-export query builders (shared by the async API + the sync worker) --
# Pure string builders (no I/O), so both the async endpoints and the sync Celery
# worker can execute the returned (source, sql, params). date_to is EXCLUSIVE.
def get_report(key: str) -> dict[str, Any] | None:
    return _BY_KEY.get(key)


def _date_predicate(source: str, date_column: str) -> str:
    col = f"_e.{date_column}"
    if source == "clickhouse":
        return f"toDate({col}) >= {{date_from:Date}} AND toDate({col}) < {{date_to:Date}}"
    return f"{col}::date >= :date_from AND {col}::date < :date_to"


def _filter_predicates(
    source: str,
    cols: list[str],
    categories: dict[str, list[str]] | None,
    search: str | None,
    params: dict[str, Any],
) -> list[str]:
    """Build WHERE predicates for category IN-lists + a global search substring,
    binding all values into ``params``. Category columns not in ``cols`` are
    ignored (they were whitelist-validated at job creation)."""
    preds: list[str] = []
    ci = 0
    for col, vals in (categories or {}).items():
        if col not in cols or not vals:
            continue
        names = []
        for vi, v in enumerate(vals):
            p = f"c{ci}_{vi}"
            params[p] = v
            names.append(f"{{{p}:String}}" if source == "clickhouse" else f":{p}")
        preds.append(f'_f."{col}" IN ({", ".join(names)})')
        ci += 1
    if search and search.strip():
        params["q"] = f"%{search.strip()}%"
        if source == "clickhouse":
            terms = [f'toString(_f."{c}") ILIKE {{q:String}}' for c in cols]
        else:
            terms = [f'CAST(_f."{c}" AS TEXT) ILIKE :q' for c in cols]
        preds.append("(" + " OR ".join(terms) + ")")
    return preds


def _wrap_filters(
    source: str,
    report: dict[str, Any],
    base: str,
    *,
    date_from: Any,
    date_to: Any,
    categories: dict[str, list[str]] | None,
    search: str | None,
) -> tuple[str, dict[str, Any]]:
    """Wrap a base subquery with the date-range predicate then the category /
    search predicates — identical shape to the old single-connection export
    query, so a consolidated (single-group) report builds the exact same SQL."""
    params: dict[str, Any] = {}
    date_col = report.get("date_column")
    if date_from is not None and date_to is not None and date_col:
        pred = _date_predicate(source, date_col)
        base = f"SELECT * FROM ({base}) AS _e WHERE {pred}"
        params = {"date_from": date_from, "date_to": date_to}
    cols = report.get("columns") or []
    if cols:
        preds = _filter_predicates(source, cols, categories, search, params)
        if preds:
            base = f"SELECT * FROM ({base}) AS _f WHERE {' AND '.join(preds)}"
    return base, params


def _group_bases(
    report: dict[str, Any],
) -> list[tuple[sources.PgTarget | None, tuple[str, ...], str]]:
    """Per connection-group (target, stream keys, base detail SQL). One group in
    consolidated mode (uses the report's pre-joined detail_sql); one per distinct
    connection in split mode (UNION of that group's per-stream detail arms).
    ClickHouse / stream-less reports → a single group with no PgTarget."""
    source = report["source"]
    detail_sql = report["detail_sql"].format(schema=settings.ra_bi_pg_schema)
    stream_objs = report.get("stream_objs") or []
    if not stream_objs:
        return [(None, (), detail_sql)]
    groups = sources.group_streams(stream_objs, source)
    if len(groups) == 1:
        grp = groups[0]
        return [(sources.target_for_group(grp, source), tuple(s.key for s in grp), detail_sql)]
    arms = report["detail_arms"]
    return [
        (
            sources.target_for_group(grp, source),
            tuple(s.key for s in grp),
            f"SELECT * FROM ({_UNION.join(arms[s.key] for s in grp)}) AS _u",
        )
        for grp in groups
    ]


def export_groups(
    key: str,
    *,
    date_from: Any = None,
    date_to: Any = None,
    categories: dict[str, list[str]] | None = None,
    search: str | None = None,
) -> list[QueryGroup]:
    """The filtered export query, one QueryGroup per connection. In consolidated
    mode this is a single group (identical to the old single UNION query); in
    split mode, one group per server whose rows the caller concatenates/merges."""
    report = _BY_KEY[key]
    source = report["source"]
    out: list[QueryGroup] = []
    for target, keys, base in _group_bases(report):
        sql, params = _wrap_filters(
            source, report, base,
            date_from=date_from, date_to=date_to, categories=categories, search=search,
        )
        out.append(QueryGroup(source=source, target=target, sql=sql, params=params, streams=keys))
    return out


def count_groups(key: str, **kw: Any) -> list[QueryGroup]:
    return [
        QueryGroup(g.source, g.target, f"SELECT count(*) AS n FROM ({g.sql}) AS _c",
                   g.params, g.streams)
        for g in export_groups(key, **kw)
    ]


def kpi_groups(key: str, **kw: Any) -> list[QueryGroup]:
    agg = _BY_KEY[key].get("kpi_agg") or "count(*) AS rows"
    return [
        QueryGroup(g.source, g.target, f"SELECT {agg} FROM ({g.sql}) AS _k", g.params, g.streams)
        for g in export_groups(key, **kw)
    ]


# --- Sizing / planning contract (used by exports.planning) -----------------
# All queries here are TIMEOUT-GUARDED: a timeout returns None ("too big/slow to
# size cheaply"), never a stall. Exact on ClickHouse, best-effort on Postgres.
@dataclass(frozen=True)
class ReportMeta:
    source: str
    date_column: str | None
    known_columns: list[str]
    available: bool


def report_meta(key: str) -> ReportMeta:
    r = _BY_KEY.get(key) or {}
    return ReportMeta(
        source=r.get("source", ""),
        date_column=r.get("date_column"),
        known_columns=list(r.get("columns") or []),
        available=bool(r.get("available")),
    )


def _sort_by_date_desc(rows: list[dict[str, Any]], date_col: str) -> None:
    """Sort merged split-group rows newest-first (Nones last). In-place."""
    rows.sort(key=lambda r: (r.get(date_col) is not None, r.get(date_col)), reverse=True)


async def bounded_count(
    key: str,
    *,
    date_from: Any = None,
    date_to: Any = None,
    categories: dict[str, list[str]] | None = None,
    search: str | None = None,
    timeout_s: float,
) -> int | None:
    """Exact row count for the filtered export (summed across connection groups),
    or None if any group can't be sized in time (Postgres scan too slow / down)."""
    total = 0
    for g in count_groups(
        key, date_from=date_from, date_to=date_to, categories=categories, search=search
    ):
        rows = await _run_group_timed(g, timeout_s)
        if rows is None:
            return None
        total += int(rows[0].get("n") or 0) if rows else 0
    return total


async def date_histogram(
    key: str,
    *,
    date_column: str,
    categories: dict[str, list[str]] | None = None,
    search: str | None = None,
    window: tuple[Any, Any] = (None, None),
    timeout_s: float,
) -> list[tuple[date, int]] | None:
    """Rows per calendar day over the *filtered* set (only days with data),
    merged across connection groups. Honours categories + search + an optional
    [from, to) window. None if any group times out."""
    win_from, win_to = window
    merged: dict[date, int] = {}
    for g in export_groups(
        key, date_from=win_from, date_to=win_to, categories=categories, search=search
    ):
        if g.source == "clickhouse":
            dexpr = f'toDate(_h."{date_column}")'
            hsql = f"SELECT {dexpr} AS d, count() AS c FROM ({g.sql}) AS _h GROUP BY d ORDER BY d"
        else:
            dexpr = f'_h."{date_column}"::date'
            hsql = (f"SELECT {dexpr} AS d, count(*) AS c FROM ({g.sql}) AS _h "
                    f"GROUP BY {dexpr} ORDER BY 1")
        rows = await _run_group_timed(
            QueryGroup(g.source, g.target, hsql, g.params), timeout_s
        )
        if rows is None:
            return None
        for r in rows:
            d = r.get("d")
            if d is None:
                continue  # NULL date bucket — can't place on a calendar day (skip it)
            if isinstance(d, datetime):
                d = d.date()
            elif not isinstance(d, date):
                d = date.fromisoformat(str(d)[:10])
            merged[d] = merged.get(d, 0) + int(r.get("c") or 0)
    return sorted(merged.items())


async def report_kpis(
    key: str,
    *,
    date_from: Any = None,
    date_to: Any = None,
    categories: dict[str, list[str]] | None = None,
    search: str | None = None,
) -> dict[str, Any] | None:
    """Aggregate KPI preview over the selected data (no row dump), summed across
    connection groups (every kpi_agg is additive: count / sum / countIf). None if
    the report is unknown/unavailable or a source is unreachable."""
    report = _BY_KEY.get(key)
    if report is None or not report.get("available"):
        return None
    merged: dict[str, Any] = {}
    try:
        for g in kpi_groups(
            key, date_from=date_from, date_to=date_to, categories=categories, search=search
        ):
            rows = await _run_group(g)
            if not rows:
                continue
            for k, v in rows[0].items():
                if isinstance(v, bool):
                    merged.setdefault(k, v)
                elif isinstance(v, int | float | Decimal):
                    merged[k] = (merged.get(k) or 0) + v
                else:
                    merged.setdefault(k, v)
    except UpstreamUnavailableError:
        log.info("report_kpis_unavailable", key=key)
        return None
    return {k: _jsonable(v) for k, v in merged.items()}


async def _count(report: dict[str, Any]) -> int:
    """Unfiltered badge count, summed across connection groups. Uses the report's
    light count_sql (SELECT 1 / SELECT count) rather than the full projection."""
    source = report["source"]
    stream_objs = report.get("stream_objs") or []
    schema = settings.ra_bi_pg_schema
    if not stream_objs:
        rows = await _run_group(
            QueryGroup(source, None, report["count_sql"].format(schema=schema), {})
        )
        return int(rows[0].get("n") or 0) if rows else 0
    groups = sources.group_streams(stream_objs, source)
    if len(groups) == 1:
        target = sources.target_for_group(groups[0], source)
        rows = await _run_group(
            QueryGroup(source, target, report["count_sql"].format(schema=schema), {})
        )
        return int(rows[0].get("n") or 0) if rows else 0
    count_arms = report["count_arms"]
    total = 0
    for grp in groups:
        sql = f"SELECT count(*) AS n FROM ({_UNION.join(count_arms[s.key] for s in grp)}) AS _c"
        rows = await _run_group(QueryGroup(source, sources.target_for_group(grp, source), sql, {}))
        total += int(rows[0].get("n") or 0) if rows else 0
    return total


async def _detail_rows(report: dict[str, Any], limit: int) -> tuple[list[str], list[list]]:
    """Up to ``limit`` rows for the sync CSV export, ordered by the report's
    order_by, merged across connection groups (split mode)."""
    order_by = report.get("order_by")
    order = f" ORDER BY {order_by}" if order_by else ""
    date_col = report.get("date_column")
    bases = _group_bases(report)
    all_rows: list[dict[str, Any]] = []
    columns: list[str] = []
    for target, _keys, base in bases:
        rows = await _run_group(
            QueryGroup(report["source"], target, f"{base}{order} LIMIT {int(limit)}", {})
        )
        if rows and not columns:
            columns = list(rows[0].keys())
        all_rows.extend(rows)
    if not columns:
        return [], []
    if len(bases) > 1 and date_col and date_col in columns:
        _sort_by_date_desc(all_rows, date_col)
        all_rows = all_rows[: int(limit)]
    data = [[_jsonable(row.get(c)) for c in columns] for row in all_rows]
    return columns, data


async def _preview_rows(
    report: dict[str, Any],
    limit: int,
    *,
    date_from: Any = None,
    date_to: Any = None,
    categories: dict[str, list[str]] | None = None,
    search: str | None = None,
) -> tuple[list[str], list[list]]:
    """The LAST ``limit`` rows (newest first) of the optionally-filtered report.

    Reuses ``export_groups`` so the on-screen preview and the bulk export apply
    the exact same filters — a filter that shows N rows here exports the same N
    rows. Ordering by ``date_column`` DESC + LIMIT is a memory-bounded top-N (safe
    even on 100M+ row ClickHouse sets). In split mode each connection returns its
    own top-N; the merged rows are re-sorted newest-first and capped."""
    date_col = report.get("date_column")
    if date_col:
        order = f'ORDER BY _p."{date_col}" DESC'
    elif report.get("order_by"):
        order = f"ORDER BY {report['order_by']}"
    else:
        order = ""
    groups = export_groups(
        report["key"], date_from=date_from, date_to=date_to,
        categories=categories, search=search,
    )
    all_rows: list[dict[str, Any]] = []
    columns: list[str] = []
    for g in groups:
        sql = f"SELECT * FROM ({g.sql}) AS _p {order} LIMIT {int(limit)}"
        rows = await _run_group(QueryGroup(g.source, g.target, sql, g.params))
        if rows and not columns:
            columns = list(rows[0].keys())
        all_rows.extend(rows)
    if not columns:
        return [], []
    if len(groups) > 1 and date_col and date_col in columns:
        _sort_by_date_desc(all_rows, date_col)
        all_rows = all_rows[: int(limit)]
    data = [[_jsonable(row.get(c)) for c in columns] for row in all_rows]
    return columns, data


async def report_detail(
    key: str,
    *,
    date_from: Any = None,
    date_to: Any = None,
    categories: dict[str, list[str]] | None = None,
    search: str | None = None,
) -> schemas.ReportDetail:
    report = _BY_KEY.get(key)
    if report is None or not report.get("available"):
        title = report["title"] if report else key
        return schemas.ReportDetail(key=key, title=title, count=None, columns=[], rows=[])
    # The user's dateTo is inclusive; the query upper bound is exclusive (matches
    # the export worker's _selection), so shift it a day forward.
    date_to_excl = (date_to + timedelta(days=1)) if date_to else None
    filtered = bool(date_from or date_to or categories or (search and search.strip()))
    try:
        # Drill-down = the last reports_detail_limit rows of the filtered set. When
        # filters are applied the count reflects the filtered total (timeout-guarded
        # on Postgres → None if it can't be sized in time); unfiltered, the fast
        # pre-aggregated badge count is used.
        columns, rows = await _preview_rows(
            report, settings.reports_detail_limit,
            date_from=date_from, date_to=date_to_excl,
            categories=categories, search=search,
        )
        if filtered:
            count = await bounded_count(
                key, date_from=date_from, date_to=date_to_excl,
                categories=categories, search=search,
                timeout_s=settings.export_count_timeout_seconds,
            )
        else:
            count = await _count(report)
    except UpstreamUnavailableError as exc:
        reason = str((getattr(exc, "details", None) or {}).get("reason", ""))
        too_big = "MEMORY_LIMIT_EXCEEDED" in reason or "code: 241" in reason
        note = (
            "This report is too large to preview interactively right now — narrow "
            "the date range or filters, or export it for the full set."
            if too_big else
            "The report source is currently unavailable. Try again shortly."
        )
        log.info("report_detail_unavailable", key=key, too_big=too_big)
        return schemas.ReportDetail(
            key=key, title=report["title"], count=None, columns=[], rows=[], note=note
        )
    return schemas.ReportDetail(
        key=key, title=report["title"], count=count, columns=columns, rows=rows
    )


async def report_export_csv(key: str) -> tuple[str, str]:
    """Return (filename, csv_text) for the full (uncapped) report set."""
    report = _BY_KEY.get(key)
    if report is None or not report.get("available"):
        raise UpstreamUnavailableError("Report is not available for export.")
    columns, rows = await _detail_rows(report, settings.reports_export_limit)
    buf = io.StringIO()
    writer = csv.writer(buf)
    if columns:
        writer.writerow(columns)
        writer.writerows(rows)
    return f"{key}.csv", buf.getvalue()
