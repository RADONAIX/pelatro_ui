"""rule snapshots and compiled executable rules

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "rule_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("rule_set_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="PUBLISHED"),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("rule_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("product_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("stats", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("issues", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("compiled_by", sa.String(36), nullable=True),
        sa.Column("compiled_by_name", sa.String(255), nullable=True),
        sa.Column("activated_by", sa.String(36), nullable=True),
        sa.Column("activated_at", _ts(), nullable=True),
        sa.Column("superseded_at", _ts(), nullable=True),
        sa.Column(
            "published_to_clickhouse", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_set_id"], ["rule_sets.id"],
            name="fk_rule_snapshots_rule_set_id_rule_sets", ondelete="SET NULL",
        ),
        sa.UniqueConstraint("version", name="uq_rule_snapshots_version"),
    )
    op.create_index("ix_rule_snapshots_status", "rule_snapshots", ["status"])
    op.create_index("ix_rule_snapshots_checksum", "rule_snapshots", ["checksum"])
    op.create_index(
        "ix_rule_snapshots_status_effective", "rule_snapshots", ["status", "effective_from"]
    )

    op.create_table(
        "executable_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("rule_key", sa.String(80), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("rule_name", sa.String(255), nullable=False),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("execution_stage", sa.String(32), nullable=False),
        sa.Column("stage_order", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("specificity", sa.Integer(), nullable=False),
        sa.Column("stacking_policy", sa.String(16), nullable=False),
        sa.Column("conflict_group", sa.String(64), nullable=True),
        sa.Column("condition_logic", sa.String(4), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("currency_code", sa.String(8), nullable=True),
        # Match dimensions — NULL means "any".
        sa.Column("service_type", sa.String(16), nullable=True),
        sa.Column("product_code", sa.String(64), nullable=True),
        sa.Column("offer_code", sa.String(64), nullable=True),
        sa.Column("tariff_plan_code", sa.String(64), nullable=True),
        sa.Column("destination_zone", sa.String(64), nullable=True),
        sa.Column("origin_zone", sa.String(64), nullable=True),
        sa.Column("time_band", sa.String(64), nullable=True),
        sa.Column("account_type", sa.String(16), nullable=True),
        sa.Column("roaming", sa.Boolean(), nullable=True),
        sa.Column("network_type", sa.String(16), nullable=True),
        sa.Column("rating_group", sa.String(64), nullable=True),
        sa.Column("on_net", sa.Boolean(), nullable=True),
        sa.Column("dimension_sets", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("predicates", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("actions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("signature", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["rule_snapshots.id"],
            name="fk_executable_rules_snapshot_id_rule_snapshots", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_executable_rules_snapshot_id", "executable_rules", ["snapshot_id"])
    op.create_index("ix_exec_rules_rule", "executable_rules", ["rule_id"])
    # The selection hot path.
    op.create_index(
        "ix_exec_rules_selection",
        "executable_rules",
        ["snapshot_id", "execution_stage", "service_type"],
    )
    op.create_index(
        "ix_exec_rules_precedence", "executable_rules", ["snapshot_id", "specificity", "priority"]
    )


def downgrade() -> None:
    op.drop_table("executable_rules")
    op.drop_table("rule_snapshots")
