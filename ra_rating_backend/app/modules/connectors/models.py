"""Source systems, their connectors and their import history."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class SourceSystem(Base, TimestampMixin):
    """An upstream system that supplies tariff configuration or CDRs."""

    __tablename__ = "source_systems"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    #: ORACLE_BRM | ERICSSON_CS | HUAWEI_CBS | … — selects the adapter.
    vendor: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: What this source supplies: RULES, CDR or REFERENCE.
    category: Mapped[str] = mapped_column(
        String(24), default="RULES", server_default="RULES", nullable=False, index=True
    )
    #: DATABASE | API | SFTP | FILE | XML | JSON | CSV | EXCEL
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="ACTIVE", server_default="ACTIVE", nullable=False, index=True
    )

    #: Non-secret connection settings — host, port, path, database, API base.
    connection: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    #: Secrets live in their own column so a connection can be shown in the UI
    #: without ever serialising a credential alongside it.
    credentials: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    #: Cron expression. Null = manual trigger only.
    schedule: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: FULL | INCREMENTAL | CHANGE_ONLY
    import_mode: Mapped[str] = mapped_column(
        String(16), default="FULL", server_default="FULL", nullable=False
    )
    #: Overrides for the adapter's field mapping, when a deployment has renamed
    #: columns. Empty means "use the adapter's own mapping".
    field_mapping: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )

    # --- Health -------------------------------------------------------------
    #: UNKNOWN | HEALTHY | DEGRADED | UNREACHABLE
    health_status: Mapped[str] = mapped_column(
        String(16), default="UNKNOWN", server_default="UNKNOWN", nullable=False, index=True
    )
    health_detail: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_import_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_import_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Running totals, so the connector list answers "is this working?" without
    #: opening the import history.
    total_imports: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    failed_imports: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    total_records_imported: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    imports: Mapped[list[ConnectorImport]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class ConnectorImport(Base):
    """One execution of a connector."""

    __tablename__ = "connector_imports"
    __table_args__ = (Index("ix_connector_imports_source_time", "source_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("source_systems.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trigger: Mapped[str] = mapped_column(
        String(16), default="MANUAL", server_default="MANUAL", nullable=False
    )
    import_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    records_read: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    records_mapped: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    #: Change detection against what is already in the canonical store — the
    #: difference between "vendor sent 4000 rules" and "4 of them changed".
    rules_created: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    rules_updated: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    rules_unchanged: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    rules_deleted: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    records_rejected: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    #: Rejected records with their reasons, for the connector error log.
    errors: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    summary: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    triggered_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source: Mapped[SourceSystem] = relationship(back_populates="imports")
