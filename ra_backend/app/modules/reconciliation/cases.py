"""Raising a case when a finished run breaches its threshold.

The rule carries the policy (raise or not, priority, owner, threshold); a run
produces the counts. This module is the only place the two meet.

Best-effort by design: the case service is a separate process on its own port,
and it being down must not fail a reconciliation that has already written its
results. A failure here is logged and the run still succeeds.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.reconciliation.plan import (
    KIND_RECONCILIATION,
    STATUS_GAP,
    STATUS_DUPLICATE,
    STATUS_MISMATCH,
    STATUS_PROCESSED_MISSING,
    STATUS_RAW_MISSING,
    ReconPlan,
)

log = get_logger("recon.cases")

#: Which statuses count as a breach, per kind.
#:
#: A reconciliation breach is any row that is not a MATCH — a mismatch, or a
#: record present on one side only. A sequence breach is a gap or a duplicate;
#: PRESENT rows are the series behaving.
_BREACH_STATUSES: dict[str, tuple[str, ...]] = {
    KIND_RECONCILIATION: (
        STATUS_MISMATCH,
        STATUS_RAW_MISSING,
        STATUS_PROCESSED_MISSING,
    ),
}
_SEQUENCE_BREACHES = (STATUS_GAP, STATUS_DUPLICATE)


def breached_rows(plan: ReconPlan, counts: dict[str, int]) -> int:
    """How many rows of this run count as a breach."""
    statuses = _BREACH_STATUSES.get(plan.kind, _SEQUENCE_BREACHES)
    return sum(int(counts.get(status, 0)) for status in statuses)


def should_raise(plan: ReconPlan, counts: dict[str, int]) -> tuple[bool, int]:
    """(raise?, breached rows).

    False whenever the author did not ask for a case — the threshold is not
    consulted at all in that case, so a rule set to "Don't raise a case" stays
    silent however badly it breaches.
    """
    routing = plan.case_routing or {}
    if not routing.get("raiseCase"):
        return False, breached_rows(plan, counts)
    breached = breached_rows(plan, counts)
    return breached >= max(1, plan.breach_threshold), breached


async def raise_case_if_breached(
    plan: ReconPlan, counts: dict[str, int], *, execution_id: str
) -> dict[str, Any] | None:
    """Post a case when the run breached at or above the rule's threshold.

    Returns the decision so the caller can report it, or None when the rule does
    not raise cases at all.
    """
    routing = plan.case_routing or {}
    if not routing.get("raiseCase"):
        return None

    raise_it, breached = should_raise(plan, counts)
    threshold = max(1, plan.breach_threshold)
    if not raise_it:
        log.info(
            "recon_case_below_threshold",
            rule_id=plan.rule_id,
            breached=breached,
            threshold=threshold,
        )
        return {"raised": False, "breachedRows": breached, "breachThreshold": threshold}

    payload = {
        "title": plan.rule_name,
        "description": (
            f"{breached:,} breached row(s) in execution {execution_id} "
            f"(threshold {threshold})."
        ),
        "assurance": plan.assurance_id,
        "ruleId": plan.rule_id,
        "ruleName": plan.rule_name,
        "severity": routing.get("priority") or plan.severity,
        "status": "Open",
        "owner": routing.get("owner") or "",
        "origin": "auto_detected",
        "affectedCount": breached,
        "createdBy": "reconciliation-engine",
    }

    try:
        async with httpx.AsyncClient(timeout=settings.recon_case_timeout_seconds) as client:
            response = await client.post(
                f"{settings.cases_api_base.rstrip('/')}/cases/ingest", json=payload
            )
            response.raise_for_status()
            created = response.json()
        log.info(
            "recon_case_raised",
            rule_id=plan.rule_id,
            breached=breached,
            threshold=threshold,
            reference=created.get("reference"),
        )
        return {
            "raised": True,
            "breachedRows": breached,
            "breachThreshold": threshold,
            "reference": created.get("reference"),
        }
    except Exception as exc:  # noqa: BLE001
        # The reconciliation itself succeeded and its results are published;
        # losing the case is a lesser failure than failing the run.
        log.warning(
            "recon_case_raise_failed",
            rule_id=plan.rule_id,
            breached=breached,
            error=str(exc),
        )
        return {
            "raised": False,
            "breachedRows": breached,
            "breachThreshold": threshold,
            "error": str(exc),
        }
