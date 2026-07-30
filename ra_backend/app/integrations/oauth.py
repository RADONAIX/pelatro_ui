"""OAuth 2.0 / OIDC clients for Google + Microsoft (server-side redirect flow).

The browser hits /api/auth/oauth/{provider}; we redirect to the provider, then
handle the callback here: exchange the code for tokens (client secret stays on
the server) and read the user's email/name from the OIDC userinfo endpoint.

The frontend's `redirect_uri` is carried across the round-trip inside a signed,
short-lived `state` (JWT) to prevent tampering / CSRF and open redirects.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt

from app.core.config import settings
from app.core.errors import AppError, UpstreamUnavailableError
from app.core.logging import get_logger

log = get_logger("oauth")

Provider = str  # "google" | "microsoft"
SUPPORTED: tuple[str, ...] = ("google", "microsoft")
SCOPE = "openid email profile"
STATE_TTL_SECONDS = 600  # 10 min to complete the round-trip


def _provider_meta(provider: Provider) -> dict[str, str]:
    if provider == "google":
        return {
            "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
            "token": "https://oauth2.googleapis.com/token",
            "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
        }
    if provider == "microsoft":
        tenant = settings.microsoft_tenant or "common"
        base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
        return {
            "authorize": f"{base}/authorize",
            "token": f"{base}/token",
            "userinfo": "https://graph.microsoft.com/oidc/userinfo",
            "client_id": settings.microsoft_client_id,
            "client_secret": settings.microsoft_client_secret,
        }
    raise AppError(f"Unsupported SSO provider '{provider}'.")


def is_configured(provider: Provider) -> bool:
    if not settings.sso_enabled or provider not in SUPPORTED:
        return False
    meta = _provider_meta(provider)
    return bool(meta["client_id"] and meta["client_secret"])


def callback_url(provider: Provider) -> str:
    # Microsoft uses the EXACT redirect_uri registered in Azure (must match the
    # token exchange too). Others derive it from the backend base URL.
    if provider == "microsoft":
        return settings.ms_redirect_uri
    return f"{settings.api_base_url.rstrip('/')}/api/auth/oauth/{provider}/callback"


def allowed_redirect(redirect_uri: str | None) -> str:
    """Vet the frontend's redirect_uri against the CORS allow-list (so SSO can't
    be abused as an open redirect). Falls back to the configured default."""
    default = settings.frontend_success_redirect
    if not redirect_uri:
        return default
    try:
        u = urlparse(redirect_uri)
        origin = f"{u.scheme}://{u.netloc}"
    except ValueError:
        return default
    allowed = set(settings.cors_origin_list)
    d = urlparse(default)
    allowed.add(f"{d.scheme}://{d.netloc}")
    if "*" in allowed or origin in allowed:
        return redirect_uri
    log.warning("oauth_redirect_rejected", redirect_uri=redirect_uri)
    return default


# --- Signed state (carries the frontend redirect_uri across the round-trip) --
def encode_state(provider: Provider, redirect_uri: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "provider": provider,
        "redirect_uri": redirect_uri,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=STATE_TTL_SECONDS)).timestamp()),
        "typ": "oauth_state",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_state(state: str) -> dict[str, Any]:
    try:
        data = jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise AppError("Invalid or expired SSO state.") from exc
    if data.get("typ") != "oauth_state":
        raise AppError("Invalid SSO state.")
    return data


def authorize_url(provider: Provider, state: str) -> str:
    meta = _provider_meta(provider)
    params = {
        "client_id": meta["client_id"],
        "redirect_uri": callback_url(provider),
        "response_type": "code",
        "response_mode": "query",
        "scope": SCOPE,
        "state": state,
        "prompt": "select_account",
    }
    if provider == "google":
        params["access_type"] = "online"
        params["include_granted_scopes"] = "true"
    return f"{meta['authorize']}?{urlencode(params)}"


async def exchange_code_for_userinfo(provider: Provider, code: str) -> dict[str, Any]:
    """Exchange the authorization code for tokens, then return the OIDC userinfo
    ({email, name, ...}). Raises on any provider/HTTP failure."""
    meta = _provider_meta(provider)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                meta["token"],
                data={
                    "client_id": meta["client_id"],
                    "client_secret": meta["client_secret"],
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": callback_url(provider),
                },
                headers={"Accept": "application/json"},
            )
            token_resp.raise_for_status()
            access_token = token_resp.json().get("access_token")
            if not access_token:
                raise UpstreamUnavailableError("SSO provider returned no access token.")

            info_resp = await client.get(
                meta["userinfo"],
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            info_resp.raise_for_status()
            return info_resp.json()
    except httpx.HTTPError as exc:
        log.warning("oauth_exchange_failed", provider=provider, error=str(exc))
        raise UpstreamUnavailableError(
            "Could not complete the SSO exchange with the provider.",
            details={"reason": str(exc)},
        ) from exc


def extract_identity(info: dict[str, Any]) -> tuple[str, str]:
    """Pull (email, display_name) out of an OIDC userinfo payload."""
    email = info.get("email") or info.get("preferred_username") or info.get("upn")
    if not email:
        raise AppError("SSO provider did not return an email address.")
    name = (
        info.get("name")
        or " ".join(filter(None, [info.get("given_name"), info.get("family_name")])).strip()
        or email.split("@")[0]
    )
    return str(email), str(name)
