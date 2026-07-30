"""The tenant a piece of work belongs to, and how the database is told about it.

Two mechanisms, and they have to agree:

**A context variable**, so ORM defaults stamp the right ``tenant_id`` on a new
row without every call site passing one. Before this, ``TenantMixin`` defaulted
to ``settings.default_tenant_id`` — correct while one tenant existed and silently
wrong the moment a second one did, because a row written for tenant B would be
stamped A and then be invisible to the people who created it.

**A Postgres session setting**, ``ra.tenant_id``, which the row-level security
policies read. This is the half that is actually load-bearing: the context
variable decides what we *write*, the GUC decides what the database will *let us
read*. A bug in the first is a wrong row; a bug in the second is a data breach,
which is why the policies fail closed — an unset GUC matches nothing rather than
everything.

``set_config(..., true)`` makes the setting transaction-local, so it cannot leak
between requests that happen to be handed the same pooled connection. That is not
a detail: a connection pool plus a session-level setting is precisely how one
tenant ends up reading another's data under load.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

#: The tenant the current task is acting for. Read by ``TenantMixin`` defaults.
_current_tenant: ContextVar[str] = ContextVar(
    "ra_current_tenant", default=settings.default_tenant_id
)

#: The Postgres setting the RLS policies read.
GUC = "ra.tenant_id"


def current_tenant() -> str:
    return _current_tenant.get()


def set_current_tenant(tenant_id: str) -> Token[str]:
    return _current_tenant.set(tenant_id)


def reset_current_tenant(token: Token[str]) -> None:
    _current_tenant.reset(token)


@contextmanager
def tenant_context(tenant_id: str) -> Iterator[str]:
    """Run a block as a given tenant. For scripts, jobs and tests."""
    token = set_current_tenant(tenant_id)
    try:
        yield tenant_id
    finally:
        reset_current_tenant(token)


async def bind_session(db: AsyncSession, tenant_id: str) -> None:
    """Tell the database which tenant this transaction belongs to.

    Transaction-local on purpose. A session-level ``SET`` would survive the
    connection's return to the pool and be inherited by whoever picks it up next,
    which is a cross-tenant read waiting for enough traffic to happen.
    """
    await db.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": GUC, "value": tenant_id},
    )


@asynccontextmanager
async def scoped(db: AsyncSession, tenant_id: str) -> AsyncIterator[str]:
    """Bind both mechanisms for a block of work outside a request.

    What the seeder, the backfill and every scheduled job use. Without it they
    run with no GUC set and — once the policies are enforcing — see nothing at
    all, which is the correct behaviour and a baffling one to debug.
    """
    token = set_current_tenant(tenant_id)
    try:
        await bind_session(db, tenant_id)
        yield tenant_id
    finally:
        reset_current_tenant(token)
