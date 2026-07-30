"""Assurance aggregations: the numbers behind the reconciliation screens.

Everything here is a set-based GROUP BY over ``rating_results`` — no Python
loops over rows. That matters because these queries back dashboards that will
be refreshed constantly against tables that grow by millions of rows per day;
an aggregate the database cannot do in one pass is an aggregate that will be
quietly turned off later.

Sign convention (identical to the rating service): variance is expected minus
actual, so **positive = undercharged = revenue leakage** and **negative =
overcharged = customer harm**. The two are never netted; a day that leaks
£10k and overcharges £10k is not a good day.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rating.constants import CLEAN_STATUSES, AssuranceStatus, ExceptionStatus
from app.modules.rating.models import RatingException, RatingResult, RatingRun

ZERO = cast(0, Numeric)

#: variance > 0 -> money the operator failed to bill.
_UNDER = func.sum(case((RatingResult.variance > 0, RatingResult.variance), else_=ZERO))
#: variance < 0 -> money the customer was overbilled. Stored positive.
_OVER = func.sum(case((RatingResult.variance < 0, -RatingResult.variance), else_=ZERO))
_MATCHED = func.sum(case((RatingResult.status.in_(CLEAN_STATUSES), 1), else_=0))

_OPEN_EXCEPTION_STATUSES = tuple(
    s.value
    for s in ExceptionStatus
    if s not in (ExceptionStatus.RESOLVED, ExceptionStatus.CLOSED)
)


def _f(value: Any) -> float:
    return float(value or 0)


def _filtered(query, *, date_from: date | None, date_to: date | None, run_id: str | None):
    if run_id:
        query = query.where(RatingResult.run_id == run_id)
    if date_from:
        query = query.where(RatingResult.event_date >= date_from)
    if date_to:
        query = query.where(RatingResult.event_date <= date_to)
    return query


async def kpis(
    db: AsyncSession,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """The headline card row: volumes, revenue, and the two exposures."""
    row = (
        await db.execute(
            _filtered(
                select(
                    func.count().label("total"),
                    _MATCHED.label("matched"),
                    func.coalesce(func.sum(RatingResult.expected_final_charge), 0).label(
                        "expected"
                    ),
                    func.coalesce(func.sum(RatingResult.actual_charge), 0).label("billed"),
                    func.coalesce(_UNDER, 0).label("under"),
                    func.coalesce(_OVER, 0).label("over"),
                ).select_from(RatingResult),
                date_from=date_from,
                date_to=date_to,
                run_id=run_id,
            )
        )
    ).one()

    by_status_rows = (
        await db.execute(
            _filtered(
                select(RatingResult.status, func.count()).group_by(RatingResult.status),
                date_from=date_from,
                date_to=date_to,
                run_id=run_id,
            )
        )
    ).all()
    by_status = {status: int(count) for status, count in by_status_rows}

    open_exceptions = int(
        (
            await db.execute(
                select(func.count())
                .select_from(RatingException)
                .where(RatingException.status.in_(_OPEN_EXCEPTION_STATUSES))
            )
        ).scalar_one()
    )

    total = int(row.total)
    return {
        "total_cdrs": total,
        "matched_cdrs": int(row.matched),
        "match_rate": round(int(row.matched) / total * 100, 2) if total else None,
        "expected_revenue": _f(row.expected),
        "billed_revenue": _f(row.billed),
        "revenue_leakage": _f(row.under),
        "customer_overcharge": _f(row.over),
        "unrated_cdrs": by_status.get(AssuranceStatus.UNRATED.value, 0),
        "no_matching_rule": by_status.get(AssuranceStatus.NO_MATCHING_RULE.value, 0),
        "open_exceptions": open_exceptions,
        "by_status": by_status,
    }


async def revenue_trend(
    db: AsyncSession,
    *,
    days: int = 30,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Expected vs billed per event day — the dashboard's main chart.

    Bucketed by the CDR's event date, not the run date: a re-rate of last
    week's traffic must land on last week's line, not distort today's.
    """
    since = date.today() - timedelta(days=days)
    rows = (
        await db.execute(
            _filtered(
                select(
                    RatingResult.event_date,
                    func.count().label("cdrs"),
                    _MATCHED.label("matched"),
                    func.coalesce(func.sum(RatingResult.expected_final_charge), 0).label(
                        "expected"
                    ),
                    func.coalesce(func.sum(RatingResult.actual_charge), 0).label("billed"),
                    func.coalesce(_UNDER, 0).label("under"),
                    func.coalesce(_OVER, 0).label("over"),
                )
                .group_by(RatingResult.event_date)
                .order_by(RatingResult.event_date),
                date_from=since,
                date_to=None,
                run_id=run_id,
            )
        )
    ).all()
    return [
        {
            "date": r.event_date.isoformat(),
            "cdrs": int(r.cdrs),
            "matched": int(r.matched),
            "expected": _f(r.expected),
            "billed": _f(r.billed),
            "undercharge": _f(r.under),
            "overcharge": _f(r.over),
        }
        for r in rows
    ]


#: The dimensions leakage can be broken down by. A whitelist, not getattr —
#: this value comes off the wire.
_LEAKAGE_DIMENSIONS = {
    "product": RatingResult.product_code,
    "service": RatingResult.service_type,
    "destination_zone": RatingResult.destination_zone,
    "time_band": RatingResult.time_band,
    "root_cause": RatingResult.root_cause,
    "status": RatingResult.status,
}


async def leakage_breakdown(
    db: AsyncSession,
    *,
    dimension: str,
    limit: int = 10,
    date_from: date | None = None,
    date_to: date | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Top-N slices of one dimension, ranked by absolute exposure.

    Ranked by undercharge + overcharge rather than net, because a product
    that leaks £5k and overcharges £5k nets to zero and would otherwise
    rank below one that leaks £100.
    """
    column = _LEAKAGE_DIMENSIONS.get(dimension)
    if column is None:
        raise ValueError(
            f"Unknown dimension '{dimension}'. One of: {sorted(_LEAKAGE_DIMENSIONS)}"
        )

    rows = (
        await db.execute(
            _filtered(
                select(
                    column.label("key"),
                    func.count().label("cdrs"),
                    _MATCHED.label("matched"),
                    func.coalesce(func.sum(RatingResult.expected_final_charge), 0).label(
                        "expected"
                    ),
                    func.coalesce(func.sum(RatingResult.actual_charge), 0).label("billed"),
                    func.coalesce(_UNDER, 0).label("under"),
                    func.coalesce(_OVER, 0).label("over"),
                )
                .group_by(column)
                .order_by((func.coalesce(_UNDER, 0) + func.coalesce(_OVER, 0)).desc())
                .limit(limit),
                date_from=date_from,
                date_to=date_to,
                run_id=run_id,
            )
        )
    ).all()
    return [
        {
            "key": r.key or "(none)",
            "cdrs": int(r.cdrs),
            "matched": int(r.matched),
            "match_rate": round(int(r.matched) / int(r.cdrs) * 100, 2) if r.cdrs else None,
            "expected": _f(r.expected),
            "billed": _f(r.billed),
            "undercharge": _f(r.under),
            "overcharge": _f(r.over),
            "net_variance": round(_f(r.under) - _f(r.over), 6),
        }
        for r in rows
    ]


async def latest_runs(db: AsyncSession, *, limit: int = 6) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(RatingRun).order_by(RatingRun.created_at.desc()).limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "status": r.status,
            "total_cdrs": r.total_cdrs,
            "rated_cdrs": r.rated_cdrs,
            "match_rate": (
                round(r.matched_count / r.rated_cdrs * 100, 2) if r.rated_cdrs else None
            ),
            "expected_revenue": _f(r.expected_revenue),
            "billed_revenue": _f(r.billed_revenue),
            "undercharge_total": _f(r.undercharge_total),
            "overcharge_total": _f(r.overcharge_total),
            "exception_count": r.exception_count,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in rows
    ]
