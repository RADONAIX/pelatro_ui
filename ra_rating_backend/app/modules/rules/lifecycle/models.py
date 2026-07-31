"""A bulk lifecycle run, recorded so it can outlive the request that started it.

Plan step **B4**. The synchronous endpoints refuse above ``MAX_SYNCHRONOUS``
because a request that takes four minutes is a request that a load balancer,
a proxy, or an impatient operator will kill halfway — and a bulk approval killed
halfway is the exact state this whole module exists to avoid. The job is the
answer to that: the same service functions, run outside the request, against a
row that says how far they got.

The row is the point, not the task. An in-memory task that vanishes on restart
leaves an operator with no way to answer "did my five thousand rules approve?".
Progress is written to the table as the run proceeds, so the answer survives a
deploy, and so a run that died mid-flight is visibly RUNNING-with-a-stale-
heartbeat rather than silently absent.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.modules.rules.canonical.base import RULE_SCHEMA, TenantMixin, new_id


class BulkRunStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    #: The operation ran and refused — bad selection, unapproved rules, a
    #: validation wall. Distinct from ERROR because there is something for the
    #: operator to fix, and the blocked report says what.
    REJECTED = "REJECTED"
    #: The run itself broke. Nothing for the operator to fix; this is ours.
    ERROR = "ERROR"

    TERMINAL = (SUCCEEDED, REJECTED, ERROR)


class RuleBulkRun(Base, TenantMixin, TimestampMixin):
    """One asynchronous bulk operation over a selection of rules."""

    __tablename__ = "rule_bulk_run"

    bulk_run_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )

    #: validate | approve | revert | activate — the same verbs as the sync API,
    #: because a job is not a different operation, only a different place to run.
    operation: Mapped[str] = mapped_column(String(24), nullable=False)
    #: The selector as submitted, kept verbatim. Re-resolved at execution rather
    #: than frozen into a rule-id list: a run queued behind others should act on
    #: the estate as it is when it runs, and an id list would silently act on
    #: rules that have since been deleted.
    selector: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(16), default=BulkRunStatus.PENDING,
        server_default=BulkRunStatus.PENDING, nullable=False, index=True,
    )

    dry_run: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    atomic: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    force: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    comment: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )

    #: Progress. `total` is 0 until the selector resolves, which is itself the
    #: first slow step on a large estate — so a UI that shows 0/0 RUNNING is
    #: showing the truth rather than a bug.
    total: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    processed: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    applied: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    #: The finished BulkResult, in the same shape the synchronous endpoint
    #: returns, so a UI renders a completed job with the component it already has.
    counts: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    blocked: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    #: Set by `activate` only.
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    touched_live_pricing: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    error: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    error_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Written on every progress flush. A RUNNING row whose heartbeat is an hour
    #: old died with its process; without this there is no way to tell that from
    #: a run that is merely slow.
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_rule_bulk_run_tenant_created", "tenant_id", "created_at"),
        Index("ix_rule_bulk_run_tenant_status", "tenant_id", "status"),
        {"schema": RULE_SCHEMA},
    )
