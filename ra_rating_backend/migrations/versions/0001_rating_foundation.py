"""rating assurance foundation — canonical metadata + canonical rule model

Phase 1 of the Rating Assurance programme. Creates every table in the `rating`
schema. Touches nothing outside it: the `administration` schema owned by the
RADONaix API is neither read nor written by this migration.

Revision ID: 0001
Revises:
Create Date: 2026-07-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _catalog_columns() -> list[sa.Column]:
    """The column set every canonical metadata table shares."""
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("source_system", sa.String(64), nullable=False, server_default="MANUAL"),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
    ]


def _effective_dated() -> list[sa.Column]:
    return [
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
    ]


def _catalog_indexes(table: str, *, effective_dated: bool = False) -> None:
    op.create_index(f"ix_{table}_code", table, ["code"], unique=True)
    op.create_index(f"ix_{table}_status", table, ["status"])
    if effective_dated:
        op.create_index(f"ix_{table}_effective_from", table, ["effective_from"])
        op.create_index(f"ix_{table}_effective_to", table, ["effective_to"])


def upgrade() -> None:
    # ---------------------------------------------------------------- access
    op.create_table(
        "role_permissions",
        sa.Column("role_slug", sa.String(64), primary_key=True),
        sa.Column("permissions", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
    )

    # ------------------------------------------------------- canonical metadata
    op.create_table(
        "currencies",
        *_catalog_columns(),
        sa.Column("symbol", sa.String(8), nullable=False, server_default=""),
        sa.Column("decimals", sa.Integer(), nullable=False, server_default="2"),
    )
    _catalog_indexes("currencies")

    op.create_table(
        "rounding_rules",
        *_catalog_columns(),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("decimals", sa.Integer(), nullable=False, server_default="2"),
    )
    _catalog_indexes("rounding_rules")

    op.create_table(
        "tax_rules",
        *_catalog_columns(),
        *_effective_dated(),
        sa.Column("tax_type", sa.String(16), nullable=False),
        sa.Column("jurisdiction", sa.String(64), nullable=False, server_default=""),
        sa.Column("rate_percent", sa.Numeric(9, 4), nullable=False),
        sa.Column("inclusive", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    _catalog_indexes("tax_rules", effective_dated=True)
    op.create_index("ix_tax_rules_jurisdiction", "tax_rules", ["jurisdiction"])

    op.create_table(
        "services",
        *_catalog_columns(),
        sa.Column("service_type", sa.String(16), nullable=False),
        sa.Column("usage_unit", sa.String(16), nullable=False),
    )
    _catalog_indexes("services")
    op.create_index("ix_services_service_type", "services", ["service_type"])

    op.create_table(
        "products",
        *_catalog_columns(),
        *_effective_dated(),
        sa.Column("product_type", sa.String(32), nullable=False, server_default="TARIFF"),
        sa.Column("account_type", sa.String(16), nullable=False, server_default="ANY"),
        sa.Column("service_types", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("currency_code", sa.String(8), nullable=True),
    )
    _catalog_indexes("products", effective_dated=True)
    op.create_index("ix_products_account_type", "products", ["account_type"])

    op.create_table(
        "offers",
        *_catalog_columns(),
        *_effective_dated(),
        sa.Column("product_id", sa.String(36), nullable=False),
        sa.Column("exclusive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"],
            name="fk_offers_product_id_products", ondelete="RESTRICT",
        ),
    )
    _catalog_indexes("offers", effective_dated=True)
    op.create_index("ix_offers_product_id", "offers", ["product_id"])

    op.create_table(
        "tariff_plans",
        *_catalog_columns(),
        *_effective_dated(),
        sa.Column("product_id", sa.String(36), nullable=True),
        sa.Column("offer_id", sa.String(36), nullable=True),
        sa.Column("service_type", sa.String(16), nullable=False),
        sa.Column("currency_code", sa.String(8), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"],
            name="fk_tariff_plans_product_id_products", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"], ["offers.id"],
            name="fk_tariff_plans_offer_id_offers", ondelete="RESTRICT",
        ),
    )
    _catalog_indexes("tariff_plans", effective_dated=True)
    op.create_index("ix_tariff_plans_product_id", "tariff_plans", ["product_id"])
    op.create_index("ix_tariff_plans_offer_id", "tariff_plans", ["offer_id"])
    op.create_index("ix_tariff_plans_service_type", "tariff_plans", ["service_type"])

    op.create_table(
        "time_bands",
        *_catalog_columns(),
        sa.Column("days", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
    )
    _catalog_indexes("time_bands")

    op.create_table(
        "destination_zones",
        *_catalog_columns(),
        sa.Column("zone_type", sa.String(16), nullable=False),
        sa.Column("country_code", sa.String(8), nullable=True),
    )
    _catalog_indexes("destination_zones")
    op.create_index("ix_destination_zones_zone_type", "destination_zones", ["zone_type"])
    op.create_index("ix_destination_zones_country_code", "destination_zones", ["country_code"])

    op.create_table(
        "destination_prefixes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("zone_id", sa.String(36), nullable=False),
        sa.Column("prefix", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["destination_zones.id"],
            name="fk_destination_prefixes_zone_id_destination_zones", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("prefix", name="uq_destination_prefixes_prefix"),
    )
    op.create_index("ix_destination_prefixes_zone_id", "destination_prefixes", ["zone_id"])
    op.create_index("ix_destination_prefixes_prefix", "destination_prefixes", ["prefix"])

    op.create_table(
        "rating_groups",
        *_catalog_columns(),
        sa.Column("service_type", sa.String(16), nullable=False),
    )
    _catalog_indexes("rating_groups")
    op.create_index("ix_rating_groups_service_type", "rating_groups", ["service_type"])

    op.create_table(
        "discount_definitions",
        *_catalog_columns(),
        *_effective_dated(),
        sa.Column("discount_type", sa.String(24), nullable=False),
        sa.Column("value", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency_code", sa.String(8), nullable=True),
        sa.Column("pre_tax", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    _catalog_indexes("discount_definitions", effective_dated=True)

    op.create_table(
        "bundle_definitions",
        *_catalog_columns(),
        *_effective_dated(),
        sa.Column("service_type", sa.String(16), nullable=False),
        sa.Column("quota_unit", sa.String(16), nullable=False),
        sa.Column("quota_value", sa.Numeric(18, 4), nullable=False),
        sa.Column("reset_period", sa.String(16), nullable=False, server_default="MONTHLY"),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    _catalog_indexes("bundle_definitions", effective_dated=True)
    op.create_index("ix_bundle_definitions_service_type", "bundle_definitions", ["service_type"])

    op.create_table(
        "promotions",
        *_catalog_columns(),
        *_effective_dated(),
        sa.Column("promotion_type", sa.String(24), nullable=False),
        sa.Column("value", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency_code", sa.String(8), nullable=True),
        sa.Column("exclusive", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    _catalog_indexes("promotions", effective_dated=True)

    # ------------------------------------------------------------------ rules
    op.create_table(
        "rule_sets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("source_system", sa.String(64), nullable=False, server_default="MANUAL"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rule_sets_code", "rule_sets", ["code"], unique=True)
    op.create_index("ix_rule_sets_status", "rule_sets", ["status"])

    op.create_table(
        "rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("rule_key", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("supersedes_id", sa.String(36), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("execution_stage", sa.String(32), nullable=False),
        sa.Column("category", sa.String(64), nullable=False, server_default=""),
        sa.Column("service_type", sa.String(16), nullable=False),
        sa.Column("rule_set_id", sa.String(36), nullable=True),
        sa.Column("product_id", sa.String(36), nullable=True),
        sa.Column("offer_id", sa.String(36), nullable=True),
        sa.Column("tariff_plan_id", sa.String(36), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("specificity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stacking_policy", sa.String(16), nullable=False, server_default="EXCLUSIVE"),
        sa.Column("conflict_group", sa.String(64), nullable=True),
        sa.Column("condition_logic", sa.String(4), nullable=False, server_default="AND"),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("currency_code", sa.String(8), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("source_system", sa.String(64), nullable=False, server_default="MANUAL"),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("submitted_by", sa.String(36), nullable=True),
        sa.Column("submitted_at", _ts(), nullable=True),
        sa.Column("approved_by", sa.String(36), nullable=True),
        sa.Column("approved_at", _ts(), nullable=True),
        sa.Column("retired_at", _ts(), nullable=True),
        sa.Column("change_comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_validation", postgresql.JSONB(), nullable=True),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_set_id"], ["rule_sets.id"],
            name="fk_rules_rule_set_id_rule_sets", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"],
            name="fk_rules_product_id_products", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"], ["offers.id"],
            name="fk_rules_offer_id_offers", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tariff_plan_id"], ["tariff_plans.id"],
            name="fk_rules_tariff_plan_id_tariff_plans", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("rule_key", "version", name="uq_rules_rule_key"),
    )
    op.create_index("ix_rules_rule_key", "rules", ["rule_key"])
    op.create_index("ix_rules_supersedes_id", "rules", ["supersedes_id"])
    op.create_index("ix_rules_rule_type", "rules", ["rule_type"])
    op.create_index("ix_rules_execution_stage", "rules", ["execution_stage"])
    op.create_index("ix_rules_service_type", "rules", ["service_type"])
    op.create_index("ix_rules_status", "rules", ["status"])
    op.create_index("ix_rules_priority", "rules", ["priority"])
    op.create_index("ix_rules_specificity", "rules", ["specificity"])
    op.create_index("ix_rules_conflict_group", "rules", ["conflict_group"])
    op.create_index("ix_rules_source_system", "rules", ["source_system"])
    op.create_index("ix_rules_created_by", "rules", ["created_by"])
    op.create_index("ix_rules_rule_set_id", "rules", ["rule_set_id"])
    op.create_index("ix_rules_product_id", "rules", ["product_id"])
    op.create_index("ix_rules_offer_id", "rules", ["offer_id"])
    op.create_index("ix_rules_tariff_plan_id", "rules", ["tariff_plan_id"])
    op.create_index("ix_rules_effective_from", "rules", ["effective_from"])
    op.create_index("ix_rules_effective_to", "rules", ["effective_to"])
    # The Phase-3 rule-selection hot path: narrow by service + status + validity
    # window before anything else is evaluated.
    op.create_index(
        "ix_rules_selection", "rules",
        ["service_type", "status", "effective_from", "effective_to"],
    )
    op.create_index("ix_rules_product_service", "rules", ["product_id", "service_type"])

    op.create_table(
        "rule_conditions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("group_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attribute", sa.String(64), nullable=False),
        sa.Column("operator", sa.String(24), nullable=False),
        sa.Column("values", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("negate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["rules.id"],
            name="fk_rule_conditions_rule_id_rules", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_rule_conditions_rule_id", "rule_conditions", ["rule_id"])
    op.create_index("ix_rule_conditions_attribute", "rule_conditions", ["attribute"])
    op.create_index("ix_rule_conditions_rule_seq", "rule_conditions", ["rule_id", "sequence"])

    op.create_table(
        "rule_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["rules.id"],
            name="fk_rule_actions_rule_id_rules", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_rule_actions_rule_id", "rule_actions", ["rule_id"])
    op.create_index("ix_rule_actions_action_type", "rule_actions", ["action_type"])
    op.create_index("ix_rule_actions_rule_seq", "rule_actions", ["rule_id", "sequence"])

    op.create_table(
        "rule_audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("rule_key", sa.String(80), nullable=False),
        sa.Column("rule_id", sa.String(36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=True),
        sa.Column("actor_id", sa.String(36), nullable=True),
        sa.Column("actor_name", sa.String(255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("diff", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rule_audit_rule_key", "rule_audit", ["rule_key"])
    op.create_index("ix_rule_audit_rule_id", "rule_audit", ["rule_id"])
    op.create_index("ix_rule_audit_key_time", "rule_audit", ["rule_key", "created_at"])

    op.create_table(
        "rule_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("service_type", sa.String(16), nullable=False),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", _ts(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _ts(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rule_templates_code", "rule_templates", ["code"], unique=True)
    op.create_index("ix_rule_templates_service_type", "rule_templates", ["service_type"])


def downgrade() -> None:
    for table in (
        "rule_templates",
        "rule_audit",
        "rule_actions",
        "rule_conditions",
        "rules",
        "rule_sets",
        "promotions",
        "bundle_definitions",
        "discount_definitions",
        "rating_groups",
        "destination_prefixes",
        "destination_zones",
        "time_bands",
        "tariff_plans",
        "offers",
        "products",
        "services",
        "tax_rules",
        "rounding_rules",
        "currencies",
        "role_permissions",
    ):
        op.drop_table(table)
