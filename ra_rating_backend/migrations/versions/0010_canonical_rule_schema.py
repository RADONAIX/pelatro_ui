"""Canonical rule model: the ``ra_rule`` schema.

Additive only. Nothing in the live rating path reads or writes these tables yet —
the legacy ``rating.rules`` / ``rule_conditions`` / ``rule_actions`` remain the
source of truth until the R4 cut-over backfills, proves parity and switches them
to read-only. This migration can therefore be applied to a running system.

Two things here are worth reading before changing them:

``btree_gist`` + the exclusion constraint on ``rule_version``. Two ACTIVE versions
of one rule whose validity windows overlap means two live prices for the same
event. No amount of application-level care prevents that under concurrency; the
constraint does.

Row-level security is enabled but **permissive** (``USING true``). The tenant
column is real from day one because retrofitting it onto a populated estate means
rewriting every unique constraint; the enforcement is tightened in step M8 once
the session GUC is wired.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels = None
depends_on = None

#: The execution plane. Referenced for cross-schema foreign keys.
RATING = "rating"
#: The canonical rule model.
RULE = "ra_rule"

MONEY = sa.Numeric(20, 6)
DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


def _tenant() -> sa.Column:
    return sa.Column(
        "tenant_id", sa.String(36), nullable=False, server_default=DEFAULT_TENANT
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    ]


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{RULE}"')
    # Needed for the "=" operator class on a varchar column inside a GiST
    # exclusion constraint alongside a range.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    _create_lookups()
    _create_rule_and_versions()
    _create_logic()
    _create_graph_and_sets()
    _create_lineage()
    _enable_rls()


# --- Lookups ----------------------------------------------------------------


def _create_lookups() -> None:
    op.create_table(
        "rule_stage",
        sa.Column("rule_stage_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("code", sa.String(48), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        # COMMON | PREPAID | POSTPAID — scopes the stage to a charging mode's
        # pipeline, which is what lets one engine serve both.
        sa.Column("applies_to", sa.String(16), server_default="COMMON", nullable=False),
        # Gap-numbered by 10 so a stage inserts without renumbering. Renumbering
        # silently reorders a live pipeline.
        sa.Column("execution_order", sa.Integer, nullable=False),
        sa.Column("is_stateful", sa.Boolean, server_default="false", nullable=False),
        sa.Column("description", sa.Text, server_default="", nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_rule_stage_tenant_code"),
        sa.UniqueConstraint("tenant_id", "execution_order", name="uq_rule_stage_tenant_order"),
        schema=RULE,
    )
    op.create_index("ix_rule_stage_tenant_id", "rule_stage", ["tenant_id"], schema=RULE)
    op.create_index("ix_rule_stage_applies_to", "rule_stage", ["applies_to"], schema=RULE)

    op.create_table(
        "rule_type",
        sa.Column("rule_type_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("code", sa.String(48), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("rule_category", sa.String(16), nullable=False),
        sa.Column("rule_stage_id", sa.String(36), nullable=False),
        sa.Column("charging_mode", sa.String(16), server_default="BOTH", nullable=False),
        # The validator's contract: a matching rule MUST carry one of these.
        sa.Column("required_action_types", ARRAY(sa.String(48)), server_default="{}",
                  nullable=False),
        sa.Column("alias_of", sa.String(48), nullable=True),
        sa.Column("is_system", sa.Boolean, server_default="false", nullable=False),
        sa.Column("description", sa.Text, server_default="", nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_rule_type_tenant_code"),
        schema=RULE,
    )
    op.create_index("ix_rule_type_tenant_id", "rule_type", ["tenant_id"], schema=RULE)
    op.create_index("ix_rule_type_rule_category", "rule_type", ["rule_category"], schema=RULE)
    op.create_index("ix_rule_type_charging_mode", "rule_type", ["charging_mode"], schema=RULE)
    op.create_index("ix_rule_type_rule_stage_id", "rule_type", ["rule_stage_id"], schema=RULE)

    op.create_table(
        "rule_stacking_policy",
        sa.Column("stacking_policy_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("allows_multiple", sa.Boolean, nullable=False),
        sa.Column("overrides_lower", sa.Boolean, nullable=False),
        sa.Column("description", sa.Text, server_default="", nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_rule_stacking_policy_tenant_code"),
        schema=RULE,
    )
    op.create_index(
        "ix_rule_stacking_policy_tenant_id", "rule_stacking_policy", ["tenant_id"], schema=RULE
    )

    op.create_table(
        "rule_conflict_group",
        sa.Column("conflict_group_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("resolution_strategy", sa.String(32),
                  server_default="HIGHEST_SPECIFICITY", nullable=False),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, server_default="", nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_rule_conflict_group_tenant_code"),
        schema=RULE,
    )
    op.create_index(
        "ix_rule_conflict_group_tenant_id", "rule_conflict_group", ["tenant_id"], schema=RULE
    )

    op.create_table(
        "rule_import_profile",
        sa.Column("profile_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, server_default="", nullable=False),
        sa.Column("vendor", sa.String(64), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("spec", JSONB, server_default="{}", nullable=False),
        sa.Column("version", sa.Integer, server_default="1", nullable=False),
        sa.Column("is_system", sa.Boolean, server_default="false", nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id", "code", "version", name="uq_rule_import_profile_code_version"
        ),
        schema=RULE,
    )
    op.create_index(
        "ix_rule_import_profile_tenant_id", "rule_import_profile", ["tenant_id"], schema=RULE
    )
    op.create_index("ix_rule_import_profile_vendor", "rule_import_profile", ["vendor"], schema=RULE)


# --- Rule + versions --------------------------------------------------------


def _create_rule_and_versions() -> None:
    op.create_table(
        "rule",
        sa.Column("rule_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("rule_key", sa.String(96), nullable=False),
        sa.Column("rule_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, server_default="", nullable=False),
        sa.Column("charging_mode", sa.String(16), nullable=False),
        sa.Column("rule_type_id", sa.String(36), nullable=False),
        sa.Column("rule_stage_id", sa.String(36), nullable=False),
        sa.Column("service_type", sa.String(16), nullable=False),
        sa.Column("source_system_id", sa.String(36), nullable=True),
        sa.Column("external_ref", sa.String(255), nullable=True),
        sa.Column("current_version_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), server_default="DRAFT", nullable=False),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("updated_by", sa.String(36), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "rule_key", name="uq_rule_tenant_key"),
        # The real idempotency key for an import: the vendor's own identifier.
        # Without it, a re-import can only match on a key derived from the rule's
        # name, so a rename forks a phantom rule and a re-run duplicates the estate.
        sa.UniqueConstraint(
            "tenant_id", "source_system_id", "external_ref",
            name="uq_rule_tenant_source_extref",
        ),
        sa.ForeignKeyConstraint(
            ["rule_type_id"], [f"{RULE}.rule_type.rule_type_id"],
            name="fk_rule_rule_type_id_rule_type", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rule_stage_id"], [f"{RULE}.rule_stage.rule_stage_id"],
            name="fk_rule_rule_stage_id_rule_stage", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"], [f"{RATING}.source_systems.id"],
            name="fk_rule_source_system_id_source_systems", ondelete="RESTRICT",
        ),
        schema=RULE,
    )
    for col in ("tenant_id", "rule_key", "charging_mode", "service_type", "status",
                "external_ref", "current_version_id", "created_by", "source_system_id",
                "rule_type_id", "rule_stage_id"):
        op.create_index(f"ix_rule_{col}", "rule", [col], schema=RULE)
    op.create_index(
        "ix_rule_catalogue", "rule",
        ["tenant_id", "charging_mode", "service_type", "status"], schema=RULE,
    )
    op.create_index(
        "ix_rule_type_stage", "rule", ["tenant_id", "rule_type_id", "rule_stage_id"], schema=RULE
    )

    op.create_table(
        "rule_version",
        sa.Column("rule_version_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("product_id", sa.String(36), nullable=True),
        sa.Column("offer_id", sa.String(36), nullable=True),
        sa.Column("tariff_plan_id", sa.String(36), nullable=True),
        # --- behaviour (the wizard's Step 4) ---
        sa.Column("priority", sa.Integer, server_default="100", nullable=False),
        # Computed from the conditions, never author-set: two hand-tuned knobs for
        # one job is how a 10,000-rule estate becomes unpredictable.
        sa.Column("specificity_score", sa.Integer, server_default="0", nullable=False),
        sa.Column("stacking_policy_id", sa.String(36), nullable=False),
        sa.Column("conflict_group_id", sa.String(36), nullable=True),
        sa.Column("fallback_policy", sa.String(24), server_default="FALLBACK_CHAIN",
                  nullable=False),
        sa.Column("stop_processing", sa.Boolean, server_default="false", nullable=False),
        # ONLINE | OFFLINE | BOTH — lets the compiler emit a session-time snapshot
        # and a bill-run snapshot from one rule set.
        sa.Column("execution_mode", sa.String(8), server_default="BOTH", nullable=False),
        sa.Column("condition_logic", sa.String(4), server_default="AND", nullable=False),
        # --- validity ---
        sa.Column("effective_from", sa.Date, nullable=False),
        sa.Column("effective_to", sa.Date, nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=True),
        # --- lifecycle ---
        sa.Column("status", sa.String(16), server_default="DRAFT", nullable=False),
        sa.Column("change_reason", sa.Text, server_default="", nullable=False),
        sa.Column("supersedes_id", sa.String(36), nullable=True),
        sa.Column("published_snapshot_id", sa.String(36), nullable=True),
        sa.Column("validation_state", sa.String(16), server_default="UNKNOWN", nullable=False),
        sa.Column("behaviour_hash", sa.String(64), nullable=False),
        sa.Column("canonical_json", JSONB, server_default="{}", nullable=False),
        sa.Column("extras", JSONB, server_default="{}", nullable=False),
        sa.Column("submitted_by", sa.String(36), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "rule_id", "version_number",
            name="uq_rule_version_tenant_rule_number",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_rule_version_validity_window",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"], [f"{RULE}.rule.rule_id"],
            name="fk_rule_version_rule_id_rule", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stacking_policy_id"], [f"{RULE}.rule_stacking_policy.stacking_policy_id"],
            name="fk_rule_version_stacking_policy_id_rule_stacking_policy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conflict_group_id"], [f"{RULE}.rule_conflict_group.conflict_group_id"],
            name="fk_rule_version_conflict_group_id_rule_conflict_group", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], [f"{RATING}.products.id"],
            name="fk_rule_version_product_id_products", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"], [f"{RATING}.offers.id"],
            name="fk_rule_version_offer_id_offers", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tariff_plan_id"], [f"{RATING}.tariff_plans.id"],
            name="fk_rule_version_tariff_plan_id_tariff_plans", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["published_snapshot_id"], [f"{RATING}.rule_snapshots.id"],
            name="fk_rule_version_published_snapshot_id_rule_snapshots", ondelete="SET NULL",
        ),
        schema=RULE,
    )
    for col in ("tenant_id", "rule_id", "status", "execution_mode", "product_id", "offer_id",
                "tariff_plan_id", "conflict_group_id", "supersedes_id"):
        op.create_index(f"ix_rule_version_{col}", "rule_version", [col], schema=RULE)
    # The selection hot path. INCLUDE keeps the tie-breakers on the index leaf so
    # ranking candidates needs no heap fetch.
    op.execute(
        f"""
        CREATE INDEX ix_rule_version_selection ON {RULE}.rule_version
          (tenant_id, status, execution_mode, effective_from, effective_to)
          INCLUDE (rule_id, priority, specificity_score)
        """
    )
    op.create_index(
        "ix_rule_version_hash", "rule_version", ["tenant_id", "behaviour_hash"], schema=RULE
    )
    op.create_index(
        "ix_rule_version_snapshot", "rule_version", ["published_snapshot_id"], schema=RULE
    )
    op.create_index(
        "ix_rule_version_validation", "rule_version",
        ["tenant_id", "validation_state"], schema=RULE,
    )
    # Two live prices for the same event, in one constraint.
    op.execute(
        f"""
        ALTER TABLE {RULE}.rule_version
          ADD CONSTRAINT ex_rule_version_active_window
          EXCLUDE USING gist (
            rule_id WITH =,
            daterange(effective_from, COALESCE(effective_to, 'infinity'::date), '[]') WITH &&
          ) WHERE (status IN ('ACTIVE', 'PUBLISHED'))
        """
    )


# --- Conditions, actions, parameters ---------------------------------------


def _create_logic() -> None:
    version_fk = f"{RULE}.rule_version.rule_version_id"

    op.create_table(
        "rule_condition_group",
        sa.Column("condition_group_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("rule_version_id", sa.String(36), nullable=False),
        sa.Column("parent_group_id", sa.String(36), nullable=True),
        sa.Column("group_logic", sa.String(4), server_default="AND", nullable=False),
        sa.Column("negated_flag", sa.Boolean, server_default="false", nullable=False),
        sa.Column("sequence_number", sa.SmallInteger, nullable=False),
        sa.Column("label", sa.String(128), server_default="", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "parent_group_id IS DISTINCT FROM condition_group_id",
            name="ck_rule_condition_group_not_self_parent",
        ),
        sa.ForeignKeyConstraint(
            ["rule_version_id"], [version_fk],
            name="fk_rule_condition_group_rule_version_id_rule_version", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_group_id"], [f"{RULE}.rule_condition_group.condition_group_id"],
            name="fk_rule_condition_group_parent_group_id_rule_condition_group",
            ondelete="CASCADE",
        ),
        schema=RULE,
    )
    for col in ("tenant_id", "rule_version_id", "parent_group_id"):
        op.create_index(f"ix_rule_condition_group_{col}", "rule_condition_group", [col],
                        schema=RULE)
    op.create_index(
        "ix_rule_condition_group_version", "rule_condition_group",
        ["rule_version_id", "sequence_number"], schema=RULE,
    )

    op.create_table(
        "rule_condition",
        sa.Column("rule_condition_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("rule_version_id", sa.String(36), nullable=False),
        sa.Column("condition_group_id", sa.String(36), nullable=False),
        sa.Column("attribute_name", sa.String(64), nullable=False),
        sa.Column("operator_code", sa.String(24), nullable=False),
        sa.Column("comparison_value", sa.Text, server_default="", nullable=False),
        sa.Column("comparison_value_type", sa.String(16), nullable=False),
        sa.Column("comparison_values", JSONB, server_default="[]", nullable=False),
        # Typed shadow so "every rule with a rate above X" is a numeric comparison.
        sa.Column("comparison_value_numeric", MONEY, nullable=True),
        # REFERENCE values keep the catalogue *code* above and the resolved id here:
        # the code survives an export moving between environments, the id joins.
        sa.Column("resolved_ref_id", sa.String(36), nullable=True),
        sa.Column("unit_code", sa.String(16), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=True),
        sa.Column("sequence_number", sa.SmallInteger, nullable=False),
        sa.Column("negated_flag", sa.Boolean, server_default="false", nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "rule_version_id", "condition_group_id", "sequence_number",
            name="uq_rule_condition_group_sequence",
        ),
        sa.ForeignKeyConstraint(
            ["rule_version_id"], [version_fk],
            name="fk_rule_condition_rule_version_id_rule_version", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["condition_group_id"], [f"{RULE}.rule_condition_group.condition_group_id"],
            name="fk_rule_condition_condition_group_id_rule_condition_group",
            ondelete="CASCADE",
        ),
        schema=RULE,
    )
    for col in ("tenant_id", "rule_version_id", "condition_group_id"):
        op.create_index(f"ix_rule_condition_{col}", "rule_condition", [col], schema=RULE)
    # "Which rules mention zone LOCAL_ONNET?" — an index scan, not the JSONB scan
    # the legacy model forced. Most of why normalising was worth the migration.
    op.create_index(
        "ix_rule_condition_attr", "rule_condition",
        ["tenant_id", "attribute_name", "comparison_value"], schema=RULE,
    )
    op.create_index(
        "ix_rule_condition_ref", "rule_condition", ["tenant_id", "resolved_ref_id"], schema=RULE
    )

    op.create_table(
        "rule_action",
        sa.Column("rule_action_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("rule_version_id", sa.String(36), nullable=False),
        sa.Column("action_type", sa.String(48), nullable=False),
        # What the action writes — charge, quantity, balance, invoice_line, session.
        sa.Column("target_attribute", sa.String(64), nullable=True),
        sa.Column("action_value", sa.Text, nullable=True),
        sa.Column("action_value_type", sa.String(16), nullable=True),
        sa.Column("action_value_numeric", MONEY, nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=True),
        sa.Column("unit_code", sa.String(16), nullable=True),
        sa.Column("resolved_ref_id", sa.String(36), nullable=True),
        sa.Column("execution_sequence", sa.SmallInteger, nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "rule_version_id", "execution_sequence", name="uq_rule_action_version_sequence"
        ),
        sa.ForeignKeyConstraint(
            ["rule_version_id"], [version_fk],
            name="fk_rule_action_rule_version_id_rule_version", ondelete="CASCADE",
        ),
        schema=RULE,
    )
    for col in ("tenant_id", "rule_version_id"):
        op.create_index(f"ix_rule_action_{col}", "rule_action", [col], schema=RULE)
    op.create_index("ix_rule_action_type", "rule_action", ["tenant_id", "action_type"],
                    schema=RULE)
    op.create_index("ix_rule_action_target", "rule_action", ["tenant_id", "target_attribute"],
                    schema=RULE)

    # The table that ends money-as-float: parameters are typed rows with a
    # numeric(20,6) column, not values inside a JSONB dict.
    op.create_table(
        "rule_parameter",
        sa.Column("rule_parameter_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("rule_version_id", sa.String(36), nullable=False),
        sa.Column("rule_action_id", sa.String(36), nullable=True),
        sa.Column("parameter_name", sa.String(64), nullable=False),
        sa.Column("parameter_value", sa.Text, server_default="", nullable=False),
        sa.Column("parameter_value_type", sa.String(16), nullable=False),
        sa.Column("parameter_value_numeric", MONEY, nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=True),
        sa.Column("unit_code", sa.String(16), nullable=True),
        sa.Column("resolved_ref_id", sa.String(36), nullable=True),
        sa.Column("sequence_number", sa.SmallInteger, server_default="0", nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "rule_version_id", "rule_action_id", "parameter_name", "sequence_number",
            name="uq_rule_parameter_scope_name_sequence",
        ),
        sa.ForeignKeyConstraint(
            ["rule_version_id"], [version_fk],
            name="fk_rule_parameter_rule_version_id_rule_version", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_action_id"], [f"{RULE}.rule_action.rule_action_id"],
            name="fk_rule_parameter_rule_action_id_rule_action", ondelete="CASCADE",
        ),
        schema=RULE,
    )
    for col in ("tenant_id", "rule_version_id", "rule_action_id"):
        op.create_index(f"ix_rule_parameter_{col}", "rule_parameter", [col], schema=RULE)
    op.create_index("ix_rule_parameter_name", "rule_parameter",
                    ["tenant_id", "parameter_name"], schema=RULE)
    op.create_index("ix_rule_parameter_ref", "rule_parameter",
                    ["tenant_id", "resolved_ref_id"], schema=RULE)


# --- Graph + sets -----------------------------------------------------------


def _create_graph_and_sets() -> None:
    rule_fk = f"{RULE}.rule.rule_id"

    op.create_table(
        "rule_dependency",
        sa.Column("rule_dependency_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("depends_on_rule_id", sa.String(36), nullable=False),
        sa.Column("dependency_type", sa.String(32), nullable=False),
        sa.Column("notes", sa.Text, server_default="", nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id", "rule_id", "depends_on_rule_id", "dependency_type",
            name="uq_rule_dependency_edge",
        ),
        sa.CheckConstraint("rule_id <> depends_on_rule_id", name="ck_rule_dependency_not_self"),
        sa.ForeignKeyConstraint(["rule_id"], [rule_fk],
                                name="fk_rule_dependency_rule_id_rule", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on_rule_id"], [rule_fk],
                                name="fk_rule_dependency_depends_on_rule_id_rule",
                                ondelete="CASCADE"),
        schema=RULE,
    )
    for col in ("tenant_id", "rule_id"):
        op.create_index(f"ix_rule_dependency_{col}", "rule_dependency", [col], schema=RULE)
    op.create_index("ix_rule_dependency_reverse", "rule_dependency",
                    ["tenant_id", "depends_on_rule_id"], schema=RULE)

    op.create_table(
        "rule_fallback",
        sa.Column("rule_fallback_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("fallback_rule_id", sa.String(36), nullable=False),
        sa.Column("fallback_level", sa.SmallInteger, nullable=False),
        sa.Column("fallback_scope", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(255), server_default="", nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "rule_id", "fallback_level",
                            name="uq_rule_fallback_level"),
        sa.CheckConstraint("rule_id <> fallback_rule_id", name="ck_rule_fallback_not_self"),
        sa.CheckConstraint("fallback_level > 0", name="ck_rule_fallback_level_positive"),
        sa.ForeignKeyConstraint(["rule_id"], [rule_fk],
                                name="fk_rule_fallback_rule_id_rule", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["fallback_rule_id"], [rule_fk],
                                name="fk_rule_fallback_fallback_rule_id_rule",
                                ondelete="CASCADE"),
        schema=RULE,
    )
    for col in ("tenant_id", "rule_id"):
        op.create_index(f"ix_rule_fallback_{col}", "rule_fallback", [col], schema=RULE)

    op.create_table(
        "rule_set",
        sa.Column("rule_set_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, server_default="", nullable=False),
        # LOGICAL (editorial grouping) | RELEASE (pinned versions the compiler
        # snapshots) | VENDOR_IMPORT (revertible as a unit)
        sa.Column("set_type", sa.String(32), server_default="LOGICAL", nullable=False),
        sa.Column("charging_mode", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), server_default="ACTIVE", nullable=False),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("source_system_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_rule_set_tenant_code"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], [f"{RATING}.source_systems.id"],
            name="fk_rule_set_source_system_id_source_systems", ondelete="RESTRICT",
        ),
        schema=RULE,
    )
    for col in ("tenant_id", "set_type", "status"):
        op.create_index(f"ix_rule_set_{col}", "rule_set", [col], schema=RULE)

    op.create_table(
        "rule_set_member",
        sa.Column("rule_set_member_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("rule_set_id", sa.String(36), nullable=False),
        sa.Column("rule_id", sa.String(36), nullable=False),
        # Pinned for RELEASE sets: this exact version ships.
        sa.Column("rule_version_id", sa.String(36), nullable=True),
        sa.Column("sequence_number", sa.Integer, server_default="0", nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("rule_set_id", "rule_id", name="uq_rule_set_member_set_rule"),
        sa.ForeignKeyConstraint(
            ["rule_set_id"], [f"{RULE}.rule_set.rule_set_id"],
            name="fk_rule_set_member_rule_set_id_rule_set", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["rule_id"], [rule_fk],
                                name="fk_rule_set_member_rule_id_rule", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["rule_version_id"], [f"{RULE}.rule_version.rule_version_id"],
            name="fk_rule_set_member_rule_version_id_rule_version", ondelete="SET NULL",
        ),
        schema=RULE,
    )
    for col in ("tenant_id", "rule_set_id"):
        op.create_index(f"ix_rule_set_member_{col}", "rule_set_member", [col], schema=RULE)
    op.create_index("ix_rule_set_member_rule", "rule_set_member",
                    ["tenant_id", "rule_id"], schema=RULE)


# --- Lineage ----------------------------------------------------------------


def _create_lineage() -> None:
    version_fk = f"{RULE}.rule_version.rule_version_id"

    op.create_table(
        "rule_ingestion_batch",
        sa.Column("batch_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("source_system_id", sa.String(36), nullable=True),
        sa.Column("profile_id", sa.String(36), nullable=True),
        sa.Column("filename", sa.String(512), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("import_mode", sa.String(16), server_default="DELTA", nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("dry_run", sa.Boolean, server_default="false", nullable=False),
        sa.Column("counts", JSONB, server_default="{}", nullable=False),
        # A failure at row 39,000 of 40,000 resumes instead of restarting.
        sa.Column("checkpoint", JSONB, server_default="{}", nullable=False),
        sa.Column("reasons", JSONB, server_default="[]", nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_by", sa.String(36), nullable=True),
        sa.Column("triggered_by_name", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_system_id"], [f"{RATING}.source_systems.id"],
            name="fk_rule_ingestion_batch_source_system_id_source_systems",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], [f"{RULE}.rule_import_profile.profile_id"],
            name="fk_rule_ingestion_batch_profile_id_rule_import_profile",
            ondelete="SET NULL",
        ),
        schema=RULE,
    )
    for col in ("tenant_id", "channel", "status"):
        op.create_index(f"ix_rule_ingestion_batch_{col}", "rule_ingestion_batch", [col],
                        schema=RULE)
    op.create_index("ix_rule_ingestion_batch_started", "rule_ingestion_batch",
                    ["tenant_id", "started_at"], schema=RULE)
    op.create_index("ix_rule_ingestion_batch_source", "rule_ingestion_batch",
                    ["tenant_id", "source_system_id", "status"], schema=RULE)
    # The same file posted twice is the same batch. Partial, because a manual
    # authoring batch has no content hash and must not collide with another.
    op.create_index(
        "uq_rule_ingestion_batch_content", "rule_ingestion_batch",
        ["tenant_id", "source_system_id", "content_hash"],
        unique=True, postgresql_where=sa.text("content_hash IS NOT NULL"), schema=RULE,
    )

    op.create_table(
        "rule_source_lineage",
        sa.Column("lineage_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("rule_version_id", sa.String(36), nullable=False),
        sa.Column("source_system_id", sa.String(36), nullable=True),
        sa.Column("batch_id", sa.String(36), nullable=True),
        # No FK: rule_ingestion_record is partitioned and lands in R5, and lineage
        # must outlive the record retention window anyway.
        sa.Column("record_id", sa.String(36), nullable=True),
        sa.Column("external_ref", sa.String(255), nullable=True),
        sa.Column("external_version", sa.String(64), nullable=True),
        sa.Column("raw_hash", sa.String(64), nullable=True),
        sa.Column("field_provenance", JSONB, server_default="{}", nullable=False),
        sa.Column("applied_aliases", JSONB, server_default="{}", nullable=False),
        sa.Column("unmapped_fields", JSONB, server_default="{}", nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_version_id"], [version_fk],
            name="fk_rule_source_lineage_rule_version_id_rule_version", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"], [f"{RATING}.source_systems.id"],
            name="fk_rule_source_lineage_source_system_id_source_systems", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], [f"{RULE}.rule_ingestion_batch.batch_id"],
            name="fk_rule_source_lineage_batch_id_rule_ingestion_batch", ondelete="SET NULL",
        ),
        schema=RULE,
    )
    op.create_index("ix_rule_source_lineage_tenant_id", "rule_source_lineage", ["tenant_id"],
                    schema=RULE)
    op.create_index("ix_rule_source_lineage_version", "rule_source_lineage",
                    ["rule_version_id"], schema=RULE)
    op.create_index("ix_rule_source_lineage_extref", "rule_source_lineage",
                    ["tenant_id", "source_system_id", "external_ref"], schema=RULE)

    op.create_table(
        "rule_validation_issue",
        sa.Column("issue_id", sa.String(36), primary_key=True),
        _tenant(),
        sa.Column("rule_version_id", sa.String(36), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        # Points at the exact condition or action row so the builder highlights it.
        sa.Column("path", sa.String(128), server_default="", nullable=False),
        sa.Column("hint", sa.Text, server_default="", nullable=False),
        sa.Column("tier", sa.String(16), server_default="STRUCTURAL", nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_version_id"], [version_fk],
            name="fk_rule_validation_issue_rule_version_id_rule_version", ondelete="CASCADE",
        ),
        schema=RULE,
    )
    op.create_index("ix_rule_validation_issue_tenant_id", "rule_validation_issue",
                    ["tenant_id"], schema=RULE)
    op.create_index("ix_rule_validation_issue_version", "rule_validation_issue",
                    ["rule_version_id", "severity"], schema=RULE)
    op.create_index("ix_rule_validation_issue_code", "rule_validation_issue",
                    ["tenant_id", "code", "severity"], schema=RULE)

    op.create_table(
        "rule_audit",
        sa.Column("audit_id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False,
                  server_default=DEFAULT_TENANT),
        # Keyed on the logical rule, so the timeline spans every version.
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("rule_version_id", sa.String(36), nullable=True),
        sa.Column("version_number", sa.Integer, nullable=True),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=True),
        sa.Column("channel", sa.String(16), nullable=True),
        sa.Column("batch_id", sa.String(36), nullable=True),
        sa.Column("actor_id", sa.String(36), nullable=True),
        sa.Column("actor_name", sa.String(255), nullable=True),
        sa.Column("comment", sa.Text, server_default="", nullable=False),
        sa.Column("diff", JSONB, server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        schema=RULE,
    )
    op.create_index("ix_rule_audit_tenant_id", "rule_audit", ["tenant_id"], schema=RULE)
    op.create_index("ix_canonical_rule_audit_rule_time", "rule_audit",
                    ["rule_id", "created_at"], schema=RULE)
    op.create_index("ix_canonical_rule_audit_actor", "rule_audit",
                    ["tenant_id", "actor_id"], schema=RULE)


# --- Row-level security -----------------------------------------------------

#: Tables carrying tenant_id. RLS is enabled with a permissive policy: the column
#: is honest from day one, the boundary is enforced in step M8 once the session
#: GUC is wired. Enabling it now means M8 is a policy swap, not a table-by-table
#: rollout on a populated estate.
_RLS_TABLES: tuple[str, ...] = (
    "rule_stage", "rule_type", "rule_stacking_policy", "rule_conflict_group",
    "rule_import_profile", "rule", "rule_version", "rule_condition_group",
    "rule_condition", "rule_action", "rule_parameter", "rule_dependency",
    "rule_fallback", "rule_set", "rule_set_member", "rule_ingestion_batch",
    "rule_source_lineage", "rule_validation_issue", "rule_audit",
)


def _enable_rls() -> None:
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {RULE}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {RULE}.{table} "
            "USING (true) WITH CHECK (true)"
        )


def downgrade() -> None:
    # Dropping the schema takes the tables, indexes, constraints and policies with
    # it. btree_gist is left installed: something else may rely on it, and an
    # extension is not this migration's to remove.
    op.execute(f'DROP SCHEMA IF EXISTS "{RULE}" CASCADE')
