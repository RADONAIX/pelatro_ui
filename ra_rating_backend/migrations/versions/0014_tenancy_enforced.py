"""M8 — real tenant isolation, enforced by Postgres rather than by convention.

Three things happen here, and the third is the one that bites.

**A tenant registry.** ``ra_rule.tenant`` and ``ra_rule.tenant_membership``.
Neither carries a ``tenant_id`` and neither is under RLS: resolving *which*
tenant a request belongs to has to happen before any tenant scope exists, and a
registry you can only read once you know your tenant is a registry nobody can
read.

**Real policies.** Migrations 0010-0012 created every policy as
``USING (true)``, which is an honest placeholder and no isolation at all. They
are replaced with ``tenant_id = current_setting('ra.tenant_id', true)``. The
``true`` second argument makes a missing setting return NULL rather than raise,
and ``tenant_id = NULL`` is NULL, so **an unset setting matches no rows**. That
is deliberate: the failure mode of forgetting to bind a tenant is an empty
result, not somebody else's data.

**FORCE ROW LEVEL SECURITY.** Without it none of the above does anything for us,
because Postgres exempts a table's *owner* from its own policies and this service
connects as the owner. ``ENABLE`` alone is why an RLS rollout can look complete,
pass a casual test, and protect nothing. ``FORCE`` is the line that makes it real.

The consequence, which is the whole risk of this migration: every connection that
touches ``ra_rule`` must now set ``ra.tenant_id`` or see nothing. The request path
does it on principal resolution; the seeder and the backfill do it through
``tenancy.context.scoped``. Anything else — a psql session, an ad-hoc script —
must do it by hand:

    SELECT set_config('ra.tenant_id', '<uuid>', false);

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels = None
depends_on = None

RULE = "ra_rule"
DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

#: The setting the policies read. Namespaced so it cannot collide with anything
#: Postgres or another extension defines.
GUC = "ra.tenant_id"

#: Every tenant-scoped table, across migrations 0010, 0011 and 0012.
_SCOPED_TABLES: tuple[str, ...] = (
    # 0010 — the canonical rule model.
    "rule_stage", "rule_type", "rule_stacking_policy", "rule_conflict_group",
    "rule_import_profile", "rule", "rule_version", "rule_condition_group",
    "rule_condition", "rule_action", "rule_parameter", "rule_dependency",
    "rule_fallback", "rule_set", "rule_set_member", "rule_ingestion_batch",
    "rule_source_lineage", "rule_validation_issue", "rule_audit",
    # 0011 — the charging / prepaid / postpaid metadata catalogue.
    "charging_unit", "pulse_profile", "charge_limit_profile", "balance_type",
    "ocs_profile", "reservation_policy", "proration_profile",
    "credit_limit_profile", "charging_profile", "balance_bucket_definition",
    "balance_priority", "billing_cycle", "invoice_component", "recurring_charge",
    "one_time_charge", "usage_aggregation_profile", "late_fee_profile",
    # 0012 — ingestion records.
    "rule_ingestion_record",
)

#: Read what belongs to my tenant; write only what will belong to it. Both halves
#: are needed: USING alone would let a row be inserted for another tenant and
#: then be invisible to the person who inserted it.
_USING = f"tenant_id = current_setting('{GUC}', true)"


def upgrade() -> None:
    _create_registry()
    _seed_default_tenant()
    _enforce()


def _create_registry() -> None:
    op.create_table(
        "tenant",
        sa.Column("tenant_id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, server_default="", nullable=False),
        # ACTIVE | SUSPENDED. Suspension leaves the data in place and makes it
        # unreachable — the opposite of deletion, which an assurance platform
        # under a retention obligation may not do.
        sa.Column("status", sa.String(16), server_default="ACTIVE", nullable=False),
        sa.Column("settings", JSONB, server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_tenant_code"),
        schema=RULE,
    )
    op.create_index("ix_tenant_code", "tenant", ["code"], schema=RULE)
    op.create_index("ix_tenant_status", "tenant", ["status"], schema=RULE)

    op.create_table(
        "tenant_membership",
        sa.Column("membership_id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id", sa.String(36),
            sa.ForeignKey(f"{RULE}.tenant.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        # No foreign key to administration.users: that schema is read-only to
        # this service and reached over a different connection, so the constraint
        # could not be enforced even if declaring it were allowed.
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("is_default", sa.Boolean, server_default="false", nullable=False),
        sa.Column("granted_by", sa.String(36), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership"),
        schema=RULE,
    )
    op.create_index(
        "ix_tenant_membership_user", "tenant_membership", ["user_id"], schema=RULE
    )


def _seed_default_tenant() -> None:
    """Register the tenant every existing row is already stamped with.

    Without this the estate is owned by a tenant that does not exist, and the
    first thing anyone does after upgrading is discover their data has no home.
    """
    op.execute(
        sa.text(
            f"""
            INSERT INTO {RULE}.tenant (tenant_id, code, name, description)
            VALUES (
                :tenant_id, 'DEFAULT', 'Default tenant',
                'Created by migration 0013. Every row written before tenancy was '
                'enforced belongs to it.'
            )
            ON CONFLICT (tenant_id) DO NOTHING
            """
        ).bindparams(tenant_id=DEFAULT_TENANT)
    )


def _partitions() -> list[str]:
    """Partitions of a tenant-scoped table, which need their own policies.

    A policy on a partitioned parent applies to a query that goes *through* the
    parent. A query against a partition directly does not see it — the partition
    has its own (empty) policy set, and an empty set on a table without RLS
    enabled means no restriction at all. So `rule_ingestion_record_p3` was
    readable by anyone who named it, which is exactly the sort of gap that
    survives a review of the parent table.
    """
    rows = op.get_bind().execute(
        sa.text(
            """
            SELECT c.relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = :schema
               AND c.relkind = 'r'
               AND c.relispartition
               AND EXISTS (
                   SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = c.oid AND a.attname = 'tenant_id'
                      AND a.attnum > 0
               )
             ORDER BY c.relname
            """
        ),
        {"schema": RULE},
    ).scalars().all()
    return list(rows)


def _enforce() -> None:
    for table in (*_SCOPED_TABLES, *_partitions()):
        qualified = f"{RULE}.{table}"
        # 0010-0012 created these as USING (true). Replaced, not amended: an
        # ALTER POLICY that half-applied would leave a table readable and
        # unwritable, which is harder to notice than either extreme.
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {qualified}")
        op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        # The line that makes it real. Without FORCE, the owner — which is how
        # this service connects — is exempt from its own policies, and the whole
        # rollout protects nothing while looking complete.
        op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {qualified} "
            f"USING ({_USING}) WITH CHECK ({_USING})"
        )


def downgrade() -> None:
    """Back to permissive. The tenant columns and data stay exactly as they are —
    only the enforcement is lifted, so a rollback is a policy change rather than
    a data migration."""
    for table in (*_SCOPED_TABLES, *_partitions()):
        qualified = f"{RULE}.{table}"
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {qualified}")
        op.execute(f"ALTER TABLE {qualified} NO FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {qualified} "
            "USING (true) WITH CHECK (true)"
        )
    op.drop_table("tenant_membership", schema=RULE)
    op.drop_table("tenant", schema=RULE)
