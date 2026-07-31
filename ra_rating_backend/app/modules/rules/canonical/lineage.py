"""Provenance: where every canonical field came from, and who changed it.

An assurance platform's job is to explain a charge. That obligation does not stop
at the rule — "the rate is 0.012" is only half an answer if nobody can say which
cell of which vendor file, run on which night, put it there. These tables hold
the other half.

``rule_ingestion_record`` is partitioned by hash on ``batch_id``. It is by a wide
margin the largest table here — a 40,000-row dump per night, kept for forensics —
and repartitioning a populated table means a rewrite, so the partitioning is
established before anything writes to it rather than retrofitted after.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.modules.rules.canonical.base import RULE_SCHEMA, TenantMixin, new_id

_VERSION_FK = f"{RULE_SCHEMA}.rule_version.rule_version_id"


class RuleImportProfile(Base, TenantMixin, TimestampMixin):
    """A declarative mapping from one vendor's export shape to the canonical model.

    Held as data rather than code so onboarding a vendor is configuration plus a
    golden-file test, not a deploy. The vendor adapters keep only the logic a
    declarative spec genuinely cannot express — Ericsson's class-header/rate-step
    flattening, BRM's RATE_PLAN→RATE_TIER nesting.
    """

    __tablename__ = "rule_import_profile"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "code", "version", name="uq_rule_import_profile_code_version"
        ),
        {"schema": RULE_SCHEMA},
    )

    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    #: ERICSSON_CS | ORACLE_BRM | HUAWEI_CBS | AMDOCS | GENERIC
    vendor: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: CSV | TSV | XLSX | JSON | XML | FIXED | DB | API
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The mapping spec itself: record path, identity, money scaling, timezone,
    #: field/condition/action mappings, postconditions.
    spec: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    #: Profiles are versioned rather than edited in place, so a batch imported six
    #: months ago can still be explained against the spec that mapped it.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    is_system: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class RuleIngestionBatch(Base, TenantMixin):
    """One run of the ingestion kernel, whatever channel it came from.

    Kept whether it succeeded or not: a rejected import is evidence about the
    source system, and support will ask for it.
    """

    __tablename__ = "rule_ingestion_batch"
    __table_args__ = (
        Index("ix_rule_ingestion_batch_started", "tenant_id", "started_at"),
        Index("ix_rule_ingestion_batch_source", "tenant_id", "source_system_id", "status"),
        # The same file posted twice is the same batch. Partial index because a
        # manual authoring batch has no content hash and must not collide.
        Index(
            "uq_rule_ingestion_batch_content",
            "tenant_id", "source_system_id", "content_hash",
            unique=True,
            postgresql_where=text("content_hash IS NOT NULL"),
        ),
        {"schema": RULE_SCHEMA},
    )

    batch_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    #: MANUAL | FILE | CONNECTOR | API
    channel: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    source_system_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_systems.id", ondelete="RESTRICT"), nullable=True
    )
    profile_id: Mapped[str | None] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.rule_import_profile.profile_id", ondelete="SET NULL"),
        nullable=True,
    )
    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: FULL (the export is the whole truth; absences are withdrawals) | DELTA
    import_mode: Mapped[str] = mapped_column(
        String(16), default="DELTA", server_default="DELTA", nullable=False
    )
    #: STAGED | VALIDATING | AWAITING_APPROVAL | COMMITTING | COMPLETED |
    #: PARTIAL | FAILED | CANCELLED | ROLLED_BACK
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    dry_run: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    #: read / mapped / new / changed / unchanged / withdrawn / rejected / quarantined
    counts: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    #: Last committed offset. A failure at row 39,000 of 40,000 resumes instead of
    #: restarting — the difference between a five-minute retry and a lost night.
    checkpoint: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    #: Grouped rejection reasons: 400 rows failing for one reason is one fix, and
    #: an operations team needs to see that immediately.
    reasons: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    triggered_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RuleSourceLineage(Base, TenantMixin):
    """Field-level provenance for one rule version."""

    __tablename__ = "rule_source_lineage"
    __table_args__ = (
        Index("ix_rule_source_lineage_version", "rule_version_id"),
        Index("ix_rule_source_lineage_extref", "tenant_id", "source_system_id", "external_ref"),
        {"schema": RULE_SCHEMA},
    )

    lineage_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_version_id: Mapped[str] = mapped_column(
        ForeignKey(_VERSION_FK, ondelete="CASCADE"), nullable=False
    )
    source_system_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_systems.id", ondelete="RESTRICT"), nullable=True
    )
    batch_id: Mapped[str | None] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.rule_ingestion_batch.batch_id", ondelete="SET NULL"),
        nullable=True,
    )
    #: No FK: rule_ingestion_record is partitioned and lands in R5, and a lineage
    #: row must outlive the record retention window anyway.
    record_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: {"priority": {"source_field": "tariffClass.rank", "raw": "10", "transform": "int"}}
    field_provenance: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    #: Which vendor synonyms were substituted, so "our file said SET_MINIMUM" has
    #: an answer that is not an argument.
    applied_aliases: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    #: Vendor fields the profile did not map. Surfaced, not swallowed.
    unmapped_fields: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class RuleValidationIssue(Base, TenantMixin):
    """One validation finding against one rule version.

    The legacy model cached a summary blob on the rule row, which can say "3
    errors" but not *which* three — so the catalogue could show a red dot and
    nothing else, and "every rule failing the unknown-zone check" was a script
    rather than a query.
    """

    __tablename__ = "rule_validation_issue"
    __table_args__ = (
        Index("ix_rule_validation_issue_version", "rule_version_id", "severity"),
        Index("ix_rule_validation_issue_code", "tenant_id", "code", "severity"),
        {"schema": RULE_SCHEMA},
    )

    issue_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_version_id: Mapped[str] = mapped_column(
        ForeignKey(_VERSION_FK, ondelete="CASCADE"), nullable=False
    )
    #: ERROR | WARNING | INFO
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    #: Points at the exact condition or action row, so the builder can highlight it
    #: instead of dumping a list of sentences at the author.
    path: Mapped[str] = mapped_column(String(128), default="", server_default="", nullable=False)
    hint: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    #: Which validation tier raised it: STRUCTURAL | MODE | SEMANTIC | CROSS_RULE
    tier: Mapped[str] = mapped_column(
        String(16), default="STRUCTURAL", server_default="STRUCTURAL", nullable=False
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class CanonicalRuleAudit(Base):
    """Append-only history for a logical rule.

    Keyed on ``rule_id`` rather than a version, so the timeline spans the whole
    life of the rule — "who changed what, when and why" for all of it, not one row.
    """

    __tablename__ = "rule_audit"
    __table_args__ = (
        Index("ix_canonical_rule_audit_rule_time", "rule_id", "created_at"),
        Index("ix_canonical_rule_audit_actor", "tenant_id", "actor_id"),
        {"schema": RULE_SCHEMA},
    )

    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rule_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: MANUAL | FILE | CONNECTOR | API — how the change arrived.
    channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    #: Field-level before/after for an edit; empty for a pure status move.
    diff: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class RuleIngestionRecord(Base):
    """One incoming record, exactly as it arrived, and what we decided about it.

    Written **before** anything is interpreted. That ordering is the whole point:
    a parse that crashes on row 12,000 still leaves 12,000 raw payloads on disk,
    so the question "what did they actually send us?" has an answer that does not
    depend on our parser having worked.

    Hash-partitioned on ``batch_id`` so one night's dump lands in one partition
    set and an old batch can be detached rather than deleted row by row. Postgres
    requires the partition key in every unique constraint, which is why the
    primary key is composite rather than the bare ``record_id``.

    No foreign key to ``rule`` or ``rule_version``: a record must outlive the rule
    it created. A rule deleted as a mistaken draft leaves its evidence behind,
    which is the opposite of what a cascade would do.
    """

    __tablename__ = "rule_ingestion_record"
    __table_args__ = (
        Index("ix_rule_ingestion_record_batch", "batch_id", "decision"),
        Index("ix_rule_ingestion_record_extref", "tenant_id", "external_ref"),
        Index("ix_rule_ingestion_record_rule", "rule_id"),
        UniqueConstraint("batch_id", "source_offset", name="uq_ingestion_record_offset"),
        {
            "schema": RULE_SCHEMA,
            "postgresql_partition_by": "HASH (batch_id)",
        },
    )

    record_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    #: Part of the primary key because Postgres requires the partition key there.
    batch_id: Mapped[str] = mapped_column(String(36), primary_key=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    #: Position in the source, so a reject report can say "row 4,182" and an
    #: operator can find it in the file they sent.
    source_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The record verbatim. Never normalised, never trimmed.
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The draft it normalised to, or NULL when it never got that far.
    canonical_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: NEW | CHANGED | UNCHANGED | WITHDRAWN | REJECTED | QUARANTINED
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rule_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    issues: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
