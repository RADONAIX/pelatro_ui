"""Relationships between rules: dependencies and fallback chains.

Both are keyed on the *logical* rule, not a version — "tax depends on base
charge" is a statement about the rules, and re-asserting it on every new version
would be noise that eventually goes stale.

``rule_fallback`` makes the documented exact → product → service → global-default
chain explicit. Today that chain is an emergent property of specificity scores,
which means it works until someone adds a rule with an unlucky score and nobody
can explain why a CDR started falling through to the default.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.modules.rules.canonical.base import RULE_SCHEMA, TenantMixin, new_id

_RULE_FK = f"{RULE_SCHEMA}.rule.rule_id"


class RuleDependency(Base, TenantMixin, TimestampMixin):
    """A directed edge between two logical rules.

    Cycles are rejected at publish by a recursive CTE, not by a constraint: a
    cycle is a property of the whole graph, and no per-row check can see it.
    """

    __tablename__ = "rule_dependency"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "rule_id", "depends_on_rule_id", "dependency_type",
            name="uq_rule_dependency_edge",
        ),
        CheckConstraint("rule_id <> depends_on_rule_id", name="ck_rule_dependency_not_self"),
        Index("ix_rule_dependency_reverse", "tenant_id", "depends_on_rule_id"),
        {"schema": RULE_SCHEMA},
    )

    rule_dependency_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_id: Mapped[str] = mapped_column(
        ForeignKey(_RULE_FK, ondelete="CASCADE"), nullable=False, index=True
    )
    depends_on_rule_id: Mapped[str] = mapped_column(
        ForeignKey(_RULE_FK, ondelete="CASCADE"), nullable=False
    )
    #: REQUIRES | PRECEDES | EXCLUDES | AMENDS
    dependency_type: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)


class RuleFallback(Base, TenantMixin, TimestampMixin):
    """Where selection goes next when this rule does not match.

    ``fallback_level`` orders the chain; ``fallback_scope`` records *why* this is
    the next stop, which is what makes a coverage report readable.
    """

    __tablename__ = "rule_fallback"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "rule_id", "fallback_level", name="uq_rule_fallback_level"
        ),
        CheckConstraint("rule_id <> fallback_rule_id", name="ck_rule_fallback_not_self"),
        CheckConstraint("fallback_level > 0", name="ck_rule_fallback_level_positive"),
        {"schema": RULE_SCHEMA},
    )

    rule_fallback_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_id: Mapped[str] = mapped_column(
        ForeignKey(_RULE_FK, ondelete="CASCADE"), nullable=False, index=True
    )
    fallback_rule_id: Mapped[str] = mapped_column(
        ForeignKey(_RULE_FK, ondelete="CASCADE"), nullable=False
    )
    #: 1 = the first alternative.
    fallback_level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: PRODUCT | SERVICE | GLOBAL_DEFAULT
    fallback_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), default="", server_default="", nullable=False)
