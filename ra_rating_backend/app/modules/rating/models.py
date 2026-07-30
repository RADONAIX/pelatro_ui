"""Rating runs, per-CDR results, calculation traces and exceptions."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
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
from app.modules.rating.constants import ExceptionStatus, RunStatus


def _uuid() -> str:
    return str(uuid.uuid4())


class RatingRun(Base, TimestampMixin):
    """One execution of the pipeline over one CDR batch."""

    __tablename__ = "rating_runs"
    __table_args__ = (Index("ix_rating_runs_batch_status", "batch_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("cdr_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The snapshot this run resolved against. Immutable, so the run can always
    #: be re-explained — and a replay against a newer snapshot is a comparison,
    #: not a rewrite of history.
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        default=RunStatus.PENDING,
        server_default=RunStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    #: REPLAY runs re-rate CDRs that were already rated; kept distinct so
    #: recovered revenue is never double-counted as new leakage.
    run_type: Mapped[str] = mapped_column(
        String(16), default="INITIAL", server_default="INITIAL", nullable=False
    )
    replay_of_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    total_cdrs: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    rated_cdrs: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    distinct_contexts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    matched_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    exception_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    expected_revenue: Mapped[float] = mapped_column(
        Numeric(20, 6), default=0, server_default="0", nullable=False
    )
    billed_revenue: Mapped[float] = mapped_column(
        Numeric(20, 6), default=0, server_default="0", nullable=False
    )
    #: Undercharge and overcharge are kept apart, never netted: £1m of each is
    #: two serious problems, not a clean book.
    undercharge_total: Mapped[float] = mapped_column(
        Numeric(20, 6), default=0, server_default="0", nullable=False
    )
    overcharge_total: Mapped[float] = mapped_column(
        Numeric(20, 6), default=0, server_default="0", nullable=False
    )

    stats: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    triggered_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    results: Mapped[list[RatingResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class ContextRuleMap(Base):
    """The rules selected for one rating context — resolved once, reused by
    every CDR that shares the context.

    This table *is* the performance design: without it, rule resolution would
    run per CDR instead of per distinct situation.
    """

    __tablename__ = "context_rule_map"
    __table_args__ = (
        UniqueConstraint("run_id", "context_hash", name="uq_context_rule_map_run_id"),
        Index("ix_context_rule_map_run", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("rating_runs.id", ondelete="CASCADE"), nullable=False
    )
    context_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    context_key: Mapped[str] = mapped_column(Text, nullable=False)
    #: How many CDRs share this context — the collapse ratio, and the first
    #: number to look at when a run is slow.
    cdr_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    #: stage -> winning executable rule id.
    selected_rules: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    #: Every rule that matched, with why it won or lost. Powers "Rejected rules"
    #: in the trace, which is what an analyst actually asks for.
    candidates: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    candidate_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RatingResult(Base):
    """The expected charge for one CDR, component by component (§16)."""

    __tablename__ = "rating_results"
    __table_args__ = (
        UniqueConstraint("run_id", "cdr_enriched_id", name="uq_rating_results_run_id"),
        Index("ix_rating_results_run_status", "run_id", "status"),
        Index("ix_rating_results_variance", "run_id", "variance"),
        Index("ix_rating_results_product", "run_id", "product_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("rating_runs.id", ondelete="CASCADE"), nullable=False
    )
    cdr_enriched_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    cdr_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    context_hash: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    subscriber_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    msisdn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    service_type: Mapped[str] = mapped_column(String(16), nullable=False)
    product_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    destination_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_band: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    #: stage -> executable rule id, so a result links straight back to the rule.
    selected_rule_ids: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )

    billable_quantity: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    billable_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    expected_base_charge: Mapped[float] = mapped_column(
        Numeric(18, 6), default=0, server_default="0", nullable=False
    )
    expected_discount: Mapped[float] = mapped_column(
        Numeric(18, 6), default=0, server_default="0", nullable=False
    )
    expected_tax: Mapped[float] = mapped_column(
        Numeric(18, 6), default=0, server_default="0", nullable=False
    )
    expected_final_charge: Mapped[float] = mapped_column(
        Numeric(18, 6), default=0, server_default="0", nullable=False
    )
    actual_charge: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    #: expected minus actual. Positive = undercharged (revenue leakage);
    #: negative = overcharged (customer harm). Never absolute — the sign is the
    #: single most important thing about the number.
    variance: Mapped[float] = mapped_column(
        Numeric(18, 6), default=0, server_default="0", nullable=False
    )
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    #: Bundle coverage, denormalised onto the result. A charge of zero is only
    #: defensible if the answer to "which allowance paid for it?" is stored
    #: alongside it; joining back to the ledger for every row on a dispute
    #: screen is both slow and easy to get wrong.
    bundle_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    bundle_consumed: Mapped[float] = mapped_column(
        Numeric(18, 4), default=0, server_default="0", nullable=False
    )
    bundle_overflow: Mapped[float] = mapped_column(
        Numeric(18, 4), default=0, server_default="0", nullable=False
    )
    #: Quantity that fell above every tier of a ladder, so was not priced at
    #: all. Distinct from zero-rated: this is a rule gap, not a free allowance.
    unpriced_quantity: Mapped[float] = mapped_column(
        Numeric(18, 4), default=0, server_default="0", nullable=False
    )

    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    root_cause: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    #: The ordered calculation steps (§16). Stored per result because "explain
    #: this charge" is the question the product exists to answer.
    trace: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)
    engine_version: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped[RatingRun] = relationship(back_populates="results")


class RatingException(Base, TimestampMixin):
    """A group of results that failed the same way, for the same reason.

    Grouped rather than per-CDR: 40,000 calls mispriced by one broken rule is
    one investigation, and an analyst handed 40,000 tickets will not find it.
    """

    __tablename__ = "rating_exceptions"
    __table_args__ = (
        Index("ix_rating_exceptions_run_status", "run_id", "status"),
        Index("ix_rating_exceptions_impact", "revenue_impact"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("rating_runs.id", ondelete="CASCADE"), nullable=False
    )
    #: Stable across runs, so the same recurring problem is recognisable.
    group_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    assurance_status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    root_cause: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(24),
        default=ExceptionStatus.NEW,
        server_default=ExceptionStatus.NEW.value,
        nullable=False,
        index=True,
    )

    service_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    product_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    destination_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)

    cdr_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    subscriber_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    revenue_impact: Mapped[float] = mapped_column(
        Numeric(20, 6), default=0, server_default="0", nullable=False
    )
    expected_total: Mapped[float] = mapped_column(
        Numeric(20, 6), default=0, server_default="0", nullable=False
    )
    actual_total: Mapped[float] = mapped_column(
        Numeric(20, 6), default=0, server_default="0", nullable=False
    )
    first_event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_event_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: A worked example — the fastest way for an analyst to see what went wrong.
    sample_result_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    probable_cause: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    recommended_action: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)

    assigned_to: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    assigned_to_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolution: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set when a replay run confirmed the fix, with the recovered amount.
    recovered_amount: Mapped[float] = mapped_column(
        Numeric(20, 6), default=0, server_default="0", nullable=False
    )

    comments: Mapped[list[ExceptionComment]] = relationship(
        back_populates="exception", cascade="all, delete-orphan"
    )


class ExceptionComment(Base):
    __tablename__ = "exception_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    exception_id: Mapped[str] = mapped_column(
        ForeignKey("rating_exceptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Status changes and assignments are recorded as comments too, so the
    #: investigation reads as one timeline rather than two.
    kind: Mapped[str] = mapped_column(
        String(16), default="COMMENT", server_default="COMMENT", nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    exception: Mapped[RatingException] = relationship(back_populates="comments")
