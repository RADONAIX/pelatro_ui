"""Balance buckets and the consumption ledger.

**Why this exists.** Everything the engine has done so far is *stateless*: a CDR
can be priced knowing only itself and the rules. Bundles break that. "300 free
minutes a month" means the 301st minute costs money and the 299th does not, so
the answer depends on every call that came before it.

That forces two things this module provides:

* a **balance bucket** — how much allowance a subscriber (or an account, for a
  shared bundle) has left in a period, and
* a **ledger** — one row per consumption, so a charge can be explained as
  "you had 40 minutes left, this call used 3, you have 37".

Without the ledger a disputed bundle charge is unanswerable, which is the exact
situation this platform exists to prevent.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class BalanceBucket(Base, TimestampMixin):
    """One subscriber's (or account's) allowance for one bundle in one period."""

    __tablename__ = "balance_buckets"
    __table_args__ = (
        # The owner key already encodes personal-vs-shared (subscriber id or
        # account id), so one constraint covers both without a nullable pair.
        UniqueConstraint(
            "owner_key", "bundle_code", "period_start", name="uq_balance_buckets_owner_key"
        ),
        Index(
            "ix_balance_buckets_lookup",
            "owner_key", "bundle_code", "period_start", "period_end",
        ),
        Index("ix_balance_buckets_period", "period_start", "period_end"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    #: account id for a shared bundle, subscriber id otherwise. Resolving the
    #: owner at allocation time is what makes shared and personal bundles the
    #: same code path downstream.
    owner_key: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_type: Mapped[str] = mapped_column(
        String(16), default="SUBSCRIBER", server_default="SUBSCRIBER", nullable=False
    )
    subscriber_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    msisdn: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    bundle_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    service_type: Mapped[str] = mapped_column(String(16), nullable=False)
    #: MINUTE / MESSAGE / MEGABYTE … — the unit the allowance is denominated in.
    quota_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    shared: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    reset_period: Mapped[str] = mapped_column(
        String(16), default="MONTHLY", server_default="MONTHLY", nullable=False
    )

    allocated: Mapped[float] = mapped_column(
        Numeric(20, 4), default=0, server_default="0", nullable=False
    )
    consumed: Mapped[float] = mapped_column(
        Numeric(20, 4), default=0, server_default="0", nullable=False
    )
    #: Usage that arrived after the allowance was exhausted. Kept apart from
    #: `consumed` so "how far over did they go?" is answerable without
    #: recomputing it from the ledger.
    overflow: Mapped[float] = mapped_column(
        Numeric(20, 4), default=0, server_default="0", nullable=False
    )
    consumption_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_system: Mapped[str] = mapped_column(
        String(64), default="MANUAL", server_default="MANUAL", nullable=False
    )
    attributes: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )

    entries: Mapped[list[BalanceLedgerEntry]] = relationship(
        back_populates="bucket", cascade="all, delete-orphan"
    )

    @property
    def remaining(self) -> float:
        return float(self.allocated) - float(self.consumed)


class BalanceLedgerEntry(Base):
    """One consumption event against a bucket.

    Append-only, and written even when nothing was consumed (an exhausted
    bundle still explains why the call was charged). A rating run can be
    replayed by discarding the run's entries and rewinding the bucket.
    """

    __tablename__ = "balance_ledger"
    __table_args__ = (
        # One entry per CDR per bucket per run: replaying a run must not
        # double-consume, and the constraint makes that structural.
        UniqueConstraint(
            "run_id", "cdr_enriched_id", "bucket_id", name="uq_balance_ledger_run_id"
        ),
        Index("ix_balance_ledger_bucket_time", "bucket_id", "event_timestamp"),
        Index("ix_balance_ledger_run", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    bucket_id: Mapped[str] = mapped_column(
        ForeignKey("balance_buckets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    cdr_enriched_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    cdr_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: What the CDR asked for, what the bundle covered, what spilled over.
    requested: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    consumed: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    overflow: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    balance_before: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    balance_after: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    bucket: Mapped[BalanceBucket] = relationship(back_populates="entries")


class UsageCounter(Base, TimestampMixin):
    """Cumulative usage per subscriber per period, for tiered charging.

    Separate from a balance bucket because it means the opposite thing: a bucket
    counts *down* from an allowance, a counter counts *up* to decide which tier
    the next unit falls in. Conflating them makes "first 1 GB free, then tiered"
    impossible to express.
    """

    __tablename__ = "usage_counters"
    __table_args__ = (
        UniqueConstraint(
            "owner_key", "counter_key", "period_start", name="uq_usage_counters_owner_key"
        ),
        Index("ix_usage_counters_lookup", "owner_key", "counter_key", "period_start"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_key: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Usually the rule key that owns the tier ladder, so two tiered rules on
    #: the same subscriber accumulate independently.
    counter_key: Mapped[str] = mapped_column(String(80), nullable=False)
    service_type: Mapped[str] = mapped_column(String(16), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    total: Mapped[float] = mapped_column(
        Numeric(20, 4), default=0, server_default="0", nullable=False
    )
    event_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    notes: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
