"""Executive dashboard aggregates for Usage Assurance.

Emits the SAME payload as the Rating builder next door, so Usage renders through
the identical six charts rather than a lookalike of its own. Only the sources
change, and there are two, both in rafms_rating:

    assurance.voice_sms_match_report        one row per reconciled voice or SMS
                                            record — every count on the screen
    assurance.assurance_leakage_monthly     monthly leakage per assurance — every
                                            money figure on the screen

Money comes from the second table because it cannot come from the first: the
match report carries debit_amount only on rows that MATCHED, so the exposure
behind an exception has no value there. Only that table's MISMATCH rows count as
at risk; its MATCH rows are money that reconciled.

Where the two assurances differ is what each dimension means:

    exception category   Rating has OVERCHARGED / UNDERCHARGED / NO_MATCHING_
                         TARIFF in a column. This table has no such column, so
                         the category is DERIVED from which side of the record
                         actually disagrees — see _GAP_CATEGORY.
    entities             Rating attributes an exception to the tariff rule that
                         produced it. Nothing here records a rule, so exceptions
                         are attributed to the calling subscriber, which is the
                         only actor this table names.
    leakage categories   Rating values leakage per destination zone. Usage has
                         one leakage stream, msc_vs_in, so that panel ranks the
                         streams the leakage table actually records.

Only settings-derived names are interpolated into SQL; every request-shaped
value is bound.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core.config import settings
from app.integrations import ra_postgres

#: This builder's assurance id.
ASSURANCE = "usage"

#: The record-level source: one row per reconciled voice or SMS record.
_TABLE = "assurance.voice_sms_match_report"

#: The money source, by month.
#:
#: The match report carries debit_amount only on rows that MATCHED, so the
#: exposure behind an exception cannot be valued from it. This table holds the
#: monthly leakage per assurance, and its MISMATCH rows are what "revenue at
#: risk" means here — MATCH rows are reconciled money and are not at risk.
_LEAKAGE_TABLE = "assurance.assurance_leakage_monthly"

#: This assurance's key in the leakage table.
_LEAKAGE_TYPE = "usage_assurance"

#: The leakage status carrying money at risk. The table's check constraint
#: allows MATCH and MISMATCH only.
_LEAKAGE_AT_RISK = "MISMATCH"

#: The status meaning MSC and IN agree. Everything else is an exception, so a
#: status added later counts as an exception rather than silently as healthy.
MATCHED_STATUS = "MATCHED"

#: debit_amount is stored as text. This cast treats blank or malformed values as
#: zero rather than failing the statement.
_MONEY = "coalesce(nullif(btrim(debit_amount), '')::numeric, 0)"

#: Which side of the record disagrees. This is the Usage equivalent of Rating's
#: reconciliation_status detail: the table records only MATCHED/UNMATCHED, so
#: the category has to come from comparing the two systems' own columns.
_GAP_CATEGORY = """
    CASE
        WHEN msc_calling_number IS DISTINCT FROM in_calling_number
         AND msc_called_number  IS DISTINCT FROM in_called_number  THEN 'BOTH_NUMBERS_DIFFER'
        WHEN msc_calling_number IS DISTINCT FROM in_calling_number THEN 'CALLING_NUMBER_DIFFERS'
        WHEN msc_called_number  IS DISTINCT FROM in_called_number  THEN 'CALLED_NUMBER_DIFFERS'
        WHEN btrim(coalesce(debit_amount, '')) = ''                THEN 'DEBIT_MISSING'
        ELSE 'UNCLASSIFIED'
    END
"""

#: Daily buckets carried by each KPI sparkline.
_SPARK_DAYS = 14
#: Monthly buckets carried by the revenue-at-risk sparkline, which is monthly.
_SPARK_MONTHS = 12
#: Window compared against the preceding equal-length window for each delta.
_DELTA_DAYS = 7

#: How many bars the two attribution panels carry.
_TOP_N = 8


async def _query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return await ra_postgres.query_database(settings.ra_rating_pg_name, sql, params)


def _num(value: Any) -> float:
    """Postgres numerics arrive as Decimal; None means nothing matched."""
    return float(value) if value is not None else 0.0


def _pct_delta(current: float, prior: float) -> float:
    """Percentage change. No prior activity reports 0, not +100% — a first load
    is not an improvement on anything."""
    if prior == 0:
        return 0.0
    return round((current - prior) / prior * 100, 2)


def _kpi(value: str, *, delta: float, higher_is_better: bool, spark: list[float]) -> dict[str, Any]:
    return {"value": value, "delta": delta, "higherIsBetter": higher_is_better, "spark": spark}


async def _totals() -> dict[str, Any]:
    rows = await _query(
        f"""
        SELECT count(*)                                            AS records,
               count(*) FILTER (WHERE status = :matched)           AS matched,
               count(*) FILTER (WHERE status <> :matched)          AS exceptions,
               coalesce(sum({_MONEY}) FILTER (WHERE status <> :matched), 0) AS at_risk,
               coalesce(sum({_MONEY}), 0)                          AS debit_total
        FROM {_TABLE}
        """,
        {"matched": MATCHED_STATUS},
    )
    return rows[0] if rows else {}


async def _window_totals(days: int, offset_days: int) -> dict[str, Any]:
    """The same counts over a window ending `offset_days` before the newest row.

    Anchored on max(created_at) rather than now() for the same reason the Rating
    builder anchors on max(event_time): this table is written in batches, so a
    wall-clock window can contain no rows and would report every KPI as having
    collapsed to zero.
    """
    rows = await _query(
        f"""
        WITH bounds AS (SELECT max(created_at) AS latest FROM {_TABLE})
        SELECT count(*)                                            AS records,
               count(*) FILTER (WHERE status = :matched)           AS matched,
               count(*) FILTER (WHERE status <> :matched)          AS exceptions,
               coalesce(sum({_MONEY}) FILTER (WHERE status <> :matched), 0) AS at_risk
        FROM {_TABLE}, bounds
        WHERE created_at >  bounds.latest - make_interval(days => :older)
          AND created_at <= bounds.latest - make_interval(days => :newer)
        """,
        {"matched": MATCHED_STATUS, "older": days + offset_days, "newer": offset_days},
    )
    return rows[0] if rows else {}


async def _daily() -> list[dict[str, Any]]:
    """One row per day the report was written, oldest first."""
    return await _query(
        f"""
        SELECT created_at::date                                    AS day,
               count(*)                                            AS records,
               count(*) FILTER (WHERE status = :matched)           AS matched,
               count(*) FILTER (WHERE status <> :matched)          AS exceptions,
               coalesce(sum({_MONEY}) FILTER (WHERE status <> :matched), 0) AS at_risk
        FROM {_TABLE}
        WHERE created_at IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """,
        {"matched": MATCHED_STATUS},
    )


async def _grouped(expression: str, *, exceptions_only: bool, limit: int | None = None) -> list[dict[str, Any]]:
    """Counts and debit grouped by one expression.

    `expression` is a literal from this module — a column name or the CASE above
    — and never a request value.
    """
    where = "WHERE status <> :matched" if exceptions_only else ""
    return await _query(
        f"""
        SELECT coalesce(({expression})::text, 'Unattributed') AS name,
               count(*)                                       AS records,
               coalesce(sum({_MONEY}), 0)                     AS debit
        FROM {_TABLE}
        {where}
        GROUP BY 1
        ORDER BY count(*) DESC
        {"LIMIT :limit" if limit else ""}
        """,
        {"matched": MATCHED_STATUS, **({"limit": limit} if limit else {})},
    )


async def _leakage_monthly() -> list[dict[str, Any]]:
    """Money at risk per month, oldest first.

    Monthly rather than daily: this is what the source records. The dashboard
    labels the panel with the cadence it is actually given rather than assuming
    the daily one the record-level panels use.
    """
    return await _query(
        f"""
        SELECT month_date                    AS month,
               coalesce(sum(amount), 0)      AS amount,
               coalesce(sum("count"), 0)     AS records
        FROM {_LEAKAGE_TABLE}
        WHERE assurance_type = :type AND status = :status
        GROUP BY month_date
        ORDER BY month_date
        """,
        {"type": _LEAKAGE_TYPE, "status": _LEAKAGE_AT_RISK},
    )


async def _leakage_by_category() -> list[dict[str, Any]]:
    """Money at risk per leakage category, worst first."""
    return await _query(
        f"""
        SELECT leakages                      AS name,
               coalesce(sum(amount), 0)      AS amount
        FROM {_LEAKAGE_TABLE}
        WHERE assurance_type = :type AND status = :status
        GROUP BY leakages
        ORDER BY sum(amount) DESC
        """,
        {"type": _LEAKAGE_TYPE, "status": _LEAKAGE_AT_RISK},
    )


async def _findings(limit: int = 5) -> list[dict[str, Any]]:
    """The exception records carrying the most debit, worst first."""
    return await _query(
        f"""
        SELECT coalesce(service_type, 'UNKNOWN')            AS service,
               coalesce(msc_calling_number, 'unknown')      AS calling,
               coalesce(msc_called_number, 'unknown')       AS called,
               ({_GAP_CATEGORY})                            AS category,
               {_MONEY}                                     AS impact
        FROM {_TABLE}
        WHERE status <> :matched
        ORDER BY {_MONEY} DESC, id
        LIMIT :limit
        """,
        {"matched": MATCHED_STATUS, "limit": limit},
    )


def _spark(daily: list[dict[str, Any]], key: str, digits: int = 2) -> list[float]:
    return [round(_num(row.get(key)), digits) for row in daily[-_SPARK_DAYS:]]


def _label(day: Any) -> str:
    return day.strftime("%d %b") if isinstance(day, date) else ""


def _month_label(month: Any) -> str:
    return month.strftime("%b %Y") if isinstance(month, date) else ""


def _titleise(name: str) -> str:
    """BOTH_NUMBERS_DIFFER -> Both Numbers Differ, matching how the Rating
    dashboard presents its own status names."""
    return name.replace("_", " ").title()


async def build_dashboard() -> dict[str, Any]:
    """The whole dashboard payload for Usage Assurance."""
    totals = await _totals()
    daily = await _daily()
    recent = await _window_totals(_DELTA_DAYS, 0)
    prior = await _window_totals(_DELTA_DAYS, _DELTA_DAYS)

    records = _num(totals.get("records"))
    matched = _num(totals.get("matched"))
    exceptions = _num(totals.get("exceptions"))

    exception_rate = round(exceptions / records * 100, 2) if records else 0.0
    reconciliation = round(matched / records * 100, 2) if records else 0.0

    r_records, r_exceptions = _num(recent.get("records")), _num(recent.get("exceptions"))
    p_records, p_exceptions = _num(prior.get("records")), _num(prior.get("exceptions"))
    r_rate = (r_exceptions / r_records * 100) if r_records else 0.0
    p_rate = (p_exceptions / p_records * 100) if p_records else 0.0
    r_recon = ((r_records - r_exceptions) / r_records * 100) if r_records else 0.0
    p_recon = ((p_records - p_exceptions) / p_records * 100) if p_records else 0.0

    leakage = await _leakage_monthly()
    at_risk = sum(_num(row.get("amount")) for row in leakage)
    # Latest month against the one before it — the comparison the source's own
    # granularity supports. A single month of data reports no change.
    risk_delta = _pct_delta(
        _num(leakage[-1].get("amount")) if leakage else 0.0,
        _num(leakage[-2].get("amount")) if len(leakage) > 1 else 0.0,
    )

    category_rows = await _grouped(_GAP_CATEGORY, exceptions_only=True)
    caller_rows = await _grouped("msc_calling_number", exceptions_only=True, limit=_TOP_N)
    service_rows = await _grouped("service_type", exceptions_only=False)
    leakage_categories = await _leakage_by_category()

    span = f"{_label(daily[0]['day'])} – {_label(daily[-1]['day'])}" if daily else ""

    return {
        "id": ASSURANCE,
        "name": "Usage Assurance",
        "subtitle": (
            f"{int(records):,} voice and SMS records reconciled between the MSC and the IN"
            + (f" · {span}" if span else "")
        ),
        "recordUnit": "CDRs",
        # The debit column carries no currency of its own; the platform's
        # figures are rupees, and the UI turns the ISO code into a glyph.
        "currency": "INR",
        "kpis": {
            "revenueAtRisk": _kpi(
                f"{at_risk:,.2f}",
                delta=risk_delta,
                higher_is_better=False,
                spark=[round(_num(row.get("amount")), 2) for row in leakage[-_SPARK_MONTHS:]],
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
                "label": _label(row["day"]),
                "healthy": _num(row.get("matched")),
                "exceptions": _num(row.get("exceptions")),
                "leakage": round(_num(row.get("at_risk")), 2),
            }
            for row in daily
        ],
        # Monthly, because that is the granularity the leakage table records.
        # `riskTrendSubtitle` tells the panel to say so rather than claim daily.
        "riskTrendSubtitle": "Monthly · INR · mismatched only",
        "revenueAtRisk": [
            {"label": _month_label(row["month"]), "value": round(_num(row.get("amount")), 2)}
            for row in leakage
        ],
        "exceptionCategories": [
            {"name": _titleise(row["name"]), "value": _num(row.get("records"))}
            for row in category_rows
        ],
        # Rating attributes an exception to the rule that caused it. Nothing in
        # this table records a rule, and the calling subscriber is the only
        # actor it names, so that is what exceptions are attributed to.
        "entitiesTitle": "Exceptions by Calling Number",
        "entities": [
            {"name": row["name"], "value": _num(row.get("records"))} for row in caller_rows
        ],
        # Ordered by money here rather than in SQL: _grouped orders by volume,
        # which is right for the count panels and wrong for a leakage ranking.
        "leakageCategories": [
            {"name": row["name"], "value": round(_num(row.get("amount")), 2)}
            for row in leakage_categories
        ],
        "businessSegments": [
            {"name": row["name"], "value": _num(row.get("records"))} for row in service_rows
        ],
        "findings": [
            {
                "finding": f"{row['service']} — {_titleise(row['category'])}",
                "source": f"{row['calling']} → {row['called']}",
                "category": _titleise(row["category"]),
                "impactCr": round(_num(row.get("impact")), 2),
            }
            for row in await _findings()
        ],
    }
