"""Canonical rating/billing source tables — the `canonical_rating` schema.

These tables are owned by the rating and billing platforms, not by case
management. They are mapped here **read-only** so a Billing Assurance
investigation can reconstruct what happened to one subscriber in one cycle.

Two consequences, both deliberate:

* they hang off their own `CanonicalBase`, so `Base.metadata.create_all()` can
  never create, alter or drop them — this service reads a schema it does not
  own and must not attempt to manage its DDL;
* the column names are the source system's, not ours. `offer_code`,
  `duration_sec`, `charge_variance` and the rest are spelled exactly as they
  are in the database so a query here can be pasted into psql unchanged.

Only Billing Assurance reads them. Every other assurance answers from the
mismatch rows its own control emitted — see `investigation_service`, which
refuses a non-Billing case before it queries.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings

CANONICAL_SCHEMA = settings.canonical_schema

# Every subscriber on this platform is invoiced on a bill cycle. The source
# tables do not carry a billing-type column, so it is fixed here and reported
# as-is rather than inferred per row.
BILLING_TYPE = "BILL_CYCLE"

JSONType = JSON().with_variant(JSONB, "postgresql")


class CanonicalBase(DeclarativeBase):
    """Separate metadata: nothing here is ever created by this service."""

    metadata = MetaData(schema=CANONICAL_SCHEMA)


class RatingReconciliation(CanonicalBase):
    """One rated event, with what rating charged against the recalculation.

    The table has no primary key of its own; `event_id` is unique per event and
    is mapped as the key so the ORM can identify a row. That is a mapping
    detail only — no DDL is emitted from it.
    """

    __tablename__ = "rating_reconciliation"

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    msisdn: Mapped[str] = mapped_column(String(30))

    # --- Subscriber / event identity ----------------------------------------
    account_type: Mapped[str | None] = mapped_column(String(20))
    offer_code: Mapped[str | None] = mapped_column(Text)
    service_type: Mapped[str | None] = mapped_column(String(30))
    called_number: Mapped[str | None] = mapped_column(String(40))
    matched_prefix: Mapped[str | None] = mapped_column(String)
    destination_zone: Mapped[str | None] = mapped_column(String)

    # --- The rule that should have applied -----------------------------------
    base_rule_id: Mapped[str | None] = mapped_column(Text)
    base_rule_name: Mapped[str | None] = mapped_column(Text)
    allowance_rule_id: Mapped[str | None] = mapped_column(Text)
    selected_discount_rules: Mapped[Any | None] = mapped_column(JSONType)
    selected_tax_rules: Mapped[Any | None] = mapped_column(JSONType)

    rate: Mapped[Decimal | None] = mapped_column(Numeric)
    duration_sec: Mapped[int | None] = mapped_column(BigInteger)
    volume_kb: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    bundle_remaining: Mapped[Decimal | None] = mapped_column(Numeric)
    original_quantity: Mapped[Decimal | None] = mapped_column(Numeric)
    free_quantity: Mapped[Decimal | None] = mapped_column(Numeric)
    chargeable_quantity: Mapped[Decimal | None] = mapped_column(Numeric)

    # --- Expected (assurance recalculation) ----------------------------------
    expected_base_charge: Mapped[Decimal | None] = mapped_column(Numeric)
    expected_discount: Mapped[Decimal | None] = mapped_column(Numeric)
    expected_surcharge: Mapped[Decimal | None] = mapped_column(Numeric)
    expected_before_tax: Mapped[Decimal | None] = mapped_column(Numeric)
    expected_tax: Mapped[Decimal | None] = mapped_column(Numeric)
    expected_final_charge: Mapped[Decimal | None] = mapped_column(Numeric)

    # --- Actual (network rating) ---------------------------------------------
    actual_charge: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    charge_variance: Mapped[Decimal | None] = mapped_column(Numeric)
    reconciliation_status: Mapped[str | None] = mapped_column(Text)

    expected_currency: Mapped[str | None] = mapped_column(String)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The rating engine's own working: base_rule, applied_rule, calculation,
    # root_cause and classification. It is the only place the *applied* (wrong)
    # rule is recorded, so the rating analysis reads it rather than guessing.
    explanation: Mapped[Any | None] = mapped_column(JSONType)


class BillingInvoice(CanonicalBase):
    """The invoice raised for the cycle.

    `case_id` carries the case reference with no separator (CASE2067), which is
    how the billing platform emits it; the service normalises CASE-2067 before
    matching.
    """

    __tablename__ = "billing_invoice"

    invoice_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    msisdn: Mapped[str | None] = mapped_column(String(20))
    case_id: Mapped[str | None] = mapped_column(String(50))

    usage_charge: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    total_invoice_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(10))

    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class MscVsPostMediation(CanonicalBase):
    """Event counts at the switch against post-mediation.

    Equal counts mean usage was captured and mediated intact, which is what
    clears the upstream stages and points an investigation downstream. `result`
    is constrained to MATCHED / UNMATCHED in the database.
    """

    __tablename__ = "msc_vs_post_mediation"

    reconciliation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    billing_date: Mapped[date] = mapped_column(Date)
    msisdn: Mapped[str] = mapped_column(String(20))

    msc_voice_count: Mapped[int | None] = mapped_column(Integer)
    msc_sms_count: Mapped[int | None] = mapped_column(Integer)
    msc_data_count: Mapped[int | None] = mapped_column(Integer)
    msc_total_usage_events: Mapped[int | None] = mapped_column(Integer)

    pm_voice_count: Mapped[int | None] = mapped_column(Integer)
    pm_sms_count: Mapped[int | None] = mapped_column(Integer)
    pm_data_count: Mapped[int | None] = mapped_column(Integer)
    pm_total_usage_events: Mapped[int | None] = mapped_column(Integer)

    result: Mapped[str | None] = mapped_column(String(20))
    remarks: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
