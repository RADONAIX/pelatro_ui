"""Snapshots and the executable rules they contain.

A snapshot is **immutable**. Activating a new one supersedes the old; it is
never edited or deleted, because a rating result from six months ago references
it by id and re-explaining that charge means reading the exact rule text that
produced it.
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
from app.modules.compiler.constants import SnapshotStatus


def _uuid() -> str:
    return str(uuid.uuid4())


class RuleSnapshot(Base, TimestampMixin):
    __tablename__ = "rule_snapshots"
    __table_args__ = (
        UniqueConstraint("version", name="uq_rule_snapshots_version"),
        Index("ix_rule_snapshots_status_effective", "status", "effective_from"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    #: Monotonic across the whole estate, so "snapshot 42" is unambiguous.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    #: Null = the whole estate. Set to compile one rule set in isolation.
    rule_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("rule_sets.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default=SnapshotStatus.PUBLISHED,
        server_default=SnapshotStatus.PUBLISHED.value,
        nullable=False,
        index=True,
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    rule_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    product_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    #: SHA-256 over the compiled rules. Two snapshots with the same checksum are
    #: the same executable set, which is how a "no-op republish" is detected.
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: Per-stage rule counts, conflict/coverage summary, compile duration.
    stats: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    #: Warnings that did not block the compile.
    issues: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)

    compiled_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    compiled_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set when ClickHouse is enabled and the executable rules were mirrored.
    published_to_clickhouse: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    rules: Mapped[list[ExecutableRule]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class ExecutableRule(Base):
    """One canonical rule, flattened for bulk selection.

    Every ``NULL`` dimension means "any", so selection is a wildcard-tolerant
    join rather than a per-rule predicate evaluation.
    """

    __tablename__ = "executable_rules"
    __table_args__ = (
        # The selection hot path: narrow to the snapshot, stage and service
        # before any dimension comparison happens.
        Index("ix_exec_rules_selection", "snapshot_id", "execution_stage", "service_type"),
        Index("ix_exec_rules_precedence", "snapshot_id", "specificity", "priority"),
        Index("ix_exec_rules_rule", "rule_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("rule_snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The canonical rule version this was compiled from.
    rule_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rule_key: Mapped[str] = mapped_column(String(80), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)

    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Position of the stage in the charging sequence — the engine orders on it.
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False)

    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    specificity: Mapped[int] = mapped_column(Integer, nullable=False)
    stacking_policy: Mapped[str] = mapped_column(String(16), nullable=False)
    conflict_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    condition_logic: Mapped[str] = mapped_column(String(4), nullable=False)

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # --- Match dimensions (NULL = any) --------------------------------------
    service_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    product_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    offer_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tariff_plan_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    destination_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_band: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    roaming: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    network_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rating_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    on_net: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    #: A dimension constrained to several values (IN) keeps its list here; the
    #: column above holds NULL and the list is checked at selection.
    dimension_sets: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    #: Conditions that could not be reduced to a dimension.
    predicates: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    #: Precompiled actions, in execution order, with references resolved.
    actions: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    #: Human-readable match signature, shown in the trace and the rule explainer.
    signature: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    snapshot: Mapped[RuleSnapshot] = relationship(back_populates="rules")
