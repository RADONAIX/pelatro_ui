"""Executive dashboard data: /assurance-dashboard.

Backs the Dashboard & KPIs screen with each assurance's own results. Distinct
from /dashboard/kpis, which reports the platform's own operational counters
rather than one assurance's findings.

Two assurances have a data source, and they are read very differently:

  * Rating   — `service.py`, one canonical reconciliation table
  * Charging — `charging.py`, whatever rules the assurance has, plus its feeds

Both return the same payload, so the UI renders either without branching. Adding
a third means adding a provider and one entry in PROVIDERS.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import Principal, require
from app.core.rbac import PermKey
from app.modules.assurance_dashboard import schemas, service, service_usage

#: Assurances with a real results table, each mapped to the builder that reads
#: it. Both emit the same payload, so both render through the same six charts.
BUILDERS = {
    service.SUPPORTED_ASSURANCE: service.build_dashboard,
    service_usage.ASSURANCE: service_usage.build_dashboard,
}
from app.modules.assurance_dashboard import charging, schemas, service

router = APIRouter(prefix="/assurance-dashboard", tags=["assurance-dashboard"])

AssuranceId = Query(min_length=1, max_length=64, description="Assurance app id, e.g. rating")

#: Assurance id -> the provider that can build its dashboard.
PROVIDERS: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {
    service.SUPPORTED_ASSURANCE: service.build_dashboard,
    charging.ASSURANCE_ID: charging.build_dashboard,
}


@router.get("", response_model=schemas.AssuranceDashboardOut)
async def get_dashboard(
    assurance: str = AssuranceId,
    _: Principal = Depends(require(PermKey.DASHBOARD, "view")),
) -> schemas.AssuranceDashboardOut:
    # Only Rating and Usage have a real results table. The others 404 rather
    # than returning an all-zero dashboard, which a caller could not tell apart
    # from a real assurance that happens to have found nothing.
    build = BUILDERS.get(assurance.strip().lower())
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
