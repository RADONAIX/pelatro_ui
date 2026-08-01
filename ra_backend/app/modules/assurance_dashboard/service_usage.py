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

#: The other status the same check constraint allows: money that reconciled.
_LEAKAGE_MATCHED = "MATCH"

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

#: Monthly buckets carried by each KPI sparkline. The source is monthly, so the
#: sparklines are too.
_SPARK_MONTHS = 12

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


async def _leakage_volume() -> list[dict[str, Any]]:
    """Reconciled and mismatched volume per month, with the money at risk.

    The three series the volume chart draws, all from the leakage table so the
    panel is internally consistent: its healthy and exception counts and its
    leakage line come from the same rows, rather than the counts coming from the
    record-level report and the money from here.
    """
    return await _query(
        f"""
        SELECT month_date                                                  AS month,
               coalesce(sum("count") FILTER (WHERE status = :matched), 0)  AS healthy,
               coalesce(sum("count") FILTER (WHERE status = :risk), 0)     AS exceptions,
               coalesce(sum(amount)  FILTER (WHERE status = :risk), 0)     AS leakage
        FROM {_LEAKAGE_TABLE}
        WHERE assurance_type = :type
        GROUP BY month_date
        ORDER BY month_date
        """,
        {"type": _LEAKAGE_TYPE, "matched": _LEAKAGE_MATCHED, "risk": _LEAKAGE_AT_RISK},
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


def _month_label(month: Any) -> str:
    return month.strftime("%b %Y") if isinstance(month, date) else ""


def _titleise(name: str) -> str:
    """BOTH_NUMBERS_DIFFER -> Both Numbers Differ, matching how the Rating
    dashboard presents its own status names."""
    return name.replace("_", " ").title()


async def build_dashboard() -> dict[str, Any]:
    """The whole dashboard payload for Usage Assurance."""
    # Every headline figure comes from the monthly leakage table, so the five
    # cards and the volume chart below them describe ONE population. Sourcing
    # the counts from the record-level match report instead put 739 evaluated
    # above a chart totalling 89.
    volume = await _leakage_volume()
    leakage = await _leakage_monthly()

    matched = sum(_num(row.get("healthy")) for row in volume)
    exceptions = sum(_num(row.get("exceptions")) for row in volume)
    records = matched + exceptions
    at_risk = sum(_num(row.get("amount")) for row in leakage)

    exception_rate = round(exceptions / records * 100, 2) if records else 0.0
    reconciliation = round(matched / records * 100, 2) if records else 0.0

    # Latest month against the one before it — the comparison this source's own
    # granularity supports. A single month of data reports no change.
    def _last(key: str, back: int = 1) -> float:
        return _num(volume[-back].get(key)) if len(volume) >= back else 0.0

    def _rate(key: str, back: int) -> float:
        total = _last("healthy", back) + _last("exceptions", back)
        return (_last(key, back) / total * 100) if total else 0.0

    risk_delta = _pct_delta(
        _num(leakage[-1].get("amount")) if leakage else 0.0,
        _num(leakage[-2].get("amount")) if len(leakage) > 1 else 0.0,
    )

    category_rows = await _grouped(_GAP_CATEGORY, exceptions_only=True)
    caller_rows = await _grouped("msc_calling_number", exceptions_only=True, limit=_TOP_N)
    service_rows = await _grouped("service_type", exceptions_only=False)
    leakage_categories = await _leakage_by_category()

    span = (
        f"{_month_label(volume[0]['month'])} – {_month_label(volume[-1]['month'])}"
        if volume
        else ""
    )

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
                delta=_pct_delta(
                    _last("healthy") + _last("exceptions"),
                    _last("healthy", 2) + _last("exceptions", 2),
                ),
                higher_is_better=True,
                spark=[
                    _num(row.get("healthy")) + _num(row.get("exceptions"))
                    for row in volume[-_SPARK_MONTHS:]
                ],
            ),
            "exceptions": _kpi(
                f"{int(exceptions):,}",
                delta=_pct_delta(_last("exceptions"), _last("exceptions", 2)),
                higher_is_better=False,
                spark=[_num(row.get("exceptions")) for row in volume[-_SPARK_MONTHS:]],
            ),
            "exceptionRate": _kpi(
                f"{exception_rate:.2f}%",
                delta=_pct_delta(_rate("exceptions", 1), _rate("exceptions", 2)),
                higher_is_better=False,
                spark=[
                    round(
                        _num(row.get("exceptions"))
                        / (_num(row.get("healthy")) + _num(row.get("exceptions")))
                        * 100,
                        2,
                    )
                    if (_num(row.get("healthy")) + _num(row.get("exceptions")))
                    else 0.0
                    for row in volume[-_SPARK_MONTHS:]
                ],
            ),
            "reconciliation": _kpi(
                f"{reconciliation:.2f}%",
                delta=_pct_delta(_rate("healthy", 1), _rate("healthy", 2)),
                higher_is_better=True,
                spark=[
                    round(
                        _num(row.get("healthy"))
                        / (_num(row.get("healthy")) + _num(row.get("exceptions")))
                        * 100,
                        2,
                    )
                    if (_num(row.get("healthy")) + _num(row.get("exceptions")))
                    else 0.0
                    for row in volume[-_SPARK_MONTHS:]
                ],
            ),
        },
        "trendTitle": "Reconciliation Volume",
        # Monthly, from the leakage table, so all three series share one source
        # and one cadence. Sourcing the counts from the record-level report and
        # only the leakage line from here would have put two populations on one
        # pair of axes.
        "trendSubtitle": "Monthly · reconciled vs mismatched",
        "trend": [
            {
                "label": _month_label(row["month"]),
                "healthy": _num(row.get("healthy")),
                "exceptions": _num(row.get("exceptions")),
                "leakage": round(_num(row.get("leakage")), 2),
            }
            for row in volume
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
