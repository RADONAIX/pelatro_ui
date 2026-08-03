"""Executive dashboard data: /assurance-dashboard.

Backs the Dashboard & KPIs screen with each assurance's own results. Distinct
from /dashboard/kpis, which reports the platform's own operational counters
rather than one assurance's findings.

Three assurances have a data source, and they are read very differently:

  * Rating   — `service.py`, one canonical reconciliation table
  * Charging — `charging.py`, whatever rules the assurance has, plus its feeds
  * Usage    — `service_usage.py`, the monthly leakage table and the match report

All three return the same payload, so the UI renders any of them without
branching. Adding a fourth means adding a provider and one entry in PROVIDERS —
ONE registry, so a provider can never be registered somewhere the lookup does
not read.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import Principal, require
from app.core.rbac import PermKey
from app.modules.assurance_dashboard import charging, schemas, service, service_usage

router = APIRouter(prefix="/assurance-dashboard", tags=["assurance-dashboard"])

AssuranceId = Query(min_length=1, max_length=64, description="Assurance app id, e.g. rating")

#: Assurance id -> the provider that can build its dashboard.
PROVIDERS: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {
    service.SUPPORTED_ASSURANCE: service.build_dashboard,
    charging.ASSURANCE_ID: charging.build_dashboard,
    service_usage.ASSURANCE: service_usage.build_dashboard,
}


@router.get("", response_model=schemas.AssuranceDashboardOut)
async def get_dashboard(
    assurance: str = AssuranceId,
    _: Principal = Depends(require(PermKey.DASHBOARD, "view")),
) -> schemas.AssuranceDashboardOut:
    # An assurance with no data source 404s rather than returning an all-zero
    # dashboard, which a caller could not tell apart from a real assurance that
    # happens to have found nothing.
    build = PROVIDERS.get(assurance.strip().lower())
    if build is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No dashboard data source for assurance '{assurance}'",
        )
    return schemas.AssuranceDashboardOut(**await build())
