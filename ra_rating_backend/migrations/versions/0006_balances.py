"""bundle balance buckets, consumption ledger and usage counters

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-30

Stateful rating state. Bundles make a charge depend on every event that came
before it, which is why these tables exist and why the ledger is append-only.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "balance_buckets",
        sa.Column("id", sa.String(36), primary_key=True),
        # Account id for a shared bundle, subscriber id otherwise — resolving
        # the owner at allocation time keeps both on one code path.
        sa.Column("owner_key", sa.String(64), nullable=False),
        sa.Column("owner_type", sa.String(16), nullable=False, server_default="SUBSCRIBER"),
        sa.Column("subscriber_id", sa.String(64), nullable=True),
        sa.Column("account_id", sa.String(64), nullable=True),
        sa.Column("msisdn", sa.String(32), nullable=True),
        sa.Column("bundle_code", sa.String(64), nullable=False),
        sa.Column("service_type", sa.String(16), nullable=False),
        sa.Column("quota_unit", sa.String(16), nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("reset_period", sa.String(16), nullable=False, server_default="MONTHLY"),
        sa.Column("allocated", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("consumed", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("overflow", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("consumption_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_consumed_at", _ts(), nullable=True),
        sa.Column("source_system", sa.String(64), nullable=False, server_default="MANUAL"),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "owner_key", "bundle_code", "period_start", name="uq_balance_buckets_owner_key"
        ),
    )
    op.create_index("ix_balance_buckets_bundle_code", "balance_buckets", ["bundle_code"])
    op.create_index("ix_balance_buckets_subscriber_id", "balance_buckets", ["subscriber_id"])
    op.create_index("ix_balance_buckets_account_id", "balance_buckets", ["account_id"])
    op.create_index("ix_balance_buckets_msisdn", "balance_buckets", ["msisdn"])
    op.create_index(
        "ix_balance_buckets_lookup",
        "balance_buckets",
        ["owner_key", "bundle_code", "period_start", "period_end"],
    )
    op.create_index("ix_balance_buckets_period", "balance_buckets", ["period_start", "period_end"])

    op.create_table(
        "balance_ledger",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("bucket_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("cdr_enriched_id", sa.String(36), nullable=False),
        sa.Column("cdr_id", sa.String(128), nullable=False),
        sa.Column("event_timestamp", _ts(), nullable=False),
        sa.Column("requested", sa.Numeric(18, 4), nullable=False),
        sa.Column("consumed", sa.Numeric(18, 4), nullable=False),
        sa.Column("overflow", sa.Numeric(18, 4), nullable=False),
        sa.Column("balance_before", sa.Numeric(20, 4), nullable=False),
        sa.Column("balance_after", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("rule_key", sa.String(80), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["bucket_id"], ["balance_buckets.id"],
            name="fk_balance_ledger_bucket_id_balance_buckets", ondelete="CASCADE",
        ),
        # Replaying a run must not double-consume; the constraint makes it structural.
        sa.UniqueConstraint(
            "run_id", "cdr_enriched_id", "bucket_id", name="uq_balance_ledger_run_id"
        ),
    )
    op.create_index("ix_balance_ledger_bucket_id", "balance_ledger", ["bucket_id"])
    op.create_index("ix_balance_ledger_cdr_enriched_id", "balance_ledger", ["cdr_enriched_id"])
    op.create_index("ix_balance_ledger_run", "balance_ledger", ["run_id"])
    op.create_index(
        "ix_balance_ledger_bucket_time", "balance_ledger", ["bucket_id", "event_timestamp"]
    )

    op.create_table(
        "usage_counters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_key", sa.String(64), nullable=False),
        sa.Column("counter_key", sa.String(80), nullable=False),
        sa.Column("service_type", sa.String(16), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("total", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "owner_key", "counter_key", "period_start", name="uq_usage_counters_owner_key"
        ),
    )
    op.create_index(
        "ix_usage_counters_lookup", "usage_counters", ["owner_key", "counter_key", "period_start"]
    )


def downgrade() -> None:
    op.drop_table("usage_counters")
    op.drop_table("balance_ledger")
    op.drop_table("balance_buckets")
