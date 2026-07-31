"""CDR storage.

**Where this runs.** The requirement puts these tables in ClickHouse, and at
1 crore rows per cycle that is right. They are defined here against Postgres
because every statement that touches them is written as *bulk set-based SQL*
over whole batches — never a Python loop over rows — so the same shape ports to
ClickHouse by swapping the engine, not by rewriting the pipeline. Running on
Postgres today means the whole pipeline is executable and testable now; see
``app/modules/cdr/store.py`` for the seam.

**Two tables, not three.** The requirement names landing, normalized and
enriched. Landing is kept separate because it is the audit copy — the bytes as
received. Normalization and enrichment both produce the same row shape, so they
share ``cdr_enriched``: enrichment updates columns on the row normalization
created. Splitting them would double the I/O for no analytical benefit.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class CdrBatch(Base, TimestampMixin):
    """One ingested file or API push."""

    __tablename__ = "cdr_batches"
    __table_args__ = (
        # Re-ingesting the same file is the single most common operational
        # mistake; the constraint makes it impossible rather than merely
        # detectable after the fact.
        UniqueConstraint("source_system", "filename", name="uq_cdr_batches_source_system"),
        Index("ix_cdr_batches_event_date", "event_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: MSC / SMS / GGSN / IMS / TAP / OCS / DIGITAL.
    cdr_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="RECEIVED", server_default="RECEIVED", nullable=False, index=True
    )

    total_records: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    loaded_records: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    duplicate_records: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    rejected_records: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    normalized_records: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    enriched_records: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    #: Sum of the actual charges as received — reconciles against the source
    #: system's own control total.
    control_total: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    records: Mapped[list[CdrLanding]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class CdrLanding(Base):
    """The record as received. Never modified — this is the audit copy."""

    __tablename__ = "cdr_landing"
    __table_args__ = (
        # Duplicate detection. A source system replaying a file must not double
        # count revenue, and the hash makes that a constraint rather than a job.
        UniqueConstraint("record_hash", name="uq_cdr_landing_record_hash"),
        Index("ix_cdr_landing_batch_status", "batch_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("cdr_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: SHA-256 over the source identity fields, not the whole row: a resend with
    #: a corrected charge is the same event, and must not land twice.
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    batch: Mapped[CdrBatch] = relationship(back_populates="records")


class CdrEnriched(Base):
    """The common usage model (§9) plus its enrichment dimensions (§10).

    ``context_hash`` is the whole point of the table: it collapses 1 crore rows
    into the ~100k distinct rating situations that actually need a rule decision.
    """

    __tablename__ = "cdr_enriched"
    __table_args__ = (
        UniqueConstraint("landing_id", name="uq_cdr_enriched_landing_id"),
        # The deterministic source identity (§6). Unique, so re-reading the same
        # source row can only ever update the row it produced the first time —
        # this constraint is what makes the whole pipeline idempotent, and no
        # amount of application-level checking substitutes for it.
        UniqueConstraint("usage_id", name="uq_cdr_enriched_usage_id"),
        # The pipeline's two hot paths: group by context within a batch, then
        # join results back to every CDR in it.
        Index("ix_cdr_enriched_batch_context", "batch_id", "context_hash"),
        Index("ix_cdr_enriched_quality", "batch_id", "quality_status"),
        Index("ix_cdr_enriched_event_date", "event_date"),
        Index("ix_cdr_enriched_subscriber", "subscriber_id"),
        # Business-duplicate detection (§7): the same call re-exported under a
        # different file name is a new usage_id and the same hash.
        Index("ix_cdr_enriched_duplicate_hash", "duplicate_hash"),
        # The execution service's driving query: "unrated usage, oldest first".
        Index("ix_cdr_enriched_processing", "processing_status", "event_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("cdr_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    landing_id: Mapped[str] = mapped_column(String(36), nullable=False)

    # --- Source identity (§6, §7) -------------------------------------------
    #: Derived from source_system + source_file + source_record_number, never
    #: generated. Nullable only because rows ingested before the MSC source
    #: existed have no source record number to derive one from.
    usage_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_record_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: SHA-256 over the business identity of the event, independent of which
    #: file carried it.
    duplicate_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- Standard usage model (§9) -----------------------------------------
    cdr_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subscriber_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    msisdn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    imsi: Mapped[str | None] = mapped_column(String(32), nullable=True)
    service_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    event_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(18, 3), nullable=True)
    usage_volume: Mapped[float | None] = mapped_column(Numeric(20, 3), nullable=True)
    calling_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    called_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: What the billing system actually charged — the other half of assurance.
    actual_charge: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    actual_discount: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    actual_tax: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- Enrichment dimensions (§10) ---------------------------------------
    product_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    offer_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tariff_plan_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    destination_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    on_net: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    time_band: Mapped[str | None] = mapped_column(String(64), nullable=True)
    roaming: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    network_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rating_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    visited_operator: Mapped[str | None] = mapped_column(String(32), nullable=True)
    apn: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- Rating context ------------------------------------------------------
    #: Readable form, e.g. VOICE|PREPAID_A|LOCAL_ONNET|PEAK|PREPAID|NO.
    context_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: SHA-1 of the key — fixed width, so it indexes and groups efficiently.
    context_hash: Mapped[str | None] = mapped_column(String(40), nullable=True)

    #: OK, or the first data-quality problem found (§10).
    quality_status: Mapped[str] = mapped_column(
        String(32), default="OK", server_default="OK", nullable=False
    )
    quality_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Voice/SMS event detail carried by an MSC record --------------------
    #: MO / MT / FORWARDED. Distinct from service_type: an incoming call and an
    #: outgoing call are the same service priced completely differently.
    call_direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    event_end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    charged_party: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cell_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    location_area_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The switch's own correlation handle — the strongest key available when
    #: matching this event to an OCS charge (§21).
    call_reference: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    serving_network: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # --- Enrichment dimensions the rating context needs (§8, §11) -----------
    subscriber_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    customer_segment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Every group the subscriber belonged to at the time of the event.
    #:
    #: A LIST on the single usage row — never a join that multiplies it. One CDR
    #: for a subscriber in six groups is one rating result, not six: fanning it
    #: out would sextuple both the expected revenue and the reported leakage,
    #: and the error would look like a pricing problem rather than a modelling
    #: one. This column is why the pipeline cannot make that mistake (§2, §9).
    subscriber_groups: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), default=list, server_default="{}", nullable=False
    )
    #: ON_NET / OFF_NET / INTERNATIONAL — who terminates the call.
    network_relation: Mapped[str | None] = mapped_column(String(24), nullable=True)
    destination_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: WEEKDAY / WEEKEND / HOLIDAY, from the calendar, not from the weekday
    #: number: a public holiday priced as a Tuesday is a systematic variance.
    day_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    bundle_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), default=list, server_default="{}", nullable=False
    )
    offer_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), default=list, server_default="{}", nullable=False
    )

    #: ENRICHED / PARTIALLY_ENRICHED / SUBSCRIBER_NOT_FOUND / ... (§10).
    #: Separate from quality_status, which describes the *record*; this
    #: describes how well we could place it in a rating situation.
    enrichment_status: Mapped[str] = mapped_column(
        String(32), default="ENRICHED", server_default="ENRICHED", nullable=False
    )
    enrichment_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: PENDING / RATED / DUPLICATE / EXCEPTION — drives the execution service's
    #: "what is left to do" query, so a re-run picks up where it stopped.
    processing_status: Mapped[str] = mapped_column(
        String(16), default="PENDING", server_default="PENDING", nullable=False
    )

    #: Vendor fields with no canonical column, kept so a rule can reference one
    #: without a schema migration.
    source_attributes: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SubscriberProduct(Base, TimestampMixin):
    """Which product a subscriber was on, and when.

    Effective-dated because a CDR must be enriched with the product the
    subscriber held **at the time of the event**, not the one they hold now —
    rating a March call against an April tariff is a classic false exception.
    """

    __tablename__ = "subscriber_products"
    __table_args__ = (
        Index("ix_subscriber_products_lookup", "msisdn", "effective_from", "effective_to"),
        Index("ix_subscriber_products_subscriber", "subscriber_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subscriber_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    msisdn: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    imsi: Mapped[str | None] = mapped_column(String(32), nullable=True)
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    offer_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tariff_plan_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_type: Mapped[str] = mapped_column(
        String(16), default="PREPAID", server_default="PREPAID", nullable=False
    )
    rating_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_system: Mapped[str] = mapped_column(
        String(64), default="MANUAL", server_default="MANUAL", nullable=False
    )


class SubscriberGroupMembership(Base, TimestampMixin):
    """Which groups a subscriber belonged to, and when (§9).

    Deliberately its own table rather than a column on ``subscriber_products``,
    because membership is many-per-subscriber and each membership has its own
    validity window: GOLD since January, VOICE_BUNDLE for one month, EMPLOYEE
    until they leave.

    **Reading it is where the trap is.** Joining these rows onto a CDR
    multiplies the CDR by its group count, which produces one rating result per
    group instead of one per call. Enrichment therefore aggregates this table
    with ``ARRAY_AGG ... GROUP BY msisdn`` *before* it meets a usage row, and
    the result lands in ``cdr_enriched.subscriber_groups`` as a list.
    """

    __tablename__ = "subscriber_group_membership"
    __table_args__ = (
        # The bulk-aggregation query's access path: every membership for the
        # subscribers in one batch, filtered to the event date.
        Index(
            "ix_subscriber_group_membership_lookup",
            "msisdn", "effective_from", "effective_to",
        ),
        UniqueConstraint(
            "msisdn", "group_code", "effective_from",
            name="uq_subscriber_group_membership_msisdn",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    msisdn: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subscriber_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    group_code: Mapped[str] = mapped_column(String(64), nullable=False)
    #: SEGMENT / LOYALTY / GEOGRAPHY / STAFF / BUNDLE / PROMOTION — lets a rule
    #: target "any loyalty tier" without listing every tier.
    group_type: Mapped[str] = mapped_column(
        String(32), default="SEGMENT", server_default="SEGMENT", nullable=False
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_system: Mapped[str] = mapped_column(
        String(64), default="MANUAL", server_default="MANUAL", nullable=False
    )


class HolidayCalendar(Base, TimestampMixin):
    """Public holidays, for the WEEKDAY / WEEKEND / HOLIDAY day type.

    Derived from a calendar rather than from the weekday number, because a
    holiday priced as an ordinary Tuesday produces a variance on every call that
    day — a large, alarming, and entirely artificial spike.
    """

    __tablename__ = "holiday_calendar"
    __table_args__ = (
        UniqueConstraint("country_code", "holiday_date", name="uq_holiday_calendar_country_code"),
        Index("ix_holiday_calendar_date", "holiday_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    country_code: Mapped[str] = mapped_column(
        String(8), default="KH", server_default="KH", nullable=False
    )
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Some operators price a half-day holiday as a weekend rather than a full
    #: holiday, so the resulting day type is data, not a constant.
    day_type: Mapped[str] = mapped_column(
        String(16), default="HOLIDAY", server_default="HOLIDAY", nullable=False
    )


class NetworkPrefix(Base, TimestampMixin):
    """Prefixes belonging to our own network, for the on-net/off-net decision."""

    __tablename__ = "network_prefixes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    prefix: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    operator_name: Mapped[str] = mapped_column(
        String(128), default="", server_default="", nullable=False
    )
    #: True for our own network. False rows describe known competitors, which is
    #: what makes "off-net but domestic" distinguishable from "unknown".
    is_home_network: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    country_code: Mapped[str | None] = mapped_column(String(8), nullable=True)


class CdrSequence(Base):
    """Per-source record counter, for the file/record sequence checks the wider
    platform already reports on. Populated at ingestion so a gap is visible
    without re-reading the landing table."""

    __tablename__ = "cdr_sequences"
    __table_args__ = (
        UniqueConstraint("source_system", "sequence_number", name="uq_cdr_sequences_source_system"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
