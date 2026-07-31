"""Rule sets and their membership.

The legacy model made membership a single FK on the rule, so a rule could belong
to exactly one set. That collapses two different ideas: an editorial grouping
("all roaming rules"), and a release — the specific set of *versions* published
together. A rule obviously belongs to both.

A ``RELEASE`` set with pinned ``rule_version_id`` values is what the compiler
snapshots. That turns "publish these 4,000 rules together" into a first-class,
diffable, revertible object rather than a status sweep across 4,000 rows.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.modules.rules.canonical.base import RULE_SCHEMA, TenantMixin, new_id
from app.modules.rules.vocabulary.modes import RuleSetType


class CanonicalRuleSet(Base, TenantMixin, TimestampMixin):
    __tablename__ = "rule_set"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_rule_set_tenant_code"),
        {"schema": RULE_SCHEMA},
    )

    rule_set_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    #: LOGICAL | RELEASE | VENDOR_IMPORT
    set_type: Mapped[str] = mapped_column(
        String(32),
        default=RuleSetType.LOGICAL,
        server_default=RuleSetType.LOGICAL.value,
        nullable=False,
        index=True,
    )
    #: Set when every member shares one charging mode — lets the catalogue offer
    #: "the prepaid release" as a filter.
    charging_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="ACTIVE", server_default="ACTIVE", nullable=False, index=True
    )
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Set for VENDOR_IMPORT sets, so an import is revertible as a unit.
    source_system_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_systems.id", ondelete="RESTRICT"), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class RuleSetMember(Base, TenantMixin, TimestampMixin):
    __tablename__ = "rule_set_member"
    __table_args__ = (
        UniqueConstraint("rule_set_id", "rule_id", name="uq_rule_set_member_set_rule"),
        Index("ix_rule_set_member_rule", "tenant_id", "rule_id"),
        {"schema": RULE_SCHEMA},
    )

    rule_set_member_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_set_id: Mapped[str] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.rule_set.rule_set_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_id: Mapped[str] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.rule.rule_id", ondelete="CASCADE"), nullable=False
    )
    #: Pinned for RELEASE sets: this exact version ships. NULL for a LOGICAL set,
    #: which tracks whatever the rule's current version happens to be.
    rule_version_id: Mapped[str | None] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.rule_version.rule_version_id", ondelete="SET NULL"),
        nullable=True,
    )
    sequence_number: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
