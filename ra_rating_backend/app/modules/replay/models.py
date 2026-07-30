"""Replay requests and their before/after comparison.

A replay is its own record, not just a second rating run, because the thing an
operator needs afterwards is the *difference*: what the fix recovered, what it
left open, and whether it introduced new problems. That comparison is computed
once, when both runs exist, and stored — recomputing it later against tables a
further replay has touched would silently change history.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class ReplayRun(Base, TimestampMixin):
    __tablename__ = "replay_runs"
    __table_args__ = (Index("ix_replay_runs_source", "source_run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    #: The run being replayed.
    source_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: The REPLAY-type rating run this produced (null until it exists).
    new_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: Snapshot the replay rated against — the fix under test.
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_version: Mapped[int | None] = mapped_column(nullable=True)
    #: Optional exception this replay was launched from.
    exception_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), default="RUNNING", server_default="RUNNING", nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Signed: original undercharge minus replay undercharge. Positive means
    #: the fix genuinely closed leakage; negative means it made things worse.
    recovered_amount: Mapped[float] = mapped_column(
        Numeric(20, 6), default=0, server_default="0", nullable=False
    )
    #: The full before/after: totals, by_status, exception counts.
    comparison: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )

    triggered_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    triggered_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
