"""Pydantic schemas for the canonical metadata API.

``Update`` models are derived from their ``Create`` counterpart by
``partial_model``: every field becomes optional so PATCH semantics fall out
without a second hand-maintained class per entity (14 entities x 2 would drift
within a sprint).
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator

from app.modules.catalog.constants import (
    AccountType,
    CatalogStatus,
    DiscountType,
    ResetPeriod,
    RoundingMode,
    ServiceType,
    TaxType,
    UsageUnit,
    Weekday,
    ZoneType,
)

CODE_PATTERN = r"^[A-Z0-9][A-Z0-9_.-]{0,63}$"


def partial_model(name: str, base: type[BaseModel]) -> type[BaseModel]:
    """Derive an all-optional variant of ``base`` for PATCH bodies."""
    fields: dict[str, Any] = {
        field_name: (info.annotation | None, None)
        for field_name, info in base.model_fields.items()
    }
    return create_model(name, __base__=BaseModel, **fields)  # type: ignore[call-overload]


class CatalogBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CatalogCreateBase(CatalogBase):
    code: str = Field(pattern=CODE_PATTERN, description="Stable, environment-independent key")
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    status: CatalogStatus = CatalogStatus.ACTIVE
    source_system: str = "MANUAL"
    attributes: dict[str, Any] = Field(default_factory=dict)


class CatalogReadBase(CatalogBase):
    id: str
    code: str
    name: str
    description: str
    status: str
    source_system: str
    attributes: dict[str, Any]
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class EffectiveDated(CatalogBase):
    effective_from: date | None = None
    effective_to: date | None = None


# --- Currency ---------------------------------------------------------------


class CurrencyCreate(CatalogCreateBase):
    symbol: str = ""
    decimals: int = Field(default=2, ge=0, le=6)


class CurrencyRead(CatalogReadBase):
    symbol: str
    decimals: int


CurrencyUpdate = partial_model("CurrencyUpdate", CurrencyCreate)


# --- Rounding ---------------------------------------------------------------


class RoundingRuleCreate(CatalogCreateBase):
    mode: RoundingMode
    decimals: int = Field(default=2, ge=0, le=6)


class RoundingRuleRead(CatalogReadBase):
    mode: str
    decimals: int


RoundingRuleUpdate = partial_model("RoundingRuleUpdate", RoundingRuleCreate)


# --- Tax --------------------------------------------------------------------


class TaxRuleCreate(CatalogCreateBase, EffectiveDated):
    tax_type: TaxType
    jurisdiction: str = ""
    rate_percent: Decimal = Field(ge=0, le=100)
    inclusive: bool = False


class TaxRuleRead(CatalogReadBase):
    tax_type: str
    jurisdiction: str
    rate_percent: Decimal
    inclusive: bool
    effective_from: date | None
    effective_to: date | None


TaxRuleUpdate = partial_model("TaxRuleUpdate", TaxRuleCreate)


# --- Service ----------------------------------------------------------------


class ServiceCreate(CatalogCreateBase):
    service_type: ServiceType
    usage_unit: UsageUnit


class ServiceRead(CatalogReadBase):
    service_type: str
    usage_unit: str


ServiceUpdate = partial_model("ServiceUpdate", ServiceCreate)


# --- Product ----------------------------------------------------------------


class ProductCreate(CatalogCreateBase, EffectiveDated):
    product_type: str = "TARIFF"
    account_type: AccountType = AccountType.ANY
    service_types: list[ServiceType] = Field(default_factory=list)
    currency_code: str | None = None


class ProductRead(CatalogReadBase):
    product_type: str
    account_type: str
    service_types: list[str]
    currency_code: str | None
    effective_from: date | None
    effective_to: date | None


ProductUpdate = partial_model("ProductUpdate", ProductCreate)


# --- Offer ------------------------------------------------------------------


class OfferCreate(CatalogCreateBase, EffectiveDated):
    product_id: str
    exclusive: bool = False


class OfferRead(CatalogReadBase):
    product_id: str
    exclusive: bool
    effective_from: date | None
    effective_to: date | None


OfferUpdate = partial_model("OfferUpdate", OfferCreate)


# --- Tariff plan ------------------------------------------------------------


class TariffPlanCreate(CatalogCreateBase, EffectiveDated):
    product_id: str | None = None
    offer_id: str | None = None
    service_type: ServiceType
    currency_code: str


class TariffPlanRead(CatalogReadBase):
    product_id: str | None
    offer_id: str | None
    service_type: str
    currency_code: str
    effective_from: date | None
    effective_to: date | None


TariffPlanUpdate = partial_model("TariffPlanUpdate", TariffPlanCreate)


# --- Time band --------------------------------------------------------------


class TimeBandCreate(CatalogCreateBase):
    days: list[Weekday] = Field(default_factory=lambda: list(Weekday))
    start_time: time
    end_time: time
    timezone: str = "UTC"
    priority: int = Field(default=100, ge=0, le=10_000)

    @field_validator("days")
    @classmethod
    def _at_least_one_day(cls, v: list[Weekday]) -> list[Weekday]:
        # Membership is enforced by the enum; this catches the empty list, which
        # would silently produce a band that never matches.
        if not v:
            raise ValueError("A time band must apply on at least one day.")
        return v


class TimeBandRead(CatalogReadBase):
    days: list[str]
    start_time: time
    end_time: time
    timezone: str
    priority: int


TimeBandUpdate = partial_model("TimeBandUpdate", TimeBandCreate)


# --- Destination zone / prefix ----------------------------------------------


class DestinationZoneCreate(CatalogCreateBase):
    zone_type: ZoneType
    country_code: str | None = None


class DestinationZoneRead(CatalogReadBase):
    zone_type: str
    country_code: str | None
    prefix_count: int = 0


DestinationZoneUpdate = partial_model("DestinationZoneUpdate", DestinationZoneCreate)


class DestinationPrefixCreate(CatalogBase):
    zone_id: str
    prefix: str = Field(pattern=r"^[0-9+*#]{1,32}$")
    description: str = ""


class DestinationPrefixRead(CatalogBase):
    id: str
    zone_id: str
    prefix: str
    description: str
    created_at: datetime
    updated_at: datetime


# --- Rating group -----------------------------------------------------------


class RatingGroupCreate(CatalogCreateBase):
    service_type: ServiceType


class RatingGroupRead(CatalogReadBase):
    service_type: str


RatingGroupUpdate = partial_model("RatingGroupUpdate", RatingGroupCreate)


# --- Discount ---------------------------------------------------------------


class DiscountCreate(CatalogCreateBase, EffectiveDated):
    discount_type: DiscountType
    value: Decimal
    currency_code: str | None = None
    pre_tax: bool = True


class DiscountRead(CatalogReadBase):
    discount_type: str
    value: Decimal
    currency_code: str | None
    pre_tax: bool
    effective_from: date | None
    effective_to: date | None


DiscountUpdate = partial_model("DiscountUpdate", DiscountCreate)


# --- Bundle -----------------------------------------------------------------


class BundleCreate(CatalogCreateBase, EffectiveDated):
    service_type: ServiceType
    quota_unit: UsageUnit
    quota_value: Decimal = Field(gt=0)
    reset_period: ResetPeriod = ResetPeriod.MONTHLY
    shared: bool = False


class BundleRead(CatalogReadBase):
    service_type: str
    quota_unit: str
    quota_value: Decimal
    reset_period: str
    shared: bool
    effective_from: date | None
    effective_to: date | None


BundleUpdate = partial_model("BundleUpdate", BundleCreate)


# --- Promotion --------------------------------------------------------------


class PromotionCreate(CatalogCreateBase, EffectiveDated):
    promotion_type: str
    value: Decimal
    currency_code: str | None = None
    exclusive: bool = True


class PromotionRead(CatalogReadBase):
    promotion_type: str
    value: Decimal
    currency_code: str | None
    exclusive: bool
    effective_from: date | None
    effective_to: date | None


PromotionUpdate = partial_model("PromotionUpdate", PromotionCreate)


# --- Envelopes --------------------------------------------------------------


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class CatalogSummary(BaseModel):
    """Counts per entity — powers the Metadata Catalogue tab badges."""

    entity: str
    label: str
    total: int
    active: int
    #: Which section of the Metadata Catalogue screen this belongs under
    #: (Commercial / Charging / Prepaid / Postpaid / Shared). Additive: a client
    #: that predates the charging catalogues can ignore it.
    group: str = "Shared"


class FieldSchema(BaseModel):
    """One editable field, described well enough for the UI to render a control."""

    key: str
    label: str
    #: STRING | NUMBER | BOOLEAN | ENUM | REFERENCE | DATE | TIME
    data_type: str
    required: bool
    #: Allowed values for ENUM.
    values: list[str] = Field(default_factory=list)
    #: Catalogue slug supplying the options for REFERENCE.
    reference: str | None = None
    #: Rendered as a multi-select (e.g. a product's service types).
    multiple: bool = False
    default: Any = None
    help: str = ""


class EntitySchema(BaseModel):
    slug: str
    label: str
    fields: list[FieldSchema]
    group: str = "Shared"
