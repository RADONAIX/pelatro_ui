"""Lookup tables: stage, type, stacking policy, conflict group.

These are rows rather than Python enums for four reasons that only show up at
scale: a tenant can add a rule type without a deploy; a report can join on the
type's display name; the mode-scoped pipeline (``rule_stage.applies_to`` +
``execution_order``) becomes queryable data instead of a tuple in a module; and a
conflict group gains an owner and a resolution strategy, which a bare string
column can never carry.

Seeded from :mod:`app.modules.rules.vocabulary` and kept in step by
``vocabulary.sync.sync_vocabulary``, so the registry and the tables cannot drift.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.modules.rules.canonical.base import RULE_SCHEMA, TenantMixin, new_id
from app.modules.rules.vocabulary.modes import ConflictResolution, RuleCategory


class RuleStage(Base, TenantMixin, TimestampMixin):
    """One position in the charging sequence.

    ``execution_order`` IS the sequence, gap-numbered by 10 so a stage can be
    inserted without renumbering — renumbering silently reorders a live pipeline.
    ``applies_to`` scopes the stage to COMMON / PREPAID / POSTPAID, which is what
    lets one engine serve both charging modes.
    """

    __tablename__ = "rule_stage"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_rule_stage_tenant_code"),
        UniqueConstraint("tenant_id", "execution_order", name="uq_rule_stage_tenant_order"),
        {"schema": RULE_SCHEMA},
    )

    rule_stage_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    applies_to: Mapped[str] = mapped_column(
        String(16),
        default=RuleCategory.COMMON,
        server_default=RuleCategory.COMMON.value,
        nullable=False,
        index=True,
    )
    execution_order: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Needs per-subscriber state (balance, counter, reservation) to evaluate.
    is_stateful: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)


class RuleTypeRow(Base, TenantMixin, TimestampMixin):
    """A kind of rule, and — the part that matters — the stage it runs at."""

    __tablename__ = "rule_type"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_rule_type_tenant_code"),
        {"schema": RULE_SCHEMA},
    )

    rule_type_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_category: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    rule_stage_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    #: Which charging modes may use this type.
    charging_mode: Mapped[str] = mapped_column(
        String(16), default="BOTH", server_default="BOTH", nullable=False, index=True
    )
    #: A matching rule MUST carry one of these actions — the validator's contract.
    #: A BASE_TARIFF rule with no SET_RATE silently rates everything at zero, which
    #: is the most expensive authoring mistake there is.
    required_action_types: Mapped[list[str]] = mapped_column(
        ARRAY(String(48)), default=list, server_default="{}", nullable=False
    )
    #: Set when this type is an operator-facing synonym for another.
    alias_of: Mapped[str | None] = mapped_column(String(48), nullable=True)
    is_system: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)


class RuleStackingPolicyRow(Base, TenantMixin, TimestampMixin):
    """How several matching rules at one stage combine."""

    __tablename__ = "rule_stacking_policy"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_rule_stacking_policy_tenant_code"),
        {"schema": RULE_SCHEMA},
    )

    stacking_policy_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: EXCLUSIVE = false, STACKABLE = true.
    allows_multiple: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: OVERRIDE = true: replaces whatever a lower-priority rule set at this stage.
    overrides_lower: Mapped[bool] = mapped_column(Boolean, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)


class RuleConflictGroupRow(Base, TenantMixin, TimestampMixin):
    """A set of mutually exclusive rules, with a declared way of choosing between them.

    The legacy model held this as a free-text column on the rule, which meant the
    resolution strategy lived in the engine and could not differ per group — so
    "for promotions, highest priority wins; for tariffs, most specific wins" was
    not expressible.
    """

    __tablename__ = "rule_conflict_group"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_rule_conflict_group_tenant_code"),
        {"schema": RULE_SCHEMA},
    )

    conflict_group_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    resolution_strategy: Mapped[str] = mapped_column(
        String(32),
        default=ConflictResolution.HIGHEST_SPECIFICITY,
        server_default=ConflictResolution.HIGHEST_SPECIFICITY.value,
        nullable=False,
    )
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
