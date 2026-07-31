"""JWT verification.

Verify-only by design. This service has no login, no refresh, no password
hashing and no token minting — ``ra_backend`` remains the sole issuer, so there
is exactly one place in the estate that can create a session.
"""

from __future__ import annotations

from typing import Any

import jwt

from app.core.config import settings


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate an access token. Raises ``jwt.PyJWTError`` on failure."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
