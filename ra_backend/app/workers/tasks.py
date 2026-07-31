"""Celery tasks.

run_export: streams a report (optionally date-filtered) to a gzipped CSV on the
configured storage, updating progress on the ExportJob row chunk-by-chunk, then
records size/checksum/KPIs and marks the job Completed. All DB access is sync
(SyncSession); report data is read via the sync streaming readers.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.logging import get_logger
from app.core.storage import Storage, get_storage
from app.modules.exports.models import ExportJob, ExportJobPart
from app.modules.reporting import service as reporting
from app.workers import streaming
from app.workers.celery_app import celery
from app.workers.db import SyncSession
from app.workers.zipcsv import ZipCsvWriter

log = get_logger("worker")


class _Cancelled(Exception):
    """Raised inside the stream loop when the job was cancelled by the user."""


class _HashingWriter:
    """Wrap a binary file handle so every byte written is also fed to a hasher.
    Lets us compute the file's SHA-256 during the single write pass instead of
    re-reading the whole (20-40 GB) artifact afterwards."""

    def __init__(self, fh: Any, digest: Any) -> None:
        self._fh = fh
        self._digest = digest

    def write(self, b: bytes) -> int:
        self._digest.update(b)
        return self._fh.write(b)

    def __getattr__(self, name: str) -> Any:  # flush/close/tell → underlying handle
        return getattr(self._fh, name)


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def _parse_date(v: Any) -> date | None:
    if not v:
        return None
    return date.fromisoformat(v) if isinstance(v, str) else v


def _is_cancelled(session, job_id: str) -> bool:
    status = session.execute(
        select(ExportJob.status).where(ExportJob.id == job_id)
    ).scalar_one_or_none()
    return status == "Cancelled"


def _artifact_ext() -> str:
    """Output extension: a .zip of split CSVs when EXPORT_CSV_SPLIT_ROWS is set,
    else the single gzipped CSV."""
    return "zip" if settings.export_csv_split_rows > 0 else "csv.gz"


def _sha256_of(storage: Storage, key: str) -> str:
    """SHA-256 by reading the finished artifact (used for the .zip path, whose
    writer seeks, so it can't be hashed incrementally as it's written)."""
    digest = hashlib.sha256()
    with storage.open_read(key) as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_report_gzip(
    storage: Storage,
    key: str,
    *,
    groups: list,
    write_header: bool,
    should_cancel,
    on_progress,
    compute_hash: bool,
) -> tuple[int, str | None]:
    """Stream a report query to a gzipped CSV at ``key``. Shared by the single-file
    export and each multi-part sub-range. Returns (rows_written, sha256_or_None).

    ``groups`` is one query group in consolidated mode, several (one per connection)
    in split mode — streamed sequentially into the one CSV (UNION ALL is unordered
    across streams, so concatenation is equivalent). The header is written once.

    - ``write_header`` False → data-only (multi-part parts >0, so the concatenated
      file has exactly one header row).
    - ``should_cancel()`` is polled once per chunk; True → deletes the partial file
      and raises ``_Cancelled``.
    - ``on_progress(rows)`` is called once per chunk (caller throttles persistence).
    - ``compute_hash`` True → returns the file's SHA-256 (single-file path); parts
      skip it (the hash is computed once over the concatenated file at finalize)."""
    digest = hashlib.sha256() if compute_hash else None
    processed = 0
    cancelled = False
    raw = storage.open_write(key)
    try:
        sink: Any = _HashingWriter(raw, digest) if digest is not None else raw
        with (
            gzip.GzipFile(fileobj=sink, mode="wb", compresslevel=settings.export_gzip_level) as gz,
            io.TextIOWrapper(gz, encoding="utf-8", newline="") as txt,
        ):
            writer = csv.writer(txt)
            wrote_header = False
            for g in groups:
                rows = streaming.stream_rows(
                    g.source, g.sql, g.params, settings.export_chunk_rows, g.target
                )
                header = next(rows)
                if write_header and not wrote_header:
                    writer.writerow(header)
                    wrote_header = True
                for chunk in rows:
                    writer.writerows(chunk)  # csv stringifies cells (dates/decimals)
                    processed += len(chunk)
                    if should_cancel():
                        cancelled = True
                        raise _Cancelled
                    on_progress(processed)
    finally:
        raw.close()  # single close; gzip leaves the passed fileobj open
        if cancelled:
            storage.delete(key)  # drop the partial file (after it's fully closed)
    return processed, (digest.hexdigest() if digest is not None else None)


def _write_report_zip(
    storage: Storage,
    key: str,
    *,
    groups: list,
    base_name: str,
    total_rows: int | None,
    should_cancel,
    on_progress,
) -> int:
    """Stream a report query into a .zip of CSV files, each ≤ EXPORT_CSV_SPLIT_ROWS
    rows (+header) so they open in Excel. Returns rows written. The SHA-256 is
    computed by the caller from the finished file (the zip writer seeks). ``groups``
    (one per connection) are streamed sequentially into the one zip."""
    processed = 0
    cancelled = False
    raw = storage.open_write(key)
    zw: ZipCsvWriter | None = None
    try:
        for g in groups:
            rows = streaming.stream_rows(
                g.source, g.sql, g.params, settings.export_chunk_rows, g.target
            )
            header = next(rows)
            if zw is None:  # first group supplies the header
                zw = ZipCsvWriter(
                    raw, header, settings.export_csv_split_rows,
                    base_name=base_name, total_rows=total_rows,
                )
            for chunk in rows:
                zw.write_rows(chunk)
                processed = zw.total_rows
                if should_cancel():
                    cancelled = True
                    raise _Cancelled
                on_progress(processed)
        if zw is not None:
            zw.close()  # finalise the zip (central directory)
    finally:
        raw.close()
        if cancelled:
            storage.delete(key)
    return processed


def _assemble_zip(
    storage: Storage, final_key: str, part_keys: list[str],
    *, base_name: str, total_rows: int | None,
) -> int:
    """Re-stream the gzipped-CSV parts (in order) into one .zip of ≤N-row CSVs
    (Excel-friendly). Part 0 carries the header; parts >0 are data-only. Returns
    total rows. Costs a decompress→recompress pass, but guarantees clean row-count
    files across part boundaries (vs a byte-concat)."""
    raw = storage.open_write(final_key)
    zw: ZipCsvWriter | None = None
    try:
        for pk in part_keys:
            with (
                storage.open_read(pk) as fh,
                gzip.GzipFile(fileobj=fh, mode="rb") as gz,
                io.TextIOWrapper(gz, encoding="utf-8", newline="") as txt,
            ):
                reader = csv.reader(txt)
                if zw is None:  # first part supplies the header row
                    zw = ZipCsvWriter(
                        raw, next(reader, []), settings.export_csv_split_rows,
                        base_name=base_name, total_rows=total_rows,
                    )
                buf: list = []
                for row in reader:
                    buf.append(row)
                    if len(buf) >= settings.export_chunk_rows:
                        zw.write_rows(buf)
                        buf = []
                if buf:
                    zw.write_rows(buf)
        total = zw.total_rows if zw is not None else 0
    finally:
        if zw is not None:
            zw.close()
        raw.close()
    return total


def _selection(params: dict[str, Any]) -> tuple[date | None, date | None, dict | None, str | None]:
    """Extract (date_from, date_to_exclusive, categories, search) from job params.
    date_to is inclusive for the user → exclusive upper bound for the query."""
    date_from = _parse_date((params or {}).get("date_from"))
    date_to = _parse_date((params or {}).get("date_to"))
    date_to_excl = (date_to + timedelta(days=1)) if date_to else None
    _filters = (params or {}).get("filters") or {}
    return date_from, date_to_excl, (_filters.get("categories") or None), _filters.get("search")


def _count_all_groups(groups: list) -> int | None:
    """Sum the row count across connection groups (split mode). None if any group
    can't be counted in time → the export streams with an unknown total."""
    total = 0
    for g in groups:
        n = streaming.count_with_timeout(
            g.source, g.sql, g.params, settings.export_count_timeout_seconds, g.target
        )
        if n is None:
            return None
        total += n
    return total


def _kpis_all_groups(groups: list) -> dict[str, Any]:
    """Merge the KPI aggregates across connection groups. Every kpi_agg is additive
    (count / sum / countIf), so numeric values sum; non-numerics take the first."""
    merged: dict[str, Any] = {}
    for g in groups:
        row = streaming.query_one(g.source, g.sql, g.params, g.target) or {}
        for k, v in row.items():
            if isinstance(v, bool):
                merged.setdefault(k, v)
            elif isinstance(v, int | float | Decimal):
                merged[k] = (merged.get(k) or 0) + v
            else:
                merged.setdefault(k, v)
    return merged


@celery.task(name="exports.run", bind=True, max_retries=2, default_retry_delay=30)
def run_export(self, job_id: str) -> dict:
    """Single-file export: count (source-aware, non-blocking) → stream to one
    gzipped CSV with incremental hashing and throttled progress → finalize."""
    session = SyncSession()
    storage = get_storage()
    key = ""
    try:
        job = session.get(ExportJob, job_id)
        if job is None:
            return {"ok": False, "reason": "job_not_found"}
        assert job is not None  # narrow for the on_progress closure below

        job.status = "Running"
        job.started_at = datetime.now(UTC)
        job.celery_task_id = self.request.id
        session.commit()

        report_key = job.report_key
        date_from, date_to_excl, categories, search = _selection(job.params or {})

        sel = {"date_from": date_from, "date_to": date_to_excl,
               "categories": categories, "search": search}

        # 1) total rows for % progress. Source-aware + timeout-guarded, summed
        # across connection groups (exact on ClickHouse, best-effort on Postgres →
        # None = indeterminate, not a stall).
        total = _count_all_groups(reporting.count_groups(report_key, **sel))
        job.total_rows = total
        session.commit()

        # 2) stream rows → artifact: a .zip of ≤N-row CSVs (Excel-friendly) when
        #    EXPORT_CSV_SPLIT_ROWS is set, else one gzipped CSV. Progress persisted
        #    at most every N seconds. In split mode each connection is one group.
        ext = _artifact_ext()
        key = f"{job.reference}.{ext}"
        groups = reporting.export_groups(report_key, **sel)
        last_commit = [0.0]

        def on_progress(rows: int, job: ExportJob = job) -> None:
            now = time.monotonic()
            if now - last_commit[0] < settings.export_progress_commit_seconds:
                return
            job.processed_rows = rows
            job.progress_pct = int(rows / total * 100) if total else 0
            session.commit()
            last_commit[0] = now

        if settings.export_csv_split_rows > 0:
            processed = _write_report_zip(
                storage, key, groups=groups,
                base_name=report_key, total_rows=total,
                should_cancel=lambda: _is_cancelled(session, job_id), on_progress=on_progress,
            )
            sha256 = _sha256_of(storage, key)
        else:
            processed, sha256 = _write_report_gzip(
                storage, key, groups=groups,
                write_header=True, should_cancel=lambda: _is_cancelled(session, job_id),
                on_progress=on_progress, compute_hash=True,
            )

        # 3) finalize: size + checksum + KPIs (merged across connection groups)
        kpi_row = _kpis_all_groups(reporting.kpi_groups(report_key, **sel))

        job.file_path = storage.locator(key)
        job.file_format = ext
        job.file_size_bytes = storage.size(key)
        job.checksum_sha256 = sha256
        job.kpis = {k: _jsonable(v) for k, v in kpi_row.items()}
        job.processed_rows = processed
        job.total_rows = total if total is not None else processed
        job.progress_pct = 100
        job.status = "Completed"
        job.completed_at = datetime.now(UTC)
        job.expires_at = datetime.now(UTC) + timedelta(hours=settings.export_retention_hours)
        job.error = None
        session.commit()
        log.info("export_completed", job_id=job_id, rows=processed, size=job.file_size_bytes)
        return {"ok": True, "rows": processed, "size": job.file_size_bytes}

    except _Cancelled:
        session.rollback()  # file already removed by the writer; status is Cancelled
        log.info("export_cancelled", job_id=job_id)
        return {"ok": False, "reason": "cancelled"}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        if key:
            storage.delete(key)
        failed = session.get(ExportJob, job_id)
        if failed is not None and failed.status != "Cancelled":
            failed.status = "Failed"
            failed.error = str(exc)[:2000]
            session.commit()
        log.error("export_failed", job_id=job_id, error=str(exc))
        raise
    finally:
        session.close()


# --- Multi-part parallel export --------------------------------------------
# A large date-range export is split (in exports.service) into per-block parts
# run concurrently as a Celery chord: group(run_export_part) | finalize_export.
# Each part streams one sub-range to its own .partNN.csv.gz; finalize concatenates
# them (gzip members concatenate into one valid stream) into the single artifact.
def _part_key(reference: str, part_index: int) -> str:
    return f"{reference}.part{part_index:03d}.csv.gz"


def _mark_part(session, job_id: str, part_index: int, status: str, error: str | None) -> None:
    p = session.execute(
        select(ExportJobPart).where(
            ExportJobPart.job_id == job_id, ExportJobPart.part_index == part_index
        )
    ).scalar_one_or_none()
    if p is not None:
        p.status = status
        if error is not None:
            p.error = error[:2000]
        session.commit()


@celery.task(
    name="exports.run_part", bind=True,
    max_retries=settings.export_part_max_retries,
    soft_time_limit=settings.export_part_soft_limit_seconds,
    time_limit=settings.export_part_soft_limit_seconds + 300,
)
def run_export_part(self, job_id: str, part_index: int) -> dict:
    """Stream one date-range slice of a multi-part export. Never raises on logical
    failure — it records the part status and returns a result dict so the chord
    always reaches finalize_export (which does cleanup / assembly)."""
    session = SyncSession()
    storage = get_storage()
    part_key = ""
    try:
        job = session.get(ExportJob, job_id)
        part = session.execute(
            select(ExportJobPart).where(
                ExportJobPart.job_id == job_id, ExportJobPart.part_index == part_index
            )
        ).scalar_one_or_none()
        if job is None or part is None:
            return {"part_index": part_index, "ok": False, "reason": "not_found"}
        assert part is not None  # narrow for the on_progress closure below
        if job.status == "Cancelled":
            part.status = "Cancelled"
            session.commit()
            return {"part_index": part_index, "ok": False, "reason": "cancelled"}

        # First part to run flips the parent Queued → Running (atomic guard).
        session.execute(
            update(ExportJob)
            .where(ExportJob.id == job_id, ExportJob.status == "Queued")
            .values(status="Running", started_at=datetime.now(UTC))
        )
        part.status = "Running"
        part.celery_task_id = self.request.id
        session.commit()

        _, _, categories, search = _selection(job.params or {})
        # date_to is exclusive here (part bounds are already exclusive upper).
        groups = reporting.export_groups(
            job.report_key, date_from=part.date_from, date_to=part.date_to,
            categories=categories, search=search,
        )
        part_key = _part_key(job.reference, part_index)
        last = [0.0]

        def on_progress(rows: int, part: ExportJobPart = part) -> None:
            now = time.monotonic()
            if now - last[0] < settings.export_progress_commit_seconds:
                return
            part.rows = rows
            session.commit()
            last[0] = now

        processed, _ = _write_report_gzip(
            storage, part_key, groups=groups,
            write_header=(part_index == 0),  # only part 0 keeps the CSV header
            should_cancel=lambda: _is_cancelled(session, job_id),
            on_progress=on_progress, compute_hash=False,
        )

        part.rows = processed
        part.status = "Completed"
        part.file_path = storage.locator(part_key)
        session.commit()
        # Bump parent aggregate: processed_rows += this part; % = completed/total parts.
        session.execute(
            update(ExportJob)
            .where(ExportJob.id == job_id)
            .values(processed_rows=ExportJob.processed_rows + processed)
        )
        done = session.execute(
            select(func.count(ExportJobPart.id)).where(
                ExportJobPart.job_id == job_id, ExportJobPart.status == "Completed"
            )
        ).scalar_one()
        if job.part_count:
            session.execute(
                update(ExportJob)
                .where(ExportJob.id == job_id)
                .values(progress_pct=int(done / job.part_count * 100))
            )
        session.commit()
        return {"part_index": part_index, "ok": True, "rows": processed}

    except _Cancelled:
        session.rollback()  # file already removed by the writer
        p = session.execute(
            select(ExportJobPart).where(
                ExportJobPart.job_id == job_id, ExportJobPart.part_index == part_index
            )
        ).scalar_one_or_none()
        if p is not None:
            p.status = "Cancelled"
            session.commit()
        return {"part_index": part_index, "ok": False, "reason": "cancelled"}
    except SoftTimeLimitExceeded as exc:
        # Part hit its own time limit — retrying would just time out again. Fail it
        # terminally so the chord reaches finalize (which fails the job cleanly).
        session.rollback()
        if part_key:
            storage.delete(part_key)
        _mark_part(session, job_id, part_index, "Failed", "part time limit exceeded")
        log.error("export_part_timed_out", job_id=job_id, part=part_index, error=str(exc))
        return {"part_index": part_index, "ok": False, "reason": "timeout"}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        if part_key:
            storage.delete(part_key)  # clear the partial before a clean re-run
        # Transient blip? Retry with backoff so one hiccup doesn't lose the job.
        # Already-Completed sibling parts are untouched — only THIS part re-runs.
        if self.request.retries < settings.export_part_max_retries:
            log.warning("export_part_retry", job_id=job_id, part=part_index,
                        attempt=self.request.retries + 1, error=str(exc))
            raise self.retry(exc=exc, countdown=settings.export_part_retry_countdown) from exc
        # Retries exhausted → record Failed and return (chord still reaches finalize).
        _mark_part(session, job_id, part_index, "Failed", str(exc))
        log.error("export_part_failed", job_id=job_id, part=part_index,
                  retries=self.request.retries, error=str(exc))
        return {"part_index": part_index, "ok": False, "reason": "error"}
    finally:
        session.close()


@celery.task(
    name="exports.finalize", bind=True, max_retries=settings.export_finalize_max_retries
)
def finalize_export(self, results: list, job_id: str) -> dict:
    """Chord callback: assemble the parts into one .csv.gz (or fail/clean up).
    Idempotent — a re-run over an already-Completed job is a no-op."""
    session = SyncSession()
    storage = get_storage()
    try:
        job = session.get(ExportJob, job_id)
        if job is None:
            return {"ok": False, "reason": "job_not_found"}
        if job.status == "Completed":
            return {"ok": True, "reason": "already_done"}

        parts = list(
            session.execute(
                select(ExportJobPart)
                .where(ExportJobPart.job_id == job_id)
                .order_by(ExportJobPart.part_index)
            ).scalars().all()
        )
        part_keys = [_part_key(job.reference, p.part_index) for p in parts]

        # Cancelled or any part not Completed (after its own retries) → do not
        # assemble a silently-incomplete file; clean up and fail with which parts.
        bad = [p for p in parts if p.status != "Completed"]
        if job.status == "Cancelled" or bad:
            for pk in part_keys:
                storage.delete(pk)
            if job.status != "Cancelled":
                detail = "; ".join(f"{p.date_from}..{p.date_to} [{p.status}]" for p in bad[:5])
                more = "" if len(bad) <= 5 else f" (+{len(bad) - 5} more)"
                job.status = "Failed"
                job.error = f"{len(bad)} export part(s) failed after retries: {detail}{more}"
                session.commit()
            log.info("export_multipart_aborted", job_id=job_id, status=job.status, failed=len(bad))
            return {"ok": False, "reason": "aborted"}

        # Assemble the parts into the final artifact.
        ext = _artifact_ext()
        final_key = f"{job.reference}.{ext}"
        if settings.export_csv_split_rows > 0:
            total = _assemble_zip(
                storage, final_key, part_keys,
                base_name=job.report_key, total_rows=sum(p.rows for p in parts),
            )
            sha = _sha256_of(storage, final_key)
        else:
            # Byte-concat the gzip members → one .csv.gz; hash during the write pass.
            digest = hashlib.sha256()
            with storage.open_write(final_key) as out:
                for pk in part_keys:
                    with storage.open_read(pk) as fh:
                        for block in iter(lambda fh=fh: fh.read(1 << 20), b""):
                            digest.update(block)
                            out.write(block)
            total = sum(p.rows for p in parts)
            sha = digest.hexdigest()
        for pk in part_keys:
            storage.delete(pk)

        # KPIs over the whole selection (once, not per part; merged across groups).
        date_from, date_to_excl, categories, search = _selection(job.params or {})
        try:
            kpi_row = _kpis_all_groups(reporting.kpi_groups(
                job.report_key, date_from=date_from, date_to=date_to_excl,
                categories=categories, search=search,
            ))
        except Exception:  # noqa: BLE001 — KPIs are best-effort; never fail the export
            kpi_row = {}

        job.file_path = storage.locator(final_key)
        job.file_format = ext
        job.file_size_bytes = storage.size(final_key)
        job.checksum_sha256 = sha
        job.kpis = {k: _jsonable(v) for k, v in kpi_row.items()}
        job.processed_rows = total
        job.total_rows = total
        job.progress_pct = 100
        job.status = "Completed"
        job.completed_at = datetime.now(UTC)
        job.expires_at = datetime.now(UTC) + timedelta(hours=settings.export_retention_hours)
        job.error = None
        session.commit()
        log.info(
            "export_completed", job_id=job_id, rows=total, size=job.file_size_bytes,
            parts=len(parts),
        )
        return {"ok": True, "rows": total, "size": job.file_size_bytes}

    except Exception as exc:  # noqa: BLE001
        session.rollback()
        # Retry the assembly (parts still exist — only success/abort deletes them).
        if self.request.retries < settings.export_finalize_max_retries:
            log.warning("export_finalize_retry", job_id=job_id,
                        attempt=self.request.retries + 1, error=str(exc))
            raise self.retry(exc=exc, countdown=settings.export_finalize_retry_countdown) from exc
        failed = session.get(ExportJob, job_id)
        if failed is not None and failed.status not in ("Cancelled", "Completed"):
            failed.status = "Failed"
            failed.error = str(exc)[:2000]
            session.commit()
            # Terminal: drop the part files we could not assemble (avoid a disk leak).
            if failed.part_count:
                for i in range(failed.part_count):
                    storage.delete(_part_key(failed.reference, i))
        log.error("export_finalize_failed", job_id=job_id, error=str(exc))
        raise
    finally:
        session.close()
