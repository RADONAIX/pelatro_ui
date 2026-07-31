"""The ingestion kernel — one path into the canonical store.

Every rule that reaches ``ra_rule.*`` passes through here, whatever produced it:
the six-step wizard, a CSV upload, an Ericsson XML dump, a nightly Oracle BRM
pull. The adapters differ; from :class:`CanonicalDraft` onward, nothing does.

The eight steps, and why each is a step rather than a line in a loop:

1. **STAGE** — open a batch row and hash the payload. A completed batch with the
   same ``(source, content_hash)`` is returned as-is, so re-posting the same file
   is idempotent rather than a second import.
2. **NORMALIZE** — the adapter's job, done before we are called. Drafts arrive.
3. **TYPE** — the codec turns every value into its declared type. Money becomes
   ``Decimal`` here and stays ``Decimal`` to the column.
4. **RESOLVE** — one batched query per catalogue for the *whole* batch, plus one
   pre-loaded rule index. This is what turns a 40,000-row import from hours into
   minutes: the legacy importers did both lookups per row, inside the commit loop.
5. **VALIDATE** — structural, mode coherence, then semantic.
6. **RECONCILE** — new / changed / unchanged / withdrawn, decided in memory
   against the pre-loaded index.
7. **COMMIT** — chunked, with a savepoint and a checkpoint per chunk, so a
   failure at row 39,000 resumes instead of restarting.
8. **PROJECT** — specificity, behaviour hash and ``canonical_json``, computed in
   the same transaction as the rows they describe.

**Dry run is the same code path.** ``dry_run=True`` runs 1-8 and rolls back at
the end. A preview produced by a different code path from the commit is a
preview that can be wrong in exactly the cases that matter, and the legacy
connector had no preview at all — it wrote straight to live pricing.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ValidationFailedError
from app.modules.rules.canonical import fingerprint
from app.modules.rules.canonical.draft import CanonicalDraft
from app.modules.rules.canonical.lineage import RuleIngestionBatch, RuleIngestionRecord
from app.modules.rules.constants import RuleStatus
from app.modules.rules.ingest import keys, reconcile, resolver, writer
from app.modules.rules.ingest.reconcile import Decision, ImportMode, Outcome
from app.modules.rules.validation import registry as validation
from app.modules.rules.validation.issues import Report, error
from app.modules.rules.vocabulary.modes import ValidationState

#: Rows per savepoint. Small enough that a failure loses little work, large
#: enough that the per-chunk flush is not the dominant cost.
CHUNK_SIZE = 500


class SourceSystemUnknown(ValidationFailedError):
    """The batch names a source system that is not registered.

    A precondition rather than a row error: nothing about the file is wrong, so
    reporting it per row would send an operator hunting through their data for a
    problem that is in their request.
    """


class BatchStatus:
    STAGED = "STAGED"
    VALIDATING = "VALIDATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMMITTING = "COMMITTING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class RecordResult:
    """What happened to one incoming rule, in the shape the preview screen wants."""

    offset: int
    rule_key: str
    decision: str
    rule_name: str = ""
    rule_id: str | None = None
    rule_version_id: str | None = None
    issues: list[dict[str, str]] = field(default_factory=list)
    reason: str = ""

    @property
    def committed(self) -> bool:
        return self.decision in (Decision.NEW, Decision.CHANGED)


@dataclass(slots=True)
class BatchResult:
    batch_id: str
    status: str
    dry_run: bool
    counts: dict[str, int]
    records: list[RecordResult]
    #: Grouped rejection reasons. Four hundred rows failing for one reason is one
    #: fix, and an operations team must see that rather than four hundred lines.
    reasons: list[dict[str, Any]]
    duration_ms: int
    #: True when nothing in this batch would alter a price that is already live,
    #: which is the only condition under which auto-commit is permitted.
    safe_to_auto_commit: bool

    @property
    def valid(self) -> bool:
        return not self.counts.get(Decision.REJECTED) and not self.counts.get(
            Decision.QUARANTINED
        )


@dataclass(slots=True)
class Actor:
    id: str | None
    name: str


async def ingest(
    db: AsyncSession,
    drafts: Iterable[CanonicalDraft],
    *,
    actor: Actor,
    channel: str = "MANUAL",
    source_system_code: str | None = None,
    import_mode: str = ImportMode.DELTA,
    filename: str | None = None,
    content_hash: str | None = None,
    dry_run: bool = False,
    tenant_id: str | None = None,
    status: str = RuleStatus.DRAFT,
    stop_on_error: bool = False,
    raw_records: Sequence[Any] | None = None,
    rule_set_code: str | None = None,
    promote_clean: bool = False,
    force_version: bool = False,
) -> BatchResult:
    """Run one batch through all eight steps. The caller owns the commit.

    ``stop_on_error`` is for the wizard, where a batch is one rule and a partial
    success is meaningless. Imports leave it off: one malformed row out of 40,000
    must not cost the other 39,999.

    ``raw_records`` are the source records the drafts were normalised from, in the
    same order. Supplied by a file or connector adapter and persisted verbatim to
    ``rule_ingestion_record``, so "what did they actually send us?" is answerable
    without re-running the parser that may itself be the problem. The wizard
    passes nothing — a hand-authored rule has no upstream record, and inventing
    one would put fiction in a forensic table.

    ``rule_set_code`` joins every rule this batch writes to a set, which is what
    makes the import addressable as one thing afterwards — validate it, approve
    it, compile it, roll it back.

    ``promote_clean`` lands a rule that validated with no errors at ``VALIDATED``
    rather than ``DRAFT``. Only imports use it. The validation genuinely happened
    during ingestion, and landing at DRAFT means the first bulk action an
    operator takes is always a re-validation that changes nothing — a ceremonial
    click that teaches people to click without reading. A rule with warnings or
    errors still lands at ``DRAFT``, because those are the ones a human should
    look at.
    """
    started = time.perf_counter()
    tenant = tenant_id or settings.default_tenant_id
    drafts = list(drafts)

    # --- 1 STAGE ----------------------------------------------------------
    cache = await resolver.build(db, tenant)
    source_system_id = cache.source_system_id(source_system_code)

    # A batch-level precondition, checked before a single row is interpreted.
    # The writer rejects an unregistered source too, but by then every row has
    # been typed, resolved and validated, and the failure arrives as N quarantined
    # records with a commit error — which reads as "my file is broken" when the
    # truth is "that source system does not exist yet". One wrong argument should
    # not look like a thousand wrong rows.
    if source_system_code and source_system_id is None:
        raise SourceSystemUnknown(
            f"Source system '{source_system_code}' is not registered. Register it "
            "under Data Sources first — a rule whose origin cannot be identified "
            "has no lineage, and the estate loses the ability to say where its "
            "prices came from.",
            details={"source_system_code": source_system_code},
        )

    if content_hash:
        existing = await _completed_batch(db, tenant, source_system_id, content_hash)
        if existing is not None:
            return _replay(existing)

    batch = RuleIngestionBatch(
        tenant_id=tenant,
        channel=channel,
        source_system_id=source_system_id,
        filename=filename,
        content_hash=content_hash,
        import_mode=import_mode,
        status=BatchStatus.VALIDATING,
        dry_run=dry_run,
        triggered_by=actor.id,
        triggered_by_name=actor.name,
    )
    db.add(batch)
    await db.flush()

    summary = reconcile.Summary()
    records: list[RecordResult] = []

    # --- 2-6: prepare every draft before writing any of them --------------
    # Deliberately a separate pass. A batch half-written and then rejected on its
    # nine-hundredth row leaves a savepoint to unwind and an operator with no idea
    # how far it got.
    prepared: list[tuple[int, CanonicalDraft, str, Outcome, Report]] = []
    for offset, draft in enumerate(drafts):
        rule_key, outcome, report = await _prepare(
            draft, cache, source_system_code, summary, force_version=force_version
        )
        if not report.valid:
            summary.record(Decision.REJECTED)
            records.append(
                RecordResult(
                    offset, rule_key, Decision.REJECTED, draft.rule_name,
                    issues=report.as_list(),
                    reason=report.errors[0].message if report.errors else "",
                )
            )
            if stop_on_error:
                break
            continue

        summary.record(outcome.decision)
        summary.seen_keys.add(rule_key)
        prepared.append((offset, draft, rule_key, outcome, report))

    # --- 6b WITHDRAWALS ---------------------------------------------------
    # Never applied here. A vendor omission silently retiring a rule that rates
    # four million CDRs a month is the exact failure this product exists to catch,
    # so it becomes a proposal in the approvals inbox instead.
    for withdrawal in reconcile.withdrawals(
        cache, summary.seen_keys,
        import_mode=import_mode, source_system_id=source_system_id,
    ):
        summary.record(Decision.WITHDRAWN)
        records.append(
            RecordResult(
                len(drafts) + len(records), withdrawal.rule_key, Decision.WITHDRAWN,
                rule_id=withdrawal.rule_id, reason=withdrawal.reason,
            )
        )

    # --- 7 COMMIT + 8 PROJECT --------------------------------------------
    batch.status = BatchStatus.COMMITTING
    committed = 0
    for chunk_start in range(0, len(prepared), CHUNK_SIZE):
        chunk = prepared[chunk_start : chunk_start + CHUNK_SIZE]
        committed_in_chunk = 0
        try:
            async with db.begin_nested():
                for offset, draft, rule_key, outcome, report in chunk:
                    records.append(
                        await _write_one(
                            db, draft, rule_key, outcome, report,
                            cache=cache, batch=batch, tenant=tenant,
                            actor=actor, channel=channel,
                            status=_status_for(status, report, promote_clean),
                            offset=offset, rule_set_code=rule_set_code,
                        )
                    )
                    committed += 1
                    committed_in_chunk += 1
        except Exception as exc:
            # The chunk's savepoint has rolled back, taking every rule in it —
            # including the ones that were fine. Retry them one at a time, each
            # in its own savepoint, so the blast radius of one bad rule is one
            # rule.
            #
            # Without this, a single legacy rule naming a conflict group the
            # canonical vocabulary lacks quarantines the entire chunk, and on an
            # estate smaller than CHUNK_SIZE that is the entire estate. The
            # backfill then writes nothing and reports its cause as "the chunk
            # containing this rule could not be written", which names the symptom
            # and not one of the rules responsible.
            #
            # The fast path stays fast: this costs a savepoint per row only on
            # the chunks that actually failed.
            batch.status = BatchStatus.PARTIAL
            batch.error = str(exc)
            del records[len(records) - committed_in_chunk :]
            committed -= committed_in_chunk
            for offset, draft, rule_key, outcome, report in chunk:
                try:
                    async with db.begin_nested():
                        records.append(
                            await _write_one(
                                db, draft, rule_key, outcome, report,
                                cache=cache, batch=batch, tenant=tenant,
                                actor=actor, channel=channel,
                                status=_status_for(status, report, promote_clean),
                                offset=offset, rule_set_code=rule_set_code,
                            )
                        )
                        committed += 1
                except Exception as row_exc:
                    records.append(
                        RecordResult(
                            offset, rule_key, Decision.QUARANTINED, draft.rule_name,
                            issues=[error("commit_failed", str(row_exc)).as_dict()],
                            reason=str(row_exc),
                        )
                    )
                    summary.record(Decision.QUARANTINED)
        batch.checkpoint = {"committed_through": chunk_start + len(chunk)}

    _write_records(db, batch, records, drafts, raw_records, tenant)

    duration_ms = int((time.perf_counter() - started) * 1000)
    batch.counts = {"read": len(drafts), **summary.counts}
    batch.reasons = _group_reasons(records)
    batch.duration_ms = duration_ms
    batch.completed_at = datetime.now(UTC)
    if batch.status != BatchStatus.PARTIAL:
        batch.status = BatchStatus.COMPLETED
    await db.flush()

    result = BatchResult(
        batch_id=batch.batch_id,
        status=batch.status,
        dry_run=dry_run,
        counts=dict(batch.counts),
        records=records,
        reasons=list(batch.reasons),
        duration_ms=duration_ms,
        safe_to_auto_commit=not summary.touches_live_pricing,
    )

    if dry_run:
        # Preview and commit are literally the same path; the only difference is
        # that this one is thrown away. Anything else and the preview is a
        # second implementation that can disagree with the thing it previews.
        await db.rollback()
    return result


# --- Steps 2-6 for one draft -------------------------------------------------


async def _prepare(
    draft: CanonicalDraft,
    cache: resolver.ResolutionCache,
    source_system_code: str | None,
    summary: reconcile.Summary,
    *,
    force_version: bool = False,
) -> tuple[str, Outcome, Report]:
    del summary
    rule_key = keys.derive(draft, source_code=source_system_code)
    report = await validation.check_draft(draft, cache)
    incoming_hash = fingerprint.behaviour_hash(draft)
    outcome = reconcile.decide(draft, rule_key, cache, incoming_hash)
    if force_version and outcome.decision == Decision.UNCHANGED:
        outcome = Outcome(
            Decision.CHANGED,
            rule_key,
            outcome.rule_id,
            reason="A new version was explicitly requested.",
        )
    return rule_key, outcome, report


async def _write_one(
    db: AsyncSession,
    draft: CanonicalDraft,
    rule_key: str,
    outcome: Outcome,
    report: Report,
    *,
    cache: resolver.ResolutionCache,
    batch: RuleIngestionBatch,
    tenant: str,
    actor: Actor,
    channel: str,
    status: str,
    offset: int,
    rule_set_code: str | None = None,
) -> RecordResult:
    """Write one prepared draft, or record that it did not need writing."""
    if outcome.decision == Decision.UNCHANGED:
        # No version cut. This is the decision that keeps a nightly full dump
        # from producing 4,000 versions a day and burying the three that moved.
        # The *descriptive* fields still follow the source, though: a vendor
        # renaming a plan is not a tariff change, but it is a change, and a
        # catalogue showing last quarter's name for a rule is a catalogue nobody
        # can reconcile against the vendor's document.
        await _refresh_descriptions(db, draft, outcome.rule_id, actor)
        # Membership is not a version. The rule was in this import, so it is in
        # this import's rule set — otherwise a re-imported unchanged file yields
        # an empty set, and every bulk action against it silently does nothing.
        if rule_set_code and outcome.rule_id:
            await writer.link_to_sets(
                db, outcome.rule_id, [rule_set_code], tenant, cache
            )
        return RecordResult(
            offset, rule_key, Decision.UNCHANGED, draft.rule_name,
            rule_id=outcome.rule_id, issues=report.as_list(), reason=outcome.reason,
        )

    stamped = _with_batch(draft, batch.batch_id, rule_set_code)
    result = await writer.write(
        db, stamped, cache=cache, rule_key=rule_key,
        actor_id=actor.id, actor_name=actor.name, tenant_id=tenant,
        channel=channel, status=status, validation_state=report.state,
    )
    await validation.persist(db, result.version.rule_version_id, tenant, report)
    return RecordResult(
        offset, rule_key, outcome.decision, draft.rule_name,
        rule_id=result.rule.rule_id,
        rule_version_id=result.version.rule_version_id,
        issues=report.as_list(),
        reason=outcome.reason,
    )


def _write_records(
    db: AsyncSession,
    batch: RuleIngestionBatch,
    records: Sequence[RecordResult],
    drafts: Sequence[CanonicalDraft],
    raw_records: Sequence[Any] | None,
    tenant: str,
) -> None:
    """One forensic row per incoming record.

    Skipped entirely when there are no raw records — the wizard authors a rule
    from nothing upstream, and a table of invented payloads is worse than an
    empty one, because it looks like evidence.

    Withdrawal outcomes have no incoming record by definition (a withdrawal *is*
    an absence), so they are recorded with the reason and no payload rather than
    with a fabricated one.
    """
    if not raw_records:
        return

    for record in records:
        raw: Any = {}
        if 0 <= record.offset < len(raw_records):
            raw = raw_records[record.offset]
        draft = drafts[record.offset] if 0 <= record.offset < len(drafts) else None

        db.add(
            RuleIngestionRecord(
                batch_id=batch.batch_id,
                tenant_id=tenant,
                source_offset=record.offset,
                external_ref=(
                    draft.provenance.external_ref if draft is not None else None
                ),
                raw_payload=_jsonable(raw),
                raw_hash=fingerprint.record_hash(_jsonable(raw)),
                canonical_payload=(
                    dict(draft.provenance.unmapped or {}) if draft is not None else None
                ),
                decision=record.decision,
                rule_id=record.rule_id,
                rule_version_id=record.rule_version_id,
                issues=list(record.issues),
                reason=record.reason,
            )
        )


def _jsonable(value: Any) -> Any:
    """Raw records arrive as dicts of strings from a parser, but a connector may
    hand back dates or Decimals. JSONB takes neither."""
    from datetime import date
    from decimal import Decimal

    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


async def _refresh_descriptions(
    db: AsyncSession, draft: CanonicalDraft, rule_id: str | None, actor: Actor
) -> None:
    """Follow the source's name and description without cutting a version.

    Deliberately narrow: only fields the behaviour hash deliberately excludes.
    Anything the hash covers would be a behaviour change, and a behaviour change
    that edited a live version in place is exactly what makes a February rating
    result impossible to re-explain in August.
    """
    if rule_id is None:
        return
    from app.modules.rules.canonical.rule import CanonicalRule

    rule = await db.get(CanonicalRule, rule_id)
    if rule is None:
        return
    if rule.rule_name == draft.rule_name and rule.description == draft.description:
        return
    rule.rule_name = draft.rule_name
    rule.description = draft.description
    rule.updated_by = actor.id


def _status_for(default: str, report: Report, promote_clean: bool) -> str:
    """Where a rule lands, given how cleanly it validated.

    A rule that passed with no issues at all is `VALIDATED`; anything with a
    warning or an error stays `DRAFT`, because a warning is precisely the case a
    human should read before the rule moves on.
    """
    if not promote_clean or default != RuleStatus.DRAFT:
        return default
    return RuleStatus.VALIDATED if report.state == ValidationState.PASS else default


def _with_batch(
    draft: CanonicalDraft, batch_id: str, rule_set_code: str | None = None
) -> CanonicalDraft:
    """Stamp the batch and the import's rule set onto the draft.

    Drafts are frozen, so this rebuilds rather than mutates — which is the
    property that stops a validation pass leaving a half-modified draft behind.
    """
    from dataclasses import replace

    set_codes = draft.set_codes
    if rule_set_code and rule_set_code not in set_codes:
        set_codes = (*set_codes, rule_set_code)
    return replace(
        draft,
        set_codes=set_codes,
        provenance=replace(draft.provenance, batch_id=batch_id),
    )


# --- Idempotency -------------------------------------------------------------


async def _completed_batch(
    db: AsyncSession, tenant: str, source_system_id: str | None, content_hash: str
) -> RuleIngestionBatch | None:
    """A finished batch for this exact payload, if there is one.

    Only COMPLETED batches count. A failed run must be retryable with the same
    file, which is the whole reason someone re-posts it.
    """
    stmt = select(RuleIngestionBatch).where(
        RuleIngestionBatch.tenant_id == tenant,
        RuleIngestionBatch.content_hash == content_hash,
        RuleIngestionBatch.status == BatchStatus.COMPLETED,
        RuleIngestionBatch.dry_run.is_(False),
    )
    if source_system_id is None:
        stmt = stmt.where(RuleIngestionBatch.source_system_id.is_(None))
    else:
        stmt = stmt.where(RuleIngestionBatch.source_system_id == source_system_id)
    return (await db.execute(stmt.limit(1))).scalar_one_or_none()


def _replay(batch: RuleIngestionBatch) -> BatchResult:
    return BatchResult(
        batch_id=batch.batch_id,
        status=batch.status,
        dry_run=False,
        counts=dict(batch.counts or {}),
        records=[],
        reasons=list(batch.reasons or []),
        duration_ms=int(batch.duration_ms or 0),
        safe_to_auto_commit=True,
    )


def _group_reasons(records: Sequence[RecordResult]) -> list[dict[str, Any]]:
    """Collapse per-row failures into per-cause rows.

    A batch where 400 rows fail is almost never 400 problems. Showing it as 400
    lines is how an operator concludes the import is unusable, when in fact one
    missing destination zone would fix all of them.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        for issue in record.issues:
            if issue["severity"] != "ERROR":
                continue
            key = (issue["code"], issue["message"])
            entry = grouped.setdefault(
                key,
                {
                    "code": issue["code"],
                    "message": issue["message"],
                    "hint": issue.get("hint", ""),
                    "count": 0,
                    "examples": [],
                },
            )
            entry["count"] += 1
            if len(entry["examples"]) < 5:
                entry["examples"].append(record.rule_key)
    return sorted(grouped.values(), key=lambda e: -e["count"])
