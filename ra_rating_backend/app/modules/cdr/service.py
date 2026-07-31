"""Ingest a CDR file: land it, normalize it, enrich it.

The three steps are one transaction per batch. Landing keeps the bytes as
received; normalization and enrichment produce the rateable row. A record that
fails any step is *recorded as failed*, never dropped — a CDR that vanishes
silently is indistinguishable from revenue that was never there.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.modules.cdr import enrich as enrichment
from app.modules.cdr import normalize as norm
from app.modules.cdr.models import CdrBatch, CdrEnriched, CdrLanding
from app.modules.imports import parser

log = get_logger("cdr")

CHUNK = 2_000


class RecordStatus:
    LOADED = "LOADED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"


async def ingest(
    db: AsyncSession,
    *,
    filename: str,
    data: bytes,
    cdr_type: str,
    source_system: str,
    actor_id: str,
) -> CdrBatch:
    profile = norm.CDR_PROFILES.get(cdr_type.upper())
    if profile is None:
        raise ValidationFailedError(
            f"Unknown CDR type '{cdr_type}'.",
            details={"known": sorted(norm.CDR_PROFILES)},
        )

    existing = (
        await db.execute(
            select(CdrBatch).where(
                CdrBatch.source_system == source_system, CdrBatch.filename == filename
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f"'{filename}' has already been ingested from {source_system}.",
            details={
                "batch_id": existing.id,
                "hint": "Re-ingesting would double-count its revenue.",
            },
        )

    started = time.perf_counter()
    _, rows = parser.parse(filename, data)

    batch = CdrBatch(
        filename=filename,
        source_system=source_system,
        cdr_type=profile.code,
        status="LOADING",
        total_records=len(rows),
        created_by=actor_id,
    )
    db.add(batch)
    await db.flush()

    # Hashes already seen, so a file that repeats a record inside itself is
    # caught as well as one that repeats a previous file's.
    seen: set[str] = set()
    loaded = duplicates = rejected = 0
    control_total = 0.0
    normalized_rows: list[tuple[str, dict[str, Any]]] = []

    for index, raw in enumerate(rows):
        landing_id = None
        try:
            record = norm.normalize(raw, profile)
        except norm.NormalizeError as exc:
            rejected += 1
            db.add(
                CdrLanding(
                    batch_id=batch.id,
                    row_number=index + 2,
                    # A rejected record still needs a unique hash for the
                    # constraint; derive one from its position.
                    record_hash=f"reject:{batch.id}:{index}",
                    payload=raw,
                    status=RecordStatus.REJECTED,
                    error=f"{exc.field + ': ' if exc.field else ''}{exc.message}",
                )
            )
            continue

        if record.record_hash in seen:
            duplicates += 1
            db.add(
                CdrLanding(
                    batch_id=batch.id,
                    row_number=index + 2,
                    record_hash=f"dup:{batch.id}:{index}",
                    payload=raw,
                    status=RecordStatus.DUPLICATE,
                    error="Duplicate of an earlier record in this file.",
                )
            )
            continue

        prior = (
            await db.execute(
                select(func.count())
                .select_from(CdrLanding)
                .where(CdrLanding.record_hash == record.record_hash)
            )
        ).scalar_one()
        if prior:
            duplicates += 1
            db.add(
                CdrLanding(
                    batch_id=batch.id,
                    row_number=index + 2,
                    record_hash=f"dup:{batch.id}:{index}",
                    payload=raw,
                    status=RecordStatus.DUPLICATE,
                    error="This event was already ingested in an earlier batch.",
                )
            )
            continue

        seen.add(record.record_hash)
        landing = CdrLanding(
            batch_id=batch.id,
            row_number=index + 2,
            record_hash=record.record_hash,
            payload=raw,
            status=RecordStatus.LOADED,
        )
        db.add(landing)
        await db.flush()
        landing_id = landing.id
        loaded += 1
        if record.values.get("actual_charge"):
            control_total += float(record.values["actual_charge"])
        normalized_rows.append((landing_id, record.values))

    # --- Enrichment ---------------------------------------------------------
    index_data = await enrichment.load_reference_index(db)
    enriched_count = 0
    quality: dict[str, int] = {}
    context_hashes: set[str] = set()

    for landing_id, values in normalized_rows:
        enriched = enrichment.enrich(values, index_data)
        quality[enriched["quality_status"]] = quality.get(enriched["quality_status"], 0) + 1
        context_hashes.add(enriched["context_hash"])
        db.add(
            CdrEnriched(
                batch_id=batch.id,
                landing_id=landing_id,
                cdr_id=enriched["cdr_id"],
                subscriber_id=enriched.get("subscriber_id"),
                account_id=enriched.get("account_id"),
                msisdn=enriched.get("msisdn"),
                imsi=enriched.get("imsi"),
                service_type=enriched["service_type"],
                event_type=enriched.get("event_type"),
                event_timestamp=enriched["event_timestamp"],
                event_date=enriched["event_date"],
                duration_seconds=enriched.get("duration_seconds"),
                usage_volume=enriched.get("usage_volume"),
                calling_number=enriched.get("calling_number"),
                called_number=enriched.get("called_number"),
                actual_charge=enriched.get("actual_charge"),
                actual_discount=enriched.get("actual_discount"),
                actual_tax=enriched.get("actual_tax"),
                currency=enriched.get("currency"),
                source_system=source_system,
                product_code=enriched.get("product_code"),
                offer_code=enriched.get("offer_code"),
                tariff_plan_code=enriched.get("tariff_plan_code"),
                account_type=enriched.get("account_type"),
                destination_zone=enriched.get("destination_zone"),
                origin_zone=enriched.get("origin_zone"),
                on_net=enriched.get("on_net"),
                time_band=enriched.get("time_band"),
                roaming=enriched.get("roaming"),
                network_type=enriched.get("network_type"),
                rating_group=enriched.get("rating_group"),
                visited_operator=enriched.get("visited_operator"),
                apn=enriched.get("apn"),
                context_key=enriched["context_key"],
                context_hash=enriched["context_hash"],
                quality_status=enriched["quality_status"],
                quality_detail=enriched.get("quality_detail"),
            )
        )
        enriched_count += 1

    batch.loaded_records = loaded
    batch.duplicate_records = duplicates
    batch.rejected_records = rejected
    batch.normalized_records = len(normalized_rows)
    batch.enriched_records = enriched_count
    batch.control_total = control_total
    batch.event_date = norm.event_date_of(
        [norm.NormalizedRecord(values=v) for _, v in normalized_rows]
    )
    batch.status = "READY" if enriched_count else "FAILED"
    distinct_contexts = len(context_hashes)
    batch.summary = {
        "quality": quality,
        "distinct_contexts": distinct_contexts,
        # The number that decides whether rating will be fast.
        "cdrs_per_context": round(enriched_count / max(1, distinct_contexts), 1),
        "ingest_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    await db.flush()
    await db.refresh(batch)
    log.info(
        "cdr_batch_ingested",
        batch=batch.id,
        loaded=loaded,
        duplicates=duplicates,
        rejected=rejected,
        contexts=distinct_contexts,
    )
    return batch


async def get_batch(db: AsyncSession, batch_id: str) -> CdrBatch:
    batch = await db.get(CdrBatch, batch_id)
    if batch is None:
        raise NotFoundError(f"CDR batch '{batch_id}' was not found.")
    return batch


async def data_quality(db: AsyncSession, batch_id: str) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(CdrEnriched.quality_status, func.count())
            .where(CdrEnriched.batch_id == batch_id)
            .group_by(CdrEnriched.quality_status)
            .order_by(func.count().desc())
        )
    ).all()
    return [{"status": status, "count": count} for status, count in rows]


async def context_frequency(
    db: AsyncSession, batch_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Most common rating contexts — where the volume actually is."""
    rows = (
        await db.execute(
            select(CdrEnriched.context_key, func.count().label("n"))
            .where(CdrEnriched.batch_id == batch_id)
            .group_by(CdrEnriched.context_key)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    return [{"context_key": key, "cdr_count": n} for key, n in rows]


def utcnow() -> datetime:
    return datetime.now(UTC)
