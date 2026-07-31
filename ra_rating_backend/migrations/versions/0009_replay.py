"""Replay runs with stored before/after comparison.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels = None
depends_on = None

SCHEMA = "rating"


def upgrade() -> None:
    op.create_table(
        "replay_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_run_id", sa.String(36), nullable=False),
        sa.Column("new_run_id", sa.String(36), nullable=True),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("snapshot_version", sa.Integer, nullable=True),
        sa.Column("exception_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), server_default="RUNNING", nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "recovered_amount", sa.Numeric(20, 6), server_default="0", nullable=False
        ),
        sa.Column("comparison", JSONB, server_default="{}", nullable=False),
        sa.Column("triggered_by", sa.String(36), nullable=True),
        sa.Column("triggered_by_name", sa.String(255), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_replay_runs_source", "replay_runs", ["source_run_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_replay_runs_source", "replay_runs", schema=SCHEMA)
    op.drop_table("replay_runs", schema=SCHEMA)
