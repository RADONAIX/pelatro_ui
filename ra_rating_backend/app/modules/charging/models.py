"""Charging, prepaid and postpaid metadata — the catalogue rules point at.

These seventeen tables are what turn "60/60 pulse" from a parameter retyped on
4,000 rules into one row referenced by 4,000 rules, and a pulse change from 4,000
new rule versions into one edit. Every rule reference reaches them the same way
the existing catalogue is reached: ``rule_parameter.parameter_value`` holds the
*code*, ``resolved_ref_id`` holds the id. No new join mechanism.

They live in the **rule-management schema** alongside ``rule`` and
``rule_version`` rather than in schemas of their own. That is deliberate: a
balance type exists only to be named by a rule, is edited on the same screen, is
granted to the same role and is restored in the same recovery. A schema boundary
between them would cut through the middle of one lifecycle, and cross-schema
foreign keys would buy nothing for it.

Two shapes are deliberately *not* here, because they are subscriber state rather
than catalogue metadata and belong with the engine work that reads them:
``balance_reservation`` / ``subscriber_balance_profile`` (prepaid runtime) and
``billing_account`` / ``billing_cycle_history`` (postpaid runtime). Both are
written by an execution plane this service does not yet have; modelling them now
would mean guessing at columns nothing populates.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.database import Base, TimestampMixin
from app.modules.catalog.constants import CatalogStatus
from app.modules.charging.constants import (
    AggregationDimension,
    AggregationWindow,
    BalanceCategory,
    BillingFrequency,
    ComponentSign,
    CreditBreachAction,
    ExpiryPolicy,
    InvoiceComponentType,
    ProrationMethod,
    PulseRoundMode,
    ReleaseMode,
    ReservationStrategy,
    ReservationTimeout,
    TriggerEvent,
)
from app.modules.rules.canonical.base import RULE_SCHEMA, TenantMixin, new_id

#: Money and allowances. Six decimal places because a per-second data rate in a
#: minor currency unit routinely needs five, and because the whole point of this
#: model is that no value in the rule path is ever a float.
MONEY = Numeric(20, 6)


class ChargingMetaMixin(TimestampMixin):
    """Columns every metadata entity in this module shares.

    Intentionally the same shape as ``catalog.CatalogMixin`` — code, name,
    description, status, source_system, attributes — so the existing generic CRUD
    router drives these entities without a single new endpoint. The one addition
    is ``tenant_id``, and the one change is that ``code`` is unique *per tenant*
    rather than globally: these tables are new, so getting that right costs
    nothing now and a full index rebuild later.
    """

    @declared_attr.directive
    def __table_args__(cls):
        extra = getattr(cls, "__extra_table_args__", ())
        return (
            UniqueConstraint(
                "tenant_id", "code", name=f"uq_{cls.__tablename__}_tenant_code"
            ),
            *extra,
            {"schema": RULE_SCHEMA},
        )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default=CatalogStatus.ACTIVE,
        server_default=CatalogStatus.ACTIVE.value,
        nullable=False,
        index=True,
    )
    source_system: Mapped[str] = mapped_column(
        String(64), default="MANUAL", server_default="MANUAL", nullable=False
    )
    #: Vendor fields with no canonical column yet. Preserved rather than dropped,
    #: so a re-export round-trips and an operator can see what we did not model.
    attributes: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


def _fk(table: str, column: str) -> ForeignKey:
    """A foreign key into another table in this schema."""
    return ForeignKey(f"{RULE_SCHEMA}.{table}.{column}", ondelete="RESTRICT")


# ============================================================================
# Shared charging metadata (plan §C.5)
# ============================================================================


class ChargingUnit(Base, TenantMixin, ChargingMetaMixin):
    """A unit of measure and its conversion to the dimension's base unit.

    ``factor`` is to the base unit of ``dimension`` (SECOND, BYTE, EVENT), so a
    MINUTE is 60 and a MEGABYTE is 1048576. Conversion happens in the engine, not
    at ingest: the rate an operator can point at in the vendor's document must be
    the rate they see on our screen.
    """

    __tablename__ = "charging_unit"

    dimension: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    base_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    factor: Mapped[Decimal] = mapped_column(
        MONEY, default=Decimal(1), server_default="1", nullable=False
    )
    decimals: Mapped[int] = mapped_column(
        Integer, default=6, server_default="6", nullable=False
    )


class PulseProfile(Base, TenantMixin, ChargingMetaMixin):
    """Pulse (block) charging terms — the 60/60, 1/1, 30/30 of a tariff sheet."""

    __tablename__ = "pulse_profile"

    service_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: First block, charged in full the moment the event becomes chargeable.
    initial_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Every block after the first. Equal to the initial block in a 60/60 tariff.
    subsequent_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    round_mode: Mapped[str] = mapped_column(
        String(16),
        default=PulseRoundMode.UP,
        server_default=PulseRoundMode.UP.value,
        nullable=False,
    )
    #: Below this, nothing is charged at all — distinct from the initial block,
    #: which is charged in full once the threshold is crossed.
    min_chargeable_seconds: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    unit_code: Mapped[str | None] = mapped_column(String(16), nullable=True)


class ChargeLimitProfile(Base, TenantMixin, ChargingMetaMixin):
    """Minimum / maximum charge and quantity bounds (plan §11 "Min/Max Profiles").

    Charge and quantity bounds live in one row because they are authored together
    and an operator reads them as one policy — "never less than 5p, never more
    than £2, never over 60 minutes".
    """

    __tablename__ = "charge_limit_profile"

    service_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    min_charge: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    max_charge: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    min_quantity: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    max_quantity: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    unit_code: Mapped[str | None] = mapped_column(String(16), nullable=True)


class ReservationPolicy(Base, TenantMixin, ChargingMetaMixin):
    """How much quota to reserve for an online session, and for how long.

    Referenced by ``RESERVE_BALANCE``. The policy is metadata rather than action
    parameters because an operator tunes reservation size once, for a whole OCS,
    and never per rule.
    """

    __tablename__ = "reservation_policy"

    ocs_profile_id: Mapped[str | None] = mapped_column(
        _fk("ocs_profile", "id"), nullable=True, index=True
    )
    initial_quota: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    subsequent_quota: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    unit_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    validity_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Re-authorise when this percentage of the granted quota is gone.
    threshold_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    release_mode: Mapped[str] = mapped_column(
        String(16),
        default=ReleaseMode.UNUSED,
        server_default=ReleaseMode.UNUSED.value,
        nullable=False,
    )
    on_timeout: Mapped[str] = mapped_column(
        String(16),
        default=ReservationTimeout.RELEASE,
        server_default=ReservationTimeout.RELEASE.value,
        nullable=False,
    )


# ============================================================================
# Prepaid metadata (plan §C.6)
# ============================================================================


class BalanceType(Base, TenantMixin, ChargingMetaMixin):
    """A kind of balance a subscriber can hold.

    The registry the current model lacks entirely: ``rating.balance_buckets``
    stores *a subscriber's* balance with the type as a free-text string, so
    nothing can answer "which balance types exist, and which are monetary".
    """

    __tablename__ = "balance_type"

    category: Mapped[str] = mapped_column(
        String(16),
        default=BalanceCategory.MAIN,
        server_default=BalanceCategory.MAIN.value,
        nullable=False,
        index=True,
    )
    unit_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Monetary balances are denominated in currency; a data bundle is not.
    #: Decides whether a DEDUCT_BALANCE against it needs a currency.
    is_monetary: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    allows_negative: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    expiry_policy: Mapped[str] = mapped_column(
        String(16),
        default=ExpiryPolicy.NONE,
        server_default=ExpiryPolicy.NONE.value,
        nullable=False,
    )


class BalanceBucketDefinition(Base, TenantMixin, ChargingMetaMixin):
    """The *definition* of a bucket — not a subscriber's balance in one.

    Named ``BalanceBucketDefinition`` in Python because ``rating.balance_buckets``
    already exists and holds subscriber state. The two are genuinely different
    things and conflating them is how "how much data does the ADD_5GB bucket
    grant" becomes unanswerable without scanning subscriber rows.
    """

    __tablename__ = "balance_bucket_definition"

    balance_type_id: Mapped[str] = mapped_column(
        _fk("balance_type", "id"), nullable=False, index=True
    )
    unit_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    initial_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    validity_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    carry_over_flag: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    shared_flag: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )


class OcsProfile(Base, TenantMixin, ChargingMetaMixin):
    """The online charging system a prepaid rule is authored against."""

    __tablename__ = "ocs_profile"

    vendor: Mapped[str] = mapped_column(
        String(64), default="", server_default="", nullable=False
    )
    reservation_strategy: Mapped[str] = mapped_column(
        String(16),
        default=ReservationStrategy.QUOTA,
        server_default=ReservationStrategy.QUOTA.value,
        nullable=False,
    )
    quota_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    initial_quota: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    subsequent_quota: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    quota_validity_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    redirect_on_exhaust: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Endpoint / realm / timeouts. Never credentials — those stay in the
    #: connector's encrypted config, which redacts on read.
    connection: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )


class ChargingProfile(Base, TenantMixin, ChargingMetaMixin):
    """The charging policy a subscriber population is governed by.

    The anchor for consumption order: ``balance_priority`` rows hang off this,
    which is what turns "spend the promotional balance before the main one" from
    a hard-coded action parameter into data an operator can change.
    """

    __tablename__ = "charging_profile"

    charging_mode: Mapped[str] = mapped_column(
        String(16), default="PREPAID", server_default="PREPAID", nullable=False, index=True
    )
    ocs_profile_id: Mapped[str | None] = mapped_column(
        _fk("ocs_profile", "id"), nullable=True, index=True
    )
    reservation_policy_id: Mapped[str | None] = mapped_column(
        _fk("reservation_policy", "id"), nullable=True, index=True
    )
    credit_limit_profile_id: Mapped[str | None] = mapped_column(
        _fk("credit_limit_profile", "id"), nullable=True, index=True
    )
    default_currency_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    #: Points at `rating.rounding_rules`, resolved through the search path like
    #: every other cross-schema reference in this service.
    rounding_rule_id: Mapped[str | None] = mapped_column(
        ForeignKey("rounding_rules.id", ondelete="RESTRICT"), nullable=True
    )
    negative_balance_allowed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )


class BalancePriority(Base, TenantMixin, ChargingMetaMixin):
    """Consumption order for one balance type, under one charging profile.

    Carries a code and a name like every other entity here so it gets the same
    CRUD screen for free, but the row that matters is the triple
    (profile, balance type, service) → order.
    """

    __tablename__ = "balance_priority"
    __extra_table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "charging_profile_id",
            "balance_type_id",
            "service_type",
            name="uq_balance_priority_scope",
        ),
    )

    charging_profile_id: Mapped[str] = mapped_column(
        _fk("charging_profile", "id"), nullable=False, index=True
    )
    balance_type_id: Mapped[str] = mapped_column(
        _fk("balance_type", "id"), nullable=False, index=True
    )
    #: ``ANY`` means the order holds for every service.
    service_type: Mapped[str] = mapped_column(
        String(16), default="ANY", server_default="ANY", nullable=False, index=True
    )
    #: Lower is consumed first.
    consumption_order: Mapped[int] = mapped_column(
        SmallInteger, default=100, server_default="100", nullable=False
    )


# ============================================================================
# Postpaid metadata (plan §C.7)
# ============================================================================


class ProrationProfile(Base, TenantMixin, ChargingMetaMixin):
    """How a part-period charge is apportioned."""

    __tablename__ = "proration_profile"

    method: Mapped[str] = mapped_column(
        String(16),
        default=ProrationMethod.DAILY,
        server_default=ProrationMethod.DAILY.value,
        nullable=False,
    )
    round_mode: Mapped[str] = mapped_column(
        String(16), default="HALF_UP", server_default="HALF_UP", nullable=False
    )
    apply_on_activation: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    apply_on_cease: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    apply_on_plan_change: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )


class BillingCycle(Base, TenantMixin, ChargingMetaMixin):
    """A billing period definition. Both a metadata entity and the target of the
    ``BILLING_CYCLE_ASSIGNMENT`` rule type — the spec lists it as both, and it is
    genuinely both: the cycle is data, assigning one is a decision."""

    __tablename__ = "billing_cycle"

    frequency: Mapped[str] = mapped_column(
        String(16),
        default=BillingFrequency.MONTHLY,
        server_default=BillingFrequency.MONTHLY.value,
        nullable=False,
        index=True,
    )
    #: Day of month the period opens. 1-28 by convention, because a cycle that
    #: starts on the 31st does not exist in February.
    cycle_start_day: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    #: Days after period end before the bill run executes.
    bill_run_offset_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    #: The cycle's own timezone, not the server's. A cycle boundary evaluated in
    #: the wrong zone moves usage between invoices.
    timezone: Mapped[str] = mapped_column(
        String(64), default="UTC", server_default="UTC", nullable=False
    )
    proration_profile_id: Mapped[str | None] = mapped_column(
        _fk("proration_profile", "id"), nullable=True, index=True
    )


class InvoiceComponent(Base, TenantMixin, ChargingMetaMixin):
    """A line-item class on an invoice, and where it posts in the ledger."""

    __tablename__ = "invoice_component"

    component_type: Mapped[str] = mapped_column(
        String(16),
        default=InvoiceComponentType.USAGE,
        server_default=InvoiceComponentType.USAGE.value,
        nullable=False,
        index=True,
    )
    gl_account: Mapped[str] = mapped_column(
        String(64), default="", server_default="", nullable=False
    )
    tax_rule_id: Mapped[str | None] = mapped_column(
        ForeignKey("tax_rules.id", ondelete="RESTRICT"), nullable=True
    )
    display_order: Mapped[int] = mapped_column(
        Integer, default=100, server_default="100", nullable=False
    )
    sign: Mapped[str] = mapped_column(
        String(8),
        default=ComponentSign.DEBIT,
        server_default=ComponentSign.DEBIT.value,
        nullable=False,
    )


class RecurringCharge(Base, TenantMixin, ChargingMetaMixin):
    """A rental. Referenced by ``ADD_RECURRING_CHARGE`` / ``MONTHLY_RENTAL``."""

    __tablename__ = "recurring_charge"

    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    offer_id: Mapped[str | None] = mapped_column(
        ForeignKey("offers.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(8), nullable=False)
    billing_cycle_id: Mapped[str | None] = mapped_column(
        _fk("billing_cycle", "id"), nullable=True, index=True
    )
    invoice_component_id: Mapped[str | None] = mapped_column(
        _fk("invoice_component", "id"), nullable=True, index=True
    )
    proration_profile_id: Mapped[str | None] = mapped_column(
        _fk("proration_profile", "id"), nullable=True, index=True
    )
    #: Billed at the start of the period it covers rather than the end.
    advance_flag: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)


class OneTimeCharge(Base, TenantMixin, ChargingMetaMixin):
    """A charge raised by an event rather than by a period."""

    __tablename__ = "one_time_charge"

    trigger_event: Mapped[str] = mapped_column(
        String(24),
        default=TriggerEvent.MANUAL,
        server_default=TriggerEvent.MANUAL.value,
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(8), nullable=False)
    invoice_component_id: Mapped[str | None] = mapped_column(
        _fk("invoice_component", "id"), nullable=True, index=True
    )
    refundable_flag: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )


class CreditLimitProfile(Base, TenantMixin, ChargingMetaMixin):
    """A postpaid spend ceiling and what happens when it is breached."""

    __tablename__ = "credit_limit_profile"

    limit_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(8), nullable=False)
    warning_threshold_pct: Mapped[int] = mapped_column(
        Integer, default=80, server_default="80", nullable=False
    )
    breach_action: Mapped[str] = mapped_column(
        String(16),
        default=CreditBreachAction.NOTIFY,
        server_default=CreditBreachAction.NOTIFY.value,
        nullable=False,
    )
    grace_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )


class UsageAggregationProfile(Base, TenantMixin, ChargingMetaMixin):
    """The window ``AGGREGATE_USAGE`` accumulates over.

    Not in the spec's table list, but ``AGGREGATE_USAGE`` is meaningless without
    it — "aggregate by what, over what period, resetting when".
    """

    __tablename__ = "usage_aggregation_profile"

    dimension: Mapped[str] = mapped_column(
        String(16),
        default=AggregationDimension.SUBSCRIBER,
        server_default=AggregationDimension.SUBSCRIBER.value,
        nullable=False,
    )
    window: Mapped[str] = mapped_column(
        String(16),
        default=AggregationWindow.CYCLE,
        server_default=AggregationWindow.CYCLE.value,
        nullable=False,
    )
    service_type: Mapped[str] = mapped_column(
        String(16), default="ANY", server_default="ANY", nullable=False, index=True
    )
    reset_policy: Mapped[str] = mapped_column(
        String(16), default="CYCLE", server_default="CYCLE", nullable=False
    )
    invoice_component_id: Mapped[str | None] = mapped_column(
        _fk("invoice_component", "id"), nullable=True, index=True
    )


class LateFeeProfile(Base, TenantMixin, ChargingMetaMixin):
    """Fee terms for ``ADD_LATE_FEE``.

    Both a flat amount and a percentage exist because operators charge either,
    and some charge "the greater of". Which applies is the action's business, not
    the profile's.
    """

    __tablename__ = "late_fee_profile"

    fee_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    fee_percentage: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    grace_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    #: 0 = unlimited. A late fee that compounds forever is a support ticket.
    max_occurrences: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    invoice_component_id: Mapped[str | None] = mapped_column(
        _fk("invoice_component", "id"), nullable=True, index=True
    )


#: Every entity this module defines, in dependency order. The migration and the
#: resolver both read it, so a new table cannot be added to one and forgotten in
#: the other.
CHARGING_MODELS: tuple[type[Base], ...] = (
    ChargingUnit,
    PulseProfile,
    ChargeLimitProfile,
    BalanceType,
    OcsProfile,
    ReservationPolicy,
    ProrationProfile,
    CreditLimitProfile,
    ChargingProfile,
    BalanceBucketDefinition,
    BalancePriority,
    BillingCycle,
    InvoiceComponent,
    RecurringCharge,
    OneTimeCharge,
    UsageAggregationProfile,
    LateFeeProfile,
)

__all__ = [
    "MONEY",
    "BalanceBucketDefinition",
    "BalancePriority",
    "BalanceType",
    "BillingCycle",
    "ChargeLimitProfile",
    "ChargingProfile",
    "ChargingUnit",
    "CreditLimitProfile",
    "InvoiceComponent",
    "LateFeeProfile",
    "OcsProfile",
    "OneTimeCharge",
    "ProrationProfile",
    "PulseProfile",
    "RecurringCharge",
    "ReservationPolicy",
    "UsageAggregationProfile",
]
