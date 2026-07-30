"""Import batch, per-row outcome, and reusable mapping templates."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin
from app.modules.imports.constants import ImportStatus


def _uuid() -> str:
    return str(uuid.uuid4())


class RuleImportTemplate(Base, TimestampMixin):
    """A saved column mapping, so the same vendor's monthly sheet imports in one click."""

    __tablename__ = "rule_import_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    source_vendor: Mapped[str] = mapped_column(
        String(64), default="", server_default="", nullable=False, index=True
    )
    #: {file heading: canonical column key | null}
    mapping: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class RuleImportBatch(Base):
    """One upload. Kept whether it succeeded or not — a rejected import is
    evidence about the source system, and support will ask for it."""

    __tablename__ = "rule_import_batches"
    __table_args__ = (Index("ix_rule_import_batches_created", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    source_system: Mapped[str] = mapped_column(
        String(64), default="FILE_IMPORT", server_default="FILE_IMPORT", nullable=False
    )
    template_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rule_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("rule_sets.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default=ImportStatus.PENDING,
        server_default=ImportStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    mapping: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    imported_rows: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    rejected_rows: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    summary: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    rows: Mapped[list[RuleImportRow]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class RuleImportRow(Base):
    """The fate of one spreadsheet row.

    ``raw`` is kept verbatim so the rejected-rows export can hand the source
    team back exactly what they sent, with the reason appended.
    """

    __tablename__ = "rule_import_rows"
    __table_args__ = (Index("ix_rule_import_rows_batch_status", "batch_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("rule_import_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 1-based, and counting the header — i.e. the line number in the file, so
    #: "row 47 failed" points at what the author sees in Excel.
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    canonical: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    errors: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    batch: Mapped[RuleImportBatch] = relationship(back_populates="rows")
