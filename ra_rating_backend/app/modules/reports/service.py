"""Run a registered report and render it to a stored artefact."""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.imports.parser import EXCEL_AVAILABLE
from app.modules.reports.models import GeneratedReport
from app.modules.reports.registry import REPORTS, ReportSpec


def get_spec(code: str) -> ReportSpec:
    spec = REPORTS.get(code)
    if spec is None:
        raise NotFoundError(
            f"Unknown report '{code}'.", details={"known": sorted(REPORTS)}
        )
    return spec


def _render_csv(headings: list[str], rows: list[list[Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headings)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")  # BOM so Excel opens it as UTF-8


def _render_xlsx(headings: list[str], rows: list[list[Any]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(headings)
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


async def generate(
    db: AsyncSession,
    *,
    code: str,
    fmt: str,
    date_from: date | None,
    date_to: date | None,
    run_id: str | None,
    actor_id: str | None,
    actor_name: str | None,
) -> GeneratedReport:
    spec = get_spec(code)
    fmt = fmt.lower()
    if fmt not in ("csv", "xlsx"):
        raise NotFoundError(f"Unsupported format '{fmt}'. Use csv or xlsx.")
    if fmt == "xlsx" and not EXCEL_AVAILABLE:
        fmt = "csv"

    headings, rows = await spec.query(
        db, date_from=date_from, date_to=date_to, run_id=run_id
    )

    if fmt == "xlsx":
        content = _render_xlsx(headings, rows)
        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        content = _render_csv(headings, rows)
        content_type = "text/csv; charset=utf-8"

    stamp = date.today().isoformat()
    report = GeneratedReport(
        report_code=spec.code,
        report_name=spec.name,
        params={
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "run_id": run_id,
        },
        format=fmt,
        row_count=len(rows),
        content=content,
        content_type=content_type,
        filename=f"{spec.code}_{stamp}.{fmt}",
        generated_by=actor_id,
        generated_by_name=actor_name,
    )
    db.add(report)
    await db.flush()
    return report
