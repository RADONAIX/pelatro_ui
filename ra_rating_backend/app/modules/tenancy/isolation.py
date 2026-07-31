"""Is tenant isolation actually in force, or only configured?

This module exists because of a mistake that is very easy to make and almost
impossible to notice. Migration 0014 creates correct policies, enables row-level
security and adds ``FORCE`` — and if the service connects as a **superuser**,
none of it does anything. Postgres exempts superusers from RLS unconditionally;
``FORCE`` closes the table-*owner* exemption, not the superuser one.

The result is a deployment that passes a code review, passes a schema
inspection, reports ``relrowsecurity = true`` on every table, and provides no
isolation whatsoever. The only way to know is to ask the database whether *this
role, right now* is subject to the policies.

So the check is empirical rather than declarative: write a row as one tenant,
try to read it as another, and see what happens. Anything less is a check on our
intentions rather than on the system's behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.logging import get_logger

log = get_logger("tenancy")

_ROLE_SQL = text(
    """
    SELECT current_user      AS role_name,
           rolsuper          AS is_superuser,
           rolbypassrls      AS bypasses_rls
      FROM pg_roles
     WHERE rolname = current_user
    """
)

_POLICY_SQL = text(
    """
    SELECT count(*) FILTER (WHERE c.relrowsecurity)      AS enabled,
           count(*) FILTER (WHERE c.relforcerowsecurity) AS forced,
           count(*)                                      AS total
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = :schema
       AND c.relkind = 'r'
       -- Partitions carry their own policies (migration 0014 forces them too),
       -- but counting them here would double-count one logical table eight times
       -- and make the ratio meaningless.
       AND NOT c.relispartition
       -- The tenant registry itself is deliberately unscoped: resolving which
       -- tenant a request belongs to has to happen before any scope exists, so a
       -- registry behind that scope is one nobody can read.
       AND c.relname NOT IN ('tenant', 'tenant_membership')
       AND EXISTS (
           SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND a.attnum > 0
       )
    """
)


@dataclass(frozen=True, slots=True)
class IsolationStatus:
    role: str
    is_superuser: bool
    bypasses_rls: bool
    tables: int
    enabled: int
    forced: int

    @property
    def policies_complete(self) -> bool:
        """Every tenant-scoped table has RLS enabled and forced."""
        return self.tables > 0 and self.enabled == self.tables == self.forced

    @property
    def enforced(self) -> bool:
        """Whether isolation actually binds *this* connection.

        Both halves are required, and the second is the one people forget: a
        superuser sails through a perfect set of policies without touching them.
        """
        return self.policies_complete and not (self.is_superuser or self.bypasses_rls)

    @property
    def problem(self) -> str:
        if self.is_superuser or self.bypasses_rls:
            attribute = "a superuser" if self.is_superuser else "granted BYPASSRLS"
            return (
                f"The service connects as '{self.role}', which is {attribute}. "
                "Postgres exempts such roles from row-level security entirely, so "
                "the tenant policies are inert and every tenant can read every "
                "other tenant's rules. Create a dedicated non-superuser role — see "
                "deploy/postgres/tenant-isolation.sql — and point RATING_DB_USER at it."
            )
        if not self.policies_complete:
            return (
                f"{self.tables - self.forced} of {self.tables} tenant-scoped tables "
                "are missing FORCE ROW LEVEL SECURITY. Run `alembic upgrade head`."
            )
        return ""


async def inspect(engine: AsyncEngine, schema: str) -> IsolationStatus:
    async with engine.connect() as conn:
        role = (await conn.execute(_ROLE_SQL)).one()
        counts = (await conn.execute(_POLICY_SQL, {"schema": schema})).one()
    return IsolationStatus(
        role=role.role_name,
        is_superuser=bool(role.is_superuser),
        bypasses_rls=bool(role.bypasses_rls),
        tables=int(counts.total),
        enabled=int(counts.enabled),
        forced=int(counts.forced),
    )


async def report_at_startup(engine: AsyncEngine, schema: str) -> IsolationStatus | None:
    """Say loudly, at boot, whether the isolation the deployment thinks it has is real.

    Loud rather than fatal, following the precedent set for an insecure JWT
    secret: refusing to start would take down a working single-tenant deployment
    on upgrade, and a single-tenant deployment genuinely does not need this. The
    log line is unambiguous, and ``/health`` reports it too, so a multi-tenant
    deployment cannot go live believing it is isolated when it is not.
    """
    try:
        status = await inspect(engine, schema)
    except Exception as exc:
        log.warning("tenant_isolation_check_failed", error=str(exc))
        return None

    if status.enforced:
        log.info(
            "tenant_isolation_enforced",
            role=status.role,
            tables=status.tables,
        )
    else:
        log.error(
            "tenant_isolation_not_enforced",
            role=status.role,
            superuser=status.is_superuser,
            bypassrls=status.bypasses_rls,
            tables=status.tables,
            forced=status.forced,
            hint=status.problem,
        )
    return status
