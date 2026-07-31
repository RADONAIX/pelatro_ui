"""Canonical metadata ORM models.

Every vendor's tariff configuration is normalised into these tables, so a rule
written against Oracle BRM data and one imported from Huawei CBS reference the
same product, zone and time band. Rules never point at vendor identifiers.

Shape shared by all of them (``CatalogMixin``): a surrogate uuid PK plus a
human-meaningful unique ``code``. Rules and imports join on ``code`` — it is
stable across environments, whereas a uuid regenerates on every seed.
"""

from __future__ import annotations

import uuid
from datetime import date, time

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin
from app.modules.catalog.constants import CatalogStatus


def _uuid() -> str:
    return str(uuid.uuid4())


class CatalogMixin(TimestampMixin):
    """Common columns for every canonical metadata entity."""

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        default=CatalogStatus.ACTIVE,
        server_default=CatalogStatus.ACTIVE.value,
        nullable=False,
        index=True,
    )
    #: Which upstream system this record came from ("MANUAL" when hand-created).
    source_system: Mapped[str] = mapped_column(
        String(64), default="MANUAL", server_default="MANUAL", nullable=False
    )
    #: Vendor-specific extras that have no canonical column yet. Keeping them
    #: preserves round-trip fidelity for connector imports (Phase 5).
    attributes: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class EffectiveDatedMixin:
    """Validity window. ``effective_to = NULL`` means open-ended."""

    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)


# --- Currency / rounding / tax ---------------------------------------------


class Currency(Base, CatalogMixin):
    __tablename__ = "currencies"

    symbol: Mapped[str] = mapped_column(String(8), default="", server_default="", nullable=False)
    #: Minor-unit digits (2 for GBP/EUR, 0 for JPY). Drives rounding and display.
    decimals: Mapped[int] = mapped_column(Integer, default=2, server_default="2", nullable=False)


class RoundingRule(Base, CatalogMixin):
    __tablename__ = "rounding_rules"

    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    decimals: Mapped[int] = mapped_column(Integer, default=2, server_default="2", nullable=False)


class TaxRule(Base, CatalogMixin, EffectiveDatedMixin):
    __tablename__ = "tax_rules"

    tax_type: Mapped[str] = mapped_column(String(16), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(
        String(64), default="", server_default="", nullable=False, index=True
    )
    #: Percentage, e.g. 15.0000 for 15%. Numeric (not float) — money maths.
    rate_percent: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False)
    #: True when the actual charge already includes this tax (tax-inclusive
    #: pricing), which changes the expected-vs-actual comparison in Phase 4.
    inclusive: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )


# --- Service / product / offer / tariff ------------------------------------


class ServiceDefinition(Base, CatalogMixin):
    __tablename__ = "services"

    service_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: The unit usage is measured in before any pulse is applied.
    usage_unit: Mapped[str] = mapped_column(String(16), nullable=False)


class Product(Base, CatalogMixin, EffectiveDatedMixin):
    __tablename__ = "products"

    product_type: Mapped[str] = mapped_column(
        String(32), default="TARIFF", server_default="TARIFF", nullable=False
    )
    account_type: Mapped[str] = mapped_column(
        String(16), default="ANY", server_default="ANY", nullable=False, index=True
    )
    #: Service types this product covers, e.g. ["VOICE", "SMS"].
    service_types: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    currency_code: Mapped[str | None] = mapped_column(String(8), nullable=True)

    offers: Mapped[list[Offer]] = relationship(back_populates="product")


class Offer(Base, CatalogMixin, EffectiveDatedMixin):
    __tablename__ = "offers"

    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    #: An exclusive offer cannot stack with another exclusive one on the same
    #: CDR — enforced by the conflict validator in Phase 2.
    exclusive: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    product: Mapped[Product] = relationship(back_populates="offers")


class TariffPlan(Base, CatalogMixin, EffectiveDatedMixin):
    __tablename__ = "tariff_plans"

    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    offer_id: Mapped[str | None] = mapped_column(
        ForeignKey("offers.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    service_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    currency_code: Mapped[str] = mapped_column(String(8), nullable=False)


# --- Rating dimensions ------------------------------------------------------


class TimeBand(Base, CatalogMixin):
    __tablename__ = "time_bands"

    #: Weekday tokens the band applies on, e.g. ["MON","TUE","WED","THU","FRI"].
    days: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    #: Exclusive. A band that wraps midnight (22:00→06:00) has end < start; the
    #: enrichment step in Phase 3 handles the wrap rather than splitting rows.
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64), default="UTC", server_default="UTC", nullable=False
    )
    #: Higher wins when two bands overlap (e.g. a public-holiday band over PEAK).
    priority: Mapped[int] = mapped_column(
        Integer, default=100, server_default="100", nullable=False
    )


class DestinationZone(Base, CatalogMixin):
    __tablename__ = "destination_zones"

    zone_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    country_code: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)

    prefixes: Mapped[list[DestinationPrefix]] = relationship(
        back_populates="zone", cascade="all, delete-orphan"
    )


class DestinationPrefix(Base, TimestampMixin):
    """A dialled-number prefix mapped to a zone.

    Longest-prefix wins at enrichment time, so overlapping entries such as
    ``44`` (UK) and ``447`` (UK mobile) coexist by design.
    """

    __tablename__ = "destination_prefixes"
    __table_args__ = (UniqueConstraint("prefix", name="uq_destination_prefixes_prefix"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    zone_id: Mapped[str] = mapped_column(
        ForeignKey("destination_zones.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prefix: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    description: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )

    zone: Mapped[DestinationZone] = relationship(back_populates="prefixes")


class RatingGroup(Base, CatalogMixin):
    __tablename__ = "rating_groups"

    service_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)


# --- Charging modifiers (referenced by rule actions) ------------------------


class DiscountDefinition(Base, CatalogMixin, EffectiveDatedMixin):
    __tablename__ = "discount_definitions"

    discount_type: Mapped[str] = mapped_column(String(24), nullable=False)
    #: Meaning depends on discount_type: percent, absolute amount, or unit count.
    value: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    currency_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    #: Applied before tax by default; a few jurisdictions require the reverse.
    pre_tax: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )


class BundleDefinition(Base, CatalogMixin, EffectiveDatedMixin):
    __tablename__ = "bundle_definitions"

    service_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    quota_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    quota_value: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    reset_period: Mapped[str] = mapped_column(
        String(16), default="MONTHLY", server_default="MONTHLY", nullable=False
    )
    #: Shared bundles are consumed across an account's subscribers, which forces
    #: the stateful rating path (Phase 6) to partition by account, not subscriber.
    shared: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )


class Promotion(Base, CatalogMixin, EffectiveDatedMixin):
    __tablename__ = "promotions"

    promotion_type: Mapped[str] = mapped_column(String(24), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    currency_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    exclusive: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
