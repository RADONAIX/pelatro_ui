"""Shared FastAPI dependencies: DB session, principal resolution, RBAC guards."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

import jwt
from fastapi import Depends, Header, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import identity
from app.core.database import get_session
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.rbac import (
    PermAction,
    PermissionMap,
    RatingPermKey,
    has_permission,
    normalize,
)
from app.core.security import decode_token
from app.modules.access.models import RatingRolePermission
from app.modules.tenancy import context as tenant_context
from app.modules.tenancy import service as tenancy

DbSession = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    """The authenticated caller: identity from `administration`, rights from `rating`."""

    id: str
    email: str
    full_name: str
    role: str
    permissions: PermissionMap = field(default_factory=dict)
    session_id: str | None = None
    #: The tenant this request acts in. Every canonical write is stamped with it
    #: and every canonical read is confined to it by row-level security.
    tenant_id: str = ""
    #: Tenants this user could switch to. One entry in a single-operator install.
    available_tenants: tuple[str, ...] = ()

    def can(self, key: RatingPermKey, action: PermAction) -> bool:
        return has_permission(self.permissions, key, action)


async def _rating_permissions(db: AsyncSession, role: str) -> PermissionMap:
    row = (
        await db.execute(
            select(RatingRolePermission).where(RatingRolePermission.role_slug == role)
        )
    ).scalar_one_or_none()
    return normalize(row.permissions if row else None, role)


async def get_current_principal(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: DbSession,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> Principal:
    if creds is None or not creds.credentials:
        raise AuthenticationError("Missing bearer token.")
    try:
        payload = decode_token(creds.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired.") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid token.") from exc

    if payload.get("type") not in (None, "access"):
        raise AuthenticationError("Wrong token type for this endpoint.")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("Invalid token subject.")

    user = await identity.load_user(user_id)
    if not user.is_active:
        raise AuthenticationError("Account is disabled.")
    if user.must_reset_password:
        # The UI blocks the whole app behind its password-change gate; refusing
        # here means a stale tab can't keep authoring rules under a temp password.
        raise AuthenticationError("Password reset required before using this service.")

    session_id = payload.get("sid")
    if session_id and not await identity.session_is_valid(session_id):
        raise AuthenticationError("Session is no longer valid. Please sign in again.")

    scope = await tenancy.resolve(
        db,
        user.id,
        # Nothing issues this claim today. Read first anyway, so the day
        # `ra_backend` adds one this service already honours it.
        claim=payload.get("tenant_id") or payload.get("tid"),
        requested=x_tenant_id,
    )
    # Both halves, together and in this order. The context variable decides what
    # a write is stamped with; the session setting decides what the database will
    # let this transaction read. Binding one without the other produces rows that
    # are written correctly and then invisible, or visible rows written wrong.
    tenant_context.set_current_tenant(scope.tenant_id)
    await tenant_context.bind_session(db, scope.tenant_id)

    return Principal(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role_id,
        permissions=await _rating_permissions(db, user.role_id),
        session_id=session_id,
        tenant_id=scope.tenant_id,
        available_tenants=scope.available,
    )


CurrentUser = Annotated[Principal, Depends(get_current_principal)]


def require(key: RatingPermKey, action: PermAction = "view"):
    """Dependency factory enforcing a rating permission for the current principal.

    Use via ``dependencies=[Depends(require(...))]`` when the handler doesn't
    need the principal, or via ``principal_with(...)`` when it does.
    """

    async def _guard(principal: CurrentUser) -> Principal:
        if not principal.can(key, action):
            raise PermissionDeniedError(
                f"Role '{principal.role}' lacks '{action}' on '{key.value}'."
            )
        return principal

    return _guard


def principal_with(key: RatingPermKey, action: PermAction = "view"):
    """An annotation that both enforces a permission and injects the principal.

    ``principal: CurrentUser = Depends(require(...))`` is rejected by FastAPI —
    ``CurrentUser`` already carries a ``Depends`` in its ``Annotated``, and it
    refuses to combine that with a default. Folding the guard into the
    annotation is the supported form.
    """
    return Annotated[Principal, Depends(require(key, action))]


@dataclass
class Pagination:
    limit: int
    offset: int


def pagination(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


PageParams = Annotated[Pagination, Depends(pagination)]
