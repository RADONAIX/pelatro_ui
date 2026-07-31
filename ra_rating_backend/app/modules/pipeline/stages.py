"""The six pipeline stages.

Each is a plain async function taking ``(db, run)`` and returning
``(records_in, records_out, records_failed, detail)``. That signature is the
contract the orchestrator and the Airflow DAG both call through, so running
in-process and running under Airflow execute the same code.

Ingestion, normalization and enrichment are separate stages here even though
they touch overlapping rows. The split matters operationally: enrichment is the
stage that fails when reference data is missing, and being able to re-run *only*
enrichment after loading a prefix — without re-parsing a 1-crore file — is the
difference between a five-minute fix and a five-hour one.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailedError
from app.core.logging import get_logger
from app.modules.cdr import enrich as enrichment
from app.modules.cdr import normalize as norm
from app.modules.cdr.models import CdrBatch, CdrEnriched, CdrLanding
from app.modules.cdr.service import RecordStatus
from app.modules.compiler import service as snapshot_svc
from app.modules.imports import parser
from app.modules.pipeline.models import PipelineRun
from app.modules.rating import service as rating_svc
from app.modules.rating.constants import RunStatus
from app.modules.rating.models import RatingRun

log = get_logger("pipeline")

StageResult = tuple[int, int, int, str]

#: Rows flushed between commits. Bounded so a large batch never needs the whole
#: file resident as ORM objects.
FLUSH_EVERY = 2_000


# --- Stage 1: ingestion -----------------------------------------------------


async def stage_ingest(db: AsyncSession, run: PipelineRun) -> StageResult:
    """Parse the upload and land every record verbatim, detecting duplicates."""
    profile = norm.CDR_PROFILES.get(run.cdr_type.upper())
    if profile is None:
        raise ValidationFailedError(f"Unknown CDR type '{run.cdr_type}'.")
    if not run.payload:
        raise ValidationFailedError("The uploaded file is no longer available for this run.")

    existing = (
        await db.execute(
            select(CdrBatch).where(
                CdrBatch.source_system == run.source_system,
                CdrBatch.filename == run.filename,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ValidationFailedError(
            f"'{run.filename}' has already been ingested from {run.source_system}.",
            details={"batch_id": existing.id, "hint": "Re-ingesting would double-count revenue."},
        )

    _, rows = parser.parse(run.filename, bytes(run.payload))

    batch = CdrBatch(
        filename=run.filename,
        source_system=run.source_system,
        cdr_type=profile.code,
        status="LOADING",
        total_records=len(rows),
        created_by=run.triggered_by,
    )
    db.add(batch)
    await db.flush()
    run.batch_id = batch.id
    run.total_records = len(rows)

    seen: set[str] = set()
    loaded = duplicates = rejected = 0

    for index, raw in enumerate(rows):
        row_number = index + 2  # header is line 1
        try:
            record = norm.normalize(raw, profile)
        except norm.NormalizeError as exc:
            # Landing still records it: a record we could not read is evidence
            # about the source system, not something to discard.
            rejected += 1
            db.add(
                CdrLanding(
                    batch_id=batch.id,
                    row_number=row_number,
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
                    batch_id=batch.id, row_number=row_number,
                    record_hash=f"dup:{batch.id}:{index}", payload=raw,
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
                    batch_id=batch.id, row_number=row_number,
                    record_hash=f"dup:{batch.id}:{index}", payload=raw,
                    status=RecordStatus.DUPLICATE,
                    error="This event was already ingested in an earlier batch.",
                )
            )
            continue

        seen.add(record.record_hash)
        db.add(
            CdrLanding(
                batch_id=batch.id, row_number=row_number,
                record_hash=record.record_hash, payload=raw,
                status=RecordStatus.LOADED,
            )
        )
        loaded += 1
        if loaded % FLUSH_EVERY == 0:
            await db.flush()

    batch.loaded_records = loaded
    batch.duplicate_records = duplicates
    batch.rejected_records = rejected
    await db.flush()

    # The payload has served its purpose; landing is now the audit copy.
    run.payload = None
    detail = f"{loaded} landed, {duplicates} duplicate, {rejected} unreadable"
    return len(rows), loaded, duplicates + rejected, detail


# --- Stage 2: normalization -------------------------------------------------


async def stage_normalize(db: AsyncSession, run: PipelineRun) -> StageResult:
    """Landed records → the common usage model."""
    profile = norm.CDR_PROFILES[run.cdr_type.upper()]
    batch = await db.get(CdrBatch, run.batch_id)
    if batch is None:
        raise ValidationFailedError("Ingestion produced no batch to normalize.")

    landed = (
        await db.execute(
            select(CdrLanding).where(
                CdrLanding.batch_id == batch.id,
                CdrLanding.status == RecordStatus.LOADED,
            )
        )
    ).scalars().all()

    produced = failed = 0
    for landing in landed:
        try:
            record = norm.normalize(landing.payload, profile)
        except norm.NormalizeError as exc:
            # Re-checked here because a record can land cleanly and still fail
            # normalization if the profile changed between stages.
            failed += 1
            landing.status = RecordStatus.REJECTED
            landing.error = exc.message
            continue

        values = record.values
        db.add(
            CdrEnriched(
                batch_id=batch.id,
                landing_id=landing.id,
                cdr_id=values["cdr_id"],
                subscriber_id=values.get("subscriber_id"),
                account_id=values.get("account_id"),
                msisdn=values.get("msisdn"),
                imsi=values.get("imsi"),
                service_type=values["service_type"],
                event_type=values.get("event_type"),
                event_timestamp=values["event_timestamp"],
                event_date=values["event_date"],
                duration_seconds=values.get("duration_seconds"),
                usage_volume=values.get("usage_volume"),
                calling_number=values.get("calling_number"),
                called_number=values.get("called_number"),
                actual_charge=values.get("actual_charge"),
                actual_discount=values.get("actual_discount"),
                actual_tax=values.get("actual_tax"),
                currency=values.get("currency"),
                source_system=run.source_system,
                network_type=values.get("network_type"),
                roaming=values.get("roaming"),
                visited_operator=values.get("visited_operator"),
                apn=values.get("apn"),
                rating_group=values.get("rating_group"),
                # Enrichment fills the dimensions and the context in stage 3.
                quality_status="PENDING_ENRICHMENT",
            )
        )
        produced += 1
        if produced % FLUSH_EVERY == 0:
            await db.flush()

    batch.normalized_records = produced
    batch.control_total = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(CdrEnriched.actual_charge), 0)).where(
                    CdrEnriched.batch_id == batch.id
                )
            )
        ).scalar_one()
        or 0
    )
    await db.flush()
    return len(landed), produced, failed, f"{produced} records on the common model"


# --- Stage 3: enrichment ----------------------------------------------------


async def stage_enrich(db: AsyncSession, run: PipelineRun) -> StageResult:
    """Resolve dimensions and build the rating context key."""
    batch = await db.get(CdrBatch, run.batch_id)
    if batch is None:
        raise ValidationFailedError("There is no batch to enrich.")

    index = await enrichment.load_reference_index(db)
    rows = (
        await db.execute(select(CdrEnriched).where(CdrEnriched.batch_id == batch.id))
    ).scalars().all()

    quality: dict[str, int] = {}
    contexts: set[str] = set()
    problem_rows = 0

    for row in rows:
        values = {
            "event_timestamp": row.event_timestamp,
            "event_date": row.event_date,
            "service_type": row.service_type,
            "msisdn": row.msisdn,
            "calling_number": row.calling_number,
            "called_number": row.called_number,
            "duration_seconds": float(row.duration_seconds) if row.duration_seconds else None,
            "usage_volume": float(row.usage_volume) if row.usage_volume else None,
            "roaming": row.roaming,
            "network_type": row.network_type,
            "visited_operator": row.visited_operator,
            "rating_group": row.rating_group,
            "product_code": row.product_code,
            "subscriber_id": row.subscriber_id,
            "account_id": row.account_id,
        }
        enriched = enrichment.enrich(values, index)

        row.subscriber_id = enriched.get("subscriber_id")
        row.account_id = enriched.get("account_id")
        row.product_code = enriched.get("product_code")
        row.offer_code = enriched.get("offer_code")
        row.tariff_plan_code = enriched.get("tariff_plan_code")
        row.account_type = enriched.get("account_type")
        row.destination_zone = enriched.get("destination_zone")
        row.origin_zone = enriched.get("origin_zone")
        row.on_net = enriched.get("on_net")
        row.time_band = enriched.get("time_band")
        row.roaming = enriched.get("roaming")
        row.rating_group = enriched.get("rating_group")
        row.context_key = enriched["context_key"]
        row.context_hash = enriched["context_hash"]
        row.quality_status = enriched["quality_status"]
        row.quality_detail = enriched.get("quality_detail")

        quality[row.quality_status] = quality.get(row.quality_status, 0) + 1
        contexts.add(row.context_hash)
        if row.quality_status != enrichment.QualityStatus.OK:
            problem_rows += 1

    batch.enriched_records = len(rows)
    batch.event_date = (
        await db.execute(
            select(CdrEnriched.event_date)
            .where(CdrEnriched.batch_id == batch.id)
            .group_by(CdrEnriched.event_date)
            .order_by(func.count().desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    batch.status = "READY" if rows else "FAILED"
    batch.summary = {
        "quality": quality,
        "distinct_contexts": len(contexts),
        # The collapse ratio — the number that predicts whether rating is fast.
        "cdrs_per_context": round(len(rows) / max(1, len(contexts)), 1),
    }
    await db.flush()

    ratio = round(len(rows) / max(1, len(contexts)), 1)
    detail = f"{len(contexts)} distinct contexts, {ratio} CDRs each"
    return len(rows), len(rows), problem_rows, detail


# --- Stage 4: rule selection ------------------------------------------------


async def stage_select(db: AsyncSession, run: PipelineRun) -> StageResult:
    """Create the rating run and resolve one rule set per distinct context."""
    snapshot = await snapshot_svc.active_snapshot(db)
    if snapshot is None:
        raise ValidationFailedError(
            "No rule snapshot is active.",
            details={"hint": "Compile and activate a snapshot before rating."},
        )

    total = int(
        (
            await db.execute(
                select(func.count()).select_from(CdrEnriched).where(
                    CdrEnriched.batch_id == run.batch_id
                )
            )
        ).scalar_one()
    )
    if total == 0:
        raise ValidationFailedError("Enrichment produced no rateable CDRs.")

    rating_run = RatingRun(
        batch_id=run.batch_id,
        snapshot_id=snapshot.id,
        snapshot_version=snapshot.version,
        status=RunStatus.RUNNING.value,
        total_cdrs=total,
        started_at=_now(),
        triggered_by=run.triggered_by,
        triggered_by_name=run.triggered_by_name,
    )
    db.add(rating_run)
    await db.flush()

    run.rating_run_id = rating_run.id
    run.snapshot_id = snapshot.id
    run.snapshot_version = snapshot.version

    contexts = await rating_svc._resolve_contexts(db, rating_run)
    rating_run.distinct_contexts = len(contexts)
    await db.flush()

    unresolved = sum(1 for c in contexts.values() if not c.selected_rules)
    detail = f"{len(contexts)} contexts resolved against snapshot v{snapshot.version}"
    return len(contexts), len(contexts), unresolved, detail


# --- Stage 5: rating --------------------------------------------------------


async def stage_rate(db: AsyncSession, run: PipelineRun) -> StageResult:
    """Apply the charging sequence to every CDR."""
    rating_run = await db.get(RatingRun, run.rating_run_id)
    if rating_run is None:
        raise ValidationFailedError("Rule selection produced no rating run.")

    contexts = {
        entry.context_hash: entry
        for entry in (
            await db.execute(
                select(rating_svc.ContextRuleMap).where(
                    rating_svc.ContextRuleMap.run_id == rating_run.id
                )
            )
        ).scalars().all()
    }

    totals = await rating_svc._rate_all(db, rating_run, contexts)

    rating_run.rated_cdrs = totals["rated"]
    rating_run.matched_count = totals["matched"]
    rating_run.expected_revenue = totals["expected"]
    rating_run.billed_revenue = totals["billed"]
    rating_run.undercharge_total = totals["undercharge"]
    rating_run.overcharge_total = totals["overcharge"]
    rating_run.stats = {
        **(rating_run.stats or {}),
        "by_status": totals["by_status"],
        "cdrs_per_context": round(totals["rated"] / max(1, len(contexts)), 1),
    }
    await db.flush()

    unpriced = totals["rated"] - totals["matched"]
    detail = (
        f"expected {float(totals['expected']):.2f}, billed {float(totals['billed']):.2f}"
    )
    return totals["rated"], totals["rated"], unpriced, detail


# --- Stage 6: assurance -----------------------------------------------------


async def stage_assure(db: AsyncSession, run: PipelineRun) -> StageResult:
    """Classify the variances and group them into investigations."""
    rating_run = await db.get(RatingRun, run.rating_run_id)
    if rating_run is None:
        raise ValidationFailedError("There is no rating run to assure.")

    await rating_svc._build_exceptions(db, rating_run)

    rating_run.status = RunStatus.COMPLETED.value
    rating_run.finished_at = _now()
    await db.flush()

    run.exception_count = rating_run.exception_count
    leakage = float(rating_run.undercharge_total)
    overcharge = float(rating_run.overcharge_total)
    match_rate = (
        round(rating_run.matched_count / rating_run.rated_cdrs * 100, 2)
        if rating_run.rated_cdrs
        else 0.0
    )
    run.summary = {
        "match_rate": match_rate,
        "expected_revenue": float(rating_run.expected_revenue),
        "billed_revenue": float(rating_run.billed_revenue),
        "revenue_leakage": leakage,
        "customer_overcharge": overcharge,
        "exception_groups": rating_run.exception_count,
        "by_status": (rating_run.stats or {}).get("by_status", {}),
    }
    detail = (
        f"{rating_run.exception_count} exception groups · "
        f"leakage {leakage:.2f} · overcharge {overcharge:.2f}"
    )
    return rating_run.rated_cdrs, rating_run.exception_count, 0, detail


STAGE_FUNCTIONS: dict[str, Any] = {
    "CDR_INGESTION": stage_ingest,
    "CDR_NORMALIZATION": stage_normalize,
    "DATA_ENRICHMENT": stage_enrich,
    "RULE_SELECTION": stage_select,
    "RATING_CALCULATION": stage_rate,
    "RATING_ASSURANCE": stage_assure,
}


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def timed() -> float:
    return time.perf_counter()


# Re-exported so the orchestrator does not import three modules for one call.
__all__ = [
    "STAGE_FUNCTIONS",
    "stage_assure",
    "stage_enrich",
    "stage_ingest",
    "stage_normalize",
    "stage_rate",
    "stage_select",
    "update",
]
