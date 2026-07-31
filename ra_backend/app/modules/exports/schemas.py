"""Exports API schemas (camelCase, mirroring the UI)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class ExportFilters(BaseModel):
    """Filters applied server-side over the FULL report (not the on-screen sample)."""

    search: str | None = None
    # column name -> selected values (OR within a column, AND across columns)
    categories: dict[str, list[str]] = Field(default_factory=dict)
    # optional date column override; defaults to the report's own date_column
    dateColumn: str | None = None


class ExportJobCreate(BaseModel):
    reportKey: str = Field(min_length=1)
    # Optional: omitted = whole report (all dates). When both are present the
    # server enforces export_max_date_span_days.
    dateFrom: date | None = None
    dateTo: date | None = None
    filters: ExportFilters = Field(default_factory=ExportFilters)


class ExportJobRow(BaseModel):
    id: str
    reference: str
    reportKey: str
    status: str
    progressPct: int
    processedRows: int
    totalRows: int | None = None
    fileSizeBytes: int | None = None
    requestedBy: str | None = None
    createdAt: datetime
    startedAt: datetime | None = None
    completedAt: datetime | None = None
    expiresAt: datetime | None = None
    error: str | None = None
    # {date_from, date_to, filters:{search, categories, dateColumn}} — lets the
    # Download Center show which filters produced each export.
    params: dict[str, Any] = Field(default_factory=dict)


class ExportJobDetail(ExportJobRow):
    kpis: dict[str, Any] | None = None
    checksumSha256: str | None = None
    fileFormat: str = "csv.gz"


class KpiPreviewRequest(BaseModel):
    reportKey: str = Field(min_length=1)
    dateFrom: date | None = None
    dateTo: date | None = None
    filters: ExportFilters = Field(default_factory=ExportFilters)


class KpiPreviewResponse(BaseModel):
    reportKey: str
    dateFrom: date | None = None
    dateTo: date | None = None
    kpis: dict[str, Any] | None = None
