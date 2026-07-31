"""Canonical rule ORM models.

Versioning model — the part worth reading twice:

``rule_key``  identifies the *logical* rule and is stable forever.
``version``   increments per change; ``(rule_key, version)`` is unique.
``id``        identifies one immutable version row.

Nothing is ever updated in place once approved: a change creates a new row with
version+1, and the previous row moves to SUPERSEDED. Rating results reference
the version ``id``, so a result from six months ago can always be re-explained
against the exact rule text that produced it — which is the entire point of an
assurance platform.
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
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin
from app.modules.rules.constants import (
    ConditionLogic,
    RuleStatus,
    StackingPolicy,
)


def _uuid() -> str:
    return str(uuid.uuid4())


class RuleSet(Base, TimestampMixin):
    """A named grouping of rules that is validated, compiled and published together."""

    __tablename__ = "rule_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="ACTIVE", server_default="ACTIVE", nullable=False, index=True
    )
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_system: Mapped[str] = mapped_column(
        String(64), default="MANUAL", server_default="MANUAL", nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    rules: Mapped[list[Rule]] = relationship(back_populates="rule_set")


class Rule(Base, TimestampMixin):
    __tablename__ = "rules"
    __table_args__ = (
        UniqueConstraint("rule_key", "version", name="uq_rules_rule_key"),
        # The hot path for rule selection (Phase 3): narrow by service + status +
        # validity window before anything else is considered.
        Index("ix_rules_selection", "service_type", "status", "effective_from", "effective_to"),
        Index("ix_rules_product_service", "product_id", "service_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)

    # --- Identity & versioning ---------------------------------------------
    #: Stable across every version of this logical rule.
    rule_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    #: The version this one was derived from — powers the diff view.
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    # --- Descriptive --------------------------------------------------------
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: Derived from rule_type; stored so the compiler can group by stage without
    #: re-deriving it for 10,000 rules on every publish.
    execution_stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    category: Mapped[str] = mapped_column(
        String(64), default="", server_default="", nullable=False
    )
    service_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # --- Canonical metadata links (nullable = "applies to any") -------------
    rule_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("rule_sets.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    offer_id: Mapped[str | None] = mapped_column(
        ForeignKey("offers.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    tariff_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("tariff_plans.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    # --- Selection semantics ------------------------------------------------
    #: Author-set tie-breaker. Higher wins.
    priority: Mapped[int] = mapped_column(
        Integer, default=100, server_default="100", nullable=False, index=True
    )
    #: Computed from the bound conditions (see service.compute_specificity).
    #: Breaks ties BEFORE priority does, so a narrow rule beats a broad one even
    #: when an author forgot to raise its priority.
    specificity: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False, index=True
    )
    stacking_policy: Mapped[str] = mapped_column(
        String(16),
        default=StackingPolicy.EXCLUSIVE,
        server_default=StackingPolicy.EXCLUSIVE.value,
        nullable=False,
    )
    #: Rules sharing a conflict group are mutually exclusive; the winner is
    #: resolved once per rating context rather than per CDR.
    conflict_group: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: AND / OR across the top-level condition groups.
    condition_logic: Mapped[str] = mapped_column(
        String(4),
        default=ConditionLogic.AND,
        server_default=ConditionLogic.AND.value,
        nullable=False,
    )

    # --- Validity -----------------------------------------------------------
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    currency_code: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # --- Lifecycle ----------------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(16),
        default=RuleStatus.DRAFT,
        server_default=RuleStatus.DRAFT.value,
        nullable=False,
        index=True,
    )
    source_system: Mapped[str] = mapped_column(
        String(64), default="MANUAL", server_default="MANUAL", nullable=False, index=True
    )
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    submitted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    change_comment: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    #: Last structural-validation outcome, cached so the catalogue can show a
    #: health column without re-running validation for every row.
    last_validation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: Free-form extras preserved from a vendor import (Phase 5).
    attributes: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )

    rule_set: Mapped[RuleSet | None] = relationship(back_populates="rules")
    conditions: Mapped[list[RuleCondition]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        order_by="RuleCondition.sequence",
        lazy="selectin",
    )
    actions: Mapped[list[RuleAction]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        order_by="RuleAction.sequence",
        lazy="selectin",
    )


class RuleCondition(Base, TimestampMixin):
    """One `attribute operator value(s)` predicate.

    ``group_index`` lets the builder express ``(A AND B) OR (C)``: conditions in
    the same group are ANDed, groups are combined with the rule's
    ``condition_logic``. Two levels is enough for every real tariff we modelled
    and keeps the compiled lookup key flat.
    """

    __tablename__ = "rule_conditions"
    __table_args__ = (Index("ix_rule_conditions_rule_seq", "rule_id", "sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    group_index: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    attribute: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    operator: Mapped[str] = mapped_column(String(24), nullable=False)
    #: Always a JSON array, even for single-value operators — one shape for the
    #: compiler to consume, no per-operator branching when building lookup keys.
    values: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    negate: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    rule: Mapped[Rule] = relationship(back_populates="conditions")


class RuleAction(Base, TimestampMixin):
    """One charging instruction the rule performs when it matches."""

    __tablename__ = "rule_actions"
    __table_args__ = (Index("ix_rule_actions_rule_seq", "rule_id", "sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: Validated against ACTION_SPECS[action_type].params at save time.
    params: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )

    rule: Mapped[Rule] = relationship(back_populates="actions")


class RuleAuditEntry(Base):
    """Append-only history for a logical rule.

    Keyed by ``rule_key`` rather than ``rule_id`` so the timeline survives across
    versions — "who changed what, when and why" for the whole life of the rule,
    not just one row.
    """

    __tablename__ = "rule_audit"
    __table_args__ = (Index("ix_rule_audit_key_time", "rule_key", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    rule_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    rule_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    #: Field-level before/after for an edit; empty for pure status moves.
    diff: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RuleTemplate(Base, TimestampMixin):
    """A reusable starting point for the "Create from template" flow."""

    __tablename__ = "rule_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    service_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: {"conditions": [...], "actions": [...]} in the same shape the create API takes.
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    is_system: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
