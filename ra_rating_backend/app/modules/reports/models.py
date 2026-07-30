"""Generated report artefacts.

The rendered bytes are stored, not regenerated on download: a certified export
handed to finance must be the same file every time it is fetched, even after
the underlying data has been replayed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class GeneratedReport(Base):
    __tablename__ = "generated_reports"
    __table_args__ = (Index("ix_generated_reports_code", "report_code", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_code: Mapped[str] = mapped_column(String(64), nullable=False)
    report_name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The window/run the report was generated over — reproducibility metadata.
    params: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    row_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    generated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    generated_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
