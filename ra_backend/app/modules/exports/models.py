"""Exports ORM model: a tracked bulk-export job.

One row per requested download. The Celery worker (app.workers.tasks.run_export)
streams the report to a ``.csv.gz`` file, updating progress on this row; the API
polls it for status and serves the file when complete.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


# Lifecycle: Queued -> Running -> Completed | Failed | Cancelled; Expired by cleanup.
class ExportJob(Base, TimestampMixin):
    __tablename__ = "export_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    report_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="Queued", nullable=False, index=True)
    # {date_from, date_to, filters} — the selection this export covers.
    params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    total_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    processed_rows: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # NULL = single-file export; >0 = multi-part (see ExportJobPart), assembled by
    # finalize_export into one .csv.gz.
    part_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Storage locator (a disk path now, an object-store URI later) + integrity.
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_format: Mapped[str] = mapped_column(String(16), default="csv.gz", nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Aggregate KPIs computed over the same selection.
    kpis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# One date-range slice of a multi-part export. Each part streams independently to
# `{reference}.part{NN}.csv.gz` (part 0 keeps the header, parts >0 are data-only);
# finalize_export concatenates them in index order into the single `{reference}.csv.gz`.
class ExportJobPart(Base, TimestampMixin):
    __tablename__ = "export_job_parts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("export_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    part_index: Mapped[int] = mapped_column(Integer, nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)  # exclusive upper bound
    status: Mapped[str] = mapped_column(String(16), default="Queued", nullable=False)
    rows: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
