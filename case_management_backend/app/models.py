"""ORM models for the assurance schema.

Two things live here:

* **ControlRule** — the rule catalog the Rule Explorer edits. A rule is metadata
  (assurance, entity scope, primitive category, parameters, schedule); the
  universal rule engine compiles it into an executable control.
* **Case** — one finding. Raised by the engine when a rule fails (carrying that
  rule's identity and the values that mismatched) or manually by an analyst.
  Its mismatch rows, notes and audit trail hang off it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import SCHEMA, Base

# JSONB on PostgreSQL (indexable, binary) while staying loadable on any other
# backend the models might be pointed at.
JSONType = JSON().with_variant(JSONB, "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Timezone-aware UTC now. All timestamp columns are timestamptz."""
    return datetime.now(timezone.utc)


TS = DateTime(timezone=True)


class ControlRule(Base):
    """A control rule as authored in the rules module.

    `id` is the human control id (UA001) — it is what a case quotes, what an
    analyst searches for, and it is stable, so it doubles as the primary key.
    """

    __tablename__ = "control_rules"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # What leakage or integrity risk this control detects.
    intent: Mapped[str] = mapped_column(Text, default="")

    assurance_code: Mapped[str] = mapped_column(String(8), index=True)
    assurance_name: Mapped[str] = mapped_column(String(80))
    assurance_group: Mapped[str] = mapped_column(String(80), default="")
    # Entity scope — the sub-module the rule is bound to (Usage Events, CDR…).
    entity_scope: Mapped[str] = mapped_column(String(80), index=True, default="")
    # Primitive category — Completeness, Reconciliation, Threshold, …
    primitive_category: Mapped[str] = mapped_column(String(60), index=True, default="")

    severity: Mapped[str] = mapped_column(String(16), default="medium")
    frequency: Mapped[str] = mapped_column(String(24), default="Daily")

    # Parameters differ per primitive; the common comparison ones are columns
    # because they are what a case reports back, the rest ride in `params`.
    source_feed: Mapped[str] = mapped_column(String(120), default="")
    target_feed: Mapped[str] = mapped_column(String(120), default="")
    tolerance_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    params: Mapped[dict] = mapped_column(JSONType, default=dict)

    # Draft rules are not scheduled; only Active ones can raise cases.
    lifecycle_state: Mapped[str] = mapped_column(String(24), index=True, default="Draft")
    # Result of the most recent execution: PASS | FAIL | WARNING | NOT_RUN.
    last_status: Mapped[str] = mapped_column(String(16), default="NOT_RUN")
    last_run_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    # Rolling counter so the explorer can show "raised 12 cases".
    cases_raised: Mapped[int] = mapped_column(Integer, default=0)

    created_by: Mapped[str] = mapped_column(String(120), default="system")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)

    cases: Mapped[list["Case"]] = relationship(back_populates="rule", lazy="noload")


Index("ix_control_rules_assurance_category", ControlRule.assurance_code, ControlRule.primitive_category)


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        Index("ix_cases_assurance_created", "assurance_code", "created_at"),
        Index("ix_cases_status_severity", "status", "severity"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Human reference shown in the UI and quoted in escalations: CASE-2031.
    reference: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")

    # --- Where the finding belongs -----------------------------------------
    # Denormalised from the rule on purpose: it keeps the list query a
    # single-table scan and freezes what the case said when it was raised, even
    # if the rule is later edited or retired.
    assurance_code: Mapped[str] = mapped_column(String(8), index=True)
    assurance_name: Mapped[str] = mapped_column(String(80), index=True)
    assurance_group: Mapped[str] = mapped_column(String(80), index=True, default="")
    module: Mapped[str] = mapped_column(String(80), index=True, default="")
    sub_module: Mapped[str] = mapped_column(String(80), default="")

    # --- The rule that raised it (null for analyst-raised cases) ------------
    # ON DELETE SET NULL: deleting a rule must not delete its history.
    rule_id: Mapped[str | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.control_rules.id", ondelete="SET NULL"),
        index=True, nullable=True,
    )
    rule_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Primitive category — the case's "issue type".
    rule_category: Mapped[str] = mapped_column(String(60), index=True, default="")
    # Identifies the execution of the rule, so every case from one run groups.
    rule_run_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    # --- Triage state -------------------------------------------------------
    origin: Mapped[str] = mapped_column(String(24), index=True, default="auto_detected")
    severity: Mapped[str] = mapped_column(String(16), index=True, default="medium")
    status: Mapped[str] = mapped_column(String(24), index=True, default="Open")
    action: Mapped[str] = mapped_column(String(60), default="NA")
    # Empty string is the marker for unassigned (never NULL — it keeps the
    # filter predicate and the sort key simple).
    owner: Mapped[str] = mapped_column(String(120), index=True, default="")

    # --- Where in the estate ------------------------------------------------
    stream: Mapped[str] = mapped_column(String(32), default="")
    node_id: Mapped[str] = mapped_column(String(48), default="")
    source_feed: Mapped[str] = mapped_column(String(120), default="")
    target_feed: Mapped[str] = mapped_column(String(120), default="")
    linked_batch: Mapped[str] = mapped_column(String(80), default="")
    linked_txn_id: Mapped[str] = mapped_column(String(80), default="")

    # --- What the rule actually measured ------------------------------------
    # Text so a control can report a count, an amount, a percentage or a
    # sequence range without needing a column per shape.
    expected_value: Mapped[str | None] = mapped_column(String(120), nullable=True)
    actual_value: Mapped[str | None] = mapped_column(String(120), nullable=True)
    variance: Mapped[str | None] = mapped_column(String(120), nullable=True)
    variance_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[str | None] = mapped_column(String(120), nullable=True)
    estimated_impact: Mapped[float] = mapped_column(Float, default=0.0)
    affected_count: Mapped[int] = mapped_column(Integer, default=0)

    # --- Attachments / free-form --------------------------------------------
    evidence: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    saved_insights: Mapped[list] = mapped_column(JSONType, default=list)
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    # Anything rule-specific the engine wants to carry through untouched.
    details: Mapped[dict] = mapped_column(JSONType, default=dict)

    # --- Timestamps ---------------------------------------------------------
    # When the underlying data problem was observed — may predate the case.
    detected_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)

    # Idempotency key for the rule engine: re-posting the same (rule, run,
    # window) collapses onto the existing case instead of duplicating it.
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)

    rule: Mapped["ControlRule | None"] = relationship(back_populates="cases", lazy="noload")
    mismatches: Mapped[list["CaseMismatch"]] = relationship(
        back_populates="case", cascade="all, delete-orphan",
        order_by="CaseMismatch.position", lazy="selectin",
    )
    attachments: Mapped[list["CaseAttachment"]] = relationship(
        back_populates="case", cascade="all, delete-orphan",
        order_by="CaseAttachment.created_at", lazy="selectin",
    )
    comments: Mapped[list["CaseComment"]] = relationship(
        back_populates="case", cascade="all, delete-orphan",
        order_by="CaseComment.created_at", lazy="selectin",
    )
    activities: Mapped[list["CaseActivity"]] = relationship(
        back_populates="case", cascade="all, delete-orphan",
        order_by="CaseActivity.created_at", lazy="selectin",
    )


class CaseMismatch(Base):
    """One evidence row: the record-level values that failed the control."""

    __tablename__ = "case_mismatches"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cases.id", ondelete="CASCADE"), index=True,
    )
    # Preserves the order the engine emitted the rows in.
    position: Mapped[int] = mapped_column(Integer, default=0)

    record_ref: Mapped[str] = mapped_column(String(120), default="")   # txn / file / invoice id
    entity: Mapped[str] = mapped_column(String(80), default="")        # node, feed, partner…
    subscriber: Mapped[str] = mapped_column(String(80), default="")
    field: Mapped[str] = mapped_column(String(80), default="")         # which attribute mismatched
    expected_value: Mapped[str | None] = mapped_column(String(160), nullable=True)
    actual_value: Mapped[str | None] = mapped_column(String(160), nullable=True)
    delta: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="MISMATCH")
    occurred_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)

    case: Mapped[Case] = relationship(back_populates="mismatches")


class CaseAttachment(Base):
    """A file attached to a case as evidence (PDF today).

    Only metadata lives here — the bytes are written under settings.uploads_dir
    as an opaque uuid filename. The client's filename is kept for display only
    and is never used to build a path, so a crafted name can't escape the
    upload directory.
    """

    __tablename__ = "case_attachments"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cases.id", ondelete="CASCADE"), index=True,
    )
    filename: Mapped[str] = mapped_column(String(255))        # as shown to the user
    content_type: Mapped[str] = mapped_column(String(120), default="application/pdf")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # Path relative to settings.uploads_dir, so the storage root can move.
    storage_path: Mapped[str] = mapped_column(String(400))
    # SHA-256 of the stored bytes — lets a re-upload be recognised and gives
    # the download an integrity check.
    checksum_sha256: Mapped[str] = mapped_column(String(64), default="")
    uploaded_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)

    case: Mapped["Case"] = relationship(back_populates="attachments")


class CaseComment(Base):
    """An investigation note."""

    __tablename__ = "case_comments"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cases.id", ondelete="CASCADE"), index=True,
    )
    author: Mapped[str] = mapped_column(String(120), default="")
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="comments")


class CaseActivity(Base):
    """Audit trail — one row per state change (created, assigned, status, …)."""

    __tablename__ = "case_activities"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cases.id", ondelete="CASCADE"), index=True,
    )
    actor: Mapped[str] = mapped_column(String(120), default="system")
    action: Mapped[str] = mapped_column(String(60))          # created | status | owner | severity…
    field: Mapped[str | None] = mapped_column(String(60), nullable=True)
    from_value: Mapped[str | None] = mapped_column(String(160), nullable=True)
    to_value: Mapped[str | None] = mapped_column(String(160), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="activities")
