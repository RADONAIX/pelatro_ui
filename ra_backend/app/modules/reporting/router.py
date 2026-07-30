"""Reporting routes: the RA report catalog (/reports)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.core.deps import DbSession, Principal, require
from app.core.rbac import AuditModule, PermKey
from app.modules.identity import service as identity_service
from app.modules.reporting import schemas, service

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/{key}", response_model=schemas.ReportDetail)
async def detail(
    key: str,
    _: Principal = Depends(require(PermKey.REPORTS, "view")),
) -> schemas.ReportDetail:
    """Unfiltered drill-down: the last N rows of the whole report (newest first)."""
    return await service.report_detail(key)


@router.post("/{key}/detail", response_model=schemas.ReportDetail)
async def detail_filtered(
    key: str,
    payload: schemas.ReportDetailRequest,
    _: Principal = Depends(require(PermKey.REPORTS, "view")),
) -> schemas.ReportDetail:
    """Filtered drill-down: the last N rows matching the applied filters, fetched
    server-side (same filter path as the bulk export, so the two stay in sync)."""
    return await service.report_detail(
        key,
        date_from=payload.dateFrom,
        date_to=payload.dateTo,
        categories=payload.filters.categories or None,
        search=payload.filters.search,
    )


@router.get("/{key}/export")
async def export(
    key: str,
    db: DbSession,
    principal: Principal = Depends(require(PermKey.REPORTS, "view")),
) -> Response:
    filename, csv_text = await service.report_export_csv(key)
    # Audit sensitive-data egress: who exported which report. Committed by the
    # request's session (get_session) on success.
    await identity_service.record_audit(
        db,
        actor=principal.email,
        actor_id=principal.id,
        action="Exported report",
        module=AuditModule.REPORTS,
        target=key,
        meta={"format": "csv", "rows": csv_text.count("\n")},
    )
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
