"""Pydantic schemas for the reporting module (RA report catalog)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class ReportDetail(BaseModel):
    """A report's drill-down as a generic table (heterogeneous reports).

    ``rows`` is the last ``reports_detail_limit`` rows of the filtered set,
    newest first; ``count`` is the filtered total (or the full total when no
    filters are applied), or None when it couldn't be sized in time."""

    key: str
    title: str
    count: int | None = None
    columns: list[str]
    rows: list[list]
    # Set when the drill-down couldn't be produced (source unreachable, or the set
    # is too large to sort/preview). The UI shows it instead of an empty table.
    note: str | None = None


class ReportFilters(BaseModel):
    """Filters applied server-side over the FULL report (mirrors ExportFilters)."""

    search: str | None = None
    # column name -> selected values (OR within a column, AND across columns)
    categories: dict[str, list[str]] = Field(default_factory=dict)
    # optional date column override; defaults to the report's own date_column
    dateColumn: str | None = None


class ReportDetailRequest(BaseModel):
    """Drill-down request: date range (inclusive) + filters. Same shape as the
    export/KPI requests so the preview and the export apply identical filters."""

    dateFrom: date | None = None
    dateTo: date | None = None
    filters: ReportFilters = Field(default_factory=ReportFilters)
