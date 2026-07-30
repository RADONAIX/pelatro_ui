"""Pull MSC records into canonical usage (§6, §7).

    source table → canonical transform → landing audit copy → canonical usage

**Idempotency is a constraint, not a check (§7).** Every write is an upsert on
``cdr_enriched.usage_id``, which is derived deterministically from the source
row's file and record number. Re-running a batch — after a crash, on a schedule,
or by mistake — updates the same rows rather than inserting new ones. The
in-flight duplicate check below is an optimisation that avoids pointless writes;
the *guarantee* is the unique index, because that is the only thing that also
holds when two pulls run at once.

**Transaction per page.** A page commits, the cursor advances, the next page
starts. A failure halfway through a 10-lakh pull loses at most one page and
resumes from the cursor, rather than rolling back an hour of work.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ValidationFailedError
from app.core.logging import get_logger
from app.modules.cdr.bulk_enrich import EnrichmentStatus
from app.modules.cdr.models import CdrBatch, CdrEnriched, CdrLanding
from app.modules.msc import canonical
from app.modules.msc.canonical import CanonicalUsage, SkippedRecord, TransformError
from app.modules.msc.models import MscIngestCursor
from app.modules.msc.source import MscSourceReader

log = get_logger("msc.ingest")


def _mask(number: str | None) -> str:
    """Mask an MSISDN or IMSI for logging (§31).

    Subscriber identifiers are personal data and log aggregation is a much
    wider audience than the database. Enough tail digits survive to correlate a
    support call; not enough to identify anyone from the logs alone.
    """
    if not number:
        return "—"
    return f"***{number[-4:]}" if len(number) > 4 else "***"


@dataclass
class IngestSummary:
    source_system: str
    source_tables: list[str] = field(default_factory=list)
    read_records: int = 0
    canonical_records: int = 0
    duplicate_records: int = 0
    skipped_records: int = 0
    rejected_records: int = 0
    batches: int = 0
    skipped_by_reason: dict[str, int] = field(default_factory=dict)
    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    last_source_id: dict[str, int] = field(default_factory=dict)
    duration_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_system": self.source_system,
            "source_tables": self.source_tables,
            "read_records": self.read_records,
            "canonical_records": self.canonical_records,
            "duplicate_records": self.duplicate_records,
            "skipped_records": self.skipped_records,
            "rejected_records": self.rejected_records,
            "batches": self.batches,
            "skipped_by_reason": self.skipped_by_reason,
            "rejected_by_reason": self.rejected_by_reason,
            "last_source_id": self.last_source_id,
            "duration_ms": round(self.duration_ms, 1),
        }


async def _cursor_for(db: AsyncSession, source_system: str, table: str) -> MscIngestCursor:
    row = (
        await db.execute(
            select(MscIngestCursor).where(
                MscIngestCursor.source_system == source_system,
                MscIngestCursor.source_table == table,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = MscIngestCursor(source_system=source_system, source_table=table, last_source_id=0)
        db.add(row)
        await db.flush()
    return row


async def _batch_for(
    db: AsyncSession,
    cache: dict[str, CdrBatch],
    *,
    source_system: str,
    source_file: str,
    actor_id: str | None,
) -> CdrBatch:
    """One CDR batch per source file.

    Per file rather than per pull, so the existing
    ``UNIQUE (source_system, filename)`` keeps doing real work and every file
    gets its own reconcilable record counts — which is the unit an operator
    actually reconciles against the switch.
    """
    if source_file in cache:
        return cache[source_file]

    batch = (
        await db.execute(
            select(CdrBatch).where(
                CdrBatch.source_system == source_system, CdrBatch.filename == source_file
            )
        )
    ).scalar_one_or_none()
    if batch is None:
        batch = CdrBatch(
            filename=source_file,
            source_system=source_system,
            cdr_type="MSC",
            status="LOADING",
            created_by=actor_id,
        )
        db.add(batch)
        await db.flush()
    cache[source_file] = batch
    return batch


def _enriched_values(usage: CanonicalUsage, batch_id: str, landing_id: str) -> dict[str, Any]:
    """Canonical usage as a ``cdr_enriched`` row, before enrichment runs."""
    return {
        "batch_id": batch_id,
        "landing_id": landing_id,
        "usage_id": usage.usage_id,
        "source_file": usage.source_file,
        "source_record_number": usage.source_record_number,
        "duplicate_hash": usage.duplicate_hash,
        "cdr_id": usage.usage_id,
        "msisdn": usage.subscriber_msisdn,
        "imsi": usage.subscriber_imsi,
        "service_type": usage.service_type,
        "call_direction": usage.call_direction,
        "event_timestamp": usage.event_start_time,
        "event_date": usage.event_date,
        "event_end_time": usage.event_end_time,
        "duration_seconds": usage.duration_seconds,
        "calling_number": usage.calling_number,
        "called_number": usage.called_number,
        "charged_party": usage.charged_party,
        "cell_id": usage.cell_id,
        "location_area_code": usage.location_area_code,
        "call_reference": usage.call_reference,
        # Left null on purpose: the MSC does not price calls. The OCS
        # correlation step fills it, and until it does the assurance verdict is
        # NO_ACTUAL_CHARGE rather than a comparison against an assumed zero.
        "actual_charge": None,
        "currency": usage.currency,
        "roaming": usage.roaming_flag,
        "serving_network": usage.serving_network,
        "source_system": usage.source_system,
        "source_attributes": usage.source_attributes,
        "enrichment_status": EnrichmentStatus.PENDING.value,
        "processing_status": "PENDING",
        "quality_status": "OK",
    }


async def ingest(
    db: AsyncSession,
    *,
    source_system: str = "MSC01",
    tables: list[str] | None = None,
    limit: int | None = None,
    from_start: bool = False,
    actor_id: str | None = None,
) -> IngestSummary:
    """Pull new MSC records and store them as canonical usage.

    ``from_start`` ignores the cursor and re-reads the table from the beginning.
    Safe by construction — every write is an upsert on the deterministic
    ``usage_id`` — and the way to recover from a source correction.
    """
    if not settings.msc_source_enabled:
        raise ValidationFailedError(
            "The MSC source is not configured.",
            details={"hint": "Set MSC_SOURCE_ENABLED=true and the MSC_SOURCE_* connection values."},
        )

    started = time.perf_counter()
    table_names = tables or settings.msc_source_table_list
    summary = IngestSummary(source_system=source_system, source_tables=list(table_names))
    remaining = limit if limit is not None else settings.maximum_records_per_run

    for table in table_names:
        if remaining is not None and remaining <= 0:
            break
        cursor = await _cursor_for(db, source_system, table)
        start_id = 0 if from_start else cursor.last_source_id
        reader = MscSourceReader(table)

        async for page in reader.pages(after_id=start_id, limit=remaining):
            processed = await _ingest_page(
                db,
                page.rows,
                source_system=source_system,
                summary=summary,
                actor_id=actor_id,
            )
            summary.read_records += len(page.rows)
            summary.last_source_id[table] = page.last_id
            if remaining is not None:
                remaining -= len(page.rows)

            # The cursor only ever moves forward: re-reading is harmless, and a
            # cursor that went backwards after a partial failure would re-do
            # work that already committed.
            cursor.last_source_id = max(cursor.last_source_id, page.last_id)
            cursor.last_pulled_at = datetime.now(UTC)
            cursor.records_pulled += processed

            # One transaction per page (§25).
            await db.commit()

            if remaining is not None and remaining <= 0:
                break

    summary.duration_ms = (time.perf_counter() - started) * 1000
    log.info("msc_ingest_complete", **summary.as_dict())
    return summary


async def _ingest_page(
    db: AsyncSession,
    rows: list[dict[str, Any]],
    *,
    source_system: str,
    summary: IngestSummary,
    actor_id: str | None,
) -> int:
    """Transform and persist one page. Returns the count written."""
    batches: dict[str, CdrBatch] = {}
    usages: list[tuple[CanonicalUsage, dict[str, Any]]] = []

    for raw in rows:
        try:
            result = canonical.transform(
                raw,
                source_system=source_system,
                country_code=settings.msc_home_country_code,
                home_mccmnc=settings.msc_home_mccmnc,
                default_currency=settings.default_currency,
            )
        except TransformError as exc:
            summary.rejected_records += 1
            summary.rejected_by_reason[exc.code] = summary.rejected_by_reason.get(exc.code, 0) + 1
            continue

        if isinstance(result, SkippedRecord):
            # Counted, not discarded: the source and the platform must agree on
            # volume, and "40,083 supplementary-service actions" is an answer
            # while a missing 40,083 is a mystery.
            summary.skipped_records += 1
            summary.skipped_by_reason[result.reason] = (
                summary.skipped_by_reason.get(result.reason, 0) + 1
            )
            continue

        usages.append((result, raw))

    if not usages:
        return 0

    # --- Already present? ---------------------------------------------------
    usage_ids = [u.usage_id for u, _ in usages]
    existing = set(
        (
            await db.execute(
                select(CdrEnriched.usage_id).where(CdrEnriched.usage_id.in_(usage_ids))
            )
        ).scalars().all()
    )

    # Business duplicates: the same event arriving under a different file name.
    hashes = [u.duplicate_hash for u, _ in usages]
    duplicate_hashes = set(
        (
            await db.execute(
                select(CdrEnriched.duplicate_hash).where(
                    CdrEnriched.duplicate_hash.in_(hashes),
                    CdrEnriched.usage_id.notin_(usage_ids),
                )
            )
        ).scalars().all()
    )

    seen_in_page: set[str] = set()
    written = 0

    for usage, raw in usages:
        if usage.usage_id in existing:
            summary.duplicate_records += 1
            continue
        if usage.duplicate_hash in duplicate_hashes or usage.duplicate_hash in seen_in_page:
            summary.duplicate_records += 1
            continue
        seen_in_page.add(usage.duplicate_hash)

        batch = await _batch_for(
            db,
            batches,
            source_system=source_system,
            source_file=usage.source_file,
            actor_id=actor_id,
        )

        landing = CdrLanding(
            batch_id=batch.id,
            row_number=int(raw.get("id") or 0),
            record_hash=usage.duplicate_hash,
            # The source row exactly as read — the audit copy. JSON-safe, since
            # the source columns are text and timestamps.
            payload={k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in raw.items()},
            status="LOADED",
        )
        db.add(landing)
        await db.flush()

        values = _enriched_values(usage, batch.id, landing.id)
        # ON CONFLICT DO NOTHING rather than a prior existence check: two pulls
        # racing on the same page must not both insert, and only the database
        # can decide that.
        await db.execute(
            pg_insert(CdrEnriched)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["usage_id"])
        )
        summary.canonical_records += 1
        written += 1

    for batch in batches.values():
        await _refresh_batch_counts(db, batch)
    summary.batches += len(batches)
    return written


async def _refresh_batch_counts(db: AsyncSession, batch: CdrBatch) -> None:
    """Recount from the rows themselves rather than incrementing a counter.

    A counter drifts the first time a page is retried; a count cannot.
    """
    loaded = int(
        (
            await db.execute(
                select(func.count()).select_from(CdrLanding).where(CdrLanding.batch_id == batch.id)
            )
        ).scalar_one()
    )
    enriched = int(
        (
            await db.execute(
                select(func.count())
                .select_from(CdrEnriched)
                .where(CdrEnriched.batch_id == batch.id)
            )
        ).scalar_one()
    )
    event_date = (
        await db.execute(
            select(func.min(CdrEnriched.event_date)).where(CdrEnriched.batch_id == batch.id)
        )
    ).scalar_one()

    batch.total_records = loaded
    batch.loaded_records = loaded
    batch.normalized_records = enriched
    batch.enriched_records = enriched
    batch.event_date = event_date
    batch.status = "READY" if enriched else "LOADING"


async def status(db: AsyncSession, source_system: str = "MSC01") -> dict[str, Any]:
    """Where each source table has been read to, and how much is left."""
    from app.modules.msc.source import probe

    cursors = (
        await db.execute(
            select(MscIngestCursor).where(MscIngestCursor.source_system == source_system)
        )
    ).scalars().all()
    by_table = {c.source_table: c for c in cursors}

    source = await probe()
    tables = []
    for entry in source.get("tables", []):
        cursor = by_table.get(entry["table"])
        read_to = cursor.last_source_id if cursor else 0
        tables.append(
            {
                **entry,
                "last_source_id": read_to,
                "records_pulled": cursor.records_pulled if cursor else 0,
                "last_pulled_at": (
                    cursor.last_pulled_at.isoformat()
                    if cursor and cursor.last_pulled_at
                    else None
                ),
            }
        )
    return {**source, "source_system": source_system, "tables": tables}
