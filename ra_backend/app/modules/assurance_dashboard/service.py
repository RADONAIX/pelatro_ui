"""Executive dashboard aggregates over the canonical rating model.

Every figure on the Rating Assurance dashboard comes from ONE table —
canonical_rating.rating_reconciliation in rafms_rating — read through the
existing ra-platform Postgres integration. Nothing is synthesised.

The only interpolated value in any statement is the schema name, taken from
settings and never from a request; everything else is bound.

Money is in the source's own currency and is NOT converted to crores. The
canonical table records single-transaction charges — the whole table sums to a
few hundred rupees — so a crore-scaled figure would round to zero and read as
an empty dashboard rather than a populated one.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core.config import settings
from app.integrations import ra_postgres

#: The one assurance backed by real data. Everything else has no equivalent
#: table, and the router refuses it rather than inventing an empty dashboard.
SUPPORTED_ASSURANCE = "rating"

#: Rows whose status means the charge reconciled cleanly. Everything else is an
#: exception — kept as a set so a new status is an exception by default rather
#: than silently counting as healthy.
MATCHED_STATUS = "MATCHED"

#: How many daily buckets the KPI sparklines carry.
_SPARK_DAYS = 14
#: The comparison window for each KPI's delta: this many days against the
#: preceding equal-length window.
_DELTA_DAYS = 7


def _table() -> str:
    # settings, never request input — see the module docstring.
    return f"{settings.ra_canonical_schema}.rating_reconciliation"


async def _query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return await ra_postgres.query_database(settings.ra_rating_pg_name, sql, params)


def _num(value: Any) -> float:
    """Postgres numerics arrive as Decimal, and None means no rows matched."""
    return float(value) if value is not None else 0.0


def _pct_delta(current: float, prior: float) -> float:
    """Percentage change, with the two degenerate cases made explicit.

    No prior activity is reported as 0 rather than +100%: a first day of data is
    not a hundred-percent improvement on anything, and the card would otherwise
    open every fresh deployment with a meaningless spike.
    """
    if prior == 0:
        return 0.0
    return round((current - prior) / prior * 100, 2)


def _kpi(
    value: str, *, delta: float, higher_is_better: bool, spark: list[float]
) -> dict[str, Any]:
    return {
        "value": value,
        "delta": delta,
        "higherIsBetter": higher_is_better,
        "spark": spark,
    }


async def _totals() -> dict[str, Any]:
    """Counts and money across the whole table."""
    rows = await _query(
        f"""
        SELECT
            count(*)                                                   AS records,
            count(*) FILTER (WHERE reconciliation_status = :matched)   AS matched,
            count(*) FILTER (WHERE reconciliation_status <> :matched)  AS exceptions,
            coalesce(sum(abs(charge_variance)), 0)                     AS abs_variance,
            coalesce(sum(expected_final_charge), 0)                    AS expected,
            coalesce(sum(actual_charge), 0)                            AS actual,
            max(expected_currency)                                     AS currency
        FROM {_table()}
        """,
        {"matched": MATCHED_STATUS},
    )
    return rows[0] if rows else {}


async def _window_totals(days: int, offset_days: int) -> dict[str, Any]:
    """The same counts over a window ending `offset_days` before the latest event.

    Anchored on max(event_time) rather than now(): the canonical table is loaded
    in batches, so "the last 7 days" of wall-clock time can easily contain no
    rows at all and would report every KPI as having collapsed to zero.
    """
    rows = await _query(
        f"""
        WITH bounds AS (SELECT max(event_time) AS latest FROM {_table()})
        SELECT
            count(*)                                                   AS records,
            count(*) FILTER (WHERE reconciliation_status = :matched)   AS matched,
            count(*) FILTER (WHERE reconciliation_status <> :matched)  AS exceptions,
            coalesce(sum(abs(charge_variance)), 0)                     AS abs_variance
        FROM {_table()}, bounds
        WHERE event_time >  bounds.latest - make_interval(days => :older)
          AND event_time <= bounds.latest - make_interval(days => :newer)
        """,
        {"matched": MATCHED_STATUS, "older": days + offset_days, "newer": offset_days},
    )
    return rows[0] if rows else {}


async def _daily() -> list[dict[str, Any]]:
    """One row per day that has events, oldest first."""
    return await _query(
        f"""
        SELECT
            event_time::date                                           AS day,
            count(*)                                                   AS records,
            count(*) FILTER (WHERE reconciliation_status = :matched)   AS matched,
            count(*) FILTER (WHERE reconciliation_status <> :matched)  AS exceptions,
            coalesce(sum(abs(charge_variance)), 0)                     AS abs_variance
        FROM {_table()}
        WHERE event_time IS NOT NULL
        GROUP BY event_time::date
        ORDER BY event_time::date
        """,
        {"matched": MATCHED_STATUS},
    )


async def _by(column: str, *, exceptions_only: bool) -> list[dict[str, Any]]:
    """Counts and leakage grouped by one whitelisted column.

    `column` is checked against a fixed set by the caller — it is a literal from
    this module, never a request value.
    """
    where = "WHERE reconciliation_status <> :matched" if exceptions_only else ""
    return await _query(
        f"""
        SELECT
            coalesce({column}::text, 'Unattributed')  AS name,
            count(*)                                  AS records,
            coalesce(sum(abs(charge_variance)), 0)    AS abs_variance
        FROM {_table()}
        {where}
        GROUP BY 1
        ORDER BY count(*) DESC
        """,
        {"matched": MATCHED_STATUS},
    )


async def _findings(limit: int = 5) -> list[dict[str, Any]]:
    """The individual events carrying the most money, worst first."""
    return await _query(
        f"""
        SELECT
            coalesce(base_rule_name, 'No matching tariff')  AS rule_name,
            coalesce(msisdn, 'unknown')                     AS msisdn,
            coalesce(destination_zone::text, 'Unzoned')     AS zone,
            reconciliation_status                           AS status,
            abs(coalesce(charge_variance, 0))               AS impact
        FROM {_table()}
        WHERE reconciliation_status <> :matched
        ORDER BY abs(coalesce(charge_variance, 0)) DESC
        LIMIT :limit
        """,
        {"matched": MATCHED_STATUS, "limit": limit},
    )


def _spark(daily: list[dict[str, Any]], key: str, digits: int = 2) -> list[float]:
    """The last N daily values for one measure, oldest first.

    Rounded like every other figure here. Summing these buckets will differ from
    the matching KPI by a fraction of a unit — the KPI totals at full precision
    in Postgres, these round per day — which is the ordinary cost of a rounded
    series and well below what a sparkline can show.
    """
    return [round(_num(row.get(key)), digits) for row in daily[-_SPARK_DAYS:]]


def _title(day: date | None) -> str:
    return day.strftime("%d %b") if isinstance(day, date) else ""


async def build_dashboard() -> dict[str, Any]:
    """The whole dashboard payload for Rating Assurance."""
    totals = await _totals()
    daily = await _daily()
    recent = await _window_totals(_DELTA_DAYS, 0)
    prior = await _window_totals(_DELTA_DAYS, _DELTA_DAYS)

    records = _num(totals.get("records"))
    matched = _num(totals.get("matched"))
    exceptions = _num(totals.get("exceptions"))
    abs_variance = _num(totals.get("abs_variance"))
    currency = (totals.get("currency") or "INR").strip()

    # Percentages are of the evaluated population, so an empty table reports
    # zero rather than dividing by it.
    exception_rate = round(exceptions / records * 100, 2) if records else 0.0
    reconciliation = round(matched / records * 100, 2) if records else 0.0

    r_records, r_exceptions = _num(recent.get("records")), _num(recent.get("exceptions"))
    p_records, p_exceptions = _num(prior.get("records")), _num(prior.get("exceptions"))
    r_rate = (r_exceptions / r_records * 100) if r_records else 0.0
    p_rate = (p_exceptions / p_records * 100) if p_records else 0.0
    r_recon = ((r_records - r_exceptions) / r_records * 100) if r_records else 0.0
    p_recon = ((p_records - p_exceptions) / p_records * 100) if p_records else 0.0

    status_rows = await _by("reconciliation_status", exceptions_only=True)
    rule_rows = await _by("base_rule_name", exceptions_only=True)
    zone_rows = await _by("destination_zone", exceptions_only=False)
    segment_rows = await _by("account_type", exceptions_only=False)

    span = ""
    if daily:
        span = f"{_title(daily[0]['day'])} – {_title(daily[-1]['day'])}"

    return {
        "id": SUPPORTED_ASSURANCE,
        "name": "Rating Assurance",
        "subtitle": (
            f"{int(records):,} rated events reconciled against expected charges"
            + (f" · {span}" if span else "")
        ),
        "recordUnit": "Events",
        "currency": currency,
        "kpis": {
            "revenueAtRisk": _kpi(
                f"{abs_variance:,.2f}",
                delta=_pct_delta(_num(recent.get("abs_variance")), _num(prior.get("abs_variance"))),
                higher_is_better=False,
                spark=_spark(daily, "abs_variance"),
            ),
            "recordsEvaluated": _kpi(
                f"{int(records):,}",
                delta=_pct_delta(r_records, p_records),
                higher_is_better=True,
                spark=_spark(daily, "records"),
            ),
            "exceptions": _kpi(
                f"{int(exceptions):,}",
                delta=_pct_delta(r_exceptions, p_exceptions),
                higher_is_better=False,
                spark=_spark(daily, "exceptions"),
            ),
            "exceptionRate": _kpi(
                f"{exception_rate:.2f}%",
                delta=_pct_delta(r_rate, p_rate),
                higher_is_better=False,
                spark=[
                    round(_num(d.get("exceptions")) / _num(d.get("records")) * 100, 2)
                    if _num(d.get("records"))
                    else 0.0
                    for d in daily[-_SPARK_DAYS:]
                ],
            ),
            "reconciliation": _kpi(
                f"{reconciliation:.2f}%",
                delta=_pct_delta(r_recon, p_recon),
                higher_is_better=True,
                spark=[
                    round(_num(d.get("matched")) / _num(d.get("records")) * 100, 2)
                    if _num(d.get("records"))
                    else 0.0
                    for d in daily[-_SPARK_DAYS:]
                ],
            ),
        },
        "trendTitle": "Reconciliation Volume",
        "trend": [
            {
                "label": _title(row["day"]),
                "healthy": _num(row.get("matched")),
                "exceptions": _num(row.get("exceptions")),
                "leakage": round(_num(row.get("abs_variance")), 2),
            }
            for row in daily
        ],
        "revenueAtRisk": [
            {"label": _title(row["day"]), "value": round(_num(row.get("abs_variance")), 2)}
            for row in daily
        ],
        "exceptionCategories": [
            {"name": row["name"], "value": _num(row.get("records"))} for row in status_rows
        ],
        "entitiesTitle": "Exceptions by Rule",
        "entities": [
            {"name": row["name"], "value": _num(row.get("records"))} for row in rule_rows
        ],
        # Sorted by money here rather than in SQL: _by orders by volume, which is
        # the right order for the count-based panels above but the wrong one for
        # a leakage ranking.
        "leakageCategories": sorted(
            ({"name": r["name"], "value": round(_num(r.get("abs_variance")), 2)} for r in zone_rows),
            key=lambda item: item["value"],
            reverse=True,
        ),
        "businessSegments": [
            {"name": row["name"], "value": _num(row.get("records"))} for row in segment_rows
        ],
        "findings": [
            {
                "finding": f"{row['rule_name']} — {row['status'].replace('_', ' ').title()}",
                "source": f"{row['msisdn']} · {row['zone']}",
                "category": row["status"].replace("_", " ").title(),
                "impactCr": round(_num(row.get("impact")), 2),
            }
            for row in await _findings()
        ],
    }
