"""Rating Assurance overview.

Phase 1 reports on what actually exists: the rule estate and catalogue
readiness. The assurance KPIs (expected vs billed revenue, leakage, match rate)
arrive with the rating engine in Phase 4 — they are returned as ``null`` rather
than zero so the UI can render "not yet available" instead of a misleading
£0.00 leakage figure.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.deps import DbSession, require
from app.core.errors import ValidationFailedError
from app.core.rbac import RatingPermKey
from app.modules.catalog import models as cm
from app.modules.dashboards import service as dash_svc
from app.modules.rules.canonical.lineage import CanonicalRuleAudit
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.constants import RuleStatus

router = APIRouter(prefix="/dashboards", tags=["dashboards"])

_view = require(RatingPermKey.DASHBOARD, "view")


class RuleEstate(BaseModel):
    total_rules: int
    logical_rules: int
    draft_count: int
    pending_approval: int
    active_count: int
    rules_with_errors: int
    expiring_within_30_days: int
    by_status: dict[str, int]
    by_service_type: dict[str, int]


class CatalogReadiness(BaseModel):
    products: int
    offers: int
    tariff_plans: int
    destination_zones: int
    destination_prefixes: int
    time_bands: int
    tax_rules: int
    #: Coverage gaps a Phase-1 estate can already detect — an empty catalogue
    #: means every CDR would land in DESTINATION_NOT_FOUND once Phase 3 runs.
    warnings: list[str]


class AssuranceKpis(BaseModel):
    """Null = the rating engine has not produced any results yet."""

    total_cdrs: int | None = None
    expected_revenue: float | None = None
    billed_revenue: float | None = None
    revenue_leakage: float | None = None
    customer_overcharge: float | None = None
    match_rate: float | None = None
    open_exceptions: int | None = None
    available: bool = False
    message: str = ""


class RecentActivity(BaseModel):
    rule_key: str
    version: int | None
    action: str
    actor_name: str | None
    comment: str
    created_at: str


class Overview(BaseModel):
    rule_estate: RuleEstate
    catalog: CatalogReadiness
    assurance: AssuranceKpis
    recent_activity: list[RecentActivity]


@router.get(
    "/assurance",
    summary="Reconciliation dashboard: KPIs, trend, leakage breakdowns, latest runs",
    dependencies=[Depends(_view)],
)
async def assurance_dashboard(
    db: DbSession,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    run_id: str | None = Query(None),
    trend_days: int = Query(30, ge=1, le=365),
) -> dict:
    window = {"date_from": date_from, "date_to": date_to, "run_id": run_id}
    return {
        "kpis": await dash_svc.kpis(db, **window),
        "trend": await dash_svc.revenue_trend(db, days=trend_days, run_id=run_id),
        "by_product": await dash_svc.leakage_breakdown(db, dimension="product", **window),
        "by_root_cause": await dash_svc.leakage_breakdown(
            db, dimension="root_cause", **window
        ),
        "latest_runs": await dash_svc.latest_runs(db),
    }


@router.get(
    "/leakage",
    summary="Leakage broken down by one dimension",
    dependencies=[Depends(_view)],
)
async def leakage(
    db: DbSession,
    dimension: str = Query("product"),
    limit: int = Query(10, ge=1, le=100),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    run_id: str | None = Query(None),
) -> list[dict]:
    try:
        return await dash_svc.leakage_breakdown(
            db,
            dimension=dimension,
            limit=limit,
            date_from=date_from,
            date_to=date_to,
            run_id=run_id,
        )
    except ValueError as exc:
        raise ValidationFailedError(str(exc)) from exc


@router.get(
    "/overview",
    response_model=Overview,
    summary="Rating Assurance overview",
    dependencies=[Depends(_view)],
)
async def overview(db: DbSession) -> Overview:
    # The canonical store (`ra_rule.*`) is what the Rule Catalogue, approvals
    # and snapshots read (RULE_COMPILE_SOURCE=CANONICAL). Counting the legacy
    # `rating.rules` table here showed 3 hand-authored rules while the
    # catalogue listed the whole imported estate.
    async def _scalar(stmt) -> int:
        return int((await db.scalar(stmt)) or 0)

    total_versions = await _scalar(
        select(func.count()).select_from(CanonicalRuleVersion)
    )
    logical = await _scalar(select(func.count()).select_from(CanonicalRule))

    by_status_rows = await db.execute(
        select(CanonicalRule.status, func.count()).group_by(CanonicalRule.status)
    )
    by_status = {str(k): int(v) for k, v in by_status_rows.all()}

    by_service_rows = await db.execute(
        select(CanonicalRule.service_type, func.count()).group_by(
            CanonicalRule.service_type
        )
    )
    by_service_type = {str(k): int(v) for k, v in by_service_rows.all()}

    draft_count = by_status.get(RuleStatus.DRAFT, 0)
    pending = by_status.get(RuleStatus.VALIDATED, 0) + by_status.get(
        RuleStatus.REVIEWED, 0
    )
    active_count = by_status.get(RuleStatus.ACTIVE, 0) + by_status.get(
        RuleStatus.PUBLISHED, 0
    )

    rules_with_errors = await _scalar(
        select(func.count())
        .select_from(CanonicalRule)
        .join(
            CanonicalRuleVersion,
            CanonicalRuleVersion.rule_version_id == CanonicalRule.current_version_id,
        )
        .where(CanonicalRuleVersion.validation_state == "ERROR")
    )

    horizon = date.today() + timedelta(days=30)
    expiring = await _scalar(
        select(func.count())
        .select_from(CanonicalRuleVersion)
        .where(
            CanonicalRuleVersion.effective_to.isnot(None),
            CanonicalRuleVersion.effective_to <= horizon,
            CanonicalRuleVersion.effective_to >= date.today(),
            CanonicalRuleVersion.status.in_(
                [RuleStatus.ACTIVE, RuleStatus.PUBLISHED]
            ),
        )
    )

    async def _count(model) -> int:
        return int((await db.execute(select(func.count()).select_from(model))).scalar_one())

    counts = {
        "products": await _count(cm.Product),
        "offers": await _count(cm.Offer),
        "tariff_plans": await _count(cm.TariffPlan),
        "destination_zones": await _count(cm.DestinationZone),
        "destination_prefixes": await _count(cm.DestinationPrefix),
        "time_bands": await _count(cm.TimeBand),
        "tax_rules": await _count(cm.TaxRule),
    }
    warnings: list[str] = []
    if counts["products"] == 0:
        warnings.append("No products defined — rules cannot be targeted at a product.")
    if counts["destination_prefixes"] == 0:
        warnings.append(
            "No destination prefixes mapped — every CDR would resolve to DESTINATION_NOT_FOUND."
        )
    if counts["time_bands"] == 0:
        warnings.append("No time bands defined — peak/off-peak pricing cannot be applied.")
    if counts["tax_rules"] == 0:
        warnings.append("No tax rules defined — expected charges will exclude tax.")

    activity_rows = (
        await db.execute(
            select(CanonicalRuleAudit, CanonicalRule.rule_key)
            .join(CanonicalRule, CanonicalRule.rule_id == CanonicalRuleAudit.rule_id)
            .order_by(CanonicalRuleAudit.created_at.desc())
            .limit(10)
        )
    ).all()

    k = await dash_svc.kpis(db)
    assurance = (
        AssuranceKpis(
            total_cdrs=k["total_cdrs"],
            expected_revenue=k["expected_revenue"],
            billed_revenue=k["billed_revenue"],
            revenue_leakage=k["revenue_leakage"],
            customer_overcharge=k["customer_overcharge"],
            match_rate=k["match_rate"],
            open_exceptions=k["open_exceptions"],
            available=True,
        )
        if k["total_cdrs"]
        else AssuranceKpis(message="No rated CDRs yet — run a rating first.")
    )

    return Overview(
        rule_estate=RuleEstate(
            total_rules=total_versions,
            logical_rules=logical,
            draft_count=draft_count,
            pending_approval=pending,
            active_count=active_count,
            rules_with_errors=rules_with_errors,
            expiring_within_30_days=expiring,
            by_status=by_status,
            by_service_type=by_service_type,
        ),
        catalog=CatalogReadiness(**counts, warnings=warnings),
        assurance=assurance,
        recent_activity=[
            RecentActivity(
                rule_key=rule_key,
                version=a.version_number,
                action=a.action,
                actor_name=a.actor_name,
                comment=a.comment or "",
                created_at=a.created_at.isoformat(),
            )
            for a, rule_key in activity_rows
        ],
    )
