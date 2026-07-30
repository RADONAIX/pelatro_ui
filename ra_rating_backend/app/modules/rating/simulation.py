"""Rule simulation (§20): what would this CDR be charged, and by which rules?

Simulation runs the **real** engine against the **real** compiled snapshot — it
is not a parallel implementation. A simulator that approximates the engine is
worse than none, because it builds confidence in a number the engine would not
produce.

Nothing is written. That is what makes it safe to run against a proposed
snapshot before it is activated.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailedError
from app.modules.cdr import enrich as enrichment
from app.modules.compiler import service as snapshot_svc
from app.modules.compiler.models import RuleSnapshot
from app.modules.rating import assurance, selection
from app.modules.rating.engine import rate_cdr


class _SyntheticCdr:
    """A CDR-shaped object the engine and enricher can both consume.

    Deliberately the same attribute surface as ``CdrEnriched`` rather than a
    dict: it means the simulator feeds the engine exactly what a real run does,
    so a divergence between them is impossible by construction.
    """

    __slots__ = (
        "account_type", "actual_charge", "apn", "called_number", "calling_number",
        "cdr_id", "context_hash", "context_key", "currency", "destination_zone",
        "duration_seconds", "event_date", "event_timestamp", "imsi", "msisdn",
        "network_type", "offer_code", "on_net", "origin_zone", "product_code",
        "quality_detail", "quality_status", "rating_group", "roaming",
        "service_type", "subscriber_id", "tariff_plan_code", "time_band",
        "usage_volume", "visited_operator",
    )

    def __init__(self, values: dict[str, Any]):
        for name in self.__slots__:
            setattr(self, name, values.get(name))


async def simulate(
    db: AsyncSession,
    *,
    service_type: str,
    event_timestamp: datetime | None = None,
    duration_seconds: float | None = None,
    usage_volume: float | None = None,
    calling_number: str | None = None,
    called_number: str | None = None,
    msisdn: str | None = None,
    actual_charge: float | None = None,
    currency: str | None = None,
    snapshot_id: str | None = None,
    # Overrides let an author test a context directly without inventing a
    # subscriber that resolves to it.
    product_code: str | None = None,
    destination_zone: str | None = None,
    time_band: str | None = None,
    account_type: str | None = None,
    roaming: bool | None = None,
) -> dict[str, Any]:
    snapshot: RuleSnapshot | None
    if snapshot_id:
        snapshot = await snapshot_svc.get_snapshot(db, snapshot_id)
    else:
        snapshot = await snapshot_svc.active_snapshot(db)
    if snapshot is None:
        raise ValidationFailedError(
            "No rule snapshot is available to simulate against.",
            details={"hint": "Compile a snapshot first."},
        )

    moment = event_timestamp or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)

    base = {
        "cdr_id": "SIMULATED",
        "service_type": service_type.upper(),
        "event_timestamp": moment,
        "event_date": moment.date(),
        "duration_seconds": duration_seconds,
        "usage_volume": usage_volume,
        "calling_number": calling_number or msisdn,
        "called_number": called_number,
        "msisdn": msisdn or calling_number,
        "currency": (currency or "").upper() or None,
        "roaming": roaming,
        "product_code": product_code,
    }

    # Enrich exactly as the pipeline would, then let explicit overrides win —
    # so the simulator answers both "what happens to this call?" and "what
    # happens in this context?".
    index = await enrichment.load_reference_index(db)
    enriched = enrichment.enrich(base, index)
    for key, value in (
        ("product_code", product_code),
        ("destination_zone", destination_zone),
        ("time_band", time_band),
        ("account_type", account_type),
        ("roaming", roaming),
    ):
        if value is not None:
            enriched[key] = value
    key, digest = enrichment.context_key(enriched)
    enriched["context_key"] = key
    enriched["context_hash"] = digest
    enriched["actual_charge"] = actual_charge

    cdr = _SyntheticCdr(enriched)
    context = enrichment.context_values(key)
    facts = selection.facts_from_cdr(cdr)

    candidates = await selection.fetch_candidates(
        db, snapshot_id=snapshot.id, context=context, event_date=cdr.event_date
    )
    selected, verdicts = selection.select_for_context(candidates, context, facts)
    ambiguous = selection.ambiguous_stages(candidates, selected)

    outcome = rate_cdr(cdr, selected)
    verdict = (
        assurance.classify(cdr, outcome, ambiguous=ambiguous)
        if actual_charge is not None
        else None
    )

    return {
        "snapshot_id": snapshot.id,
        "snapshot_version": snapshot.version,
        "context_key": key,
        "enrichment": {
            "product_code": enriched.get("product_code"),
            "offer_code": enriched.get("offer_code"),
            "tariff_plan_code": enriched.get("tariff_plan_code"),
            "account_type": enriched.get("account_type"),
            "destination_zone": enriched.get("destination_zone"),
            "origin_zone": enriched.get("origin_zone"),
            "on_net": enriched.get("on_net"),
            "time_band": enriched.get("time_band"),
            "roaming": enriched.get("roaming"),
            "quality_status": enriched.get("quality_status"),
        },
        "selected_rules": [
            {
                "stage": stage,
                "rule_key": rule.rule_key,
                "rule_version": rule.rule_version,
                "rule_name": rule.rule_name,
                "specificity": rule.specificity,
                "priority": rule.priority,
                "signature": rule.signature,
                "actions": rule.actions,
            }
            for stage, rule in sorted(selected.items(), key=lambda kv: kv[1].stage_order)
        ],
        # Every rule considered, and why each won or lost — the question an
        # author actually has is "why did it not pick mine?".
        "candidates": [v.as_dict() for v in verdicts],
        "candidate_count": len(candidates),
        "ambiguous_stages": ambiguous,
        "calculation": {
            "billable_quantity": float(outcome.billable_quantity),
            "billable_unit": outcome.billable_unit,
            "base_charge": float(outcome.base_charge),
            "discount": float(outcome.discount),
            "tax": float(outcome.tax),
            "expected_charge": float(outcome.final_charge),
            "currency": outcome.currency,
            "zero_rated": outcome.zero_rated,
            "missing_stages": outcome.missing_stages,
            "error": outcome.error,
        },
        "trace": outcome.trace_dicts(),
        "comparison": (
            {
                "actual_charge": actual_charge,
                "variance": float(verdict.variance),
                "status": verdict.status,
                "root_cause": verdict.root_cause,
                "explanation": verdict.explanation,
            }
            if verdict
            else None
        ),
    }


async def compare_snapshots(
    db: AsyncSession, *, from_snapshot_id: str, to_snapshot_id: str, **cdr: Any
) -> dict[str, Any]:
    """Rate the same CDR against two snapshots — the what-if before publishing.

    Answers the question a tariff change actually raises: *by how much would
    this reprice?*
    """
    before = await simulate(db, snapshot_id=from_snapshot_id, **cdr)
    after = await simulate(db, snapshot_id=to_snapshot_id, **cdr)
    delta = Decimal(str(after["calculation"]["expected_charge"])) - Decimal(
        str(before["calculation"]["expected_charge"])
    )
    return {
        "before": before,
        "after": after,
        "delta": float(delta),
        "changed": delta != 0,
        "rules_changed": [
            stage
            for stage in {r["stage"] for r in before["selected_rules"]}
            | {r["stage"] for r in after["selected_rules"]}
            if _rule_at(before, stage) != _rule_at(after, stage)
        ],
    }


def _rule_at(result: dict[str, Any], stage: str) -> tuple[str, int] | None:
    for rule in result["selected_rules"]:
        if rule["stage"] == stage:
            return rule["rule_key"], rule["rule_version"]
    return None


def default_event_date() -> date:
    return datetime.now(UTC).date()
