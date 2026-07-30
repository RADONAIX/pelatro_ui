"""Monitoring routes (/monitoring)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import Principal, require
from app.core.rbac import PermKey
from app.modules.monitoring import schemas, service

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/servers", response_model=list[schemas.MonitoredServer])
async def list_servers(
    _: Principal = Depends(require(PermKey.DASHBOARD, "view")),
) -> list[schemas.MonitoredServer]:
    """The monitored fleet, derived from Prometheus' live scrape targets.

    Adding a server to `deploy/prometheus/targets/nodes.json` makes it appear here
    (and therefore in the UI's server selector) with no code change or rebuild.
    Returns [] when Prometheus is disabled or unreachable."""
    return await service.list_servers()
