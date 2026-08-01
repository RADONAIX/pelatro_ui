"""Raising a case when a finished run breaches its threshold.

The rule carries the policy (raise or not, priority, owner, threshold); a run
produces the counts. This module is the only place the two meet.

Best-effort by design: the case service is a separate process on its own port,
and it being down must not fail a reconciliation that has already written its
results. A failure here is logged and the run still succeeds.

REGISTERING ON DEMAND
The case service keeps its own control-rule registry and rejects a case whose
ruleId it does not know. A rule's FIRST run happens inside the create request
that authored it — before anything has had a chance to register it there — so
that first case used to be lost to a 400 while every later run succeeded.
`_register_rule` closes that: an unknown-rule rejection registers the rule and
retries once, which makes the first run behave like every other one and also
covers a rule created directly against the API or replayed by the scheduler.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.reconciliation.plan import (
    KIND_DUPLICATE,
    KIND_RECONCILIATION,
    KIND_SEQUENCE,
    KIND_THRESHOLD,
    STATUS_MATCH,
    STATUS_PRESENT,
    ReconPlan,
)

log = get_logger("recon.cases")

#: The one HEALTHY status per kind. Everything else is a breach.
#:
#: Stated as what passes rather than as a list of what fails, deliberately. An
#: enumeration of failure statuses silently under-counts the moment a kind
#: gains an outcome nobody remembered to add here — and under-counting breaches
#: means a case that should have been raised is not. Inverting it makes the
#: safe direction the default: an unrecognised status is a breach.
_HEALTHY_STATUS: dict[str, str] = {
    KIND_RECONCILIATION: STATUS_MATCH,
    # A sequence's series is behaving when the expected value is PRESENT.
    KIND_SEQUENCE: STATUS_PRESENT,
}


def breached_rows(plan: ReconPlan, counts: dict[str, int]) -> int:
    """How many rows of this run count as a breach.

    Everything that is not the kind's healthy status. For a reconciliation that
    is every row that is not a MATCH — a mismatch, or a record present on one
    side only, or any outcome added later.

    A row-level rule (Duplicate, Threshold) only ever writes breaching rows —
    the report IS the breach set — so its total counts, whatever those rows are
    labelled. That matters because a threshold rule labels its rows with the
    rule's own name, which no fixed list could enumerate.
    """
    total = int(counts.get("total", 0))
    if plan.kind in (KIND_DUPLICATE, KIND_THRESHOLD):
        return total
    healthy = int(counts.get(_HEALTHY_STATUS.get(plan.kind, STATUS_MATCH), 0))
    # Never negative: `total` is the authority, and a missing count reads as 0.
    return max(0, total - healthy)


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


#: Our rule kinds, in the case service's own primitive-category vocabulary.
#: An unmapped kind registers with no category rather than a wrong one — the
#: field is optional there, and a value outside its list is rejected.
_CATEGORY_FOR_KIND = {
    KIND_RECONCILIATION: "Reconciliation",
    KIND_SEQUENCE: "Sequence",
    KIND_DUPLICATE: "Duplicate",
    KIND_THRESHOLD: "Threshold",
}

#: A control id opens with its assurance code — RA904 is Rating, CA910 Charging.
#: That is the case service's own numbering convention, and the only reliable
#: translation from our lowercase app ids ("rating") to its codes ("RA"), which
#: is what it resolves an assurance by.
_RULE_ID_PREFIX = re.compile(r"^([A-Z]{2,4})[0-9]+$")


def _assurance_code(plan: ReconPlan) -> str:
    match = _RULE_ID_PREFIX.match(plan.rule_id.strip().upper())
    return match.group(1) if match else plan.assurance_id


def _base() -> str:
    return settings.cases_api_base.rstrip("/")


async def _register_rule(client: httpx.AsyncClient, plan: ReconPlan) -> bool:
    """Register this rule with the case service. True when it is now known.

    Only what the case service needs to classify a case: the rest of the rule —
    its keys, metrics and generated SQL — stays here, where it is executed.
    A 409 counts as success; it means something else registered it first.
    """
    payload = {
        "id": plan.rule_id,
        "name": plan.rule_name,
        "assurance": _assurance_code(plan),
        "primitiveCategory": _CATEGORY_FOR_KIND.get(plan.kind, ""),
        "severity": plan.severity,
        "frequency": plan.frequency,
        "sourceFeed": f"{plan.left.database}:{plan.left.qualified}",
        "targetFeed": f"{plan.right.database}:{plan.right.qualified}",
        "lifecycleState": "Active",
        "createdBy": "reconciliation-engine",
    }
    response = await client.post(f"{_base()}/rules", json=payload)
    if response.status_code == 409:
        return True
    if response.is_success:
        log.info("recon_rule_registered", rule_id=plan.rule_id)
        return True
    log.warning(
        "recon_rule_register_failed",
        rule_id=plan.rule_id,
        status=response.status_code,
        detail=response.text[:200],
    )
    return False


def _rule_unknown(response: httpx.Response) -> bool:
    """Whether a rejection means "I do not know that rule" specifically.

    Narrow on purpose: a 400 for a bad severity or a malformed body must not
    trigger a registration attempt that cannot fix it.
    """
    return response.status_code == 400 and "unknown rule" in response.text.lower()


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
            response = await client.post(f"{_base()}/cases/ingest", json=payload)

            # A rule's first run happens inside the request that created it, so
            # the case service can legitimately not know the rule yet. Register
            # it and post once more, rather than losing the first finding.
            if _rule_unknown(response) and await _register_rule(client, plan):
                response = await client.post(f"{_base()}/cases/ingest", json=payload)

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
