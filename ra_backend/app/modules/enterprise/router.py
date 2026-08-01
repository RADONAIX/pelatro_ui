"""Enterprise Dashboard data: /enterprise-dashboard.

One endpoint, one payload — the cross-assurance view. Distinct from
/assurance-dashboard, which reports a single assurance's reconciliation
results, and from /dashboard/kpis, which reports the platform's own operational
counters.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import Principal, require
from app.core.logging import get_logger
from app.core.rbac import PermKey
from app.modules.enterprise import service

router = APIRouter(prefix="/enterprise-dashboard", tags=["enterprise-dashboard"])

log = get_logger("enterprise.router")


@router.get("")
async def get_enterprise_dashboard(
    _: Principal = Depends(require(PermKey.DASHBOARD, "view")),
) -> dict[str, Any]:
    try:
        return await service.dashboard()
    except ValueError as exc:
        # An empty source table is a 404, not a 200 full of zeros: a caller
        # cannot tell "nothing was loaded" from "everything reconciled".
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
