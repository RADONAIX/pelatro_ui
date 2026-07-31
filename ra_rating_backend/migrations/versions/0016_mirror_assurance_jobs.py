"""Mirror rating-assurance job schedule, runs and result rows.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mirror_assurance_schedule",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="1440"),
        sa.Column("window_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("tolerance", sa.Numeric(20, 6), nullable=False, server_default="0.01"),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("updated_by", sa.String(36)),
        sa.Column("updated_by_name", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_mirror_assurance_schedule_next_run_at",
        "mirror_assurance_schedule",
        ["next_run_at"],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO mirror_assurance_schedule (id, enabled)
            VALUES ('rating-assurance', false)
            ON CONFLICT (id) DO NOTHING
            """
        )
    )

    op.create_table(
        "mirror_assurance_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tolerance", sa.Numeric(20, 6), nullable=False),
        sa.Column("stages", JSONB(), nullable=False, server_default="[]"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("undercharged_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overcharged_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exception_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_variance", sa.Numeric(24, 6), nullable=False, server_default="0"),
        sa.Column("summary", JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("triggered_by", sa.String(36)),
        sa.Column("triggered_by_name", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_mirror_assurance_run_status_created",
        "mirror_assurance_run",
        ["status", "created_at"],
    )
    op.create_index("ix_mirror_assurance_run_status", "mirror_assurance_run", ["status"])

    op.create_table(
        "mirror_assurance_result",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("mirror_assurance_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(100), nullable=False),
        sa.Column("service_type", sa.String(30)),
        sa.Column("reconciliation_status", sa.String(40), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True)),
        sa.Column("payload", JSONB(), nullable=False),
    )
    op.create_index("ix_mirror_assurance_result_run_id", "mirror_assurance_result", ["run_id"])
    op.create_index(
        "ix_mirror_assurance_result_run_ordinal",
        "mirror_assurance_result",
        ["run_id", "ordinal"],
    )
    op.create_index(
        "ix_mirror_assurance_result_run_status",
        "mirror_assurance_result",
        ["run_id", "reconciliation_status"],
    )


def downgrade() -> None:
    op.drop_table("mirror_assurance_result")
    op.drop_table("mirror_assurance_run")
    op.drop_table("mirror_assurance_schedule")
