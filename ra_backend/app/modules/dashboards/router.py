"""Assurance dashboards backed by real assurance output."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.deps import Principal, require
from app.core.rbac import PermKey
from app.modules.dashboards import usage

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


@router.get("/usage")
async def usage_dashboard(
    _: Principal = Depends(require(PermKey.DASHBOARD, "view")),
) -> dict[str, Any]:
    """Every panel of the Usage Assurance dashboard, from
    assurance.voice_sms_match_report and nothing else."""
    return await usage.dashboard()
