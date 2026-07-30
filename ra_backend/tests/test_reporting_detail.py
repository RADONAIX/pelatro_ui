"""Tests for the report drill-down (last-N, server-side filtered).

DB-free: ``service._run`` is stubbed to capture the SQL/params the service
builds, so we assert the query shape (last-N-by-date ordering, the inclusive
dateTo → exclusive upper bound, and that filters are pushed into the query)
without touching ClickHouse / Postgres.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.config import settings
from app.modules.reporting import service


def _capture(monkeypatch) -> list[tuple[str, str, dict]]:
    """Capture the (source, sql, params) of every query group the service runs,
    without touching a database."""
    captured: list[tuple[str, str, dict]] = []

    async def fake_run(group):
        captured.append((group.source, group.sql, group.params or {}))
        return []

    monkeypatch.setattr(service, "_run_group", fake_run)
    return captured


def _first_available(with_date: bool = False):
    for r in service.REPORTS:
        if r.get("available") and (not with_date or r.get("date_column")):
            return r
    raise AssertionError("no available report in the catalog")


@pytest.mark.asyncio
async def test_unfiltered_preview_is_last_n_by_date(monkeypatch):
    captured = _capture(monkeypatch)
    report = _first_available(with_date=True)

    await service.report_detail(report["key"])

    preview = [sql for _, sql, _ in captured if "LIMIT" in sql]
    assert preview, "expected a LIMIT-bounded preview query"
    sql = preview[0]
    assert f'ORDER BY _p."{report["date_column"]}" DESC' in sql
    assert f"LIMIT {settings.reports_detail_limit}" in sql


@pytest.mark.asyncio
async def test_filtered_preview_shifts_dateto_and_pushes_filters(monkeypatch):
    captured = _capture(monkeypatch)
    report = _first_available(with_date=True)

    await service.report_detail(
        report["key"],
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 31),   # inclusive
        search="needle",
    )

    preview = [(sql, params) for _, sql, params in captured if "LIMIT" in sql]
    assert preview, "expected a filtered preview query"
    sql, params = preview[0]
    # dateTo is inclusive → the query upper bound is the next day (exclusive),
    # matching the export worker so preview and export cover the same rows.
    assert any(str(v) == "2026-02-01" for v in params.values()), params
    assert any(str(v) == "2026-01-01" for v in params.values()), params
    # the global search became an ILIKE predicate bound as a parameter
    assert "ILIKE" in sql.upper()
    assert any("needle" in str(v) for v in params.values()), params
