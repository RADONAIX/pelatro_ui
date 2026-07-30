"""Bundle coverage on rating results.

A zero charge is only defensible when the allowance that paid for it is stored
next to it, so the dispute screen can answer "which bundle covered this?"
without joining the ledger for every row.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels = None
depends_on = None

SCHEMA = "rating"


def upgrade() -> None:
    op.add_column(
        "rating_results",
        sa.Column("bundle_code", sa.String(64), nullable=True),
        schema=SCHEMA,
    )
    for column in ("bundle_consumed", "bundle_overflow", "unpriced_quantity"):
        op.add_column(
            "rating_results",
            sa.Column(
                column, sa.Numeric(18, 4), nullable=False, server_default="0"
            ),
            schema=SCHEMA,
        )
    op.create_index(
        "ix_rating_results_bundle_code",
        "rating_results",
        ["bundle_code"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_rating_results_bundle_code", "rating_results", schema=SCHEMA)
    for column in (
        "unpriced_quantity",
        "bundle_overflow",
        "bundle_consumed",
        "bundle_code",
    ):
        op.drop_column("rating_results", column, schema=SCHEMA)
