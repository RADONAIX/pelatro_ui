"""file-based rule import: batches, per-row outcome, mapping templates

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "rule_import_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_vendor", sa.String(64), nullable=False, server_default=""),
        sa.Column("mapping", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_rule_import_templates_code", "rule_import_templates", ["code"], unique=True
    )
    op.create_index(
        "ix_rule_import_templates_source_vendor", "rule_import_templates", ["source_vendor"]
    )

    op.create_table(
        "rule_import_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("source_system", sa.String(64), nullable=False, server_default="FILE_IMPORT"),
        sa.Column("template_id", sa.String(36), nullable=True),
        sa.Column("rule_set_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("mapping", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", _ts(), nullable=True),
        # SET NULL, not RESTRICT: deleting a rule set must not be blocked by the
        # historical import batches that happened to target it.
        sa.ForeignKeyConstraint(
            ["rule_set_id"], ["rule_sets.id"],
            name="fk_rule_import_batches_rule_set_id_rule_sets", ondelete="SET NULL",
        ),
    )
    op.create_index("ix_rule_import_batches_status", "rule_import_batches", ["status"])
    op.create_index("ix_rule_import_batches_created", "rule_import_batches", ["created_at"])

    op.create_table(
        "rule_import_rows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("canonical", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("errors", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("rule_id", sa.String(36), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["rule_import_batches.id"],
            name="fk_rule_import_rows_batch_id_rule_import_batches", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_rule_import_rows_batch_id", "rule_import_rows", ["batch_id"])
    op.create_index("ix_rule_import_rows_status", "rule_import_rows", ["status"])
    op.create_index(
        "ix_rule_import_rows_batch_status", "rule_import_rows", ["batch_id", "status"]
    )


def downgrade() -> None:
    op.drop_table("rule_import_rows")
    op.drop_table("rule_import_batches")
    op.drop_table("rule_import_templates")
