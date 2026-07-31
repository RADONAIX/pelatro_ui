"""Rating-run orchestration: contexts → selection → rating → assurance.

    detect batch → build contexts → select rules per context
    → rate every CDR → compare with actual → classify → group exceptions

**On scale.** The expensive part of rating is *rule resolution*, and that runs
once per distinct rating context (~100k for 1 crore CDRs), not once per CDR —
which is the requirement's §27 constraint and is satisfied here. The per-CDR
arithmetic that follows is O(n) and runs in Python in this version, streaming in
chunks so memory stays flat. Pushing that arithmetic into bulk SQL is the
ClickHouse deployment step: the shape is already right (one decision per
context, joined back to every row), so it is a translation of
``_rate_chunk`` into a set-based statement, not a redesign.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.modules.balances.engine import BalanceEngine
from app.modules.balances.engine import to_quota_unit as balance_quantity
from app.modules.cdr.models import CdrBatch, CdrEnriched
from app.modules.compiler import service as snapshot_svc
from app.modules.compiler.models import ExecutableRule
from app.modules.rating import assurance, selection
from app.modules.rating.assurance import ExceptionGroup
from app.modules.rating.constants import CLEAN_STATUSES, AssuranceStatus, RunStatus
from app.modules.rating.engine import ENGINE_VERSION, rate_cdr
from app.modules.rating.models import (
    ContextRuleMap,
    ExceptionComment,
    RatingException,
    RatingResult,
    RatingRun,
)
from app.modules.rules.constants import ActionType, ExecutionStage

log = get_logger("rating")

#: Rows held in memory at once. Bounded so a 1-crore batch does not need
#: 1 crore ORM objects resident.
CHUNK_SIZE = 5_000

ZERO = Decimal("0")


def _dec(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else ZERO


async def start_run(
    db: AsyncSession,
    *,
    batch_id: str,
    actor_id: str,
    actor_name: str,
    snapshot_id: str | None = None,
    run_type: str = "INITIAL",
    replay_of_run_id: str | None = None,
) -> RatingRun:
    batch = await db.get(CdrBatch, batch_id)
    if batch is None:
        raise NotFoundError(f"CDR batch '{batch_id}' was not found.")

    if snapshot_id:
        snapshot = await snapshot_svc.get_snapshot(db, snapshot_id)
    else:
        snapshot = await snapshot_svc.active_snapshot(db)
        if snapshot is None:
            raise ValidationFailedError(
                "No rule snapshot is active.",
                details={"hint": "Compile and activate a snapshot before rating."},
            )

    enriched = int(
        (
            await db.execute(
                select(func.count()).select_from(CdrEnriched).where(
                    CdrEnriched.batch_id == batch_id
                )
            )
        ).scalar_one()
    )
    if enriched == 0:
        raise ValidationFailedError(
            "This batch has no enriched CDRs to rate.",
            details={"hint": "Run enrichment on the batch first."},
        )

    run = RatingRun(
        batch_id=batch_id,
        snapshot_id=snapshot.id,
        snapshot_version=snapshot.version,
        status=RunStatus.RUNNING.value,
        run_type=run_type,
        replay_of_run_id=replay_of_run_id,
        total_cdrs=enriched,
        started_at=datetime.now(UTC),
        triggered_by=actor_id,
        triggered_by_name=actor_name,
    )
    db.add(run)
    await db.flush()
    return run


async def execute_run(db: AsyncSession, run: RatingRun) -> RatingRun:
    """Run the pipeline to completion for one batch."""
    started = time.perf_counter()
    try:
        contexts = await _resolve_contexts(db, run)
        totals = await _rate_all(db, run, contexts)
        await _build_exceptions(db, run)

        run.status = RunStatus.COMPLETED.value
        run.rated_cdrs = totals["rated"]
        run.matched_count = totals["matched"]
        run.distinct_contexts = len(contexts)
        run.expected_revenue = totals["expected"]
        run.billed_revenue = totals["billed"]
        run.undercharge_total = totals["undercharge"]
        run.overcharge_total = totals["overcharge"]
        run.stats = {
            "by_status": totals["by_status"],
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            # The collapse ratio is the headline performance number: how many
            # CDRs each rule decision served.
            "cdrs_per_context": round(run.total_cdrs / max(1, len(contexts)), 1),
            "engine_version": ENGINE_VERSION,
        }
    except Exception as exc:
        run.status = RunStatus.FAILED.value
        run.error = str(exc)
        log.error("rating_run_failed", run_id=run.id, error=str(exc))
    finally:
        run.finished_at = datetime.now(UTC)
        await db.flush()
        await db.refresh(run)
    return run


async def _resolve_contexts(db: AsyncSession, run: RatingRun) -> dict[str, ContextRuleMap]:
    """Resolve the rule set for each distinct context — once, not per CDR."""
    rows = (
        await db.execute(
            select(
                CdrEnriched.context_hash,
                CdrEnriched.context_key,
                func.count().label("cdr_count"),
                func.min(CdrEnriched.event_date).label("event_date"),
                func.min(CdrEnriched.id).label("sample_id"),
            )
            .where(CdrEnriched.batch_id == run.batch_id)
            .group_by(CdrEnriched.context_hash, CdrEnriched.context_key)
        )
    ).all()

    maps: dict[str, ContextRuleMap] = {}
    for context_hash, context_key, cdr_count, event_date, sample_id in rows:
        if not context_hash:
            continue
        context = selection.context_from_hash_key(context_key)
        candidates = await selection.fetch_candidates(
            db,
            snapshot_id=run.snapshot_id,
            context=context,
            event_date=event_date,
        )
        # Residual predicates are evaluated against a representative CDR here so
        # the cached decision is complete; rules whose predicates depend on
        # per-CDR values (duration, called number) are re-checked per row below.
        sample = await db.get(CdrEnriched, sample_id)
        facts = selection.facts_from_cdr(sample) if sample else dict(context)
        selected, verdicts = selection.select_for_context(candidates, context, facts)

        entry = ContextRuleMap(
            run_id=run.id,
            context_hash=context_hash,
            context_key=context_key,
            cdr_count=cdr_count,
            selected_rules={stage: rule.id for stage, rule in selected.items()},
            candidates=[v.as_dict() for v in verdicts][:50],
            candidate_count=len(candidates),
        )
        db.add(entry)
        maps[context_hash] = entry

    await db.flush()
    log.info("contexts_resolved", run_id=run.id, contexts=len(maps))
    return maps


async def _rate_all(
    db: AsyncSession, run: RatingRun, contexts: dict[str, ContextRuleMap]
) -> dict[str, Any]:
    rule_ids = {
        rule_id for entry in contexts.values() for rule_id in entry.selected_rules.values()
    }
    rules_by_id: dict[str, ExecutableRule] = {}
    if rule_ids:
        for rule in (
            await db.execute(select(ExecutableRule).where(ExecutableRule.id.in_(rule_ids)))
        ).scalars().all():
            rules_by_id[rule.id] = rule

    # Ambiguity is a property of the context, so compute it once per context
    # rather than per CDR.
    ambiguity: dict[str, list[str]] = {}
    for context_hash, entry in contexts.items():
        selected = {
            stage: rules_by_id[rid]
            for stage, rid in entry.selected_rules.items()
            if rid in rules_by_id
        }
        matched = [
            rules_by_id[c["rule_id"]]
            for c in entry.candidates
            if c.get("selected") is False and c["rule_id"] in rules_by_id
        ]
        ambiguity[context_hash] = selection.ambiguous_stages(
            [*selected.values(), *matched], selected
        )

    totals = {
        "rated": 0,
        "matched": 0,
        "expected": ZERO,
        "billed": ZERO,
        "undercharge": ZERO,
        "overcharge": ZERO,
        "by_status": {},
    }

    # Bundles and tiered rates make rating order-dependent: the same three calls
    # in a different order produce different charges once an allowance runs out.
    balances = BalanceEngine(run_id=run.id)
    await balances.load(db)

    offset = 0
    while True:
        chunk = (
            await db.execute(
                select(CdrEnriched)
                .where(CdrEnriched.batch_id == run.batch_id)
                # Partition by owner, then strict event order within it. This
                # ordering IS the stateful contract — without it bundle
                # consumption is non-deterministic across runs.
                .order_by(
                    func.coalesce(
                        CdrEnriched.account_id,
                        CdrEnriched.subscriber_id,
                        CdrEnriched.msisdn,
                        CdrEnriched.id,
                    ),
                    CdrEnriched.event_timestamp,
                    CdrEnriched.id,
                )
                .limit(CHUNK_SIZE)
                .offset(offset)
            )
        ).scalars().all()
        if not chunk:
            break
        await _rate_chunk(
            db, run, list(chunk), contexts, rules_by_id, ambiguity, totals, balances
        )
        await db.flush()
        offset += CHUNK_SIZE

    totals["balances"] = await balances.flush(db)
    return totals


async def _rate_chunk(
    db: AsyncSession,
    run: RatingRun,
    chunk: list[CdrEnriched],
    contexts: dict[str, ContextRuleMap],
    rules_by_id: dict[str, ExecutableRule],
    ambiguity: dict[str, list[str]],
    totals: dict[str, Any],
    balances: BalanceEngine,
) -> None:
    """Rate one chunk.

    The stateless arithmetic here is what moves into bulk ClickHouse SQL at
    scale. The stateful part — bundle consumption and tier position — stays in
    a partitioned Python worker, because it cannot be expressed as a set
    operation over unordered rows.
    """
    for cdr in chunk:
        entry = contexts.get(cdr.context_hash or "")
        selected = (
            {
                stage: rules_by_id[rid]
                for stage, rid in entry.selected_rules.items()
                if rid in rules_by_id
            }
            if entry
            else {}
        )
        # A cached decision may include rules whose residual predicates depend
        # on this row's own values; drop the ones that do not actually hold.
        facts = selection.facts_from_cdr(cdr)
        selected = {
            stage: rule
            for stage, rule in selected.items()
            if selection.predicates_match(rule, facts)
        }

        # --- Stateful inputs, resolved before the pure calculation ---------
        consumption = None
        bundle_rule = selected.get(ExecutionStage.BUNDLE)
        if bundle_rule is not None:
            action = next(
                (
                    a
                    for a in (bundle_rule.actions or [])
                    if a.get("action_type") == ActionType.CONSUME_BUNDLE.value
                ),
                None,
            )
            code = str((action or {}).get("params", {}).get("bundle") or "")
            if code:
                bucket = await balances.bucket_for(db, cdr, code)
                if bucket is not None:
                    consumption = balances.consume(bucket, cdr, rule_key=bundle_rule.rule_key)

        tier_start = None
        base_rule = selected.get(ExecutionStage.BASE_CHARGE)
        if base_rule is not None and any(
            a.get("action_type") == ActionType.SET_TIERED_RATE.value
            for a in (base_rule.actions or [])
        ):
            tier_action = next(
                a
                for a in base_rule.actions
                if a.get("action_type") == ActionType.SET_TIERED_RATE.value
            )
            unit = str((tier_action.get("params") or {}).get("unit") or "SECOND")
            counter = await balances.counter_for(db, cdr, base_rule.rule_key, unit)
            quantity = balance_quantity(cdr, unit)
            tier_start = balances.advance_counter(counter, quantity)

        outcome = rate_cdr(cdr, selected, bundle=consumption, tier_start=tier_start)
        verdict = assurance.classify(
            cdr, outcome, ambiguous=ambiguity.get(cdr.context_hash or "", [])
        )

        result = RatingResult(
            run_id=run.id,
            cdr_enriched_id=cdr.id,
            cdr_id=cdr.cdr_id,
            context_hash=cdr.context_hash,
            subscriber_id=cdr.subscriber_id,
            msisdn=cdr.msisdn,
            service_type=cdr.service_type,
            product_code=cdr.product_code,
            destination_zone=cdr.destination_zone,
            time_band=cdr.time_band,
            event_date=cdr.event_date,
            selected_rule_ids={stage: rule.id for stage, rule in selected.items()},
            billable_quantity=outcome.billable_quantity,
            billable_unit=outcome.billable_unit,
            expected_base_charge=outcome.base_charge,
            expected_discount=outcome.discount,
            expected_tax=outcome.tax,
            expected_final_charge=outcome.final_charge,
            actual_charge=cdr.actual_charge,
            variance=verdict.variance,
            currency=outcome.currency or cdr.currency,
            bundle_code=outcome.bundle_code,
            bundle_consumed=outcome.bundle_consumed,
            bundle_overflow=outcome.bundle_overflow,
            unpriced_quantity=outcome.unpriced_quantity,
            status=verdict.status,
            root_cause=verdict.root_cause,
            trace=[
                *outcome.trace_dicts(),
                {
                    "step": len(outcome.trace) + 1,
                    "stage": "ASSURANCE",
                    "label": verdict.status,
                    "detail": verdict.explanation,
                    "value": str(verdict.variance),
                    "rule_key": None,
                },
            ],
            engine_version=ENGINE_VERSION,
        )
        db.add(result)

        totals["rated"] += 1
        totals["expected"] += _dec(outcome.final_charge)
        totals["billed"] += _dec(cdr.actual_charge)
        if verdict.status in CLEAN_STATUSES:
            totals["matched"] += 1
        # Signed, and kept apart: netting them would hide the exposure.
        if verdict.variance > 0:
            totals["undercharge"] += verdict.variance
        elif verdict.variance < 0:
            totals["overcharge"] += abs(verdict.variance)
        totals["by_status"][verdict.status] = totals["by_status"].get(verdict.status, 0) + 1


async def _build_exceptions(db: AsyncSession, run: RatingRun) -> None:
    """Collapse failing results into grouped, actionable exceptions."""
    groups: dict[str, ExceptionGroup] = {}

    offset = 0
    while True:
        chunk = (
            await db.execute(
                select(RatingResult)
                .where(
                    RatingResult.run_id == run.id,
                    RatingResult.status.notin_(list(CLEAN_STATUSES)),
                )
                .order_by(RatingResult.id)
                .limit(CHUNK_SIZE)
                .offset(offset)
            )
        ).scalars().all()
        if not chunk:
            break

        for result in chunk:
            rule_key = None
            base_rule_id = (result.selected_rule_ids or {}).get("BASE_CHARGE")
            if base_rule_id:
                rule = await db.get(ExecutableRule, base_rule_id)
                rule_key = rule.rule_key if rule else None

            key = "|".join(
                [
                    result.status,
                    result.root_cause or "UNKNOWN",
                    result.service_type or "*",
                    result.product_code or "*",
                    result.destination_zone or "*",
                    rule_key or "*",
                ]
            )
            group = groups.get(key)
            if group is None:
                group = ExceptionGroup(
                    group_key=key,
                    assurance_status=result.status,
                    root_cause=result.root_cause or "UNKNOWN",
                    service_type=result.service_type,
                    product_code=result.product_code,
                    destination_zone=result.destination_zone,
                    rule_key=rule_key,
                )
                groups[key] = group

            explanation = ""
            for step in reversed(result.trace or []):
                if step.get("stage") == "ASSURANCE":
                    explanation = step.get("detail", "")
                    break

            group.add(
                result_id=result.id,
                subscriber=result.subscriber_id or result.msisdn,
                expected=_dec(result.expected_final_charge),
                actual=_dec(result.actual_charge) if result.actual_charge is not None else None,
                variance=_dec(result.variance),
                event_date=result.event_date,
                explanation=explanation,
            )
        offset += CHUNK_SIZE

    for group in groups.values():
        probable, action = assurance.advice_for(group.root_cause)
        db.add(
            RatingException(
                run_id=run.id,
                group_key=group.group_key,
                title=assurance.title_for(group),
                assurance_status=group.assurance_status,
                root_cause=group.root_cause,
                severity=assurance.severity_for(group.revenue_impact, group.cdr_count),
                service_type=group.service_type,
                product_code=group.product_code,
                destination_zone=group.destination_zone,
                rule_key=group.rule_key,
                cdr_count=group.cdr_count,
                subscriber_count=len(group.subscribers),
                revenue_impact=group.revenue_impact,
                expected_total=group.expected_total,
                actual_total=group.actual_total,
                first_event_date=group.first_event_date,
                last_event_date=group.last_event_date,
                sample_result_id=group.sample_result_id,
                probable_cause=f"{group.sample_explanation} {probable}".strip(),
                recommended_action=action,
                details={"sample_explanation": group.sample_explanation},
            )
        )

    run.exception_count = len(groups)
    await db.flush()
    log.info("exceptions_built", run_id=run.id, groups=len(groups))


# --- Exception workflow -----------------------------------------------------


async def get_exception(db: AsyncSession, exception_id: str) -> RatingException:
    # Comments are loaded eagerly: the detail schema serialises them, and a
    # lazy load from an async session dies with MissingGreenlet.
    row = (
        await db.execute(
            select(RatingException)
            .options(selectinload(RatingException.comments))
            .where(RatingException.id == exception_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Exception '{exception_id}' was not found.")
    return row


async def transition_exception(
    db: AsyncSession,
    exception_id: str,
    *,
    status: str,
    comment: str,
    assigned_to: str | None,
    assigned_to_name: str | None,
    resolution: str | None,
    actor_id: str,
    actor_name: str,
) -> RatingException:
    from app.modules.rating.constants import EXCEPTION_TRANSITIONS, ExceptionStatus

    row = await get_exception(db, exception_id)
    allowed = EXCEPTION_TRANSITIONS.get(row.status, ())
    if status != row.status and status not in allowed:
        raise ConflictError(
            f"An exception cannot move from '{row.status}' to '{status}'.",
            details={"allowed": list(allowed)},
        )

    previous = row.status
    row.status = status
    if assigned_to is not None:
        row.assigned_to = assigned_to or None
        row.assigned_to_name = assigned_to_name
    if resolution is not None:
        row.resolution = resolution
    now = datetime.now(UTC)
    if status == ExceptionStatus.RESOLVED:
        row.resolved_at = now
    elif status == ExceptionStatus.CLOSED:
        row.closed_at = now

    body = comment or f"Status changed from {previous} to {status}."
    db.add(
        ExceptionComment(
            exception_id=row.id,
            kind="STATUS" if status != previous else "COMMENT",
            body=body,
            author_id=actor_id,
            author_name=actor_name,
        )
    )
    await db.flush()
    await db.refresh(row)
    return row


async def run_summary(db: AsyncSession, run_id: str) -> dict[str, Any]:
    run = await db.get(RatingRun, run_id)
    if run is None:
        raise NotFoundError(f"Rating run '{run_id}' was not found.")

    rows = (
        await db.execute(
            select(RatingResult.status, func.count(), func.sum(RatingResult.variance))
            .where(RatingResult.run_id == run_id)
            .group_by(RatingResult.status)
        )
    ).all()
    match_rate = (run.matched_count / run.rated_cdrs * 100) if run.rated_cdrs else 0.0
    return {
        "run_id": run.id,
        "status": run.status,
        "snapshot_version": run.snapshot_version,
        "total_cdrs": run.total_cdrs,
        "rated_cdrs": run.rated_cdrs,
        "distinct_contexts": run.distinct_contexts,
        "match_rate": round(match_rate, 2),
        "expected_revenue": float(run.expected_revenue),
        "billed_revenue": float(run.billed_revenue),
        "revenue_leakage": float(run.undercharge_total),
        "customer_overcharge": float(run.overcharge_total),
        "exception_count": run.exception_count,
        "by_status": [
            {"status": status, "count": count, "variance": float(variance or 0)}
            for status, count, variance in rows
        ],
        "stats": run.stats,
    }


ASSURANCE_STATUSES = [s.value for s in AssuranceStatus]
