"""The canonical rule and its versions — the split the legacy model lacks.

``rule`` is the *logical* rule: stable identity, never rewritten, one row for the
whole life of "peak on-net voice for PREPAID_A". ``rule_version`` is one immutable
statement of what that rule said between two dates. A rating result six months old
references a version id, so it can always be re-explained against the exact text
that produced it — which is the entire point of an assurance platform.

The legacy ``rating.rules`` fused the two into one row keyed by
``(rule_key, version)``. That works until you need to ask "what is this rule,
independent of its versions?" — for a dependency edge, a conflict-group
membership, a set membership or a withdrawal proposal — at which point every
question has to pick a version arbitrarily.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin
from app.modules.rules.canonical.base import RULE_SCHEMA, TenantMixin, new_id
from app.modules.rules.constants import RuleStatus
from app.modules.rules.vocabulary.modes import (
    ExecutionMode,
    FallbackPolicy,
    ValidationState,
)


class CanonicalRule(Base, TenantMixin, TimestampMixin):
    __tablename__ = "rule"
    __table_args__ = (
        UniqueConstraint("tenant_id", "rule_key", name="uq_rule_tenant_key"),
        # The real idempotency key for an import: a vendor's own primary key.
        # Without it, re-importing can only match on a key derived from the rule's
        # name, so a rename forks a phantom logical rule and a re-run duplicates
        # the estate.
        UniqueConstraint(
            "tenant_id", "source_system_id", "external_ref", name="uq_rule_tenant_source_extref"
        ),
        Index("ix_rule_catalogue", "tenant_id", "charging_mode", "service_type", "status"),
        Index("ix_rule_type_stage", "tenant_id", "rule_type_id", "rule_stage_id"),
        {"schema": RULE_SCHEMA},
    )

    rule_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)

    #: Stable logical identity. Immutable once written — see the deterministic
    #: derivation in the ingestion kernel; names never participate in identity.
    rule_key: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)

    #: PREPAID | POSTPAID | BOTH. Scopes the stage pipeline the rule runs through.
    charging_mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    rule_type_id: Mapped[str] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.rule_type.rule_type_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    #: Denormalized from the type so the compiler can group 10,000 rules by stage
    #: without a join per rule on every publish.
    rule_stage_id: Mapped[str] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.rule_stage.rule_stage_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    service_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    #: NULL = authored by hand. Otherwise the connector or file source it came from.
    source_system_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_systems.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    #: The vendor's own identifier for this rule, verbatim.
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    #: Pointer to the version currently in force. Maintained by the writer, not a
    #: computed view, because the catalogue reads it for every row.
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    status: Mapped[str] = mapped_column(
        String(16),
        default=RuleStatus.DRAFT,
        server_default=RuleStatus.DRAFT.value,
        nullable=False,
        index=True,
    )
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    versions: Mapped[list[CanonicalRuleVersion]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        order_by="CanonicalRuleVersion.version_number.desc()",
        foreign_keys="CanonicalRuleVersion.rule_id",
    )


class CanonicalRuleVersion(Base, TenantMixin):
    """One immutable statement of a rule, valid between two dates."""

    __tablename__ = "rule_version"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "rule_id", "version_number", name="uq_rule_version_tenant_rule_number"
        ),
        # The selection hot path. INCLUDE keeps the tie-breakers on the index leaf
        # so ranking candidates needs no heap fetch.
        Index(
            "ix_rule_version_selection",
            "tenant_id", "status", "execution_mode", "effective_from", "effective_to",
            postgresql_include=["rule_id", "priority", "specificity_score"],
        ),
        Index("ix_rule_version_hash", "tenant_id", "behaviour_hash"),
        Index("ix_rule_version_snapshot", "published_snapshot_id"),
        Index("ix_rule_version_validation", "tenant_id", "validation_state"),
        # Two live prices for the same event is the most expensive class of
        # production incident there is, and no amount of application-level care
        # prevents it under concurrency. The database does.
        ExcludeConstraint(
            ("rule_id", "="),
            (
                text("daterange(effective_from, COALESCE(effective_to, 'infinity'::date), '[]')"),
                "&&",
            ),
            name="ex_rule_version_active_window",
            where=text("status IN ('ACTIVE', 'PUBLISHED')"),
            using="gist",
        ),
        {"schema": RULE_SCHEMA},
    )

    rule_version_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_id: Mapped[str] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.rule.rule_id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- What the version targets ------------------------------------------
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    offer_id: Mapped[str | None] = mapped_column(
        ForeignKey("offers.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    tariff_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("tariff_plans.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    # --- Behaviour (the wizard's Step 4) -----------------------------------
    priority: Mapped[int] = mapped_column(
        Integer, default=100, server_default="100", nullable=False
    )
    #: Computed from the bound conditions, never author-set. Two hand-tuned knobs
    #: for one job is how a 10,000-rule estate becomes unpredictable.
    specificity_score: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    stacking_policy_id: Mapped[str] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.rule_stacking_policy.stacking_policy_id", ondelete="RESTRICT"),
        nullable=False,
    )
    conflict_group_id: Mapped[str | None] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.rule_conflict_group.conflict_group_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    fallback_policy: Mapped[str] = mapped_column(
        String(24),
        default=FallbackPolicy.FALLBACK_CHAIN,
        server_default=FallbackPolicy.FALLBACK_CHAIN.value,
        nullable=False,
    )
    #: Halt this stage once this rule has matched.
    stop_processing: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    #: ONLINE | OFFLINE | BOTH. Lets the compiler emit a session-time snapshot and
    #: a bill-run snapshot from one rule set, instead of an online engine carrying
    #: thousands of invoice rules it can never fire.
    execution_mode: Mapped[str] = mapped_column(
        String(8),
        default=ExecutionMode.BOTH,
        server_default=ExecutionMode.BOTH.value,
        nullable=False,
        index=True,
    )
    #: AND / OR across the top-level condition groups.
    condition_logic: Mapped[str] = mapped_column(
        String(4), default="AND", server_default="AND", nullable=False
    )

    # --- Validity -----------------------------------------------------------
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # --- Lifecycle ----------------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(16),
        default=RuleStatus.DRAFT,
        server_default=RuleStatus.DRAFT.value,
        nullable=False,
        index=True,
    )
    change_reason: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    #: The version this one was derived from — powers the diff view.
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    #: The snapshot that published this version, for the catalogue's Snapshot column.
    published_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("rule_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    validation_state: Mapped[str] = mapped_column(
        String(16),
        default=ValidationState.UNKNOWN,
        server_default=ValidationState.UNKNOWN.value,
        nullable=False,
    )

    #: Hash of everything that changes behaviour — excludes name, description and
    #: category, because a vendor renaming a plan is not a tariff change and
    #: versioning 4,000 rules for it makes the audit trail useless in a week.
    #: Indexed, unlike the legacy fingerprint that hid inside a JSONB blob.
    behaviour_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Denormalized projection of the whole version, maintained in the same
    #: transaction as the normalized rows. The compiler and engine read this one
    #: column instead of five tables; a nightly job re-derives it and alerts on
    #: mismatch so the denormalization cannot drift silently.
    canonical_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    #: Vendor fields we do not model yet, preserved verbatim and surfaced in the
    #: UI as "N unmapped fields" — visible, not silently discarded.
    extras: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )

    submitted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    rule: Mapped[CanonicalRule] = relationship(
        back_populates="versions", foreign_keys=[rule_id]
    )
