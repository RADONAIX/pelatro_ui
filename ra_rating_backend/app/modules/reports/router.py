"""Reports API: catalogue, generation, and download of stored artefacts."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.errors import NotFoundError
from app.core.rbac import RatingPermKey
from app.modules.reports import service as svc
from app.modules.reports.models import GeneratedReport
from app.modules.reports.registry import REPORTS

router = APIRouter(prefix="/reports", tags=["reports"])

# Reports read the same data the dashboard does, so they share its permission.
_view = require(RatingPermKey.DASHBOARD, "view")
ReportRunner = principal_with(RatingPermKey.DASHBOARD, "view")


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ReportInfo(BaseModel):
    code: str
    name: str
    description: str
    category: str


class GeneratedRead(Base):
    id: str
    report_code: str
    report_name: str
    params: dict
    format: str
    row_count: int
    filename: str
    generated_by_name: str | None
    created_at: datetime


class GenerateRequest(BaseModel):
    format: str = "csv"
    date_from: date | None = None
    date_to: date | None = None
    run_id: str | None = None


@router.get(
    "",
    response_model=list[ReportInfo],
    summary="The report catalogue",
    dependencies=[Depends(_view)],
)
async def catalogue() -> list[ReportInfo]:
    return [
        ReportInfo(
            code=s.code, name=s.name, description=s.description, category=s.category
        )
        for s in REPORTS.values()
    ]


@router.get(
    "/generated",
    response_model=list[GeneratedRead],
    summary="Previously generated reports, newest first",
    dependencies=[Depends(_view)],
)
async def generated(
    db: DbSession,
    page: PageParams,
    report_code: str | None = Query(None),
) -> list[GeneratedReport]:
    stmt = select(GeneratedReport)
    if report_code:
        stmt = stmt.where(GeneratedReport.report_code == report_code)
    stmt = (
        stmt.order_by(GeneratedReport.created_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get(
    "/generated/{report_id}/download",
    summary="Download a stored report artefact, byte-identical every time",
    dependencies=[Depends(_view)],
)
async def download(db: DbSession, report_id: str) -> Response:
    row = await db.get(GeneratedReport, report_id)
    if row is None:
        raise NotFoundError(f"Report '{report_id}' was not found.")
    return Response(
        content=row.content,
        media_type=row.content_type,
        headers={"Content-Disposition": f'attachment; filename="{row.filename}"'},
    )


@router.get(
    "/{code}/data",
    summary="Report rows as JSON, for the interactive drill-down table",
    dependencies=[Depends(_view)],
)
async def report_data(
    db: DbSession,
    code: str,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    run_id: str | None = Query(None),
    search: str | None = Query(None, max_length=200),
    limit: int = Query(1000, ge=1, le=10_000),
) -> dict:
    spec = svc.get_spec(code)
    headings, rows = await spec.query(
        db, date_from=date_from, date_to=date_to, run_id=run_id
    )
    total = len(rows)
    if search and search.strip():
        needle = search.strip().lower()
        rows = [r for r in rows if any(needle in str(v).lower() for v in r)]
    matched = len(rows)
    return {
        "code": spec.code,
        "name": spec.name,
        "columns": headings,
        "rows": rows[:limit],
        "count": matched,
        "total": total,
    }


@router.post(
    "/{code}/generate",
    response_model=GeneratedRead,
    summary="Run a report over a window and store the artefact",
)
async def generate(
    db: DbSession,
    code: str,
    payload: GenerateRequest,
    principal: ReportRunner,
) -> GeneratedReport:
    return await svc.generate(
        db,
        code=code,
        fmt=payload.format,
        date_from=payload.date_from,
        date_to=payload.date_to,
        run_id=payload.run_id,
        actor_id=principal.id,
        actor_name=principal.full_name,
    )
