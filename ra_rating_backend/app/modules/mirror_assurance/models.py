"""Control-plane records for mirror rating-assurance executions."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class MirrorAssuranceSchedule(Base, TimestampMixin):
    __tablename__ = "mirror_assurance_schedule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    interval_minutes: Mapped[int] = mapped_column(Integer, default=1440, server_default="1440")
    window_hours: Mapped[int] = mapped_column(Integer, default=24, server_default="24")
    tolerance: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), default=Decimal("0.01"), server_default="0.01"
    )
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(36))
    updated_by_name: Mapped[str | None] = mapped_column(String(255))


class MirrorAssuranceRun(Base, TimestampMixin):
    __tablename__ = "mirror_assurance_run"
    __table_args__ = (Index("ix_mirror_assurance_run_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED", index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tolerance: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    stages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    matched_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    undercharged_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    overcharged_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    exception_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_variance: Mapped[Decimal] = mapped_column(
        Numeric(24, 6), nullable=False, default=Decimal(0), server_default="0"
    )
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triggered_by: Mapped[str | None] = mapped_column(String(36))
    triggered_by_name: Mapped[str | None] = mapped_column(String(255))


class MirrorAssuranceResult(Base):
    __tablename__ = "mirror_assurance_result"
    __table_args__ = (
        Index("ix_mirror_assurance_result_run_ordinal", "run_id", "ordinal"),
        Index("ix_mirror_assurance_result_run_status", "run_id", "reconciliation_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("mirror_assurance_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[str] = mapped_column(String(100), nullable=False)
    service_type: Mapped[str | None] = mapped_column(String(30))
    reconciliation_status: Mapped[str] = mapped_column(String(40), nullable=False)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
