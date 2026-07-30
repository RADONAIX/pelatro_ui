"""Read-only identity bridge to the existing ``administration`` schema.

Deliberately raw SQL rather than importing ``ra_backend``'s ORM models: this
service must not take a code dependency on the other codebase, and the explicit
SELECT documents exactly which columns we touch. The engine behind
``IdentitySessionFactory`` is read-only at the Postgres level (see
``core.database``), so nothing here can mutate the existing backend's data.

A tiny in-process TTL cache keeps a burst of UI calls (the rule catalogue alone
fans out to several endpoints) from re-querying the identity DB per request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text

from app.core.config import settings
from app.core.database import IdentitySessionFactory
from app.core.errors import AuthenticationError, UpstreamUnavailableError
from app.core.logging import get_logger

log = get_logger("identity")


@dataclass(frozen=True)
class IdentityUser:
    id: str
    email: str
    full_name: str
    role_id: str
    is_active: bool
    must_reset_password: bool


_USER_SQL = text(
    """
    SELECT u.id,
           u.email,
           u.full_name,
           u.role_id,
           u.status,
           u.deleted_at,
           u.must_reset_password
      FROM administration.users u
     WHERE u.id = :user_id
    """
)

# A session id in the token means the user can be logged out from the other
# service; honour that immediately rather than waiting for the token to expire.
_SESSION_SQL = text(
    """
    SELECT s.revoked_at, s.expires_at
      FROM administration.user_sessions s
     WHERE s.id = :session_id
    """
)


_cache: dict[str, tuple[float, IdentityUser]] = {}


def _cache_get(user_id: str) -> IdentityUser | None:
    if settings.identity_cache_seconds <= 0:
        return None
    hit = _cache.get(user_id)
    if hit is None:
        return None
    expires_at, user = hit
    if expires_at <= time.monotonic():
        _cache.pop(user_id, None)
        return None
    return user


def _cache_put(user: IdentityUser) -> None:
    if settings.identity_cache_seconds <= 0:
        return
    if len(_cache) > 5_000:  # unbounded growth guard for a long-lived worker
        _cache.clear()
    _cache[user.id] = (time.monotonic() + settings.identity_cache_seconds, user)


def invalidate_cache(user_id: str | None = None) -> None:
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(user_id, None)


async def load_user(user_id: str) -> IdentityUser:
    cached = _cache_get(user_id)
    if cached is not None:
        return cached

    try:
        async with IdentitySessionFactory() as db:
            row = (await db.execute(_USER_SQL, {"user_id": user_id})).mappings().first()
    except Exception as exc:
        log.warning("identity_lookup_failed", error=str(exc))
        raise UpstreamUnavailableError(
            "Identity database is unreachable.", details={"reason": str(exc)}
        ) from exc

    if row is None:
        raise AuthenticationError("User no longer exists.")

    is_active = row["deleted_at"] is None and (row["status"] or "").lower() == "active"
    user = IdentityUser(
        id=row["id"],
        email=row["email"],
        full_name=row["full_name"],
        role_id=row["role_id"],
        is_active=is_active,
        must_reset_password=bool(row["must_reset_password"]),
    )
    _cache_put(user)
    return user


async def session_is_valid(session_id: str) -> bool:
    """False if the session was revoked (signed out elsewhere) or has expired.

    Not cached: revocation must take effect on the next request, which is the
    whole point of binding tokens to a session row.
    """
    try:
        async with IdentitySessionFactory() as db:
            row = (
                await db.execute(_SESSION_SQL, {"session_id": session_id})
            ).mappings().first()
    except Exception as exc:
        log.warning("identity_session_lookup_failed", error=str(exc))
        raise UpstreamUnavailableError(
            "Identity database is unreachable.", details={"reason": str(exc)}
        ) from exc

    if row is None or row["revoked_at"] is not None:
        return False
    expires_at: datetime = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > datetime.now(UTC)


async def ping() -> bool:
    try:
        async with IdentitySessionFactory() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
