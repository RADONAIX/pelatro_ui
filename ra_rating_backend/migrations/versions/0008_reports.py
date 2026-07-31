"""Generated report artefacts.

Stored bytes, not regenerated on download — a certified export must be
byte-identical every time it is fetched.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels = None
depends_on = None

SCHEMA = "rating"


def upgrade() -> None:
    op.create_table(
        "generated_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("report_code", sa.String(64), nullable=False),
        sa.Column("report_name", sa.String(255), nullable=False),
        sa.Column("params", JSONB, server_default="{}", nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("row_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("content_type", sa.String(80), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("generated_by", sa.String(36), nullable=True),
        sa.Column("generated_by_name", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_generated_reports_code",
        "generated_reports",
        ["report_code", "created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_generated_reports_code", "generated_reports", schema=SCHEMA)
    op.drop_table("generated_reports", schema=SCHEMA)
