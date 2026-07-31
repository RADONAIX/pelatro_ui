"""Superset client for minting guest tokens for embedded dashboards.

Flow (Superset stable API v1):
  1. POST /api/v1/security/login  (admin db login)        -> access_token
  2. GET  /api/v1/security/csrf_token/ (with bearer)       -> csrf_token + cookie
  3. POST /api/v1/security/guest_token/ (bearer + csrf)    -> guest token

The guest token is short-lived (~5 min by default); the embedded SDK calls our
endpoint again via fetchGuestToken to refresh, so we mint a fresh one per call.
Enable/disable and credentials come from settings (SUPERSET_* env vars).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import UpstreamUnavailableError
from app.core.logging import get_logger

log = get_logger("superset")


async def mint_guest_token() -> str:
    """Return a Superset guest token for the configured embedded dashboard."""
    if not settings.superset_enabled:
        raise UpstreamUnavailableError("Superset integration is disabled.")

    base = settings.superset_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(base_url=base, timeout=15.0) as client:
            # 1. Admin login → access token.
            login = await client.post(
                "/api/v1/security/login",
                json={
                    "username": settings.superset_admin_username,
                    "password": settings.superset_admin_password,
                    "provider": "db",
                    "refresh": True,
                },
            )
            login.raise_for_status()
            access_token = login.json()["access_token"]
            auth = {"Authorization": f"Bearer {access_token}"}

            # 2. CSRF token (required for the guest_token POST). The cookie set
            #    on this response is retained by the client and sent back.
            csrf_resp = await client.get("/api/v1/security/csrf_token/", headers=auth)
            csrf_resp.raise_for_status()
            csrf_token = csrf_resp.json()["result"]

            # 3. Guest token scoped to the embedded dashboard.
            guest = await client.post(
                "/api/v1/security/guest_token/",
                headers={**auth, "X-CSRFToken": csrf_token, "Referer": base},
                json={
                    "user": {
                        "username": settings.superset_guest_username,
                        "first_name": "RADONaix",
                        "last_name": "Embed",
                    },
                    "resources": [
                        {"type": "dashboard", "id": settings.superset_dashboard_id}
                    ],
                    "rls": [],
                },
            )
            guest.raise_for_status()
            return guest.json()["token"]
    except (httpx.HTTPError, KeyError) as exc:
        log.warning("superset_guest_token_failed", error=str(exc))
        raise UpstreamUnavailableError(
            "Could not obtain a Superset guest token.",
            details={"reason": str(exc)},
        ) from exc


async def ping() -> dict[str, Any]:
    """Lightweight reachability + auth check for diagnostics."""
    if not settings.superset_enabled:
        return {"enabled": False}
    try:
        token = await mint_guest_token()
        return {"enabled": True, "ok": True, "token_len": len(token)}
    except UpstreamUnavailableError as exc:
        return {"enabled": True, "ok": False, "error": exc.message}
