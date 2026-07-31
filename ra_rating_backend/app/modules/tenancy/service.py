"""Resolving which tenant a caller is acting for.

Order of preference, most authoritative first:

1. **A ``tenant_id`` claim in the token.** Nothing issues one today, but reading
   it first means the day ``ra_backend`` adds one, this service already honours
   it and the membership table quietly becomes a fallback.
2. **An explicitly requested tenant**, from the ``X-Tenant-ID`` header — the
   tenant switcher a group operator's analyst needs. Checked against membership,
   so requesting one you do not belong to is a refusal rather than an empty page.
3. **The user's default membership.**
4. **The deployment's default tenant**, for a single-operator install where
   nobody has been granted anything explicitly. This is what keeps every existing
   deployment working unchanged after tenancy ships.

Step 4 is the compatibility hinge and it is worth being clear about what it
costs: a user with no membership rows sees the default tenant. In a
single-tenant deployment that is exactly right. In a multi-tenant one it means
membership must actually be granted, so :func:`is_multi_tenant` reports whether
more than one tenant exists, and the resolver refuses the silent fallback once
it does.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import PermissionDeniedError
from app.modules.tenancy.models import Tenant, TenantMembership


@dataclass(frozen=True, slots=True)
class TenantScope:
    """The tenant a request acts in, plus the ones it could switch to."""

    tenant_id: str
    code: str = ""
    name: str = ""
    #: Every tenant this user may act in, for the switcher. One entry in a
    #: single-operator deployment.
    available: tuple[str, ...] = ()

    @property
    def can_switch(self) -> bool:
        return len(self.available) > 1


async def memberships(db: AsyncSession, user_id: str) -> list[TenantMembership]:
    return list(
        (
            await db.execute(
                select(TenantMembership)
                .where(TenantMembership.user_id == user_id)
                .order_by(TenantMembership.is_default.desc(), TenantMembership.tenant_id)
            )
        )
        .scalars()
        .all()
    )


async def tenant_count(db: AsyncSession) -> int:
    from sqlalchemy import func

    return int(
        (
            await db.execute(
                select(func.count()).select_from(Tenant).where(Tenant.status == "ACTIVE")
            )
        ).scalar_one()
    )


async def resolve(
    db: AsyncSession,
    user_id: str,
    *,
    claim: str | None = None,
    requested: str | None = None,
) -> TenantScope:
    """Work out the tenant for this request, or refuse.

    Never returns a tenant the caller is not entitled to. A request for one they
    do not belong to raises rather than silently falling back, because a silent
    fallback means an analyst who mis-typed a tenant id spends an afternoon
    wondering why the estate looks wrong.
    """
    rows = await memberships(db, user_id)
    available = tuple(m.tenant_id for m in rows)

    if claim:
        # A token claim is authoritative — the issuer has already decided. It is
        # still checked against membership when membership exists, because two
        # sources of truth that disagree should fail loudly.
        if available and claim not in available:
            raise PermissionDeniedError(
                "The token names a tenant this user does not belong to."
            )
        return await _scope(db, claim, available or (claim,))

    if requested:
        if requested not in available:
            raise PermissionDeniedError(
                f"You do not have access to tenant '{requested}'.",
                details={"available": list(available)},
            )
        return await _scope(db, requested, available)

    if rows:
        return await _scope(db, rows[0].tenant_id, available)

    # No membership. Fine while there is one tenant; a configuration error once
    # there are several, and reported as one rather than guessed at.
    if await tenant_count(db) > 1:
        raise PermissionDeniedError(
            "This deployment has several tenants and your account is not a member "
            "of any of them. Ask an administrator to grant you access.",
        )
    return await _scope(db, settings.default_tenant_id, (settings.default_tenant_id,))


async def _scope(
    db: AsyncSession, tenant_id: str, available: tuple[str, ...]
) -> TenantScope:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is not None and tenant.status != "ACTIVE":
        raise PermissionDeniedError(
            f"Tenant '{tenant.code}' is suspended.",
            details={"tenant_id": tenant_id},
        )
    return TenantScope(
        tenant_id=tenant_id,
        code=getattr(tenant, "code", ""),
        name=getattr(tenant, "name", ""),
        available=available,
    )
