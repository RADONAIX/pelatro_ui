"""Analytics routes: Superset embedded-dashboard guest tokens (/superset)."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.integrations import superset

router = APIRouter(prefix="/superset", tags=["analytics"])


@router.get("/guest-token")
async def guest_token() -> dict[str, str]:
    """Mint a short-lived Superset guest token for the embedded dashboard.

    Called by the frontend's `fetchGuestToken` (embedded SDK), which refreshes
    on expiry — so each call returns a fresh token. Intentionally unauthenticated
    to match the SDK's tokenless fetch; the token is dashboard-scoped and brief.
    """
    token = await superset.mint_guest_token()
    return {"token": token}


@router.get("/config")
async def config() -> dict[str, str | bool]:
    """Embed config the frontend can read instead of hard-coding values."""
    return {
        "enabled": settings.superset_enabled,
        "base_url": settings.superset_base_url,
        "dashboard_id": settings.superset_dashboard_id,
    }


@router.get("/health")
async def health() -> dict:
    """Diagnostics: confirm Superset is reachable and guest-token minting works."""
    return await superset.ping()
