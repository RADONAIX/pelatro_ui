"""Tenant isolation, tested against the database rather than against our intent.

The trap this file is built around: row-level security can be configured
perfectly and enforce nothing. Postgres exempts superusers from RLS
unconditionally, and ``FORCE ROW LEVEL SECURITY`` closes the table-*owner*
exemption but not that one. A deployment connecting as ``postgres`` reports
``relrowsecurity = true`` on every table, passes a schema review, and lets every
tenant read every other tenant's tariffs.

So the isolation tests connect as a **purpose-made non-superuser role** and skip
when they cannot create one. A test that ran as the superuser would pass — it
would read the rows it expected to read — while proving the opposite of what it
claims. Skipping is honest; passing would not be.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import NullPool, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.modules.rules.canonical.lookups import RuleStage
from app.modules.tenancy import context, isolation

pytestmark = pytest.mark.asyncio

TENANT_A = settings.default_tenant_id
TENANT_B = "22222222-2222-2222-2222-222222222222"

_ROLE = "ra_isolation_probe"
_PASSWORD = "probe"
_PROBE_URL = (
    f"postgresql+asyncpg://{_ROLE}:{_PASSWORD}@{settings.rating_db_host}"
    f":{settings.rating_db_port}/{settings.rating_db_name}"
)

_GRANTS = (
    f"CREATE ROLE {_ROLE} LOGIN PASSWORD '{_PASSWORD}' NOSUPERUSER NOBYPASSRLS",
    f"GRANT CONNECT ON DATABASE {settings.rating_db_name} TO {_ROLE}",
    f"GRANT USAGE ON SCHEMA rating, ra_rule TO {_ROLE}",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
    f"IN SCHEMA rating, ra_rule TO {_ROLE}",
)


async def _drop_role(conn) -> None:
    """Remove the role and everything granted to it.

    ``DROP ROLE`` fails while any privilege still references the role, so a
    previous run that granted table rights and did not clean up leaves the next
    run unable to create its own probe — and the tests skip for a reason that has
    nothing to do with what they test. ``DROP OWNED BY`` clears the grants first.
    """
    exists = (
        await conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": _ROLE}
        )
    ).scalar_one_or_none()
    if not exists:
        return
    await conn.execute(text(f"REASSIGN OWNED BY {_ROLE} TO CURRENT_USER"))
    await conn.execute(text(f"DROP OWNED BY {_ROLE}"))
    await conn.execute(text(f"DROP ROLE IF EXISTS {_ROLE}"))


@pytest_asyncio.fixture
async def probe_engine():
    """An engine connected as a role that RLS actually applies to.

    Skips rather than fails when the role cannot be created: a developer without
    CREATEROLE should not be blocked, and the alternative — running as superuser
    — would produce a green test that proves nothing.
    """
    admin = create_async_engine(
        settings.rating_database_url, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin.connect() as conn:
            await conn.execute(text("SELECT 1"))
            await _drop_role(conn)
            for statement in _GRANTS:
                await conn.execute(text(statement))
    except Exception as exc:
        await admin.dispose()
        pytest.skip(f"cannot create the isolation probe role: {exc}")

    engine = create_async_engine(
        _PROBE_URL,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": settings.rating_db_schema}},
    )
    try:
        status = await isolation.inspect(engine, "ra_rule")
        if not status.policies_complete:
            pytest.skip(
                "tenant policies are not fully applied — run `alembic upgrade head`"
            )
        yield engine
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await _drop_role(conn)
        await admin.dispose()


def _sessions(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False)


async def _stage_count(db) -> int:
    return int(
        (await db.execute(select(func.count()).select_from(RuleStage))).scalar_one()
    )


# --- The check itself --------------------------------------------------------


async def test_the_isolation_check_reports_enforcement_truthfully(probe_engine):
    """The check has to be right about itself before anything else it says means
    anything."""
    status = await isolation.inspect(probe_engine, "ra_rule")
    assert not status.is_superuser
    assert not status.bypasses_rls
    assert status.policies_complete
    assert status.enforced
    assert status.problem == ""


async def test_a_superuser_connection_is_reported_as_not_enforced():
    """The failure this whole module exists for. The default connection in this
    repo is a superuser, so it is the one that must be caught."""
    from app.core.database import engine as app_engine

    status = await isolation.inspect(app_engine, "ra_rule")
    if not (status.is_superuser or status.bypasses_rls):
        pytest.skip("the configured role is already non-superuser")
    assert not status.enforced
    assert "superuser" in status.problem or "BYPASSRLS" in status.problem
    assert "tenant-isolation.sql" in status.problem


# --- Reads -------------------------------------------------------------------


async def test_an_unbound_connection_sees_nothing(probe_engine):
    """Fail closed. Forgetting to bind a tenant must produce an empty result, not
    everybody's data — an empty result is a bug report, the other is a breach."""
    async with _sessions(probe_engine)() as db:
        assert await _stage_count(db) == 0


async def test_a_bound_connection_sees_its_own_tenant(probe_engine):
    async with _sessions(probe_engine)() as db:
        await context.bind_session(db, TENANT_A)
        assert await _stage_count(db) > 0


async def test_a_connection_cannot_see_another_tenant(probe_engine):
    """The headline. Tenant B is registered and owns nothing, so it must see
    nothing — not an error, not a subset, nothing."""
    async with _sessions(probe_engine)() as db:
        await context.bind_session(db, TENANT_B)
        assert await _stage_count(db) == 0


async def test_a_partition_cannot_be_read_around_its_parent(probe_engine):
    """`rule_ingestion_record` is partitioned, and a policy on a partitioned
    parent does not apply to a query that names a partition directly. Before
    migration 0014 forced the partitions too, anyone who knew the name
    `rule_ingestion_record_p0` could read every tenant's import records."""
    async with _sessions(probe_engine)() as db:
        await context.bind_session(db, TENANT_B)
        count = (
            await db.execute(
                text("SELECT count(*) FROM ra_rule.rule_ingestion_record_p0")
            )
        ).scalar_one()
        assert count == 0


# --- Writes ------------------------------------------------------------------


async def test_a_write_for_another_tenant_is_refused(probe_engine):
    """`WITH CHECK`, not just `USING`. Without it a row could be inserted for
    another tenant and then be invisible to whoever inserted it — which reads as
    data loss and is worse than a refusal."""
    async with _sessions(probe_engine)() as db:
        await context.bind_session(db, TENANT_A)
        with pytest.raises(Exception, match=r"(?i)policy|denied|violat"):
            await db.execute(
                text(
                    "INSERT INTO ra_rule.rule_stage "
                    "(rule_stage_id, tenant_id, code, name, execution_order) "
                    "VALUES ('probe-cross', :tenant, 'PROBE_CROSS', 'X', 9999)"
                ),
                {"tenant": TENANT_B},
            )
            await db.flush()
        await db.rollback()


async def test_a_write_for_my_own_tenant_is_allowed(probe_engine):
    """The control. A test that only proves writes fail has not proved isolation,
    it has proved the table is read-only."""
    async with _sessions(probe_engine)() as db:
        await context.bind_session(db, TENANT_A)
        await db.execute(
            text(
                "INSERT INTO ra_rule.rule_stage "
                "(rule_stage_id, tenant_id, code, name, execution_order) "
                "VALUES ('probe-own', :tenant, 'PROBE_OWN', 'X', 9998)"
            ),
            {"tenant": TENANT_A},
        )
        await db.flush()
        await db.rollback()


# --- The context variable ----------------------------------------------------


def test_the_orm_default_follows_the_current_tenant():
    """The other half of the mechanism. The session setting decides what we may
    read; this decides what a new row is stamped with, and a mismatch produces
    rows that are written correctly and then invisible."""
    from app.modules.rules.canonical.rule import CanonicalRule

    default = CanonicalRule.__table__.c.tenant_id.default.arg
    with context.tenant_context(TENANT_B):
        assert default(None) == TENANT_B
    assert default(None) == TENANT_A


def test_the_tenant_registry_is_deliberately_unscoped():
    """A registry you can only read once you know your tenant is a registry
    nobody can read. Pinned, because 'every table gets a policy' is the obvious
    and wrong instinct."""
    from app.modules.tenancy.models import Tenant, TenantMembership

    assert Tenant.__table__.name == "tenant"
    assert TenantMembership.__table__.name == "tenant_membership"
    # Neither appears in the migration's scoped list.
    import importlib.util
    import pathlib

    path = pathlib.Path("migrations/versions/0014_tenancy_enforced.py")
    spec = importlib.util.spec_from_file_location("m0014", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "tenant" not in module._SCOPED_TABLES
    assert "tenant_membership" not in module._SCOPED_TABLES
