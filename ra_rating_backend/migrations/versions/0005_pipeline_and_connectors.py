"""staged pipeline runs, source systems and connector imports

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-30

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    # --- Pipeline -----------------------------------------------------------
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("cdr_type", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="QUEUED"),
        sa.Column("current_stage", sa.String(32), nullable=True),
        sa.Column("stages", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("batch_id", sa.String(36), nullable=True),
        sa.Column("rating_run_id", sa.String(36), nullable=True),
        sa.Column("snapshot_id", sa.String(36), nullable=True),
        sa.Column("snapshot_version", sa.Integer(), nullable=True),
        # The upload, held only until ingestion consumes it.
        sa.Column("payload", sa.LargeBinary(), nullable=True),
        sa.Column("total_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exception_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("started_at", _ts(), nullable=True),
        sa.Column("finished_at", _ts(), nullable=True),
        sa.Column("triggered_by", sa.String(36), nullable=True),
        sa.Column("triggered_by_name", sa.String(255), nullable=True),
        sa.Column("connector_id", sa.String(36), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pipeline_runs_source_system", "pipeline_runs", ["source_system"])
    op.create_index("ix_pipeline_runs_status", "pipeline_runs", ["status"])
    op.create_index("ix_pipeline_runs_batch_id", "pipeline_runs", ["batch_id"])
    op.create_index("ix_pipeline_runs_rating_run_id", "pipeline_runs", ["rating_run_id"])
    op.create_index("ix_pipeline_runs_connector_id", "pipeline_runs", ["connector_id"])
    op.create_index("ix_pipeline_runs_status_created", "pipeline_runs", ["status", "created_at"])

    op.create_table(
        "pipeline_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("stage", sa.String(32), nullable=True),
        sa.Column("level", sa.String(8), nullable=False, server_default="INFO"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pipeline_events_run_id", "pipeline_events", ["run_id"])
    op.create_index("ix_pipeline_events_run_time", "pipeline_events", ["run_id", "created_at"])

    # --- Connectors ---------------------------------------------------------
    op.create_table(
        "source_systems",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("vendor", sa.String(64), nullable=False),
        sa.Column("category", sa.String(24), nullable=False, server_default="RULES"),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("connection", postgresql.JSONB(), nullable=False, server_default="{}"),
        # Secrets kept apart from connection settings so a connection can be
        # rendered without ever serialising a credential beside it.
        sa.Column("credentials", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("schedule", sa.String(64), nullable=True),
        sa.Column("import_mode", sa.String(16), nullable=False, server_default="FULL"),
        sa.Column("field_mapping", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("health_status", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("health_detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_tested_at", _ts(), nullable=True),
        sa.Column("last_import_at", _ts(), nullable=True),
        sa.Column("last_import_status", sa.String(16), nullable=True),
        sa.Column("total_imports", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_imports", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_records_imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_source_systems_code", "source_systems", ["code"], unique=True)
    op.create_index("ix_source_systems_vendor", "source_systems", ["vendor"])
    op.create_index("ix_source_systems_category", "source_systems", ["category"])
    op.create_index("ix_source_systems_status", "source_systems", ["status"])
    op.create_index("ix_source_systems_health_status", "source_systems", ["health_status"])

    op.create_table(
        "connector_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False, server_default="MANUAL"),
        sa.Column("import_mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("records_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_mapped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rules_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rules_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rules_unchanged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rules_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("triggered_by", sa.String(36), nullable=True),
        sa.Column("triggered_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source_systems.id"],
            name="fk_connector_imports_source_id_source_systems", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_connector_imports_source_id", "connector_imports", ["source_id"])
    op.create_index("ix_connector_imports_status", "connector_imports", ["status"])
    op.create_index(
        "ix_connector_imports_source_time", "connector_imports", ["source_id", "created_at"]
    )


def downgrade() -> None:
    for table in (
        "connector_imports",
        "source_systems",
        "pipeline_events",
        "pipeline_runs",
    ):
        op.drop_table(table)
