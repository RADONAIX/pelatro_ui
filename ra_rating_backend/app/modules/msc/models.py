"""Bookkeeping for the MSC source pull.

The raw records themselves are never copied into this database — they stay in
the operator's switch archive, which this service reads read-only. What lives
here is only the position we have read to, so an incremental pull knows where to
resume.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class MscIngestCursor(Base, TimestampMixin):
    """How far the reader has got through one source table.

    A high-water mark on the source's own monotonic ``id``, so an incremental
    pull reads only new rows. Kept per (source system, table) because an
    operator runs sm_msc01..sm_msc08 and they advance independently.

    The cursor is an *optimisation*, not the correctness mechanism: a lost or
    reset cursor causes rows to be re-read, and the unique constraint on
    ``cdr_enriched.usage_id`` means re-reading is a no-op rather than a
    duplicate. Correctness never depends on a mutable counter being right.
    """

    __tablename__ = "msc_ingest_cursor"
    __table_args__ = (
        UniqueConstraint(
            "source_system", "source_table", name="uq_msc_ingest_cursor_source_system"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_table: Mapped[str] = mapped_column(String(128), nullable=False)
    #: The highest source id already read — an exclusive lower bound for the
    #: next pull. BigInteger because the source's own key is a bigint.
    last_source_id: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    last_pulled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    records_pulled: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
