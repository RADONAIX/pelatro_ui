"""Charging, prepaid and postpaid metadata in the rule-management schema.

Additive only. Seventeen new tables in ``ra_rule``, nothing altered, nothing
dropped, no existing query touched — a running system can take this migration.

Two decisions worth reading before changing them:

**Same schema as the rules.** These tables exist to be referenced by a rule
parameter, are edited on the same screens, are granted to the same role and are
restored in the same recovery as ``rule`` and ``rule_version``. Putting a schema
boundary between them would cut through the middle of one lifecycle for no
operational benefit, and would make every reference a cross-schema foreign key.

**``balance_bucket_definition``, not ``balance_bucket``.** ``rating.balance_buckets``
already exists and holds a *subscriber's* balance. This table holds the
definition of what a bucket grants. Reusing the name would leave "how much data
does ADD_5GB give" answerable only by scanning subscriber rows.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels = None
depends_on = None

#: The rule-management schema. The metadata catalogue shares it with the rules.
RULE = "ra_rule"
#: The execution plane, for the handful of cross-schema references.
RATING = "rating"

MONEY = sa.Numeric(20, 6)
DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

#: Every table this migration creates, in creation order (parents first).
_TABLES: tuple[str, ...] = (
    "charging_unit",
    "pulse_profile",
    "charge_limit_profile",
    "balance_type",
    "ocs_profile",
    "reservation_policy",
    "proration_profile",
    "credit_limit_profile",
    "charging_profile",
    "balance_bucket_definition",
    "balance_priority",
    "billing_cycle",
    "invoice_component",
    "recurring_charge",
    "one_time_charge",
    "usage_aggregation_profile",
    "late_fee_profile",
)


def _common(table: str) -> list[sa.Column]:
    """The columns every metadata entity shares.

    Deliberately the same shape as the existing ``rating`` catalogue tables, so
    the generic CRUD router drives these entities without a new endpoint. The
    difference is ``tenant_id`` and a per-tenant unique code rather than a
    globally unique one — new tables, so getting it right costs nothing now.
    """
    del table
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False,
                  server_default=DEFAULT_TENANT),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, server_default="", nullable=False),
        sa.Column("status", sa.String(16), server_default="ACTIVE", nullable=False),
        sa.Column("source_system", sa.String(64), server_default="MANUAL",
                  nullable=False),
        sa.Column("attributes", JSONB, server_default="{}", nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    ]


def _create(table: str, *columns: sa.Column, extra: tuple = ()) -> None:
    """Create one metadata table plus its indexes.

    Indexes are issued explicitly rather than through ``index=True`` on the
    columns: an inline flag lets SQLAlchemy pick the name, and a name this
    migration did not choose is a name a later migration cannot reliably drop.
    Every ``*_id`` column is indexed — they are all foreign keys — alongside the
    tenant, code and status columns the list screens filter on.
    """
    op.create_table(
        table,
        *_common(table),
        *columns,
        sa.UniqueConstraint("tenant_id", "code", name=f"uq_{table}_tenant_code"),
        *extra,
        schema=RULE,
    )
    indexed = ["tenant_id", "code", "status"] + [
        c.name for c in columns if c.name.endswith("_id") or c.name in _FILTERED
    ]
    for column in indexed:
        op.create_index(f"ix_{table}_{column}", table, [column], schema=RULE)


#: Columns the catalogue list screens filter on, so they earn an index even
#: though they are not keys.
_FILTERED: frozenset[str] = frozenset(
    {
        "dimension", "service_type", "category", "charging_mode", "frequency",
        "component_type", "trigger_event", "effective_from", "effective_to",
    }
)


def _self_fk(column: str, table: str, *, nullable: bool = True) -> sa.Column:
    """A foreign key into another table in this schema."""
    return sa.Column(
        column,
        sa.String(36),
        sa.ForeignKey(f"{RULE}.{table}.id", ondelete="RESTRICT"),
        nullable=nullable,
    )


def _rating_fk(column: str, table: str) -> sa.Column:
    """A foreign key into the execution plane's catalogue."""
    return sa.Column(
        column,
        sa.String(36),
        sa.ForeignKey(f"{RATING}.{table}.id", ondelete="RESTRICT"),
        nullable=True,
    )


def upgrade() -> None:
    # ra_rule already exists (migration 0010); the guard keeps this migration
    # runnable against a database where the schema was created by hand.
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{RULE}"')

    _create_shared()
    _create_prepaid()
    _create_postpaid()
    _enable_rls()


# --- Shared charging (plan §C.5) --------------------------------------------


def _create_shared() -> None:
    _create(
        "charging_unit",
        # TIME | VOLUME | EVENT | MESSAGE | CURRENCY. A rate's unit must match
        # its dimension — per-megabyte voice is a modelling error, not a taste.
        sa.Column("dimension", sa.String(16), nullable=False),
        sa.Column("base_unit", sa.String(16), nullable=False),
        # Multiplier to the dimension's base unit: 60 for MINUTE, 1048576 for MB.
        sa.Column("factor", MONEY, server_default="1", nullable=False),
        sa.Column("decimals", sa.Integer, server_default="6", nullable=False),
    )

    _create(
        "pulse_profile",
        sa.Column("service_type", sa.String(16), nullable=False),
        sa.Column("initial_seconds", sa.Integer, nullable=False),
        sa.Column("subsequent_seconds", sa.Integer, nullable=False),
        sa.Column("round_mode", sa.String(16), server_default="UP", nullable=False),
        sa.Column("min_chargeable_seconds", sa.Integer, server_default="0",
                  nullable=False),
        sa.Column("unit_code", sa.String(16), nullable=True),
    )

    _create(
        "charge_limit_profile",
        sa.Column("service_type", sa.String(16), nullable=False),
        sa.Column("min_charge", MONEY, nullable=True),
        sa.Column("max_charge", MONEY, nullable=True),
        sa.Column("min_quantity", MONEY, nullable=True),
        sa.Column("max_quantity", MONEY, nullable=True),
        sa.Column("currency_code", sa.String(8), nullable=True),
        sa.Column("unit_code", sa.String(16), nullable=True),
    )


# --- Prepaid (plan §C.6) ----------------------------------------------------


def _create_prepaid() -> None:
    _create(
        "balance_type",
        sa.Column("category", sa.String(16), server_default="MAIN", nullable=False),
        sa.Column("unit_code", sa.String(16), nullable=True),
        # Decides whether a deduction against it needs a currency at all.
        sa.Column("is_monetary", sa.Boolean, server_default="true", nullable=False),
        sa.Column("allows_negative", sa.Boolean, server_default="false",
                  nullable=False),
        sa.Column("expiry_policy", sa.String(16), server_default="NONE",
                  nullable=False),
    )

    _create(
        "ocs_profile",
        sa.Column("vendor", sa.String(64), server_default="", nullable=False),
        sa.Column("reservation_strategy", sa.String(16), server_default="QUOTA",
                  nullable=False),
        sa.Column("quota_unit", sa.String(16), nullable=True),
        sa.Column("initial_quota", MONEY, nullable=True),
        sa.Column("subsequent_quota", MONEY, nullable=True),
        sa.Column("quota_validity_seconds", sa.Integer, nullable=True),
        sa.Column("redirect_on_exhaust", sa.String(255), nullable=True),
        # Endpoint, realm, timeouts. Never credentials — those stay in the
        # connector's encrypted config, which redacts on read.
        sa.Column("connection", JSONB, server_default="{}", nullable=False),
    )

    _create(
        "reservation_policy",
        _self_fk("ocs_profile_id", "ocs_profile"),
        sa.Column("initial_quota", MONEY, nullable=True),
        sa.Column("subsequent_quota", MONEY, nullable=True),
        sa.Column("unit_code", sa.String(16), nullable=True),
        sa.Column("validity_seconds", sa.Integer, nullable=True),
        sa.Column("threshold_pct", sa.Integer, nullable=True),
        sa.Column("release_mode", sa.String(16), server_default="UNUSED",
                  nullable=False),
        sa.Column("on_timeout", sa.String(16), server_default="RELEASE",
                  nullable=False),
    )

    _create(
        "proration_profile",
        sa.Column("method", sa.String(16), server_default="DAILY", nullable=False),
        sa.Column("round_mode", sa.String(16), server_default="HALF_UP",
                  nullable=False),
        sa.Column("apply_on_activation", sa.Boolean, server_default="true",
                  nullable=False),
        sa.Column("apply_on_cease", sa.Boolean, server_default="true",
                  nullable=False),
        sa.Column("apply_on_plan_change", sa.Boolean, server_default="true",
                  nullable=False),
    )

    _create(
        "credit_limit_profile",
        sa.Column("limit_amount", MONEY, nullable=False),
        sa.Column("currency_code", sa.String(8), nullable=False),
        sa.Column("warning_threshold_pct", sa.Integer, server_default="80",
                  nullable=False),
        sa.Column("breach_action", sa.String(16), server_default="NOTIFY",
                  nullable=False),
        sa.Column("grace_days", sa.Integer, server_default="0", nullable=False),
    )

    _create(
        "charging_profile",
        sa.Column("charging_mode", sa.String(16), server_default="PREPAID",
                  nullable=False),
        _self_fk("ocs_profile_id", "ocs_profile"),
        _self_fk("reservation_policy_id", "reservation_policy"),
        _self_fk("credit_limit_profile_id", "credit_limit_profile"),
        sa.Column("default_currency_code", sa.String(8), nullable=True),
        _rating_fk("rounding_rule_id", "rounding_rules"),
        sa.Column("negative_balance_allowed", sa.Boolean, server_default="false",
                  nullable=False),
    )

    _create(
        "balance_bucket_definition",
        _self_fk("balance_type_id", "balance_type", nullable=False),
        sa.Column("unit_code", sa.String(16), nullable=True),
        sa.Column("initial_amount", MONEY, nullable=True),
        sa.Column("currency_code", sa.String(8), nullable=True),
        sa.Column("validity_days", sa.Integer, nullable=True),
        sa.Column("carry_over_flag", sa.Boolean, server_default="false",
                  nullable=False),
        sa.Column("shared_flag", sa.Boolean, server_default="false", nullable=False),
    )

    _create(
        "balance_priority",
        _self_fk("charging_profile_id", "charging_profile", nullable=False),
        _self_fk("balance_type_id", "balance_type", nullable=False),
        sa.Column("service_type", sa.String(16), server_default="ANY",
                  nullable=False),
        # Lower is consumed first. This row is what turns "spend promotional
        # before main" from a hard-coded action parameter into data.
        sa.Column("consumption_order", sa.SmallInteger, server_default="100",
                  nullable=False),
        extra=(
            sa.UniqueConstraint(
                "tenant_id", "charging_profile_id", "balance_type_id",
                "service_type", name="uq_balance_priority_scope",
            ),
        ),
    )


# --- Postpaid (plan §C.7) ---------------------------------------------------


def _create_postpaid() -> None:
    _create(
        "billing_cycle",
        sa.Column("frequency", sa.String(16), server_default="MONTHLY",
                  nullable=False),
        sa.Column("cycle_start_day", sa.Integer, server_default="1", nullable=False),
        sa.Column("bill_run_offset_days", sa.Integer, server_default="0",
                  nullable=False),
        # The cycle's own timezone, not the server's. A boundary evaluated in the
        # wrong zone moves usage between invoices.
        sa.Column("timezone", sa.String(64), server_default="UTC", nullable=False),
        _self_fk("proration_profile_id", "proration_profile"),
    )

    _create(
        "invoice_component",
        sa.Column("component_type", sa.String(16), server_default="USAGE",
                  nullable=False),
        sa.Column("gl_account", sa.String(64), server_default="", nullable=False),
        _rating_fk("tax_rule_id", "tax_rules"),
        sa.Column("display_order", sa.Integer, server_default="100", nullable=False),
        sa.Column("sign", sa.String(8), server_default="DEBIT", nullable=False),
    )

    _create(
        "recurring_charge",
        _rating_fk("product_id", "products"),
        _rating_fk("offer_id", "offers"),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency_code", sa.String(8), nullable=False),
        _self_fk("billing_cycle_id", "billing_cycle"),
        _self_fk("invoice_component_id", "invoice_component"),
        _self_fk("proration_profile_id", "proration_profile"),
        sa.Column("advance_flag", sa.Boolean, server_default="true", nullable=False),
        sa.Column("effective_from", sa.Date, nullable=True),
        sa.Column("effective_to", sa.Date, nullable=True),
    )

    _create(
        "one_time_charge",
        sa.Column("trigger_event", sa.String(24), server_default="MANUAL",
                  nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency_code", sa.String(8), nullable=False),
        _self_fk("invoice_component_id", "invoice_component"),
        sa.Column("refundable_flag", sa.Boolean, server_default="false",
                  nullable=False),
    )

    _create(
        "usage_aggregation_profile",
        sa.Column("dimension", sa.String(16), server_default="SUBSCRIBER",
                  nullable=False),
        sa.Column("window", sa.String(16), server_default="CYCLE", nullable=False),
        sa.Column("service_type", sa.String(16), server_default="ANY",
                  nullable=False),
        sa.Column("reset_policy", sa.String(16), server_default="CYCLE",
                  nullable=False),
        _self_fk("invoice_component_id", "invoice_component"),
    )

    _create(
        "late_fee_profile",
        sa.Column("fee_amount", MONEY, nullable=True),
        sa.Column("fee_percentage", sa.Numeric(9, 4), nullable=True),
        sa.Column("currency_code", sa.String(8), nullable=True),
        sa.Column("grace_days", sa.Integer, server_default="0", nullable=False),
        # 0 = unlimited. A late fee that compounds forever is a support ticket.
        sa.Column("max_occurrences", sa.Integer, server_default="0", nullable=False),
        _self_fk("invoice_component_id", "invoice_component"),
    )


# --- Row-level security -----------------------------------------------------


def _enable_rls() -> None:
    """Same posture as migration 0010: enabled, permissive, tightened in M8.

    Enabling it now means the M8 cut-over is a policy swap rather than a
    table-by-table rollout across a populated estate.
    """
    for table in _TABLES:
        op.execute(f"ALTER TABLE {RULE}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {RULE}.{table} "
            "USING (true) WITH CHECK (true)"
        )


def downgrade() -> None:
    # Reverse creation order so the foreign keys unwind cleanly. The schema
    # itself is NOT dropped — migration 0010 owns it and its rule tables are
    # still in it.
    for table in reversed(_TABLES):
        op.drop_table(table, schema=RULE)
