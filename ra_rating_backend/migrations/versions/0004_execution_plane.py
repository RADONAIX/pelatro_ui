"""cdr ingestion, enrichment, rating runs, results and exceptions

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-29

These are the execution-plane tables. In a ClickHouse deployment cdr_landing,
cdr_enriched and rating_results move there; every statement that touches them is
already written as bulk set-based SQL over whole batches, so the port is an
engine swap rather than a pipeline rewrite.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    # --- Reference data enrichment depends on -------------------------------
    op.create_table(
        "subscriber_products",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("subscriber_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=True),
        sa.Column("msisdn", sa.String(32), nullable=False),
        sa.Column("imsi", sa.String(32), nullable=True),
        sa.Column("product_code", sa.String(64), nullable=False),
        sa.Column("offer_code", sa.String(64), nullable=True),
        sa.Column("tariff_plan_code", sa.String(64), nullable=True),
        sa.Column("account_type", sa.String(16), nullable=False, server_default="PREPAID"),
        sa.Column("rating_group", sa.String(64), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("source_system", sa.String(64), nullable=False, server_default="MANUAL"),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_subscriber_products_msisdn", "subscriber_products", ["msisdn"])
    op.create_index("ix_subscriber_products_subscriber", "subscriber_products", ["subscriber_id"])
    op.create_index(
        "ix_subscriber_products_lookup",
        "subscriber_products",
        ["msisdn", "effective_from", "effective_to"],
    )

    op.create_table(
        "network_prefixes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("prefix", sa.String(32), nullable=False),
        sa.Column("operator_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("is_home_network", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("country_code", sa.String(8), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_network_prefixes_prefix", "network_prefixes", ["prefix"], unique=True)

    # --- CDR ingestion ------------------------------------------------------
    op.create_table(
        "cdr_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("cdr_type", sa.String(24), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="RECEIVED"),
        sa.Column("total_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("loaded_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("normalized_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enriched_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("control_total", sa.Numeric(20, 6), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
        # Re-ingesting a file would double-count its revenue; make it impossible.
        sa.UniqueConstraint("source_system", "filename", name="uq_cdr_batches_source_system"),
    )
    op.create_index("ix_cdr_batches_source_system", "cdr_batches", ["source_system"])
    op.create_index("ix_cdr_batches_cdr_type", "cdr_batches", ["cdr_type"])
    op.create_index("ix_cdr_batches_status", "cdr_batches", ["status"])
    op.create_index("ix_cdr_batches_event_date", "cdr_batches", ["event_date"])

    op.create_table(
        "cdr_landing",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("record_hash", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["cdr_batches.id"],
            name="fk_cdr_landing_batch_id_cdr_batches", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("record_hash", name="uq_cdr_landing_record_hash"),
    )
    op.create_index("ix_cdr_landing_batch_id", "cdr_landing", ["batch_id"])
    op.create_index("ix_cdr_landing_status", "cdr_landing", ["status"])
    op.create_index("ix_cdr_landing_batch_status", "cdr_landing", ["batch_id", "status"])

    op.create_table(
        "cdr_enriched",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("landing_id", sa.String(36), nullable=False),
        sa.Column("cdr_id", sa.String(128), nullable=False),
        sa.Column("subscriber_id", sa.String(64), nullable=True),
        sa.Column("account_id", sa.String(64), nullable=True),
        sa.Column("msisdn", sa.String(32), nullable=True),
        sa.Column("imsi", sa.String(32), nullable=True),
        sa.Column("service_type", sa.String(16), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=True),
        sa.Column("event_timestamp", _ts(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("duration_seconds", sa.Numeric(18, 3), nullable=True),
        sa.Column("usage_volume", sa.Numeric(20, 3), nullable=True),
        sa.Column("calling_number", sa.String(32), nullable=True),
        sa.Column("called_number", sa.String(32), nullable=True),
        sa.Column("actual_charge", sa.Numeric(18, 6), nullable=True),
        sa.Column("actual_discount", sa.Numeric(18, 6), nullable=True),
        sa.Column("actual_tax", sa.Numeric(18, 6), nullable=True),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("product_code", sa.String(64), nullable=True),
        sa.Column("offer_code", sa.String(64), nullable=True),
        sa.Column("tariff_plan_code", sa.String(64), nullable=True),
        sa.Column("account_type", sa.String(16), nullable=True),
        sa.Column("destination_zone", sa.String(64), nullable=True),
        sa.Column("origin_zone", sa.String(64), nullable=True),
        sa.Column("on_net", sa.Boolean(), nullable=True),
        sa.Column("time_band", sa.String(64), nullable=True),
        sa.Column("roaming", sa.Boolean(), nullable=True),
        sa.Column("network_type", sa.String(16), nullable=True),
        sa.Column("rating_group", sa.String(64), nullable=True),
        sa.Column("visited_operator", sa.String(32), nullable=True),
        sa.Column("apn", sa.String(64), nullable=True),
        sa.Column("context_key", sa.Text(), nullable=True),
        sa.Column("context_hash", sa.String(40), nullable=True),
        sa.Column("quality_status", sa.String(32), nullable=False, server_default="OK"),
        sa.Column("quality_detail", sa.Text(), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["cdr_batches.id"],
            name="fk_cdr_enriched_batch_id_cdr_batches", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("landing_id", name="uq_cdr_enriched_landing_id"),
    )
    op.create_index("ix_cdr_enriched_batch_id", "cdr_enriched", ["batch_id"])
    op.create_index("ix_cdr_enriched_cdr_id", "cdr_enriched", ["cdr_id"])
    op.create_index("ix_cdr_enriched_service_type", "cdr_enriched", ["service_type"])
    op.create_index("ix_cdr_enriched_subscriber", "cdr_enriched", ["subscriber_id"])
    op.create_index("ix_cdr_enriched_event_date", "cdr_enriched", ["event_date"])
    # The pipeline's hot path: collapse a batch to its distinct contexts.
    op.create_index("ix_cdr_enriched_batch_context", "cdr_enriched", ["batch_id", "context_hash"])
    op.create_index("ix_cdr_enriched_quality", "cdr_enriched", ["batch_id", "quality_status"])

    op.create_table(
        "cdr_sequences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("sequence_number", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "source_system", "sequence_number", name="uq_cdr_sequences_source_system"
        ),
    )

    # --- Rating -------------------------------------------------------------
    op.create_table(
        "rating_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("snapshot_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("run_type", sa.String(16), nullable=False, server_default="INITIAL"),
        sa.Column("replay_of_run_id", sa.String(36), nullable=True),
        sa.Column("total_cdrs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rated_cdrs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("distinct_contexts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exception_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expected_revenue", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("billed_revenue", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("undercharge_total", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("overcharge_total", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("stats", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", _ts(), nullable=True),
        sa.Column("finished_at", _ts(), nullable=True),
        sa.Column("triggered_by", sa.String(36), nullable=True),
        sa.Column("triggered_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["cdr_batches.id"],
            name="fk_rating_runs_batch_id_cdr_batches", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_rating_runs_batch_id", "rating_runs", ["batch_id"])
    op.create_index("ix_rating_runs_snapshot_id", "rating_runs", ["snapshot_id"])
    op.create_index("ix_rating_runs_status", "rating_runs", ["status"])
    op.create_index("ix_rating_runs_batch_status", "rating_runs", ["batch_id", "status"])

    op.create_table(
        "context_rule_map",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("context_hash", sa.String(40), nullable=False),
        sa.Column("context_key", sa.Text(), nullable=False),
        sa.Column("cdr_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selected_rules", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("candidates", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["rating_runs.id"],
            name="fk_context_rule_map_run_id_rating_runs", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("run_id", "context_hash", name="uq_context_rule_map_run_id"),
    )
    op.create_index("ix_context_rule_map_run", "context_rule_map", ["run_id"])

    op.create_table(
        "rating_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("cdr_enriched_id", sa.String(36), nullable=False),
        sa.Column("cdr_id", sa.String(128), nullable=False),
        sa.Column("context_hash", sa.String(40), nullable=True),
        sa.Column("subscriber_id", sa.String(64), nullable=True),
        sa.Column("msisdn", sa.String(32), nullable=True),
        sa.Column("service_type", sa.String(16), nullable=False),
        sa.Column("product_code", sa.String(64), nullable=True),
        sa.Column("destination_zone", sa.String(64), nullable=True),
        sa.Column("time_band", sa.String(64), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("selected_rule_ids", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("billable_quantity", sa.Numeric(18, 4), nullable=True),
        sa.Column("billable_unit", sa.String(16), nullable=True),
        sa.Column("expected_base_charge", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("expected_discount", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("expected_tax", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("expected_final_charge", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("actual_charge", sa.Numeric(18, 6), nullable=True),
        sa.Column("variance", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("root_cause", sa.String(32), nullable=True),
        sa.Column("trace", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("engine_version", sa.String(16), nullable=False),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["rating_runs.id"],
            name="fk_rating_results_run_id_rating_runs", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("run_id", "cdr_enriched_id", name="uq_rating_results_run_id"),
    )
    op.create_index("ix_rating_results_cdr_enriched_id", "rating_results", ["cdr_enriched_id"])
    op.create_index("ix_rating_results_cdr_id", "rating_results", ["cdr_id"])
    op.create_index("ix_rating_results_context_hash", "rating_results", ["context_hash"])
    op.create_index("ix_rating_results_status", "rating_results", ["status"])
    op.create_index("ix_rating_results_root_cause", "rating_results", ["root_cause"])
    op.create_index("ix_rating_results_event_date", "rating_results", ["event_date"])
    op.create_index("ix_rating_results_run_status", "rating_results", ["run_id", "status"])
    op.create_index("ix_rating_results_variance", "rating_results", ["run_id", "variance"])
    op.create_index("ix_rating_results_product", "rating_results", ["run_id", "product_code"])

    op.create_table(
        "rating_exceptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("group_key", sa.String(255), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("assurance_status", sa.String(24), nullable=False),
        sa.Column("root_cause", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="NEW"),
        sa.Column("service_type", sa.String(16), nullable=True),
        sa.Column("product_code", sa.String(64), nullable=True),
        sa.Column("destination_zone", sa.String(64), nullable=True),
        sa.Column("rule_key", sa.String(80), nullable=True),
        sa.Column("cdr_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subscriber_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue_impact", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("expected_total", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("actual_total", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("first_event_date", sa.Date(), nullable=True),
        sa.Column("last_event_date", sa.Date(), nullable=True),
        sa.Column("sample_result_id", sa.String(36), nullable=True),
        sa.Column("probable_cause", sa.Text(), nullable=False, server_default=""),
        sa.Column("recommended_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("assigned_to", sa.String(36), nullable=True),
        sa.Column("assigned_to_name", sa.String(255), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolved_at", _ts(), nullable=True),
        sa.Column("closed_at", _ts(), nullable=True),
        sa.Column("recovered_amount", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["rating_runs.id"],
            name="fk_rating_exceptions_run_id_rating_runs", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_rating_exceptions_group_key", "rating_exceptions", ["group_key"])
    op.create_index("ix_rating_exceptions_status", "rating_exceptions", ["status"])
    op.create_index("ix_rating_exceptions_severity", "rating_exceptions", ["severity"])
    op.create_index("ix_rating_exceptions_root_cause", "rating_exceptions", ["root_cause"])
    op.create_index("ix_rating_exceptions_assurance_status", "rating_exceptions", ["assurance_status"])
    op.create_index("ix_rating_exceptions_product_code", "rating_exceptions", ["product_code"])
    op.create_index("ix_rating_exceptions_rule_key", "rating_exceptions", ["rule_key"])
    op.create_index("ix_rating_exceptions_assigned_to", "rating_exceptions", ["assigned_to"])
    op.create_index("ix_rating_exceptions_run_status", "rating_exceptions", ["run_id", "status"])
    op.create_index("ix_rating_exceptions_impact", "rating_exceptions", ["revenue_impact"])

    op.create_table(
        "exception_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("exception_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="COMMENT"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_id", sa.String(36), nullable=True),
        sa.Column("author_name", sa.String(255), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["exception_id"], ["rating_exceptions.id"],
            name="fk_exception_comments_exception_id_rating_exceptions", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_exception_comments_exception_id", "exception_comments", ["exception_id"])


def downgrade() -> None:
    for table in (
        "exception_comments",
        "rating_exceptions",
        "rating_results",
        "context_rule_map",
        "rating_runs",
        "cdr_sequences",
        "cdr_enriched",
        "cdr_landing",
        "cdr_batches",
        "network_prefixes",
        "subscriber_products",
    ):
        op.drop_table(table)
