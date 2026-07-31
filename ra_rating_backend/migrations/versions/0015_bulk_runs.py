"""Bulk lifecycle runs — the record behind an asynchronous bulk operation.

Plan step B4. Tenant-scoped and policy-enforced from creation, like every other
table in `ra_rule`: adding the RLS policy later would mean a window in which one
tenant could read another's job history, and job history names rule sets.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.core.config import settings

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

SCHEMA = settings.rule_db_schema
TABLE = "rule_bulk_run"
GUC = "app.tenant_id"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("bulk_run_id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id", sa.String(36), nullable=False,
            server_default=settings.default_tenant_id,
        ),
        sa.Column("operation", sa.String(24), nullable=False),
        sa.Column("selector", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("dry_run", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("atomic", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("force", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("comment", sa.Text, nullable=False, server_default=""),
        sa.Column("total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("processed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("applied", sa.Integer, nullable=False, server_default="0"),
        sa.Column("counts", JSONB, nullable=False, server_default="{}"),
        sa.Column("blocked", JSONB, nullable=False, server_default="[]"),
        sa.Column("snapshot", JSONB, nullable=True),
        sa.Column(
            "touched_live_pricing", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column("error", sa.Text, nullable=False, server_default=""),
        sa.Column("error_details", JSONB, nullable=True),
        sa.Column("actor_id", sa.String(36), nullable=True),
        sa.Column("actor_name", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{TABLE}_tenant_id", TABLE, ["tenant_id"], schema=SCHEMA
    )
    op.create_index(f"ix_{TABLE}_status", TABLE, ["status"], schema=SCHEMA)
    op.create_index(
        f"ix_{TABLE}_tenant_created", TABLE, ["tenant_id", "created_at"], schema=SCHEMA
    )
    op.create_index(
        f"ix_{TABLE}_tenant_status", TABLE, ["tenant_id", "status"], schema=SCHEMA
    )

    # FORCE as well as ENABLE: without it the table's owner — which is who the
    # service usually connects as — is exempt from its own policy, and the
    # isolation reads as configured while doing nothing.
    qualified = f"{SCHEMA}.{TABLE}"
    op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {qualified} "
        f"USING (tenant_id = current_setting('{GUC}', true)) "
        f"WITH CHECK (tenant_id = current_setting('{GUC}', true))"
    )


    # A snapshot compiled from the canonical store points at `ra_rule.rule_set`,
    # which the legacy `rule_set_id` FK (to `rating.rule_sets`) cannot hold.
    # Additive, and null for every snapshot compiled the legacy way.
    op.add_column(
        "rule_snapshots",
        sa.Column("canonical_rule_set_id", sa.String(36), nullable=True),
        schema=settings.rating_db_schema,
    )
    op.create_index(
        "ix_rule_snapshots_canonical_rule_set",
        "rule_snapshots",
        ["canonical_rule_set_id"],
        schema=settings.rating_db_schema,
    )


def downgrade() -> None:
    rating = settings.rating_db_schema
    # IF EXISTS throughout: this revision landed in two pieces during
    # development, and a downgrade that fails halfway leaves the estate stamped
    # at a revision whose schema it does not have.
    op.execute(f"DROP INDEX IF EXISTS {rating}.ix_rule_snapshots_canonical_rule_set")
    op.execute(
        f"ALTER TABLE {rating}.rule_snapshots "
        f"DROP COLUMN IF EXISTS canonical_rule_set_id"
    )
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.{TABLE}")
