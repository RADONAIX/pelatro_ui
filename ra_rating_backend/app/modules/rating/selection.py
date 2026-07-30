"""Intelligent rule selection (§12).

The requirement's constraint: *do not evaluate 10,000 rules for each of 1 crore
CDRs.* The design that satisfies it:

1. CDRs collapse to distinct **rating contexts** (~100k for 1 crore rows).
2. Candidates are fetched per context with one indexed query — the wildcard join
   described in ``compiler/constants.py``.
3. The winner per stage is chosen by specificity → priority → version.
4. The decision is cached in ``context_rule_map`` and joined back to every CDR.

So rule resolution runs ~100k times, not 10^11 times.

The four fallback levels in §12 are not coded as four passes: they *emerge* from
the specificity ordering, because an exact-match rule scores higher than a
product rule, which scores higher than a service rule, which scores higher than
an unconditional default.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cdr.enrich import context_values
from app.modules.compiler.models import ExecutableRule
from app.modules.rules.constants import STAGE_ORDER, Operator


@dataclass
class Candidate:
    rule: ExecutableRule
    selected: bool = False
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule.id,
            "rule_key": self.rule.rule_key,
            "rule_version": self.rule.rule_version,
            "rule_name": self.rule.rule_name,
            "stage": self.rule.execution_stage,
            "specificity": self.rule.specificity,
            "priority": self.rule.priority,
            "signature": self.rule.signature,
            "selected": self.selected,
            "reason": self.reason,
        }


async def fetch_candidates(
    db: AsyncSession,
    *,
    snapshot_id: str,
    context: dict[str, Any],
    event_date: date,
) -> list[ExecutableRule]:
    """Rules whose dimensions are compatible with this context.

    One query, and every predicate is a column comparison against an indexed
    table — ``NULL`` on the rule side means "any", which is why this is a join
    rather than a scan.
    """
    stmt = select(ExecutableRule).where(
        ExecutableRule.snapshot_id == snapshot_id,
        ExecutableRule.effective_from <= event_date,
        or_(
            ExecutableRule.effective_to.is_(None),
            ExecutableRule.effective_to >= event_date,
        ),
    )

    for column in (
        "service_type", "product_code", "offer_code", "tariff_plan_code",
        "destination_zone", "origin_zone", "time_band", "account_type",
        "network_type", "rating_group", "roaming", "on_net",
    ):
        value = context.get(column)
        attribute = getattr(ExecutableRule, column)
        if value is None:
            # The context does not carry this dimension, so only rules that
            # leave it open can match. A rule demanding a product cannot apply
            # to a CDR whose product could not be resolved.
            stmt = stmt.where(attribute.is_(None))
        else:
            stmt = stmt.where(or_(attribute.is_(None), attribute == value))

    stmt = stmt.order_by(
        ExecutableRule.stage_order,
        ExecutableRule.specificity.desc(),
        ExecutableRule.priority.desc(),
        ExecutableRule.rule_version.desc(),
    )
    return list((await db.execute(stmt)).scalars().all())


def _dimension_sets_match(rule: ExecutableRule, context: dict[str, Any]) -> bool:
    """Check the IN-style dimensions the column comparison left open."""
    for column, allowed in (rule.dimension_sets or {}).items():
        value = context.get(column)
        if value is None:
            return False
        if isinstance(value, bool):
            value = "TRUE" if value else "FALSE"
        if str(value).upper() not in allowed:
            return False
    return True


def evaluate_predicate(predicate: dict[str, Any], facts: dict[str, Any]) -> bool:
    """Evaluate one residual condition against a CDR's facts.

    These are the conditions that could not be reduced to a dimension. They run
    against the handful of candidates a context already matched, so a Python
    evaluator here is not on the hot path.
    """
    attribute = predicate["attribute"]
    operator = predicate["operator"]
    values = predicate.get("values") or []
    actual = facts.get(attribute)

    if operator == Operator.EXISTS:
        result = actual not in (None, "")
    elif operator == Operator.NOT_EXISTS:
        result = actual in (None, "")
    elif actual is None:
        # An absent fact cannot satisfy a positive comparison. Treating it as a
        # match would silently price usage on a rule that was never meant for it.
        result = operator in {Operator.NOT_EQUALS, Operator.NOT_IN}
    else:
        result = _compare(operator, actual, values)

    return not result if predicate.get("negate") else result


def _compare(operator: str, actual: Any, values: list[Any]) -> bool:
    def num(value: Any) -> Decimal | None:
        try:
            return Decimal(str(value))
        except Exception:
            return None

    text = str(actual).upper()
    upper = [str(v).upper() for v in values]

    match operator:
        case Operator.EQUALS:
            return text == upper[0] if upper else False
        case Operator.NOT_EQUALS:
            return text != upper[0] if upper else True
        case Operator.IN:
            return text in upper
        case Operator.NOT_IN:
            return text not in upper
        case Operator.STARTS_WITH:
            return text.startswith(upper[0]) if upper else False
        case Operator.CONTAINS:
            return upper[0] in text if upper else False
        case _:
            left = num(actual)
            rights = [num(v) for v in values]
            if left is None or any(r is None for r in rights):
                return False
            match operator:
                case Operator.GREATER_THAN:
                    return left > rights[0]
                case Operator.LESS_THAN:
                    return left < rights[0]
                case Operator.GREATER_OR_EQUAL:
                    return left >= rights[0]
                case Operator.LESS_OR_EQUAL:
                    return left <= rights[0]
                case Operator.BETWEEN:
                    return len(rights) >= 2 and rights[0] <= left <= rights[1]
    return False


def predicates_match(rule: ExecutableRule, facts: dict[str, Any]) -> bool:
    """Residual predicates, honouring the rule's AND/OR group semantics."""
    predicates = rule.predicates or []
    if not predicates:
        return True

    groups: dict[int, list[bool]] = {}
    for predicate in predicates:
        index = int(predicate.get("group_index", 0))
        groups.setdefault(index, []).append(evaluate_predicate(predicate, facts))

    # Within a group: AND. Across groups: the rule's condition_logic.
    group_results = [all(results) for results in groups.values()]
    return any(group_results) if rule.condition_logic == "OR" else all(group_results)


def select_for_context(
    candidates: list[ExecutableRule],
    context: dict[str, Any],
    facts: dict[str, Any] | None = None,
) -> tuple[dict[str, ExecutableRule], list[Candidate]]:
    """Resolve one winner per charging stage.

    Returns ``(stage -> winning rule, every candidate with its verdict)``. The
    verdicts are what the trace shows as "rejected rules", which is how an
    analyst confirms the engine chose what they expected.
    """
    facts = facts or dict(context)
    verdicts: list[Candidate] = []
    by_stage: dict[str, list[ExecutableRule]] = {}

    for rule in candidates:
        entry = Candidate(rule=rule)
        if not _dimension_sets_match(rule, context):
            entry.reason = "a set-valued dimension excluded this context"
            verdicts.append(entry)
            continue
        if not predicates_match(rule, facts):
            entry.reason = "a condition on the CDR's values did not hold"
            verdicts.append(entry)
            continue
        by_stage.setdefault(rule.execution_stage, []).append(rule)
        verdicts.append(entry)

    selected: dict[str, ExecutableRule] = {}
    for stage, matches in by_stage.items():
        ordered = sorted(
            matches,
            key=lambda r: (r.specificity, r.priority, r.rule_version),
            reverse=True,
        )
        winner = ordered[0]
        selected[stage] = winner

        runner_up = ordered[1] if len(ordered) > 1 else None
        for entry in verdicts:
            if entry.rule.id == winner.id:
                entry.selected = True
                entry.reason = _win_reason(winner, runner_up)
            elif entry.rule.execution_stage == stage and not entry.reason:
                entry.reason = (
                    f"outranked by {winner.rule_key} "
                    f"(specificity {winner.specificity} vs {entry.rule.specificity}, "
                    f"priority {winner.priority} vs {entry.rule.priority})"
                )
    return selected, verdicts


def _win_reason(winner: ExecutableRule, runner_up: ExecutableRule | None) -> str:
    if runner_up is None:
        return "the only rule matching this context at this stage"
    if winner.specificity != runner_up.specificity:
        return f"most specific match (specificity {winner.specificity})"
    if winner.priority != runner_up.priority:
        return f"highest priority among equally specific rules ({winner.priority})"
    return f"highest version among equally ranked rules (v{winner.rule_version})"


def ambiguous_stages(
    candidates: list[ExecutableRule], selected: dict[str, ExecutableRule]
) -> list[str]:
    """Stages where the winner was not actually distinguishable.

    Reported as MULTIPLE_RULE_MATCH rather than hidden: the engine had to pick
    one, and the analyst needs to know the choice was arbitrary.
    """
    out: list[str] = []
    for stage, winner in selected.items():
        peers = [
            r
            for r in candidates
            if r.execution_stage == stage
            and r.id != winner.id
            and r.specificity == winner.specificity
            and r.priority == winner.priority
            and r.rule_version == winner.rule_version
        ]
        if peers:
            out.append(stage)
    return out


def stage_order_key(stage: str) -> int:
    return STAGE_ORDER.index(stage) if stage in STAGE_ORDER else len(STAGE_ORDER)


def facts_from_cdr(row: Any) -> dict[str, Any]:
    """The values residual predicates evaluate against."""
    timestamp: datetime | None = getattr(row, "event_timestamp", None)
    return {
        "service_type": row.service_type,
        "product": row.product_code,
        "offer": row.offer_code,
        "tariff_plan": row.tariff_plan_code,
        "destination_zone": row.destination_zone,
        "origin_zone": row.origin_zone,
        "time_band": row.time_band,
        "account_type": row.account_type,
        "roaming": row.roaming,
        "on_net": row.on_net,
        "network_type": row.network_type,
        "rating_group": row.rating_group,
        "duration_seconds": row.duration_seconds,
        "usage_volume": row.usage_volume,
        "calling_number": row.calling_number,
        "called_number": row.called_number,
        "msisdn": row.msisdn,
        "imsi": row.imsi,
        "subscriber_id": row.subscriber_id,
        "currency": row.currency,
        "apn": row.apn,
        "visited_operator": row.visited_operator,
        "event_timestamp": timestamp.isoformat() if timestamp else None,
        "day_of_week": (
            ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")[timestamp.weekday()]
            if timestamp
            else None
        ),
    }


def context_from_hash_key(key: str) -> dict[str, Any]:
    return context_values(key)
