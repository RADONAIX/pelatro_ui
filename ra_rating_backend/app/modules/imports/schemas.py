"""Schemas for the file-import API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ColumnInfo(BaseModel):
    key: str
    label: str
    kind: str
    required: bool
    description: str
    aliases: list[str] = Field(default_factory=list)


class RowIssue(BaseModel):
    row_number: int
    column: str = ""
    message: str


class PreviewRow(BaseModel):
    row_number: int
    raw: dict[str, str]
    #: The canonical rule this row would create; null when the row is rejected.
    canonical: dict[str, Any] | None = None
    status: str
    errors: list[RowIssue] = Field(default_factory=list)


class PreviewResponse(BaseModel):
    filename: str
    #: Headings found in the file, in file order.
    headings: list[str]
    #: heading -> canonical column key (null where nothing matched).
    mapping: dict[str, str | None]
    unmapped_headings: list[str]
    #: Canonical columns the importer needs that the mapping does not supply.
    missing_required: list[str]
    total_rows: int
    valid_rows: int
    rejected_rows: int
    #: A bounded sample — enough to judge the mapping without shipping 50k rows.
    rows: list[PreviewRow]
    truncated: bool


class CommitResponse(Base):
    id: str
    filename: str
    status: str
    total_rows: int
    valid_rows: int
    imported_rows: int
    rejected_rows: int
    summary: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None


class BatchSummary(Base):
    id: str
    filename: str
    source_system: str
    status: str
    total_rows: int
    valid_rows: int
    imported_rows: int
    rejected_rows: int
    created_by_name: str | None
    created_at: datetime
    completed_at: datetime | None


class BatchRow(Base):
    id: str
    row_number: int
    raw: dict[str, Any]
    canonical: dict[str, Any] | None
    status: str
    errors: list[Any]
    rule_id: str | None


class BatchDetail(BatchSummary):
    mapping: dict[str, str | None]
    summary: dict[str, Any]
    rows: list[BatchRow] = Field(default_factory=list)


class TemplateCreate(Base):
    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_.-]{0,63}$")
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    source_vendor: str = ""
    mapping: dict[str, str | None]


class TemplateRead(Base):
    id: str
    code: str
    name: str
    description: str
    source_vendor: str
    mapping: dict[str, str | None]
    created_by: str | None
    created_at: datetime
    updated_at: datetime
