"""Exports business logic: create/track/download bulk export jobs + KPI preview."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.core.storage import get_storage
from app.modules.exports import planning, schemas
from app.modules.exports.models import ExportJob, ExportJobPart
from app.modules.identity.models import User
from app.modules.reporting import service as reporting

log = get_logger("exports")

_ACTIVE_STATUSES = ("Queued", "Running")


def _part_file(reference: str, part_index: int) -> str:
    """Part filename — must match app.workers.tasks._part_key."""
    return f"{reference}.part{part_index:03d}.csv.gz"


def _final_key(job: ExportJob) -> str:
    """The finished artifact's storage key — .zip (split CSVs) or .csv.gz,
    per the job's file_format (set by the worker on completion)."""
    return f"{job.reference}.{job.file_format or 'csv.gz'}"


def _artifact_keys(job: ExportJob) -> list[str]:
    """Every on-disk file a job may own: the final artifact plus, for a multi-part
    job, each part file (parts are normally removed by finalize, but a failed /
    cancelled job can leave them behind)."""
    keys = [_final_key(job)]
    if job.part_count:
        keys += [_part_file(job.reference, i) for i in range(job.part_count)]
    return keys


def _delete_artifacts(job: ExportJob) -> None:
    storage = get_storage()
    for key in _artifact_keys(job):
        try:
            storage.delete(key)
        except Exception as exc:  # noqa: BLE001 — best-effort file cleanup
            log.warning("export_delete_file_failed", key=key, error=str(exc))


# Rough gzip level-1 ratio for RA CSV rows; multipart briefly holds the part files
# AND the concatenated output, so its on-disk peak is ~2x the gzipped total.
_GZIP_RATIO = 5
_GB = 1024**3


def _estimated_peak_bytes(est_rows: int | None, *, multipart: bool) -> int:
    """Rough on-disk peak for an export of ``est_rows``. 0 when the size is unknown
    (the planner already caps un-sizable jobs, so we fall back to the free floor)."""
    if not est_rows:
        return 0
    gzipped = est_rows * settings.export_bytes_per_row_estimate // _GZIP_RATIO
    return gzipped * (2 if multipart else 1)


async def _check_capacity(
    db: AsyncSession, *, est_rows: int | None = None, multipart: bool = False
) -> None:
    """Refuse a new export when the volume can't hold it or the live-artifact
    footprint is at its cap — so a job fails fast at request time instead of
    mid-write after hours of streaming. Sized from the planner's row estimate."""
    os.makedirs(settings.reports_dir, exist_ok=True)
    free = shutil.disk_usage(settings.reports_dir).free
    # Keep the free floor AVAILABLE after this export's estimated peak.
    needed = settings.export_min_free_bytes + _estimated_peak_bytes(est_rows, multipart=multipart)
    if free < needed:
        raise ValidationFailedError(
            f"Not enough free disk for this export (~{needed // _GB} GB required, "
            f"{free // _GB} GB free). Narrow the filters/date range or free up space."
        )
    used = (
        await db.execute(select(func.coalesce(func.sum(ExportJob.file_size_bytes), 0)))
    ).scalar_one()
    if used and used >= settings.export_max_total_storage_gb * _GB:
        raise ValidationFailedError(
            "Export storage is full; wait for older exports to expire before requesting more."
        )


class ExportsDisabledError(AppError):
    status_code = 503
    code = "exports_disabled"


class TooManyJobsError(AppError):
    status_code = 429
    code = "too_many_jobs"


class ExportNotReadyError(AppError):
    status_code = 409
    code = "export_not_ready"


class ExportExpiredError(AppError):
    status_code = 410
    code = "export_expired"


def _reference() -> str:
    return f"EXP-{uuid.uuid4().hex[:10].upper()}"


def _to_row(job: ExportJob, requester_name: str | None = None) -> schemas.ExportJobRow:
    return schemas.ExportJobRow(
        id=job.id,
        reference=job.reference,
        reportKey=job.report_key,
        status=job.status,
        progressPct=job.progress_pct,
        processedRows=job.processed_rows,
        totalRows=job.total_rows,
        fileSizeBytes=job.file_size_bytes,
        # Prefer the resolved human name; fall back to the raw id if unresolved.
        requestedBy=requester_name or job.requested_by,
        createdAt=job.created_at,
        startedAt=job.started_at,
        completedAt=job.completed_at,
        expiresAt=job.expires_at,
        error=job.error,
        params=job.params or {},
    )


def _to_detail(job: ExportJob, requester_name: str | None = None) -> schemas.ExportJobDetail:
    return schemas.ExportJobDetail(
        **_to_row(job, requester_name).model_dump(),
        kpis=job.kpis,
        checksumSha256=job.checksum_sha256,
        fileFormat=job.file_format,
    )


async def _requester_names(db: AsyncSession, jobs: list[ExportJob]) -> dict[str, str]:
    """Map requester ids -> display name (full name, or email) in one query."""
    ids = {j.requested_by for j in jobs if j.requested_by}
    if not ids:
        return {}
    rows = (
        await db.execute(select(User.id, User.full_name, User.email).where(User.id.in_(ids)))
    ).all()
    return {r.id: (r.full_name or r.email) for r in rows}


def _validate_export_request(report: dict, payload: schemas.ExportJobCreate) -> None:
    """Validate the date range (when supplied) and whitelist filter columns.
    Raises ValidationFailedError on any problem."""
    # Dates are optional (omitted = whole report). Only when both are supplied do
    # we require a date column + enforce ordering and the max span.
    if payload.dateFrom is not None and payload.dateTo is not None:
        if not report.get("date_column"):
            raise ValidationFailedError("This report does not support date-range export.")
        if payload.dateFrom > payload.dateTo:
            raise ValidationFailedError("dateFrom must be on or before dateTo.")
        span = (payload.dateTo - payload.dateFrom).days + 1
        if span > settings.export_max_date_span_days:
            raise ValidationFailedError(
                f"Date span {span}d exceeds the maximum of {settings.export_max_date_span_days}d."
            )
    # Whitelist filter columns against the report's known columns (injection guard).
    cols = reporting.report_columns(payload.reportKey)
    unknown = [c for c in payload.filters.categories if c not in cols]
    if payload.filters.dateColumn and payload.filters.dateColumn not in cols:
        unknown.append(payload.filters.dateColumn)
    if unknown:
        raise ValidationFailedError(f"Unknown filter column(s): {', '.join(unknown)}.")


async def create_export_job(
    db: AsyncSession, payload: schemas.ExportJobCreate, *, requester_id: str
) -> schemas.ExportJobRow:
    report = reporting.get_report(payload.reportKey)
    if report is None or not report.get("available"):
        raise NotFoundError(f"Unknown report '{payload.reportKey}'.")
    _validate_export_request(report, payload)

    active = (
        await db.execute(
            select(func.count(ExportJob.id)).where(
                ExportJob.requested_by == requester_id,
                ExportJob.status.in_(_ACTIVE_STATUSES),
            )
        )
    ).scalar_one()
    if active >= settings.export_max_concurrent_per_user:
        raise TooManyJobsError(
            f"You already have {active} export(s) running. Wait for one to finish."
        )

    # Size the export and decide single-file vs parallel parts vs reject. Large
    # exports fan out so no single task runs near the visibility_timeout ceiling;
    # an un-partitionable giant is rejected (fail fast) rather than run for hours.
    plan = await planning.plan_export(
        payload.reportKey,
        date_from=payload.dateFrom,
        date_to=payload.dateTo,
        categories=payload.filters.categories or None,
        search=payload.filters.search,
    )
    if plan.rejected:
        raise ValidationFailedError(plan.reason)
    multipart = plan.mode == "multipart"
    # Disk pre-flight now that we know the estimated size + mode.
    await _check_capacity(db, est_rows=plan.est_total_rows, multipart=multipart)

    job = ExportJob(
        reference=_reference(),
        report_key=payload.reportKey,
        status="Queued",
        params={
            "date_from": payload.dateFrom.isoformat() if payload.dateFrom else None,
            "date_to": payload.dateTo.isoformat() if payload.dateTo else None,
            "filters": payload.filters.model_dump(),
        },
        requested_by=requester_id,
        total_rows=plan.est_total_rows,
        part_count=len(plan.parts) if multipart else None,
        # Excel-friendly .zip of split CSVs when enabled, else a single .csv.gz.
        # The worker re-affirms this on completion.
        file_format="zip" if settings.export_csv_split_rows > 0 else "csv.gz",
    )
    db.add(job)
    await db.flush()  # assigns job.id
    if multipart:
        for p in plan.parts:
            db.add(
                ExportJobPart(
                    job_id=job.id, part_index=p.index,
                    date_from=p.date_from, date_to=p.date_to, status="Queued",
                )
            )
    await db.refresh(job)
    # Commit before enqueuing so the worker(s) can read the rows (avoids a race).
    await db.commit()

    try:
        if multipart:
            from celery import chord

            from app.workers.tasks import finalize_export, run_export_part

            header = [run_export_part.s(job.id, p.index) for p in plan.parts]
            result = chord(header)(finalize_export.s(job.id))
            job.celery_task_id = result.id  # the finalize (assembly) task
        else:
            from app.workers.tasks import run_export

            result = run_export.delay(job.id)
            job.celery_task_id = result.id
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — broker unreachable: surface as Failed
        log.error("export_enqueue_failed", job_id=job.id, error=str(exc))
        job.status = "Failed"
        job.error = "Could not queue the export (job broker unavailable)."
        await db.commit()
    await db.refresh(job)
    return _to_row(job)


async def list_export_jobs(
    db: AsyncSession, *, requester_id: str, all_jobs: bool, limit: int, offset: int
) -> list[schemas.ExportJobRow]:
    stmt = select(ExportJob).order_by(ExportJob.created_at.desc())
    if not all_jobs:
        stmt = stmt.where(ExportJob.requested_by == requester_id)
    rows = list((await db.execute(stmt.limit(limit).offset(offset))).scalars().all())
    names = await _requester_names(db, rows)
    return [_to_row(j, names.get(j.requested_by or "")) for j in rows]


async def _get(db: AsyncSession, job_id: str) -> ExportJob:
    job = (
        await db.execute(select(ExportJob).where(ExportJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise NotFoundError("Export job not found.")
    return job


async def get_export_job(db: AsyncSession, job_id: str) -> schemas.ExportJobDetail:
    job = await _get(db, job_id)
    names = await _requester_names(db, [job])
    return _to_detail(job, names.get(job.requested_by or ""))


async def _download_ready(db: AsyncSession, job_id: str) -> ExportJob:
    """Validate a job is downloadable (Completed, not expired, file present)."""
    job = await _get(db, job_id)
    if job.status != "Completed" or not job.file_path:
        raise ExportNotReadyError(f"Export is '{job.status}', not ready for download.")
    if job.expires_at and job.expires_at < datetime.now(UTC):
        raise ExportExpiredError("This export has expired and is no longer available.")
    if not get_storage().exists(_final_key(job)):
        raise ExportExpiredError("The export file is no longer available.")
    return job


async def get_export_download(db: AsyncSession, job_id: str) -> tuple[str, Iterator[bytes]]:
    """FastAPI-streamed download (fallback when X-Accel offload is disabled)."""
    job = await _download_ready(db, job_id)
    storage = get_storage()
    key = _final_key(job)

    def _iter() -> Iterator[bytes]:
        with storage.open_read(key) as fh:
            yield from iter(lambda: fh.read(1 << 16), b"")

    return key, _iter()


async def get_export_download_xaccel(db: AsyncSession, job_id: str) -> tuple[str, str]:
    """Authorize the download, then hand off to nginx: returns (filename,
    X-Accel-Redirect path). nginx serves the file directly with native
    Range/resume + sendfile, so multi-GB downloads are resumable and bypass the
    proxy read timeout."""
    job = await _download_ready(db, job_id)
    filename = _final_key(job)
    return filename, f"{settings.export_xaccel_location}/{filename}"


async def _revoke_job_tasks(db: AsyncSession, job: ExportJob) -> None:
    """Best-effort revoke of a job's Celery task(s): the main/finalize task and,
    for a multi-part job, every started part task."""
    task_ids = [job.celery_task_id] if job.celery_task_id else []
    if job.part_count:
        task_ids += list(
            (
                await db.execute(
                    select(ExportJobPart.celery_task_id).where(
                        ExportJobPart.job_id == job.id,
                        ExportJobPart.celery_task_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    if not task_ids:
        return
    try:
        from app.workers.celery_app import celery

        celery.control.revoke(task_ids, terminate=True)
    except Exception as exc:  # noqa: BLE001 — best-effort; status is already Cancelled
        log.warning("export_revoke_failed", job_id=job.id, error=str(exc))


async def cancel_export_job(db: AsyncSession, job_id: str) -> schemas.ExportJobRow:
    job = await _get(db, job_id)
    if job.status in ("Completed", "Failed", "Cancelled"):
        # Terminal: just drop the file(s) and report current state.
        _delete_artifacts(job)
        if job.status == "Completed":
            job.status = "Cancelled"
            await db.flush()
        return _to_row(job)

    # Flag first: in-flight parts poll the parent status per chunk and self-cancel;
    # any not-yet-started part short-circuits when it starts.
    job.status = "Cancelled"
    await db.flush()
    await _revoke_job_tasks(db, job)
    _delete_artifacts(job)
    return _to_row(job)


async def delete_export_job(db: AsyncSession, job_id: str) -> None:
    """Hard-delete a finished job (row + files) so it no longer appears in the
    Download Center. Active jobs must be cancelled first. Part rows cascade."""
    job = await _get(db, job_id)
    if job.status in _ACTIVE_STATUSES:
        raise ExportNotReadyError("Cancel the export before deleting it.")
    _delete_artifacts(job)
    await db.delete(job)
    await db.flush()


async def purge_expired(db: AsyncSession) -> int:
    """Delete export jobs past their expires_at and remove their files. Returns
    the number of jobs purged. Safe to run repeatedly (startup + periodically)."""
    now = datetime.now(UTC)
    jobs = (
        (await db.execute(select(ExportJob).where(ExportJob.expires_at < now))).scalars().all()
    )
    if not jobs:
        return 0
    for job in jobs:
        _delete_artifacts(job)  # final + any orphan part files
        await db.delete(job)  # part rows cascade (FK ondelete=CASCADE)
    await db.commit()
    log.info("exports_purged", count=len(jobs))
    return len(jobs)


async def preview_kpis(payload: schemas.KpiPreviewRequest) -> schemas.KpiPreviewResponse:
    report = reporting.get_report(payload.reportKey)
    if report is None or not report.get("available"):
        raise NotFoundError(f"Unknown report '{payload.reportKey}'.")
    if payload.dateFrom is not None and payload.dateTo is not None:
        if not report.get("date_column"):
            raise ValidationFailedError("This report does not support date-range KPIs.")
        if payload.dateFrom > payload.dateTo:
            raise ValidationFailedError("dateFrom must be on or before dateTo.")
    kpis = await reporting.report_kpis(
        payload.reportKey,
        date_from=payload.dateFrom,
        date_to=payload.dateTo + timedelta(days=1) if payload.dateTo else None,  # inclusive end
        categories=payload.filters.categories or None,
        search=payload.filters.search,
    )
    return schemas.KpiPreviewResponse(
        reportKey=payload.reportKey, dateFrom=payload.dateFrom, dateTo=payload.dateTo, kpis=kpis
    )


