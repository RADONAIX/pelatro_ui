"""Usage Assurance dashboard, computed from assurance.voice_sms_match_report.

Every figure on that dashboard comes from this one table — the MSC-versus-IN
match report for voice and SMS. Nothing is generated, and nothing is blended in
from another source, so what the screen shows can always be traced back to a
row here.

All aggregation happens in SQL. The table is small today, but the shape of the
queries is what would let it stay affordable if it were not: one grouped pass
per panel, no row ever crossing into the API.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations import ra_postgres

log = get_logger("dashboards.usage")

#: The single source for this dashboard.
SOURCE_TABLE = "assurance.voice_sms_match_report"

#: The status a row carries when MSC and IN agree. Everything else is a miss.
MATCHED = "MATCHED"

#: debit_amount is stored as text; this is the cast used everywhere so a blank
#: or malformed value counts as zero rather than failing the whole query.
_AMOUNT = "coalesce(nullif(btrim(debit_amount), '')::numeric, 0)"


async def _query(sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    return await ra_postgres.query_database(settings.app_rules_db_name, sql, params)


async def headline() -> dict[str, Any]:
    """The five KPI figures, in one pass over the table."""
    rows = await _query(
        f"""
        SELECT count(*)                                              AS evaluated,
               count(*) FILTER (WHERE status = '{MATCHED}')          AS matched,
               count(*) FILTER (WHERE status <> '{MATCHED}')         AS unmatched,
               coalesce(sum({_AMOUNT}) FILTER (WHERE status = '{MATCHED}'), 0)  AS debit_matched,
               coalesce(sum({_AMOUNT}), 0)                           AS debit_total,
               count(DISTINCT service_type)                          AS services,
               min(created_at)                                       AS first_seen,
               max(created_at)                                       AS last_seen
        FROM {SOURCE_TABLE}
        """
    )
    row = rows[0]
    evaluated = int(row["evaluated"] or 0)
    matched = int(row["matched"] or 0)
    unmatched = int(row["unmatched"] or 0)
    return {
        "evaluated": evaluated,
        "matched": matched,
        "unmatched": unmatched,
        # Percentages are derived here rather than in the browser so every
        # consumer of this endpoint reports the same number.
        "matchRatePct": round(matched * 100.0 / evaluated, 2) if evaluated else 0.0,
        "unmatchedRatePct": round(unmatched * 100.0 / evaluated, 2) if evaluated else 0.0,
        "debitMatched": float(row["debit_matched"] or 0),
        "debitTotal": float(row["debit_total"] or 0),
        "services": int(row["services"] or 0),
        "firstSeen": row["first_seen"],
        "lastSeen": row["last_seen"],
    }


async def by_service() -> list[dict[str, Any]]:
    """Matched vs unmatched per service type — the dashboard's main chart."""
    rows = await _query(
        f"""
        SELECT service_type,
               count(*)                                      AS evaluated,
               count(*) FILTER (WHERE status = '{MATCHED}')   AS matched,
               count(*) FILTER (WHERE status <> '{MATCHED}')  AS unmatched,
               coalesce(sum({_AMOUNT}), 0)                    AS debit
        FROM {SOURCE_TABLE}
        GROUP BY service_type
        ORDER BY count(*) DESC
        """
    )
    return [
        {
            "service": r["service_type"],
            "evaluated": int(r["evaluated"]),
            "matched": int(r["matched"]),
            "unmatched": int(r["unmatched"]),
            "debit": float(r["debit"] or 0),
            "matchRatePct": round(int(r["matched"]) * 100.0 / int(r["evaluated"]), 2)
            if int(r["evaluated"])
            else 0.0,
        }
        for r in rows
    ]


async def status_split() -> list[dict[str, Any]]:
    rows = await _query(
        f"""
        SELECT status, count(*) AS n, coalesce(sum({_AMOUNT}), 0) AS debit
        FROM {SOURCE_TABLE}
        GROUP BY status
        ORDER BY count(*) DESC
        """
    )
    return [
        {"status": r["status"], "count": int(r["n"]), "debit": float(r["debit"] or 0)}
        for r in rows
    ]


async def top_numbers(*, side: str = "calling", limit: int = 5) -> list[dict[str, Any]]:
    """The subscribers with the most unmatched records.

    Which side is looked at is a fixed choice, not a free string — the column
    name is interpolated, so it must never come from a request unchecked.
    """
    column = "msc_calling_number" if side == "calling" else "msc_called_number"
    rows = await _query(
        f"""
        SELECT {column} AS number, count(*) AS n
        FROM {SOURCE_TABLE}
        WHERE status <> '{MATCHED}' AND {column} IS NOT NULL AND btrim({column}) <> ''
        GROUP BY 1
        ORDER BY count(*) DESC
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return [{"number": r["number"], "unmatched": int(r["n"])} for r in rows]


async def daily_trend() -> list[dict[str, Any]]:
    """Matched vs unmatched per day.

    One row per day the table actually holds. A single load produces a single
    point — the UI says so rather than drawing a line through one value and
    implying a trend that was never measured.
    """
    rows = await _query(
        f"""
        SELECT created_at::date                              AS day,
               count(*)                                      AS evaluated,
               count(*) FILTER (WHERE status = '{MATCHED}')   AS matched,
               count(*) FILTER (WHERE status <> '{MATCHED}')  AS unmatched,
               coalesce(sum({_AMOUNT}), 0)                    AS debit
        FROM {SOURCE_TABLE}
        GROUP BY 1
        ORDER BY 1
        """
    )
    return [
        {
            "day": str(r["day"]),
            "evaluated": int(r["evaluated"]),
            "matched": int(r["matched"]),
            "unmatched": int(r["unmatched"]),
            "debit": float(r["debit"] or 0),
        }
        for r in rows
    ]


async def findings(limit: int = 5) -> list[dict[str, Any]]:
    """Unmatched records, the ones the screen names individually.

    Ordered by debit first so anything carrying money surfaces above the rest;
    unmatched rows generally carry none, which is itself the finding.
    """
    rows = await _query(
        f"""
        SELECT id, service_type, msc_calling_number, in_calling_number,
               msc_called_number, in_called_number, {_AMOUNT} AS debit, status, created_at
        FROM {SOURCE_TABLE}
        WHERE status <> '{MATCHED}'
        ORDER BY {_AMOUNT} DESC, id
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return [
        {
            "id": int(r["id"]),
            "service": r["service_type"],
            "mscCalling": r["msc_calling_number"],
            "inCalling": r["in_calling_number"],
            "mscCalled": r["msc_called_number"],
            "inCalled": r["in_called_number"],
            "debit": float(r["debit"] or 0),
            "status": r["status"],
            "createdAt": r["created_at"],
            # What actually differs, so the row explains itself.
            "gap": _gap(r),
        }
        for r in rows
    ]


def _gap(row: dict[str, Any]) -> str:
    """Which side is missing or disagrees, in words."""
    missing_in = not (row["in_calling_number"] or "").strip()
    missing_msc = not (row["msc_calling_number"] or "").strip()
    if missing_in and not missing_msc:
        return "Present in MSC, absent from IN"
    if missing_msc and not missing_in:
        return "Present in IN, absent from MSC"
    if (row["msc_called_number"] or "") != (row["in_called_number"] or ""):
        return "Called number differs"
    if (row["msc_calling_number"] or "") != (row["in_calling_number"] or ""):
        return "Calling number differs"
    return "No matching counterpart"


async def dashboard() -> dict[str, Any]:
    """Everything the Usage dashboard renders, from the one table."""
    return {
        "source": SOURCE_TABLE,
        "headline": await headline(),
        "byService": await by_service(),
        "statusSplit": await status_split(),
        "topCalling": await top_numbers(side="calling"),
        "topCalled": await top_numbers(side="called"),
        "daily": await daily_trend(),
        "findings": await findings(),
    }
