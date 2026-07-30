"""The report catalogue: every standard reconciliation report, as data.

Each report is a name, a description, and a query function returning
``(headings, rows)``. Registering them as data rather than as endpoints means
the catalogue screen, the generator and the (future) scheduler all read the
same list — a report added here appears everywhere at once.

All queries are set-based over ``rating_results`` / ``rating_exceptions``; a
report that needs a Python loop over CDRs is a report that stops being run
once the tables are big.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rating.constants import CLEAN_STATUSES, AssuranceStatus
from app.modules.rating.models import RatingException, RatingResult

ZERO = cast(0, Numeric)
_UNDER = func.sum(case((RatingResult.variance > 0, RatingResult.variance), else_=ZERO))
_OVER = func.sum(case((RatingResult.variance < 0, -RatingResult.variance), else_=ZERO))
_MATCHED = func.sum(case((RatingResult.status.in_(CLEAN_STATUSES), 1), else_=0))

Rows = tuple[list[str], list[list[Any]]]
QueryFn = Callable[..., Awaitable[Rows]]


@dataclass(frozen=True)
class ReportSpec:
    code: str
    name: str
    description: str
    category: str
    query: QueryFn


def _window(stmt, date_from: date | None, date_to: date | None, run_id: str | None):
    if run_id:
        stmt = stmt.where(RatingResult.run_id == run_id)
    if date_from:
        stmt = stmt.where(RatingResult.event_date >= date_from)
    if date_to:
        stmt = stmt.where(RatingResult.event_date <= date_to)
    return stmt


def _num(value: Any) -> float:
    return round(float(value or 0), 6)


async def _grouped(
    db: AsyncSession,
    column,
    heading: str,
    *,
    date_from: date | None,
    date_to: date | None,
    run_id: str | None,
) -> Rows:
    """The shared shape of every per-dimension reconciliation report."""
    rows = (
        await db.execute(
            _window(
                select(
                    column.label("key"),
                    func.count(),
                    _MATCHED,
                    func.coalesce(func.sum(RatingResult.expected_final_charge), 0),
                    func.coalesce(func.sum(RatingResult.actual_charge), 0),
                    func.coalesce(_UNDER, 0),
                    func.coalesce(_OVER, 0),
                )
                .group_by(column)
                .order_by((func.coalesce(_UNDER, 0) + func.coalesce(_OVER, 0)).desc()),
                date_from,
                date_to,
                run_id,
            )
        )
    ).all()
    headings = [
        heading, "cdrs", "matched", "match_rate_pct",
        "expected", "billed", "undercharge", "overcharge", "net_leakage",
    ]
    out = [
        [
            key or "(none)", int(cdrs), int(matched),
            round(int(matched) / int(cdrs) * 100, 2) if cdrs else 0,
            _num(expected), _num(billed), _num(under), _num(over),
            _num(float(under or 0) - float(over or 0)),
        ]
        for key, cdrs, matched, expected, billed, under, over in rows
    ]
    return headings, out


async def _daily_summary(db, *, date_from=None, date_to=None, run_id=None) -> Rows:
    return await _grouped(
        db, RatingResult.event_date, "event_date",
        date_from=date_from, date_to=date_to, run_id=run_id,
    )


async def _by_product(db, *, date_from=None, date_to=None, run_id=None) -> Rows:
    return await _grouped(
        db, RatingResult.product_code, "product",
        date_from=date_from, date_to=date_to, run_id=run_id,
    )


async def _by_service(db, *, date_from=None, date_to=None, run_id=None) -> Rows:
    return await _grouped(
        db, RatingResult.service_type, "service",
        date_from=date_from, date_to=date_to, run_id=run_id,
    )


async def _by_destination(db, *, date_from=None, date_to=None, run_id=None) -> Rows:
    return await _grouped(
        db, RatingResult.destination_zone, "destination_zone",
        date_from=date_from, date_to=date_to, run_id=run_id,
    )


async def _by_root_cause(db, *, date_from=None, date_to=None, run_id=None) -> Rows:
    return await _grouped(
        db, RatingResult.root_cause, "root_cause",
        date_from=date_from, date_to=date_to, run_id=run_id,
    )


def _record_report(*statuses: str) -> QueryFn:
    """A per-CDR detail report filtered to the given assurance statuses."""

    async def query(db, *, date_from=None, date_to=None, run_id=None) -> Rows:
        rows = (
            await db.execute(
                _window(
                    select(RatingResult)
                    .where(RatingResult.status.in_(statuses))
                    .order_by(func.abs(RatingResult.variance).desc())
                    .limit(10_000),
                    date_from,
                    date_to,
                    run_id,
                )
            )
        ).scalars().all()
        headings = [
            "cdr_id", "event_date", "msisdn", "service", "product",
            "destination_zone", "status", "root_cause",
            "expected", "billed", "variance", "currency", "run_id",
        ]
        out = [
            [
                r.cdr_id, r.event_date.isoformat(), r.msisdn or "", r.service_type,
                r.product_code or "", r.destination_zone or "", r.status,
                r.root_cause or "", _num(r.expected_final_charge),
                _num(r.actual_charge), _num(r.variance), r.currency or "", r.run_id,
            ]
            for r in rows
        ]
        return headings, out

    return query


async def _tax_reconciliation(db, *, date_from=None, date_to=None, run_id=None) -> Rows:
    rows = (
        await db.execute(
            _window(
                select(
                    RatingResult.product_code,
                    func.count(),
                    func.coalesce(func.sum(RatingResult.expected_tax), 0),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    RatingResult.root_cause == "TAX_INCORRECT",
                                    RatingResult.variance,
                                ),
                                else_=ZERO,
                            )
                        ),
                        0,
                    ),
                    func.sum(
                        case((RatingResult.root_cause == "TAX_INCORRECT", 1), else_=0)
                    ),
                ).group_by(RatingResult.product_code),
                date_from,
                date_to,
                run_id,
            )
        )
    ).all()
    headings = ["product", "cdrs", "expected_tax", "tax_variance", "tax_exceptions"]
    return headings, [
        [p or "(none)", int(c), _num(tax), _num(var), int(exc)]
        for p, c, tax, var, exc in rows
    ]


async def _exception_ageing(db, *, date_from=None, date_to=None, run_id=None) -> Rows:
    stmt = select(RatingException).order_by(RatingException.created_at)
    if run_id:
        stmt = stmt.where(RatingException.run_id == run_id)
    rows = (await db.execute(stmt)).scalars().all()
    headings = [
        "title", "status", "severity", "root_cause", "cdr_count",
        "revenue_impact", "assigned_to", "age_days", "created_at",
    ]
    today = date.today()
    out = [
        [
            e.title, e.status, e.severity, e.root_cause, e.cdr_count,
            _num(e.revenue_impact), e.assigned_to_name or "",
            (today - e.created_at.date()).days, e.created_at.date().isoformat(),
        ]
        for e in rows
    ]
    return headings, out


REPORTS: dict[str, ReportSpec] = {
    spec.code: spec
    for spec in (
        ReportSpec(
            "daily_reconciliation_summary",
            "Daily Reconciliation Summary",
            "Expected vs billed revenue and both exposures, one row per event day.",
            "Reconciliation",
            _daily_summary,
        ),
        ReportSpec(
            "product_reconciliation",
            "Product Leakage Report",
            "Leakage and overcharge per product, worst exposure first.",
            "Reconciliation",
            _by_product,
        ),
        ReportSpec(
            "service_reconciliation",
            "Service Reconciliation",
            "The same reconciliation cut by service (voice, SMS, data, roaming).",
            "Reconciliation",
            _by_service,
        ),
        ReportSpec(
            "destination_reconciliation",
            "Destination Reconciliation",
            "Leakage per destination zone — where mispriced routes hide.",
            "Reconciliation",
            _by_destination,
        ),
        ReportSpec(
            "root_cause_summary",
            "Rule Leakage Report",
            "Variance grouped by diagnosed root cause: wrong pulse, wrong tax, missing rule…",
            "Reconciliation",
            _by_root_cause,
        ),
        ReportSpec(
            "undercharge_detail",
            "Undercharge Report",
            "Every CDR billed below its expected charge, biggest variance first.",
            "Detail",
            _record_report(AssuranceStatus.UNDERCHARGED.value),
        ),
        ReportSpec(
            "overcharge_detail",
            "Overcharge Report",
            "Every CDR billed above its expected charge — customer harm, not revenue.",
            "Detail",
            _record_report(AssuranceStatus.OVERCHARGED.value),
        ),
        ReportSpec(
            "no_matching_rule",
            "No Matching Rule Report",
            "CDRs no rule could price, plus ambiguous multi-rule matches.",
            "Detail",
            _record_report(
                AssuranceStatus.NO_MATCHING_RULE.value,
                AssuranceStatus.MULTIPLE_RULE_MATCH.value,
                AssuranceStatus.PRODUCT_NOT_FOUND.value,
            ),
        ),
        ReportSpec(
            "unrated_cdrs",
            "Unrated CDR Report",
            "Rateable usage the billing system produced no charge for at all.",
            "Detail",
            _record_report(AssuranceStatus.UNRATED.value),
        ),
        ReportSpec(
            "tax_reconciliation",
            "Tax Reconciliation",
            "Expected tax and tax-attributed variance per product.",
            "Financial",
            _tax_reconciliation,
        ),
        ReportSpec(
            "exception_ageing",
            "Exception Ageing Report",
            "Open investigations by age — what has been sitting unworked.",
            "Operations",
            _exception_ageing,
        ),
    )
}
