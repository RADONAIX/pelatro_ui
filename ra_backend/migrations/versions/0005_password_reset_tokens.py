"""password reset tokens (users.reset_token_hash, reset_token_expires_at)

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-03

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("reset_token_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "users",
        sa.Column("reset_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_reset_token_hash", "users", ["reset_token_hash"])


def downgrade() -> None:
    op.drop_index("ix_users_reset_token_hash", table_name="users")
    op.drop_column("users", "reset_token_expires_at")
    op.drop_column("users", "reset_token_hash")
