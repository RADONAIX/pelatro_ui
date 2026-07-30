"""multi-part exports (export_job_parts + export_jobs.part_count)

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-07

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("export_jobs", sa.Column("part_count", sa.Integer(), nullable=True))
    op.create_table(
        "export_job_parts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("part_index", sa.Integer(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="Queued", nullable=False),
        sa.Column("rows", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=True),
        sa.Column("celery_task_id", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["export_jobs.id"], name=op.f("fk_export_job_parts_job_id_export_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_export_job_parts")),
    )
    op.create_index(
        op.f("ix_export_job_parts_job_id"), "export_job_parts", ["job_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_export_job_parts_job_id"), table_name="export_job_parts")
    op.drop_table("export_job_parts")
    op.drop_column("export_jobs", "part_count")
