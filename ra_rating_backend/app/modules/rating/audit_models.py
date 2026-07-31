"""Per-usage audit: rule evaluation, calculation steps, final result, exceptions.

These four tables exist so that *every* number this platform reports can be
defended line by line. The existing ``rating_results.trace`` and
``context_rule_map.candidates`` already explain a charge, but both are JSONB and
one of them is truncated to fifty entries — good enough to render a screen, not
good enough to answer "show me every rule that was rejected for undercharged
prepaid traffic last month" without scanning the whole table.

They are keyed on ``usage_id`` — the deterministic source identity from §6 —
rather than on a run, which is what lets a re-rate correct history in place
instead of appending another opinion beside it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class EvaluationStatus(StrEnum):
    """The life of one rule against one usage record (§5.7).

    The distinction that matters is ``REJECTED`` vs ``MATCHED_NOT_SELECTED``:
    the first means the rule did not apply, the second means it did and lost.
    Collapsing them — which is what a plain "did not fire" flag does — makes a
    misconfigured priority indistinguishable from a correctly narrow condition,
    and that is the single most common rule-estate defect.
    """

    CANDIDATE = "CANDIDATE"
    MATCHED = "MATCHED"
    REJECTED = "REJECTED"
    MATCHED_NOT_SELECTED = "MATCHED_NOT_SELECTED"
    SELECTED = "SELECTED"
    APPLIED = "APPLIED"


class RejectionReason(StrEnum):
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    CONDITION_FAILED = "CONDITION_FAILED"
    NOT_EFFECTIVE = "NOT_EFFECTIVE"
    LOWER_PRIORITY = "LOWER_PRIORITY"
    LOWER_SPECIFICITY = "LOWER_SPECIFICITY"
    LOWER_VERSION = "LOWER_VERSION"
    EXCLUSIVE_GROUP_TAKEN = "EXCLUSIVE_GROUP_TAKEN"
    NOT_STACKABLE = "NOT_STACKABLE"
    STOPPED_BY_EARLIER_RULE = "STOPPED_BY_EARLIER_RULE"


class ComponentType(StrEnum):
    """One step of the calculation plan (§5.8, §16)."""

    BILLABLE_QUANTITY = "BILLABLE_QUANTITY"
    PULSE = "PULSE"
    BUNDLE_DEDUCTION = "BUNDLE_DEDUCTION"
    BASE_CHARGE = "BASE_CHARGE"
    MINIMUM_CHARGE = "MINIMUM_CHARGE"
    MAXIMUM_CHARGE = "MAXIMUM_CHARGE"
    DISCOUNT = "DISCOUNT"
    SURCHARGE = "SURCHARGE"
    TAX = "TAX"
    ROUNDING = "ROUNDING"


class RatingStatus(StrEnum):
    """The assurance verdict for one usage record (§22)."""

    MATCHED = "MATCHED"
    OVERCHARGED = "OVERCHARGED"
    UNDERCHARGED = "UNDERCHARGED"
    ZERO_CHARGED = "ZERO_CHARGED"
    NO_RULE_FOUND = "NO_RULE_FOUND"
    NO_ACTUAL_CHARGE = "NO_ACTUAL_CHARGE"
    ENRICHMENT_FAILED = "ENRICHMENT_FAILED"
    AMBIGUOUS_RULE = "AMBIGUOUS_RULE"
    CALCULATION_FAILED = "CALCULATION_FAILED"


class ActualChargeStatus(StrEnum):
    """How confidently the billed amount was correlated (§21)."""

    MATCHED = "MATCHED"
    NO_ACTUAL_CHARGE = "NO_ACTUAL_CHARGE"
    MULTIPLE_ACTUAL_MATCHES = "MULTIPLE_ACTUAL_MATCHES"
    PARTIAL_MATCH = "PARTIAL_MATCH"


class UsageExceptionStatus(StrEnum):
    OPEN = "OPEN"
    RETRYING = "RETRYING"
    RESOLVED = "RESOLVED"
    #: Retried up to the configured limit and still failing. Distinct from
    #: OPEN so a permanent data defect is not silently retried forever.
    EXHAUSTED = "EXHAUSTED"
    IGNORED = "IGNORED"


class RuleEvaluationAudit(Base):
    """Why each rule won or lost, for one usage record (§5.7, §13)."""

    __tablename__ = "rule_evaluation_audit"
    __table_args__ = (
        # One verdict per rule per usage record. Re-rating overwrites rather
        # than appending, so the audit always reflects the current result.
        UniqueConstraint("usage_id", "rule_id", name="uq_rule_evaluation_audit_usage_id"),
        Index("ix_rule_evaluation_audit_usage", "usage_id", "rule_stage"),
        # "Which rules are never selected?" — the question that finds dead rules
        # in an estate nobody has pruned.
        Index("ix_rule_evaluation_audit_rule_status", "rule_id", "evaluation_status"),
        Index("ix_rule_evaluation_audit_run", "run_id"),
    )

    evaluation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    usage_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rule_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rule_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rule_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rule_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluation_status: Mapped[str] = mapped_column(String(24), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The human sentence behind the reason code, e.g. "outranked by SMART20_OFFNET
    #: (specificity 5 vs 2)". Written once here so every screen that explains the
    #: decision tells the same story.
    detail: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    specificity_score: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    rule_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    exclusive_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CalculationComponent(Base):
    """One ordered step of the charge, with its inputs and its output (§5.8).

    Stored as rows rather than as a JSON blob because the recurring analyst
    question is cross-record — "how much tax did we expect on off-net voice in
    March" — and that is a ``SUM ... GROUP BY`` over this table, not a scan that
    parses a document per row.
    """

    __tablename__ = "calculation_component"
    __table_args__ = (
        UniqueConstraint(
            "usage_id", "sequence_number", name="uq_calculation_component_usage_id"
        ),
        Index("ix_calculation_component_usage", "usage_id"),
        Index("ix_calculation_component_type", "component_type"),
        Index("ix_calculation_component_run", "run_id"),
    )

    component_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    usage_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    component_type: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rule_key: Mapped[str | None] = mapped_column(String(80), nullable=True)

    #: The quantity this step consumed — seconds, bytes, messages.
    input_quantity: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    #: The running amount before this step.
    input_amount: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    #: What this step added or removed. Signed: a discount is negative.
    adjustment_amount: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    #: The running amount after this step.
    output_amount: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    calculation_detail: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RatingResultFinal(Base, TimestampMixin):
    """The one authoritative assurance verdict per usage record (§5.9).

    **Why this table exists beside ``rating_results``.** That table is keyed on
    ``(run_id, cdr_enriched_id)`` and is deliberately append-only: the replay
    module re-rates the same CDRs into a second run precisely so the two can be
    compared. Putting a global unique key on it would break that. So the
    per-run history stays where it is, and this table holds the current answer,
    upserted on ``(usage_id, charge_component)`` — which is the constraint §5.9
    asks for and the reason re-running a batch cannot double-count.
    """

    __tablename__ = "rating_result_final"
    __table_args__ = (
        UniqueConstraint(
            "usage_id", "charge_component", name="uq_rating_result_final_usage_id"
        ),
        Index("ix_rating_result_final_status", "rating_status", "event_date"),
        Index("ix_rating_result_final_variance", "absolute_variance"),
        Index("ix_rating_result_final_run", "run_id"),
        Index("ix_rating_result_final_subscriber", "subscriber_msisdn"),
    )

    rating_result_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    usage_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: TOTAL for the whole-charge verdict. Kept in the key so a future
    #: per-component assurance (tax alone, discount alone) needs no migration.
    charge_component: Mapped[str] = mapped_column(
        String(32), default="TOTAL", server_default="TOTAL", nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    cdr_enriched_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    subscriber_msisdn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    service_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    call_direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    selected_base_rule_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: stage -> rule id for every stage that contributed, so the result links
    #: back to the whole plan and not only to the base tariff.
    selected_rule_ids: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )

    expected_charge: Mapped[float] = mapped_column(
        Numeric(18, 6), default=0, server_default="0", nullable=False
    )
    actual_charge: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    #: actual - expected, per §22. Positive means the customer was charged more
    #: than the rules allow; negative means revenue was lost. Signed, always.
    variance_amount: Mapped[float] = mapped_column(
        Numeric(18, 6), default=0, server_default="0", nullable=False
    )
    absolute_variance: Mapped[float] = mapped_column(
        Numeric(18, 6), default=0, server_default="0", nullable=False
    )
    variance_percentage: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    rating_status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    enrichment_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actual_charge_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    explanation: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )


class UsageException(Base, TimestampMixin):
    """One usage record that could not be assured, and why (§5.10).

    Per-record and retryable, unlike ``RatingException`` in
    ``rating/models.py``, which groups thousands of results into one
    investigation. Both are needed, and they are deliberately different classes:
    the grouped view is what an analyst works from, this one is what the retry
    loop and the reconciliation count work from.
    """

    __tablename__ = "rating_usage_exception"
    __table_args__ = (
        # One open exception per usage record per failure kind: a batch retried
        # three times must not leave three identical rows.
        UniqueConstraint(
            "usage_id", "exception_type", name="uq_rating_usage_exception_usage_id"
        ),
        Index("ix_rating_usage_exception_status", "status", "exception_code"),
        Index("ix_rating_usage_exception_run", "run_id"),
    )

    exception_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    usage_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: ENRICHMENT / RULE / CALCULATION / ACTUAL_CHARGE / SOURCE — which stage
    #: failed, so the exception list can be filtered by the team that owns it.
    exception_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The machine code, matching RatingAssuranceError.exception_code.
    exception_code: Mapped[str] = mapped_column(String(48), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default=UsageExceptionStatus.OPEN,
        server_default=UsageExceptionStatus.OPEN.value,
        nullable=False,
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
