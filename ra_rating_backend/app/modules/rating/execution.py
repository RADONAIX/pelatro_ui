"""The rating assurance batch (§23, §25).

    unprocessed usage → enrich → context → candidate rules → evaluate
    → resolve → plan → calculate → correlate actual → compare → persist

**Shape of the loop.** Records are read in fixed-size chunks, never all at once.
Per chunk: a handful of bulk reference queries, one rule load reused across the
whole run, in-memory evaluation, then bulk writes and a commit. Nothing here
issues a query per CDR, and no transaction spans the batch — a failure costs one
chunk, not an hour.

**Why rule resolution is cached per context.** Around fifty active rules and up
to ten lakh records is fifty million evaluations if done naively. But records
collapse into a few thousand distinct *rating situations*, and the rule decision
depends only on the situation. So resolution runs once per context and is reused;
the per-record work is the arithmetic and the conditions that genuinely depend on
this record's own values (duration, called number, group membership), which are
re-checked per row.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ValidationFailedError
from app.core.logging import get_logger
from app.modules.cdr import bulk_enrich
from app.modules.cdr.models import CdrEnriched
from app.modules.compiler import service as snapshot_svc
from app.modules.compiler.models import ExecutableRule
from app.modules.rating import comparison, plan, resolution
from app.modules.rating.actual_charge import ActualChargeService
from app.modules.rating.allowance import AllowanceReader
from app.modules.rating.audit_models import (
    CalculationComponent,
    RatingResultFinal,
    RatingStatus,
    RuleEvaluationAudit,
    UsageException,
    UsageExceptionStatus,
)
from app.modules.rating.engine import ENGINE_VERSION, rate_cdr

log = get_logger("rating.execution")

ZERO = Decimal("0")

#: processing_status values on cdr_enriched.
PENDING = "PENDING"
RATED = "RATED"
EXCEPTION = "EXCEPTION"


def _mask(number: str | None) -> str:
    """Mask a subscriber identifier for logs (§31)."""
    if not number:
        return "—"
    return f"***{number[-4:]}" if len(number) > 4 else "***"


@dataclass
class BatchSummary:
    batch_id: str
    total_records: int = 0
    processed_records: int = 0
    successful_records: int = 0
    exception_records: int = 0
    duplicate_records: int = 0
    status: str = "COMPLETED"
    by_status: dict[str, int] = field(default_factory=dict)
    by_enrichment: dict[str, int] = field(default_factory=dict)
    distinct_contexts: int = 0
    rules_loaded: int = 0
    expected_total: Decimal = ZERO
    actual_total: Decimal = ZERO
    undercharge_total: Decimal = ZERO
    overcharge_total: Decimal = ZERO
    duration_ms: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "total_records": self.total_records,
            "processed_records": self.processed_records,
            "successful_records": self.successful_records,
            "exception_records": self.exception_records,
            "duplicate_records": self.duplicate_records,
            "status": self.status,
            "by_status": self.by_status,
            "by_enrichment": self.by_enrichment,
            "distinct_contexts": self.distinct_contexts,
            "rules_loaded": self.rules_loaded,
            "expected_total": str(self.expected_total),
            "actual_total": str(self.actual_total),
            "undercharge_total": str(self.undercharge_total),
            "overcharge_total": str(self.overcharge_total),
            "duration_ms": round(self.duration_ms, 1),
            "error": self.error,
        }


def _new_batch_id() -> str:
    """``RATE-20260730-1A2B3C4D`` — sortable by day, unique within it."""
    return f"RATE-{datetime.now(UTC):%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


async def execute_rating_batch(
    db: AsyncSession,
    *,
    batch_size: int | None = None,
    limit: int | None = None,
    reprocess: bool = False,
    usage_ids: list[str] | None = None,
    snapshot_id: str | None = None,
    actor_id: str | None = None,
) -> BatchSummary:
    """Rate every unprocessed usage record and store the assurance verdict.

    ``reprocess`` re-rates records already rated; ``usage_ids`` restricts the
    run to named records (the re-rate endpoint). Both are safe to repeat: every
    write is an upsert keyed on ``usage_id``.
    """
    started = time.perf_counter()
    batch_size = batch_size or settings.rating_batch_size
    limit = limit if limit is not None else settings.maximum_records_per_run
    summary = BatchSummary(batch_id=_new_batch_id())

    snapshot = (
        await snapshot_svc.get_snapshot(db, snapshot_id)
        if snapshot_id
        else await snapshot_svc.active_snapshot(db)
    )
    if snapshot is None:
        raise ValidationFailedError(
            "No rule snapshot is active, so nothing can be rated.",
            details={"hint": "Compile and activate a rule snapshot first."},
        )

    # --- Rules: loaded once for the whole run (§12) ------------------------
    rules = list(
        (
            await db.execute(
                select(ExecutableRule).where(ExecutableRule.snapshot_id == snapshot.id)
            )
        ).scalars().all()
    )
    rules_by_key = {r.rule_key: r for r in rules}
    summary.rules_loaded = len(rules)

    summary.total_records = await _count_pending(db, reprocess=reprocess, usage_ids=usage_ids)
    if limit is not None:
        summary.total_records = min(summary.total_records, limit)

    # Resolution cache, keyed on (context_hash, event_date). Shared across
    # chunks, which is where most of the saving is: the same handful of
    # situations recur throughout a batch.
    cache: dict[tuple[str, date], resolution.Resolution] = {}
    charges = ActualChargeService()
    remaining = limit
    last_id: str | None = None

    try:
        while remaining is None or remaining > 0:
            size = batch_size if remaining is None else min(batch_size, remaining)
            chunk = await _next_chunk(
                db,
                after_id=last_id,
                size=size,
                reprocess=reprocess,
                usage_ids=usage_ids,
            )
            if not chunk:
                break
            last_id = chunk[-1].id

            await _process_chunk(
                db,
                chunk,
                summary=summary,
                rules=rules,
                rules_by_key=rules_by_key,
                snapshot_id=snapshot.id,
                cache=cache,
                charges=charges,
            )
            # One transaction per chunk (§25).
            await db.commit()

            if remaining is not None:
                remaining -= len(chunk)
    except Exception as exc:
        await db.rollback()
        summary.status = "FAILED"
        summary.error = str(exc)
        log.error("rating_batch_failed", batch_id=summary.batch_id, error=str(exc))

    summary.distinct_contexts = len(cache)
    summary.duration_ms = (time.perf_counter() - started) * 1000
    log.info("rating_batch_complete", **summary.as_dict())
    return summary


def _pending_filter(*, reprocess: bool, usage_ids: list[str] | None):
    conditions = [CdrEnriched.usage_id.is_not(None)]
    if usage_ids:
        conditions.append(CdrEnriched.usage_id.in_(usage_ids))
    elif not reprocess:
        conditions.append(CdrEnriched.processing_status == PENDING)
    return conditions


async def _count_pending(
    db: AsyncSession, *, reprocess: bool, usage_ids: list[str] | None
) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(CdrEnriched)
                .where(*_pending_filter(reprocess=reprocess, usage_ids=usage_ids))
            )
        ).scalar_one()
    )


async def _next_chunk(
    db: AsyncSession,
    *,
    after_id: str | None,
    size: int,
    reprocess: bool,
    usage_ids: list[str] | None,
) -> list[CdrEnriched]:
    """One page of work, keyset-paged on the primary key.

    Keyset rather than OFFSET: with OFFSET, page N re-scans and discards
    N x size rows, so a ten-lakh batch degrades quadratically. It also stays
    correct as rows are updated underneath the cursor, which they are — this
    loop marks the rows it processes.
    """
    conditions = _pending_filter(reprocess=reprocess, usage_ids=usage_ids)
    if after_id is not None:
        conditions.append(CdrEnriched.id > after_id)
    return list(
        (
            await db.execute(
                select(CdrEnriched).where(*conditions).order_by(CdrEnriched.id).limit(size)
            )
        ).scalars().all()
    )


async def _process_chunk(
    db: AsyncSession,
    chunk: list[CdrEnriched],
    *,
    summary: BatchSummary,
    rules: list[ExecutableRule],
    rules_by_key: dict[str, ExecutableRule],
    snapshot_id: str,
    cache: dict[tuple[str, date], resolution.Resolution],
    charges: ActualChargeService,
) -> None:
    # --- Enrich the whole chunk with a fixed number of queries (§8) --------
    reference = await bulk_enrich.load_reference(
        db,
        msisdns={row.msisdn for row in chunk if row.msisdn},
        event_days={row.event_date for row in chunk},
    )

    enriched: list[tuple[CdrEnriched, dict[str, Any]]] = []
    for row in chunk:
        values = bulk_enrich.assign(row, reference)
        _apply_enrichment(row, values)
        enriched.append((row, values))
        summary.by_enrichment[row.enrichment_status] = (
            summary.by_enrichment.get(row.enrichment_status, 0) + 1
        )

    # --- Allowances, read-only and bulk-loaded for the chunk (§18) ---------
    allowances = AllowanceReader()
    await allowances.load(db, [row for row, _ in enriched])

    # --- Actual charges, one bulk correlation for the chunk (§21) ----------
    actuals = await charges.for_chunk([row for row, _ in enriched])

    audit_rows: list[dict[str, Any]] = []
    component_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    exception_rows: list[dict[str, Any]] = []

    for row, values in enriched:
        summary.processed_records += 1
        outcome = _rate_one(
            row,
            values,
            rules=rules,
            rules_by_key=rules_by_key,
            cache=cache,
            actual=actuals.get(row.usage_id),
            allowances=allowances,
            batch_id=summary.batch_id,
        )
        audit_rows.extend(outcome["audit"])
        component_rows.extend(outcome["components"])
        result_rows.append(outcome["result"])
        if outcome["exception"]:
            exception_rows.append(outcome["exception"])

        verdict = outcome["result"]["rating_status"]
        summary.by_status[verdict] = summary.by_status.get(verdict, 0) + 1
        if verdict in {RatingStatus.MATCHED, RatingStatus.ZERO_CHARGED}:
            summary.successful_records += 1
        if outcome["exception"]:
            summary.exception_records += 1

        summary.expected_total += Decimal(str(outcome["result"]["expected_charge"]))
        if outcome["result"]["actual_charge"] is not None:
            summary.actual_total += Decimal(str(outcome["result"]["actual_charge"]))
        variance = Decimal(str(outcome["result"]["variance_amount"]))
        # Kept apart, never netted: a million of each is two serious problems,
        # not a clean book.
        if variance > ZERO:
            summary.overcharge_total += variance
        elif variance < ZERO:
            summary.undercharge_total += abs(variance)

        row.processing_status = EXCEPTION if outcome["exception"] else RATED

    await _write(db, audit_rows, component_rows, result_rows, exception_rows)


def _apply_enrichment(row: CdrEnriched, values: dict[str, Any]) -> None:
    """Write the enrichment back onto the usage row — one row, always."""
    for name in (
        "subscriber_id", "account_id", "product_code", "offer_code", "tariff_plan_code",
        "account_type", "subscriber_type", "customer_segment", "subscriber_groups",
        "destination_zone", "origin_zone", "on_net", "network_relation",
        "destination_type", "time_band", "day_type", "bundle_ids", "offer_ids",
        "rating_group", "context_key", "context_hash", "enrichment_status",
        "enrichment_error",
    ):
        if name in values:
            setattr(row, name, values[name])


def _rate_one(
    row: CdrEnriched,
    values: dict[str, Any],
    *,
    rules: list[ExecutableRule],
    rules_by_key: dict[str, ExecutableRule],
    cache: dict[tuple[str, date], resolution.Resolution],
    actual: Any,
    allowances: AllowanceReader,
    batch_id: str,
) -> dict[str, Any]:
    """Rate ONE usage record. Returns exactly one result row (§2)."""
    facts = resolution.facts_for(row)
    context_hash = row.context_hash or ""

    # Rules are matched in memory against this record's own facts. The cache
    # holds the *candidate set* per context; the evaluation that decides the
    # winner still sees this record's duration, called number and groups.
    key = (context_hash, row.event_date)
    candidates = _candidates_in_memory(rules, values, row.event_date)
    resolved = resolution.resolve(candidates, facts)
    cache[key] = resolved

    selected = resolved.selected
    base_rule = selected.get("BASE_CHARGE")
    rateable = values.get("rateable", False)

    expected = ZERO
    trace: list[dict[str, Any]] = []
    calculation_failed = False

    bundle = allowances.for_usage(row, selected)

    if rateable and base_rule is not None and not resolved.is_ambiguous:
        try:
            result = rate_cdr(
                row,
                selected,
                bundle=bundle,
                # §18/§28: the allowance reduces the duration, and what remains
                # is rounded up to whole pulses. Opt-in, so the existing
                # pipeline's convention is untouched.
                bundle_before_pulse=True,
            )
            expected = Decimal(str(result.final_charge))
            trace = result.trace_dicts()
            if result.error:
                calculation_failed = True
        except Exception as exc:
            calculation_failed = True
            log.warning(
                "rating_calculation_failed",
                batch_id=batch_id,
                usage_id=row.usage_id,
                msisdn=_mask(row.msisdn),
                error=str(exc),
            )

    verdict = comparison.compare(
        expected=expected,
        actual_charge=actual,
        enrichment_status=row.enrichment_status,
        has_base_rule=base_rule is not None,
        is_ambiguous=resolved.is_ambiguous,
        calculation_failed=calculation_failed,
    )

    calculation_plan = plan.build(row.usage_id, resolved)

    return {
        "audit": [
            {
                "usage_id": row.usage_id,
                "run_id": batch_id,
                "rule_id": v.rule.id,
                "rule_key": v.rule.rule_key,
                "rule_name": v.rule.rule_name,
                "rule_stage": v.stage,
                "evaluation_status": v.status,
                "rejection_reason": v.reason,
                "detail": v.detail,
                "priority": v.rule.priority,
                "specificity_score": v.rule.specificity,
                "rule_version": v.rule.rule_version,
                "exclusive_group": v.rule.conflict_group,
            }
            for v in resolved.verdicts
        ],
        "components": plan.components_from_trace(
            row.usage_id,
            trace,
            run_id=batch_id,
            rule_ids={k: r.id for k, r in rules_by_key.items()},
        ),
        "result": {
            "usage_id": row.usage_id,
            "charge_component": "TOTAL",
            "run_id": batch_id,
            "cdr_enriched_id": row.id,
            "subscriber_msisdn": row.msisdn,
            "service_type": row.service_type,
            "call_direction": row.call_direction,
            "event_date": row.event_date,
            "selected_base_rule_id": base_rule.id if base_rule else None,
            "selected_rule_ids": {
                stage: rule.id for stage, rule in selected.items()
            },
            "expected_charge": verdict.expected,
            "actual_charge": verdict.actual,
            "variance_amount": verdict.variance,
            "absolute_variance": verdict.absolute_variance,
            "variance_percentage": verdict.variance_percentage,
            "currency": row.currency or settings.default_currency,
            "rating_status": verdict.status,
            "enrichment_status": row.enrichment_status,
            "actual_charge_status": actual.status if actual else None,
            "explanation": verdict.explanation,
        },
        "exception": _exception_for(row, verdict, resolved, calculation_plan, batch_id),
    }


def _candidates_in_memory(
    rules: list[ExecutableRule], values: dict[str, Any], event_date: date
) -> list[ExecutableRule]:
    """Scope-match the pre-loaded rules against one context (§12).

    In memory rather than by query: with ~50 active rules this is a few hundred
    comparisons, and the alternative is a database round trip per context.
    """
    out: list[ExecutableRule] = []
    for rule in rules:
        if rule.effective_from and rule.effective_from > event_date:
            continue
        if rule.effective_to and rule.effective_to < event_date:
            continue
        if _scope_matches(rule, values):
            out.append(rule)
    return out


#: rule column -> the enriched field it is compared against. A NULL on the rule
#: is a wildcard (§5.2).
_SCOPE_FIELDS: tuple[tuple[str, str], ...] = (
    ("service_type", "service_type"),
    ("product_code", "product_code"),
    ("offer_code", "offer_code"),
    ("tariff_plan_code", "tariff_plan_code"),
    ("destination_zone", "destination_zone"),
    ("origin_zone", "origin_zone"),
    ("time_band", "time_band"),
    ("account_type", "account_type"),
    ("rating_group", "rating_group"),
)


def _scope_matches(rule: ExecutableRule, values: dict[str, Any]) -> bool:
    for column, field_name in _SCOPE_FIELDS:
        wanted = getattr(rule, column, None)
        if wanted is None:
            continue  # wildcard
        actual = values.get(field_name)
        if actual is None or str(actual).upper() != str(wanted).upper():
            return False
    for column, field_name in (("roaming", "roaming"), ("on_net", "on_net")):
        wanted = getattr(rule, column, None)
        if wanted is None:
            continue
        if bool(values.get(field_name)) != bool(wanted):
            return False
    return True


def _exception_for(
    row: CdrEnriched,
    verdict: comparison.Comparison,
    resolved: resolution.Resolution,
    calculation_plan: plan.CalculationPlan,
    batch_id: str,
) -> dict[str, Any] | None:
    """An exception row for the states that need investigation (§5.10).

    A variance is *not* an exception here — it is a result with a status, and
    the grouped exception module turns patterns of them into investigations. An
    exception is a record the pipeline could not process at all.
    """
    mapping = {
        RatingStatus.ENRICHMENT_FAILED: ("ENRICHMENT", row.enrichment_status),
        RatingStatus.NO_RULE_FOUND: ("RULE", "NO_RULE_FOUND"),
        RatingStatus.AMBIGUOUS_RULE: ("RULE", "AMBIGUOUS_RULE_MATCH"),
        RatingStatus.CALCULATION_FAILED: ("CALCULATION", "CALCULATION_FAILED"),
        RatingStatus.NO_ACTUAL_CHARGE: ("ACTUAL_CHARGE", "NO_ACTUAL_CHARGE"),
    }
    entry = mapping.get(verdict.status)
    if entry is None:
        return None
    exception_type, code = entry

    return {
        "usage_id": row.usage_id,
        "run_id": batch_id,
        "exception_type": exception_type,
        "exception_code": code,
        "error_message": verdict.explanation,
        "context": {
            "enrichment_status": row.enrichment_status,
            "enrichment_error": row.enrichment_error,
            "ambiguous_stages": resolved.ambiguous_stages,
            "ambiguity_detail": resolved.ambiguity_detail,
            "plan": calculation_plan.as_dict(),
            "context_key": row.context_key,
        },
        "status": UsageExceptionStatus.OPEN,
    }


#: PostgreSQL's extended-query Bind message counts parameters in a *signed*
#: 16-bit integer, so one statement carries at most 32,767 of them. A multi-row
#: INSERT spends (rows x columns) parameters, which means a 5,000-row chunk of a
#: 21-column table asks for 105,000 and the statement is rejected outright.
#:
#: This is a hard protocol limit, not a tuning knob: it cannot be raised, and it
#: only shows up once the batch size is realistic — which is exactly when it
#: matters. Inserts are therefore split into sub-batches sized from the actual
#: column count, with headroom below the ceiling.
_MAX_BIND_PARAMS = 30_000


def _param_safe_batches(model: Any, rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split rows so no single statement exceeds the bind-parameter limit.

    Sized from the **table's** column count, not the dict's. SQLAlchemy binds a
    parameter for every client-side default too — the UUID primary key,
    ``retry_count``, the status default — so a seven-key dict against an
    eleven-column table spends eleven parameters per row. Sizing on the dict
    undercounts by exactly that margin and the statement still blows the limit,
    which is how this was missed the first time.
    """
    if not rows:
        return []
    columns = max(1, len(model.__table__.columns))
    per_statement = max(1, _MAX_BIND_PARAMS // columns)
    return [rows[i : i + per_statement] for i in range(0, len(rows), per_statement)]


async def _write(
    db: AsyncSession,
    audit_rows: list[dict[str, Any]],
    component_rows: list[dict[str, Any]],
    result_rows: list[dict[str, Any]],
    exception_rows: list[dict[str, Any]],
) -> None:
    """Bulk upserts (§25).

    Every statement is an upsert on the natural key rather than an insert, so
    a re-run corrects the previous answer in place instead of appending a
    second opinion beside it.
    """
    if audit_rows:
        # Cleared and rewritten, not merged. The unique key is
        # (usage_id, rule_id), but rule_id is the *compiled* rule's id and a new
        # id is minted every time a snapshot is compiled. Upserting alone would
        # therefore leave the previous snapshot's verdicts sitting beside the
        # current ones, and the explanation would show two contradictory rule
        # sets for one charge. The audit describes the verdict that stands now.
        usage_ids = sorted({r["usage_id"] for r in audit_rows})
        await db.execute(
            RuleEvaluationAudit.__table__.delete().where(
                RuleEvaluationAudit.usage_id.in_(usage_ids)
            )
        )
        for batch in _param_safe_batches(RuleEvaluationAudit, audit_rows):
            await db.execute(pg_insert(RuleEvaluationAudit).values(batch))

    if component_rows:
        # Components are re-derived wholesale on each run, so stale steps from
        # a previous plan must go: a plan that no longer applies a discount must
        # not leave last run's discount row behind.
        usage_ids = sorted({r["usage_id"] for r in component_rows})
        await db.execute(
            CalculationComponent.__table__.delete().where(
                CalculationComponent.usage_id.in_(usage_ids)
            )
        )
        for batch in _param_safe_batches(CalculationComponent, component_rows):
            await db.execute(pg_insert(CalculationComponent).values(batch))

    for batch in _param_safe_batches(RatingResultFinal, result_rows):
        statement = pg_insert(RatingResultFinal).values(batch)
        await db.execute(
            statement.on_conflict_do_update(
                # THE §5.9 constraint: one final result per usage record per
                # charge component, whatever happens.
                index_elements=["usage_id", "charge_component"],
                set_={
                    name: getattr(statement.excluded, name)
                    for name in (
                        "run_id", "cdr_enriched_id", "selected_base_rule_id",
                        "selected_rule_ids", "expected_charge", "actual_charge",
                        "variance_amount", "absolute_variance", "variance_percentage",
                        "currency", "rating_status", "enrichment_status",
                        "actual_charge_status", "explanation", "updated_at",
                    )
                },
            )
        )

    for batch in _param_safe_batches(UsageException, exception_rows):
        statement = pg_insert(UsageException).values(batch)
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=["usage_id", "exception_type"],
                set_={
                    "exception_code": statement.excluded.exception_code,
                    "error_message": statement.excluded.error_message,
                    "context": statement.excluded.context,
                    "run_id": statement.excluded.run_id,
                    "status": statement.excluded.status,
                },
            )
        )


async def resolve_exceptions_for(db: AsyncSession, usage_ids: list[str]) -> int:
    """Close exceptions for records that now rate cleanly."""
    if not usage_ids:
        return 0
    clean = list(
        (
            await db.execute(
                select(RatingResultFinal.usage_id).where(
                    RatingResultFinal.usage_id.in_(usage_ids),
                    RatingResultFinal.rating_status.in_(
                        [RatingStatus.MATCHED, RatingStatus.ZERO_CHARGED]
                    ),
                )
            )
        ).scalars().all()
    )
    if not clean:
        return 0
    result = await db.execute(
        update(UsageException)
        .where(
            UsageException.usage_id.in_(clean),
            UsageException.status != UsageExceptionStatus.RESOLVED,
        )
        .values(status=UsageExceptionStatus.RESOLVED, resolved_at=datetime.now(UTC))
    )
    return int(result.rowcount or 0)


__all__ = ["ENGINE_VERSION", "BatchSummary", "execute_rating_batch", "resolve_exceptions_for"]
