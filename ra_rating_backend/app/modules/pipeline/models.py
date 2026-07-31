"""Pipeline run tracking."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.modules.pipeline.constants import PipelineStatus, initial_stages


def _uuid() -> str:
    return str(uuid.uuid4())


class PipelineRun(Base, TimestampMixin):
    """One end-to-end execution: file in, exceptions out.

    Created the moment a file is accepted, so the caller gets an id back
    immediately and polls for progress. A 1-crore batch cannot be a synchronous
    HTTP request, and pretending otherwise produces timeouts that look like
    data loss.
    """

    __tablename__ = "pipeline_runs"
    __table_args__ = (Index("ix_pipeline_runs_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cdr_type: Mapped[str] = mapped_column(String(24), nullable=False)

    status: Mapped[str] = mapped_column(
        String(24),
        default=PipelineStatus.QUEUED,
        server_default=PipelineStatus.QUEUED.value,
        nullable=False,
        index=True,
    )
    current_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Per-stage status, timings and counters. One JSONB column rather than a
    #: child table: it is always read whole, never queried across runs.
    stages: Mapped[list] = mapped_column(
        JSONB, default=initial_stages, server_default="[]", nullable=False
    )

    #: Filled in as the stages that create them complete.
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    rating_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    snapshot_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: The upload, held until ingestion consumes it. Cleared afterwards so a
    #: run's row does not keep a multi-MB payload alive forever.
    payload: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    total_records: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    exception_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    triggered_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Set when the run was launched by a connector's schedule rather than a person.
    connector_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class PipelineEvent(Base):
    """Append-only log of what the pipeline did, for the run detail timeline."""

    __tablename__ = "pipeline_events"
    __table_args__ = (Index("ix_pipeline_events_run_time", "run_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    level: Mapped[str] = mapped_column(
        String(8), default="INFO", server_default="INFO", nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
