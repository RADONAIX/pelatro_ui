"""Exports routes: bulk async report downloads (/exports)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.deps import DbSession, PageParams, Principal, require
from app.core.rbac import AuditModule, PermKey, RoleSlug
from app.modules.exports import schemas, service
from app.modules.identity import service as identity_service


def _require_exports_enabled() -> None:
    """Gate the whole module: 503 when bulk exports are turned off (no Redis/worker)."""
    if not settings.exports_enabled:
        raise service.ExportsDisabledError("Bulk exports are disabled on this deployment.")


router = APIRouter(
    prefix="/exports", tags=["exports"], dependencies=[Depends(_require_exports_enabled)]
)


@router.post("", response_model=schemas.ExportJobRow, status_code=201)
async def create_export(
    payload: schemas.ExportJobCreate,
    db: DbSession,
    principal: Principal = Depends(require(PermKey.EXPORTS, "edit")),
) -> schemas.ExportJobRow:
    row = await service.create_export_job(db, payload, requester_id=principal.id)
    await identity_service.record_audit(
        db,
        actor=principal.email,
        actor_id=principal.id,
        action="Requested export",
        module=AuditModule.EXPORTS,
        target=payload.reportKey,
        meta={
            "reference": row.reference,
            "dateFrom": payload.dateFrom.isoformat() if payload.dateFrom else None,
            "dateTo": payload.dateTo.isoformat() if payload.dateTo else None,
            "filters": payload.filters.model_dump(),
        },
    )
    return row


@router.get("", response_model=list[schemas.ExportJobRow])
async def list_exports(
    db: DbSession,
    page: PageParams,
    principal: Principal = Depends(require(PermKey.EXPORTS, "view")),
) -> list[schemas.ExportJobRow]:
    # Admins see every job; everyone else sees only their own.
    return await service.list_export_jobs(
        db,
        requester_id=principal.id,
        all_jobs=(principal.role == RoleSlug.ADMIN),
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/kpis", response_model=schemas.KpiPreviewResponse)
async def kpi_preview(
    payload: schemas.KpiPreviewRequest,
    _: Principal = Depends(require(PermKey.EXPORTS, "view")),
) -> schemas.KpiPreviewResponse:
    return await service.preview_kpis(payload)


@router.get("/{job_id}", response_model=schemas.ExportJobDetail)
async def get_export(
    job_id: str,
    db: DbSession,
    _: Principal = Depends(require(PermKey.EXPORTS, "view")),
) -> schemas.ExportJobDetail:
    return await service.get_export_job(db, job_id)


@router.get("/{job_id}/download")
async def download_export(
    job_id: str,
    db: DbSession,
    _: Principal = Depends(require(PermKey.EXPORTS, "view")),
) -> Response:
    # Large artifacts (tens of GB) are handed to nginx via X-Accel-Redirect so it
    # serves them directly with native Range/resume + sendfile. The empty 200 body
    # is replaced by nginx with the file at the internal location; we only carry the
    # download filename. Content-Length/Content-Range are set by nginx.
    if settings.export_xaccel_enabled:
        filename, xaccel_path = await service.get_export_download_xaccel(db, job_id)
        return Response(
            media_type=_media_type(filename),
            headers={
                "X-Accel-Redirect": xaccel_path,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
    # Fallback (local dev / no nginx): stream the file through FastAPI.
    filename, body = await service.get_export_download(db, job_id)
    return StreamingResponse(
        body,
        media_type=_media_type(filename),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _media_type(filename: str) -> str:
    return "application/zip" if filename.endswith(".zip") else "application/gzip"


@router.post("/{job_id}/cancel", response_model=schemas.ExportJobRow)
async def cancel_export(
    job_id: str,
    db: DbSession,
    _: Principal = Depends(require(PermKey.EXPORTS, "edit")),
) -> schemas.ExportJobRow:
    """Stop a running/queued export (marks it Cancelled; row is kept)."""
    return await service.cancel_export_job(db, job_id)


@router.delete("/{job_id}", status_code=204)
async def delete_export(
    job_id: str,
    db: DbSession,
    _: Principal = Depends(require(PermKey.EXPORTS, "edit")),
) -> None:
    """Permanently remove a finished export (row + file) from the Download Center."""
    await service.delete_export_job(db, job_id)
