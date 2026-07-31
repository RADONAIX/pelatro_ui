"""Import APIs (group 4) — the kernel, over HTTP.

The legacy `/rule-imports` surface next door still works and is untouched. This
one differs in four ways that matter, each replacing a defect the plan names:

**Preview and commit are the same request path.** ``dry_run=true`` runs all eight
kernel steps and rolls back. The legacy connector import had no preview at all —
it wrote straight to live pricing — and a preview produced by different code from
the commit is wrong in exactly the cases where it matters.

**Re-posting a file is idempotent.** A completed batch with the same content hash
returns itself rather than importing twice.

**Unchanged rules do not cut versions.** A nightly full dump re-imported reports
"3 changed, 3,997 unchanged" instead of creating 4,000 versions and burying the
three that moved.

**A withdrawal is proposed, never applied.** A rule the vendor stopped exporting
keeps rating until a human approves its retirement, and a batch containing one
can never auto-commit.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.core.config import settings
from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.rbac import RatingPermKey
from app.modules.rules.api import ingest_schemas as s
from app.modules.rules.canonical.lineage import (
    RuleIngestionBatch,
    RuleIngestionRecord,
)
from app.modules.rules.canonical.rule import CanonicalRule
from app.modules.rules.canonical.sets import CanonicalRuleSet, RuleSetMember
from app.modules.rules.ingest import files, kernel, references, resolver
from app.modules.rules.ingest import sets as import_sets
from app.modules.rules.ingest.reconcile import Decision, ImportMode

router = APIRouter(prefix="/rule-ingest", tags=["rule-ingest"])

_view = require(RatingPermKey.RULES, "view")
Importer = principal_with(RatingPermKey.RULES, "edit")

#: Uploads are held in memory while they are parsed, so the ceiling is real
#: rather than advisory. Anything larger belongs on a connector feed.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


async def _read(file: UploadFile) -> bytes:
    data = await file.read()
    if not data:
        raise ValidationFailedError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValidationFailedError(
            f"The file is {len(data) / 1e6:.0f} MB; the limit for an upload is "
            f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB. Use a connector for a feed this size."
        )
    return data


def _actor(principal) -> kernel.Actor:
    return kernel.Actor(id=principal.id, name=principal.full_name or principal.email)


def _mapping(raw: str | None) -> dict[str, str | None] | None:
    if not raw:
        return None
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailedError("`mapping` is not valid JSON.") from exc
    if not isinstance(loaded, dict):
        raise ValidationFailedError("`mapping` must be an object of heading → column.")
    return loaded


# --- Mapping helpers --------------------------------------------------------


@router.get(
    "/columns",
    summary="Every column an uploaded file may carry",
    dependencies=[Depends(_view)],
)
async def columns() -> list[dict[str, Any]]:
    """What the mapping screen renders. Includes the canonical-only columns, so
    the UI does not need to know they come from a different registry."""
    return files.column_catalogue()


@router.post(
    "/preview",
    response_model=s.PreviewResponse,
    summary="Parse a file and show what it would do, without creating a batch",
)
async def preview(
    db: DbSession,
    principal: Importer,
    file: UploadFile = File(...),
    mapping: str | None = Form(None, description="JSON object: heading → column key"),
    source_system_code: str | None = Form(None),
    default_charging_mode: str | None = Form(None),
    default_currency: str | None = Form(None),
    effective_from: date | None = Form(None),
) -> s.PreviewResponse:
    """Read the file, map it, and run the whole kernel as a dry run.

    Two questions get answered together, because an operator asks them together:
    "did you understand my file?" (the mapping and the sample rows) and "what
    would this do to my estate?" (the decision counts and the issues).
    """
    data = await _read(file)
    parsed = files.parse_file(
        file.filename or "upload",
        data,
        mapping=_mapping(mapping),
        default_effective_from=effective_from,
        source_system_code=source_system_code,
        default_charging_mode=default_charging_mode,
        default_currency=default_currency,
    )

    missing = await _survey(db, parsed.drafts)
    result = await kernel.ingest(
        db,
        parsed.drafts,
        actor=_actor(principal),
        channel="FILE",
        source_system_code=source_system_code,
        filename=file.filename,
        dry_run=True,
        raw_records=parsed.raw,
    )
    return _preview(parsed, result, file.filename or "upload", missing)


# --- Batches ----------------------------------------------------------------


@router.post(
    "/batches",
    response_model=s.BatchDetail,
    status_code=201,
    summary="Import a file through the kernel",
)
async def create_batch(
    db: DbSession,
    principal: Importer,
    file: UploadFile = File(...),
    mapping: str | None = Form(None),
    source_system_code: str | None = Form(None),
    import_mode: str = Form(ImportMode.DELTA, description="FULL | DELTA"),
    default_charging_mode: str | None = Form(None),
    default_currency: str | None = Form(None),
    effective_from: date | None = Form(None),
    dry_run: bool = Form(False),
    rule_set_id: str | None = Form(
        None, description="Join this import's rules to an existing rule set."
    ),
    create_rule_set: bool = Form(
        False,
        description="Create a rule set for this import, so it can be validated, "
                    "approved and rolled back as one unit.",
    ),
    create_missing_references: bool = Form(
        False,
        description="Create placeholder catalogue entries for codes this file "
                    "references and the catalogue lacks. Off by default, reported "
                    "when used, and never for a catalogue where a placeholder "
                    "would change what gets priced.",
    ),
) -> s.BatchDetail:
    """Commit a file. Identical to ``/preview`` except that it keeps the result.

    ``import_mode=FULL`` declares the export to be the whole truth for its source,
    so a rule the store holds and the file omits becomes a **withdrawal
    proposal**. It is never applied here: a vendor omission silently retiring a
    rule that rates four million CDRs a month is the exact failure this product
    exists to catch.
    """
    if import_mode not in set(ImportMode):
        raise ValidationFailedError(
            f"'{import_mode}' is not an import mode. Use FULL or DELTA."
        )
    if import_mode == ImportMode.FULL and not source_system_code:
        raise ValidationFailedError(
            "A FULL import needs a source system: withdrawal is scoped to the "
            "system that sent the export, or an Ericsson dump would propose "
            "retiring every hand-authored rule in the estate.",
            details={"field": "source_system_code"},
        )

    data = await _read(file)
    parsed = files.parse_file(
        file.filename or "upload",
        data,
        mapping=_mapping(mapping),
        default_effective_from=effective_from,
        source_system_code=source_system_code,
        default_charging_mode=default_charging_mode,
        default_currency=default_currency,
    )
    # Created before the kernel runs, so the resolver picks it up and every rule
    # the batch writes joins it.
    rule_set = await import_sets.resolve(
        db,
        tenant_id=principal.tenant_id or settings.default_tenant_id,
        rule_set_id=rule_set_id,
        create=create_rule_set,
        source_system_id=None,
        source_code=source_system_code,
        filename=file.filename,
        actor_id=principal.id,
    )

    missing = await _survey(db, parsed.drafts)
    created: dict[str, list[str]] = {}
    if create_missing_references and missing:
        created = await references.create_stubs(
            db, missing, parsed.drafts,
            actor_id=principal.id,
            source_label=f"import of {file.filename or 'a file'}",
        )
        # Re-survey: the stubs exist now, so what remains is what genuinely
        # cannot be created and still blocks the batch.
        missing = await _survey(db, parsed.drafts)

    result = await kernel.ingest(
        db,
        parsed.drafts,
        actor=_actor(principal),
        channel="FILE",
        source_system_code=source_system_code,
        import_mode=import_mode,
        filename=file.filename,
        content_hash=parsed.content_hash,
        dry_run=dry_run,
        raw_records=parsed.raw,
        rule_set_code=rule_set.code if rule_set else None,
        # The validation already happened during ingestion. Landing a clean rule
        # at DRAFT would make the operator's first bulk action a re-validation
        # that changes nothing.
        promote_clean=True,
    )
    if rule_set is not None and not dry_run:
        await _ensure_batch_set_membership(
            db,
            batch_id=result.batch_id,
            rule_set_id=rule_set.rule_set_id,
            tenant_id=principal.tenant_id or settings.default_tenant_id,
        )
    if dry_run:
        detail = _detail_from_result(result, parsed)
    else:
        detail = await _detail(db, result.batch_id)
    detail.missing_metadata = _missing_out(missing)
    detail.created_references = created
    if rule_set is not None:
        detail.rule_set_id = rule_set.rule_set_id
        detail.rule_set_code = rule_set.code
    return detail


async def _ensure_batch_set_membership(
    db: DbSession, *, batch_id: str, rule_set_id: str, tenant_id: str
) -> None:
    """Link an idempotently replayed batch to the set created for this request.

    The ingestion kernel deliberately returns the original completed batch when
    identical content is posted again. The HTTP endpoint has already created a
    fresh import set by then, so the replay bypasses the writer calls that would
    normally create memberships. Rebuild them from the batch's forensic records;
    for an unchanged row, pin the rule's current version.
    """
    rows = (
        await db.execute(
            select(
                RuleIngestionRecord.rule_id,
                RuleIngestionRecord.rule_version_id,
                CanonicalRule.current_version_id,
            )
            .join(CanonicalRule, CanonicalRule.rule_id == RuleIngestionRecord.rule_id)
            .where(
                RuleIngestionRecord.batch_id == batch_id,
                RuleIngestionRecord.rule_id.isnot(None),
                CanonicalRule.tenant_id == tenant_id,
            )
        )
    ).all()
    if not rows:
        return

    existing = set(
        (
            await db.execute(
                select(RuleSetMember.rule_id).where(
                    RuleSetMember.rule_set_id == rule_set_id
                )
            )
        )
        .scalars()
        .all()
    )
    for rule_id, imported_version_id, current_version_id in rows:
        if rule_id in existing:
            continue
        db.add(
            RuleSetMember(
                tenant_id=tenant_id,
                rule_set_id=rule_set_id,
                rule_id=rule_id,
                rule_version_id=imported_version_id or current_version_id,
                sequence_number=0,
            )
        )
        existing.add(rule_id)
    await db.flush()


@router.get(
    "/rule-sets",
    response_model=list[s.RuleSetOut],
    summary="Canonical rule sets an import can join",
    dependencies=[Depends(_view)],
)
async def list_rule_sets(db: DbSession, limit: int = 100) -> list[s.RuleSetOut]:
    """The sets `/rule-ingest/batches` will actually accept.

    Ordered newest first: the set an operator wants to add to is almost always
    one they made recently, and a code-sorted list buries it under a year of
    vendor imports.
    """
    counts = dict(
        (
            await db.execute(
                select(RuleSetMember.rule_set_id, func.count()).group_by(
                    RuleSetMember.rule_set_id
                )
            )
        ).all()
    )
    rows = (
        (
            await db.execute(
                select(CanonicalRuleSet)
                .order_by(CanonicalRuleSet.created_at.desc())
                .limit(min(limit, 500))
            )
        )
        .scalars()
        .all()
    )
    return [
        s.RuleSetOut(
            rule_set_id=r.rule_set_id,
            code=r.code,
            name=r.name,
            description=r.description or "",
            set_type=r.set_type,
            status=getattr(r, "status", "") or "",
            rule_count=int(counts.get(r.rule_set_id, 0)),
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get(
    "/batches",
    response_model=s.BatchList,
    summary="Import history",
    dependencies=[Depends(_view)],
)
async def list_batches(
    db: DbSession,
    page: PageParams,
    channel: str | None = Query(None, description="MANUAL | FILE | CONNECTOR | API"),
    status: str | None = Query(None),
    source_system_id: str | None = Query(None),
    include_dry_runs: bool = Query(
        False, description="Previews are excluded by default — they changed nothing."
    ),
) -> s.BatchList:
    stmt = select(RuleIngestionBatch)
    if channel:
        stmt = stmt.where(RuleIngestionBatch.channel == channel)
    if status:
        stmt = stmt.where(RuleIngestionBatch.status == status)
    if source_system_id:
        stmt = stmt.where(RuleIngestionBatch.source_system_id == source_system_id)
    if not include_dry_runs:
        stmt = stmt.where(RuleIngestionBatch.dry_run.is_(False))

    total = int(
        (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = (
        await db.execute(
            stmt.order_by(RuleIngestionBatch.started_at.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    ).scalars().all()
    return s.BatchList(
        items=[_summary(b) for b in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/batches/{batch_id}",
    response_model=s.BatchDetail,
    summary="One batch: counts, decisions, checkpoint",
    dependencies=[Depends(_view)],
)
async def get_batch(db: DbSession, batch_id: str) -> s.BatchDetail:
    return await _detail(db, batch_id)


@router.get(
    "/batches/{batch_id}/records",
    response_model=s.RecordList,
    summary="What happened to each incoming record",
    dependencies=[Depends(_view)],
)
async def batch_records(
    db: DbSession,
    batch_id: str,
    page: PageParams,
    decision: str | None = Query(
        None, description="NEW | CHANGED | UNCHANGED | WITHDRAWN | REJECTED | QUARANTINED"
    ),
) -> s.RecordList:
    await _batch(db, batch_id)
    stmt = select(RuleIngestionRecord).where(RuleIngestionRecord.batch_id == batch_id)
    if decision:
        stmt = stmt.where(RuleIngestionRecord.decision == decision)

    total = int(
        (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = (
        await db.execute(
            stmt.order_by(RuleIngestionRecord.source_offset)
            .limit(page.limit)
            .offset(page.offset)
        )
    ).scalars().all()
    return s.RecordList(
        items=[
            s.RecordOut(
                record_id=r.record_id,
                source_offset=r.source_offset,
                external_ref=r.external_ref,
                decision=r.decision,
                rule_id=r.rule_id,
                rule_version_id=r.rule_version_id,
                issues=list(r.issues or []),
                reason=r.reason,
                raw_payload=dict(r.raw_payload or {}),
            )
            for r in rows
        ],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/batches/{batch_id}/rejects.csv",
    summary="The rejected rows, as a file to fix and re-upload",
    dependencies=[Depends(_view)],
)
async def rejects_csv(db: DbSession, batch_id: str) -> StreamingResponse:
    """The original columns plus why each row failed.

    A CSV rather than JSON because the person fixing it is working in a
    spreadsheet — the whole point is that they correct the cells and send the
    file back, not that they read a stack trace.
    """
    await _batch(db, batch_id)
    rows = (
        await db.execute(
            select(RuleIngestionRecord)
            .where(
                RuleIngestionRecord.batch_id == batch_id,
                RuleIngestionRecord.decision.in_(
                    [Decision.REJECTED, Decision.QUARANTINED]
                ),
            )
            .order_by(RuleIngestionRecord.source_offset)
        )
    ).scalars().all()

    buffer = io.StringIO()
    headings: list[str] = []
    for row in rows:
        for key in (row.raw_payload or {}):
            if key not in headings:
                headings.append(key)

    writer = csv.writer(buffer)
    writer.writerow(["_row", "_decision", "_reason", *headings])
    for row in rows:
        payload = row.raw_payload or {}
        writer.writerow(
            [
                # 1-based and counting the header, so it matches what the
                # operator sees in their spreadsheet.
                row.source_offset + 2,
                row.decision,
                row.reason,
                *[payload.get(h, "") for h in headings],
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="rejects-{batch_id}.csv"'
        },
    )


@router.post(
    "/batches/{batch_id}/cancel",
    response_model=s.BatchDetail,
    summary="Abandon a batch awaiting approval",
)
async def cancel_batch(
    db: DbSession, batch_id: str, principal: Importer
) -> s.BatchDetail:
    batch = await _batch(db, batch_id)
    if batch.status in (kernel.BatchStatus.COMPLETED, kernel.BatchStatus.CANCELLED):
        raise ValidationFailedError(
            f"A {batch.status.lower()} batch cannot be cancelled.",
            details={"status": batch.status},
        )
    batch.status = kernel.BatchStatus.CANCELLED
    batch.approved_by = principal.id
    await db.flush()
    return await _detail(db, batch_id)


# --- Connector sync ---------------------------------------------------------


@router.post(
    "/connectors/{source_id}/sync",
    response_model=s.BatchDetail,
    status_code=201,
    summary="Pull a source system's rules through the kernel",
)
async def connector_sync(
    db: DbSession,
    source_id: str,
    principal: Importer,
    payload: s.ConnectorSync = Body(default_factory=lambda: s.ConnectorSync()),
) -> s.BatchDetail:
    """Fetch from a registered source and import it, with a real preview.

    The legacy connector import wrote directly to live pricing with no dry run,
    which is why a nightly delta could not be inspected before it changed what
    subscribers were charged. Here ``dry_run`` runs the identical path and rolls
    back, so the preview is the commit minus the commit.
    """
    from app.modules.connectors import adapters
    from app.modules.connectors import service as connector_service
    from app.modules.connectors.models import SourceSystem
    from app.modules.rules.ingest.adapters import payload as payload_adapter

    source = await db.get(SourceSystem, source_id)
    if source is None:
        raise NotFoundError(f"Source system '{source_id}' was not found.")

    # The vendor adapters already know how to read their own exports. Reusing
    # them means Ericsson's class-header flattening and BRM's RATE_PLAN nesting
    # are not re-derived here; only the conversion to a draft is new.
    fetched = await connector_service.fetch_payload(source, None)
    mapped, _spec = adapters.map_records(source.vendor, fetched)

    drafts = []
    raw_rows: list[dict[str, Any]] = []
    rejections: list[s.ParseRejection] = []
    for entry in mapped:
        if entry.canonical is None:
            rejections.append(
                s.ParseRejection(
                    offset=entry.index, message=entry.error, column=entry.field
                )
            )
            continue
        try:
            conversion = payload_adapter.convert(
                entry.canonical,
                source_system_code=source.code,
                default_effective_from=payload.effective_from,
                default_charging_mode=payload.default_charging_mode,
                default_currency=payload.default_currency,
                raw=entry.raw,
            )
        except payload_adapter.PayloadError as exc:
            rejections.append(
                s.ParseRejection(
                    offset=entry.index, message=exc.message, column=exc.field
                )
            )
            continue
        drafts.append(conversion.draft)
        raw_rows.append(dict(entry.raw))

    if not drafts and not rejections:
        raise ValidationFailedError(
            f"{source.name} returned no records. Check the connector's query or path."
        )

    result = await kernel.ingest(
        db,
        drafts,
        actor=_actor(principal),
        channel="CONNECTOR",
        source_system_code=source.code,
        import_mode=payload.import_mode,
        filename=f"{source.code} sync",
        dry_run=payload.dry_run,
        raw_records=raw_rows,
    )
    if payload.dry_run:
        detail = _detail_from_result(result, None)
        detail.parse_rejections = rejections
        return detail
    return await _detail(db, result.batch_id)


# --- Assembly ---------------------------------------------------------------


async def _batch(db: DbSession, batch_id: str) -> RuleIngestionBatch:
    batch = await db.get(RuleIngestionBatch, batch_id)
    if batch is None:
        raise NotFoundError(f"Import batch '{batch_id}' was not found.")
    return batch


def _summary(batch: RuleIngestionBatch) -> s.BatchSummary:
    counts = dict(batch.counts or {})
    return s.BatchSummary(
        batch_id=batch.batch_id,
        channel=batch.channel,
        source_system_id=batch.source_system_id,
        filename=batch.filename,
        import_mode=batch.import_mode,
        status=batch.status,
        dry_run=batch.dry_run,
        counts=counts,
        duration_ms=batch.duration_ms,
        started_at=batch.started_at,
        completed_at=batch.completed_at,
        triggered_by_name=batch.triggered_by_name,
        # A batch that changed or withdrew anything touched live pricing, and the
        # approvals inbox needs to sort on that rather than on row counts.
        touched_live_pricing=bool(
            counts.get(Decision.CHANGED) or counts.get(Decision.WITHDRAWN)
        ),
    )


async def _detail(db: DbSession, batch_id: str) -> s.BatchDetail:
    batch = await _batch(db, batch_id)
    reasons = list(batch.reasons or [])
    decisions = dict(
        (
            await db.execute(
                select(RuleIngestionRecord.decision, func.count())
                .where(RuleIngestionRecord.batch_id == batch_id)
                .group_by(RuleIngestionRecord.decision)
            )
        ).all()
    )
    return s.BatchDetail(
        **_summary(batch).model_dump(),
        reasons=reasons,
        checkpoint=dict(batch.checkpoint or {}),
        error=batch.error,
        record_decisions={k: int(v) for k, v in decisions.items()},
    )


def _detail_from_result(
    result: kernel.BatchResult, parsed: files.ParsedFile | None
) -> s.BatchDetail:
    """A dry run's result, assembled in memory — the rows were rolled back."""
    counts = dict(result.counts)
    return s.BatchDetail(
        batch_id=result.batch_id,
        channel="FILE",
        source_system_id=None,
        filename=None,
        import_mode=ImportMode.DELTA,
        status=result.status,
        dry_run=True,
        counts=counts,
        duration_ms=result.duration_ms,
        started_at=None,
        completed_at=None,
        triggered_by_name=None,
        touched_live_pricing=not result.safe_to_auto_commit,
        reasons=result.reasons,
        checkpoint={},
        error=None,
        record_decisions={
            decision: sum(1 for r in result.records if r.decision == decision)
            for decision in {r.decision for r in result.records}
        },
        parse_rejections=[
            s.ParseRejection(offset=r.offset, message=r.message, column=r.column)
            for r in (parsed.rejections if parsed else [])
        ],
    )


async def _survey(
    db: DbSession, drafts: list
) -> list[references.MissingCatalogue]:
    """What the catalogue is missing for this batch, grouped by catalogue."""
    from app.core.config import settings

    cache = await resolver.build(db, settings.default_tenant_id, with_rule_index=False)
    return await references.survey(db, drafts, cache)


def _missing_out(
    missing: list[references.MissingCatalogue],
) -> list[s.MissingMetadata]:
    return [s.MissingMetadata(**entry.as_dict()) for entry in missing]


def _preview(
    parsed: files.ParsedFile,
    result: kernel.BatchResult,
    filename: str,
    missing: list[references.MissingCatalogue] | None = None,
) -> s.PreviewResponse:
    return s.PreviewResponse(
        filename=filename,
        headings=parsed.headings,
        mapping={k: v for k, v in parsed.mapping.items() if v},
        unmapped_headings=parsed.unmapped_headings,
        row_count=len(parsed.rows),
        content_hash=parsed.content_hash,
        counts=dict(result.counts),
        reasons=result.reasons,
        safe_to_auto_commit=result.safe_to_auto_commit,
        notes=sorted(set(parsed.notes)),
        missing_metadata=_missing_out(missing or []),
        parse_rejections=[
            s.ParseRejection(offset=r.offset, message=r.message, column=r.column)
            for r in parsed.rejections[:100]
        ],
        sample=[
            s.PreviewRow(
                offset=record.offset,
                rule_key=record.rule_key,
                rule_name=record.rule_name,
                decision=record.decision,
                issues=list(record.issues),
                reason=record.reason,
            )
            for record in result.records[:50]
        ],
    )
