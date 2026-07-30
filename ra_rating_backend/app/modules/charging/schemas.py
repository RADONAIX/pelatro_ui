"""Pydantic schemas for the charging / prepaid / postpaid metadata catalogue.

Same construction as ``catalog.schemas``: a Create model per entity, a Read model
per entity, and an Update derived mechanically by ``partial_model``. Reusing the
existing bases is what lets these seventeen entities be served by the catalogue
router that already exists, rather than by a second router that would drift.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field, model_validator

from app.modules.catalog.constants import RoundingMode, ServiceType
from app.modules.catalog.schemas import (
    CatalogCreateBase,
    CatalogReadBase,
    partial_model,
)
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
    UnitDimension,
)

# --- Charging units ---------------------------------------------------------


class ChargingUnitCreate(CatalogCreateBase):
    dimension: UnitDimension
    base_unit: str = Field(min_length=1, max_length=16)
    #: Multiplier to the dimension's base unit — 60 for MINUTE, 1048576 for MEGABYTE.
    factor: Decimal = Field(default=Decimal(1), gt=0)
    decimals: int = Field(default=6, ge=0, le=6)


class ChargingUnitRead(CatalogReadBase):
    dimension: str
    base_unit: str
    factor: Decimal
    decimals: int


ChargingUnitUpdate = partial_model("ChargingUnitUpdate", ChargingUnitCreate)


# --- Pulse profiles ---------------------------------------------------------


class PulseProfileCreate(CatalogCreateBase):
    service_type: ServiceType
    initial_seconds: int = Field(ge=1)
    subsequent_seconds: int = Field(ge=1)
    round_mode: PulseRoundMode = PulseRoundMode.UP
    min_chargeable_seconds: int = Field(default=0, ge=0)
    unit_code: str | None = None


class PulseProfileRead(CatalogReadBase):
    service_type: str
    initial_seconds: int
    subsequent_seconds: int
    round_mode: str
    min_chargeable_seconds: int
    unit_code: str | None


PulseProfileUpdate = partial_model("PulseProfileUpdate", PulseProfileCreate)


# --- Min/max profiles -------------------------------------------------------


class ChargeLimitProfileCreate(CatalogCreateBase):
    service_type: ServiceType = ServiceType.ANY
    min_charge: Decimal | None = Field(default=None, ge=0)
    max_charge: Decimal | None = Field(default=None, ge=0)
    min_quantity: Decimal | None = Field(default=None, ge=0)
    max_quantity: Decimal | None = Field(default=None, ge=0)
    currency_code: str | None = None
    unit_code: str | None = None

    @model_validator(mode="after")
    def _bounds_ordered(self):
        """A maximum below its minimum silently zeroes every charge it touches,
        and does it without an error anyone would see."""
        for lo, hi, label in (
            (self.min_charge, self.max_charge, "charge"),
            (self.min_quantity, self.max_quantity, "quantity"),
        ):
            if lo is not None and hi is not None and hi < lo:
                raise ValueError(
                    f"max_{label} ({hi}) is below min_{label} ({lo}); no value can "
                    "satisfy both."
                )
        if self.min_charge is not None and not self.currency_code:
            raise ValueError("A charge bound needs a currency.")
        if self.max_charge is not None and not self.currency_code:
            raise ValueError("A charge bound needs a currency.")
        return self


class ChargeLimitProfileRead(CatalogReadBase):
    service_type: str
    min_charge: Decimal | None
    max_charge: Decimal | None
    min_quantity: Decimal | None
    max_quantity: Decimal | None
    currency_code: str | None
    unit_code: str | None


ChargeLimitProfileUpdate = partial_model(
    "ChargeLimitProfileUpdate", ChargeLimitProfileCreate
)


# --- OCS profiles -----------------------------------------------------------


class OcsProfileCreate(CatalogCreateBase):
    vendor: str = ""
    reservation_strategy: ReservationStrategy = ReservationStrategy.QUOTA
    quota_unit: str | None = None
    initial_quota: Decimal | None = Field(default=None, ge=0)
    subsequent_quota: Decimal | None = Field(default=None, ge=0)
    quota_validity_seconds: int | None = Field(default=None, ge=1)
    redirect_on_exhaust: str | None = None
    connection: dict = Field(
        default_factory=dict,
        description="Endpoint, realm, timeouts. Never credentials.",
    )


class OcsProfileRead(CatalogReadBase):
    vendor: str
    reservation_strategy: str
    quota_unit: str | None
    initial_quota: Decimal | None
    subsequent_quota: Decimal | None
    quota_validity_seconds: int | None
    redirect_on_exhaust: str | None
    connection: dict


OcsProfileUpdate = partial_model("OcsProfileUpdate", OcsProfileCreate)


# --- Reservation policies ---------------------------------------------------


class ReservationPolicyCreate(CatalogCreateBase):
    ocs_profile_id: str | None = None
    initial_quota: Decimal | None = Field(default=None, ge=0)
    subsequent_quota: Decimal | None = Field(default=None, ge=0)
    unit_code: str | None = None
    validity_seconds: int | None = Field(default=None, ge=1)
    threshold_pct: int | None = Field(default=None, ge=1, le=100)
    release_mode: ReleaseMode = ReleaseMode.UNUSED
    on_timeout: ReservationTimeout = ReservationTimeout.RELEASE


class ReservationPolicyRead(CatalogReadBase):
    ocs_profile_id: str | None
    initial_quota: Decimal | None
    subsequent_quota: Decimal | None
    unit_code: str | None
    validity_seconds: int | None
    threshold_pct: int | None
    release_mode: str
    on_timeout: str


ReservationPolicyUpdate = partial_model(
    "ReservationPolicyUpdate", ReservationPolicyCreate
)


# --- Balance types / buckets ------------------------------------------------


class BalanceTypeCreate(CatalogCreateBase):
    category: BalanceCategory = BalanceCategory.MAIN
    unit_code: str | None = None
    is_monetary: bool = True
    allows_negative: bool = False
    expiry_policy: ExpiryPolicy = ExpiryPolicy.NONE


class BalanceTypeRead(CatalogReadBase):
    category: str
    unit_code: str | None
    is_monetary: bool
    allows_negative: bool
    expiry_policy: str


BalanceTypeUpdate = partial_model("BalanceTypeUpdate", BalanceTypeCreate)


class BalanceBucketDefinitionCreate(CatalogCreateBase):
    balance_type_id: str
    unit_code: str | None = None
    initial_amount: Decimal | None = Field(default=None, ge=0)
    currency_code: str | None = None
    validity_days: int | None = Field(default=None, ge=1)
    carry_over_flag: bool = False
    shared_flag: bool = False


class BalanceBucketDefinitionRead(CatalogReadBase):
    balance_type_id: str
    unit_code: str | None
    initial_amount: Decimal | None
    currency_code: str | None
    validity_days: int | None
    carry_over_flag: bool
    shared_flag: bool


BalanceBucketDefinitionUpdate = partial_model(
    "BalanceBucketDefinitionUpdate", BalanceBucketDefinitionCreate
)


# --- Charging profiles / balance priorities ---------------------------------


class ChargingProfileCreate(CatalogCreateBase):
    charging_mode: str = "PREPAID"
    ocs_profile_id: str | None = None
    reservation_policy_id: str | None = None
    credit_limit_profile_id: str | None = None
    default_currency_code: str | None = None
    rounding_rule_id: str | None = None
    negative_balance_allowed: bool = False


class ChargingProfileRead(CatalogReadBase):
    charging_mode: str
    ocs_profile_id: str | None
    reservation_policy_id: str | None
    credit_limit_profile_id: str | None
    default_currency_code: str | None
    rounding_rule_id: str | None
    negative_balance_allowed: bool


ChargingProfileUpdate = partial_model("ChargingProfileUpdate", ChargingProfileCreate)


class BalancePriorityCreate(CatalogCreateBase):
    charging_profile_id: str
    balance_type_id: str
    service_type: ServiceType = ServiceType.ANY
    #: Lower is consumed first.
    consumption_order: int = Field(default=100, ge=0, le=32767)


class BalancePriorityRead(CatalogReadBase):
    charging_profile_id: str
    balance_type_id: str
    service_type: str
    consumption_order: int


BalancePriorityUpdate = partial_model("BalancePriorityUpdate", BalancePriorityCreate)


# --- Proration / billing cycles ---------------------------------------------


class ProrationProfileCreate(CatalogCreateBase):
    method: ProrationMethod = ProrationMethod.DAILY
    round_mode: RoundingMode = RoundingMode.HALF_UP
    apply_on_activation: bool = True
    apply_on_cease: bool = True
    apply_on_plan_change: bool = True


class ProrationProfileRead(CatalogReadBase):
    method: str
    round_mode: str
    apply_on_activation: bool
    apply_on_cease: bool
    apply_on_plan_change: bool


ProrationProfileUpdate = partial_model("ProrationProfileUpdate", ProrationProfileCreate)


class BillingCycleCreate(CatalogCreateBase):
    frequency: BillingFrequency = BillingFrequency.MONTHLY
    #: Capped at 28 — a cycle that opens on the 31st does not exist in February,
    #: and every workaround for that is a source of billing disputes.
    cycle_start_day: int = Field(default=1, ge=1, le=28)
    bill_run_offset_days: int = Field(default=0, ge=0, le=60)
    timezone: str = "UTC"
    proration_profile_id: str | None = None


class BillingCycleRead(CatalogReadBase):
    frequency: str
    cycle_start_day: int
    bill_run_offset_days: int
    timezone: str
    proration_profile_id: str | None


BillingCycleUpdate = partial_model("BillingCycleUpdate", BillingCycleCreate)


# --- Invoice components and charges -----------------------------------------


class InvoiceComponentCreate(CatalogCreateBase):
    component_type: InvoiceComponentType = InvoiceComponentType.USAGE
    gl_account: str = ""
    tax_rule_id: str | None = None
    display_order: int = Field(default=100, ge=0)
    sign: ComponentSign = ComponentSign.DEBIT


class InvoiceComponentRead(CatalogReadBase):
    component_type: str
    gl_account: str
    tax_rule_id: str | None
    display_order: int
    sign: str


InvoiceComponentUpdate = partial_model("InvoiceComponentUpdate", InvoiceComponentCreate)


class RecurringChargeCreate(CatalogCreateBase):
    product_id: str | None = None
    offer_id: str | None = None
    amount: Decimal = Field(ge=0)
    currency_code: str = Field(min_length=3, max_length=8)
    billing_cycle_id: str | None = None
    invoice_component_id: str | None = None
    proration_profile_id: str | None = None
    advance_flag: bool = True
    effective_from: date | None = None
    effective_to: date | None = None

    @model_validator(mode="after")
    def _window_ordered(self):
        if (
            self.effective_from
            and self.effective_to
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to is before effective_from.")
        return self


class RecurringChargeRead(CatalogReadBase):
    product_id: str | None
    offer_id: str | None
    amount: Decimal
    currency_code: str
    billing_cycle_id: str | None
    invoice_component_id: str | None
    proration_profile_id: str | None
    advance_flag: bool
    effective_from: date | None
    effective_to: date | None


RecurringChargeUpdate = partial_model("RecurringChargeUpdate", RecurringChargeCreate)


class OneTimeChargeCreate(CatalogCreateBase):
    trigger_event: TriggerEvent = TriggerEvent.MANUAL
    amount: Decimal = Field(ge=0)
    currency_code: str = Field(min_length=3, max_length=8)
    invoice_component_id: str | None = None
    refundable_flag: bool = False


class OneTimeChargeRead(CatalogReadBase):
    trigger_event: str
    amount: Decimal
    currency_code: str
    invoice_component_id: str | None
    refundable_flag: bool


OneTimeChargeUpdate = partial_model("OneTimeChargeUpdate", OneTimeChargeCreate)


# --- Credit, aggregation, late fees -----------------------------------------


class CreditLimitProfileCreate(CatalogCreateBase):
    limit_amount: Decimal = Field(ge=0)
    currency_code: str = Field(min_length=3, max_length=8)
    warning_threshold_pct: int = Field(default=80, ge=1, le=100)
    breach_action: CreditBreachAction = CreditBreachAction.NOTIFY
    grace_days: int = Field(default=0, ge=0, le=365)


class CreditLimitProfileRead(CatalogReadBase):
    limit_amount: Decimal
    currency_code: str
    warning_threshold_pct: int
    breach_action: str
    grace_days: int


CreditLimitProfileUpdate = partial_model(
    "CreditLimitProfileUpdate", CreditLimitProfileCreate
)


class UsageAggregationProfileCreate(CatalogCreateBase):
    dimension: AggregationDimension = AggregationDimension.SUBSCRIBER
    window: AggregationWindow = AggregationWindow.CYCLE
    service_type: ServiceType = ServiceType.ANY
    reset_policy: str = "CYCLE"
    invoice_component_id: str | None = None


class UsageAggregationProfileRead(CatalogReadBase):
    dimension: str
    window: str
    service_type: str
    reset_policy: str
    invoice_component_id: str | None


UsageAggregationProfileUpdate = partial_model(
    "UsageAggregationProfileUpdate", UsageAggregationProfileCreate
)


class LateFeeProfileCreate(CatalogCreateBase):
    fee_amount: Decimal | None = Field(default=None, ge=0)
    fee_percentage: Decimal | None = Field(default=None, ge=0, le=100)
    currency_code: str | None = None
    grace_days: int = Field(default=0, ge=0, le=365)
    #: 0 = unlimited.
    max_occurrences: int = Field(default=0, ge=0)
    invoice_component_id: str | None = None

    @model_validator(mode="after")
    def _has_terms(self):
        if self.fee_amount is None and self.fee_percentage is None:
            raise ValueError(
                "A late fee profile needs an amount, a percentage, or both — "
                "otherwise it charges nothing and looks configured."
            )
        if self.fee_amount is not None and not self.currency_code:
            raise ValueError("A flat late fee needs a currency.")
        return self


class LateFeeProfileRead(CatalogReadBase):
    fee_amount: Decimal | None
    fee_percentage: Decimal | None
    currency_code: str | None
    grace_days: int
    max_occurrences: int
    invoice_component_id: str | None


LateFeeProfileUpdate = partial_model("LateFeeProfileUpdate", LateFeeProfileCreate)
