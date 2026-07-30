"""Candidate selection, condition evaluation and rule resolution (§12–§15).

Three responsibilities, in order:

``candidates_for``   which rules could possibly apply to this situation
``evaluate``         do this rule's conditions actually hold for this record
``resolve``          which single rule wins each stage, and why the rest lost

**Evaluation is allow-listed, never executed (§13).** Conditions are data —
attribute, operator, values — matched against a fixed table of comparison
functions. Nothing from a rule upload is ever passed to ``eval``, ``exec`` or a
SQL string, so an uploaded rule file cannot become code.

**Ambiguity is fatal, not tie-broken (§15).** When two base-rate rules have the
same priority, the same specificity and the same version, the engine does not
pick one. There is no correct answer to pick, and an arbitrary winner produces a
charge that cannot be defended when the customer disputes it. The record is
raised as AMBIGUOUS_RULE_MATCH instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.compiler.models import ExecutableRule
from app.modules.rating.audit_models import EvaluationStatus, RejectionReason
from app.modules.rules.constants import STAGE_ORDER, ExecutionStage

# --- Operators (§13) --------------------------------------------------------
# The spec's vocabulary and the platform's canonical vocabulary say the same
# things with different words. Aliasing rather than renaming keeps existing
# rules, the compiler and the rule-authoring UI untouched.
_OPERATOR_ALIASES: dict[str, str] = {
    "IS_NULL": "NOT_EXISTS",
    "IS_NOT_NULL": "EXISTS",
    "GREATER_THAN_OR_EQUAL": "GREATER_OR_EQUAL",
    "LESS_THAN_OR_EQUAL": "LESS_OR_EQUAL",
}

SUPPORTED_OPERATORS: frozenset[str] = frozenset(
    {
        "EQUALS",
        "NOT_EQUALS",
        "IN",
        "NOT_IN",
        "CONTAINS",
        "NOT_CONTAINS",
        "GREATER_THAN",
        "GREATER_OR_EQUAL",
        "LESS_THAN",
        "LESS_OR_EQUAL",
        "BETWEEN",
        "EXISTS",
        "NOT_EXISTS",
        "STARTS_WITH",
    }
)

#: Stages where exactly one rule may apply (§14). Everything else may stack,
#: subject to the rule's own stacking policy.
SINGLE_RULE_STAGES: frozenset[str] = frozenset(
    {
        ExecutionStage.BASE_CHARGE,
        ExecutionStage.TARIFF_SELECTION,
        ExecutionStage.PULSE,
        ExecutionStage.MINIMUM_CHARGE,
        ExecutionStage.ROUNDING,
    }
)


class ConditionError(ValueError):
    """A condition names an operator this evaluator does not implement."""


def _normalise_operator(operator: str) -> str:
    name = str(operator or "").strip().upper()
    name = _OPERATOR_ALIASES.get(name, name)
    if name not in SUPPORTED_OPERATORS:
        raise ConditionError(f"Operator '{operator}' is not supported.")
    return name


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _as_list(value: Any) -> list[str] | None:
    """Whether the fact is list-valued, and its upper-cased members.

    ``subscriber_groups`` is the reason this exists: ``CONTAINS GOLD`` against a
    list must mean membership, not substring. Treating the list as a string
    would make ``CONTAINS OLD`` match ``GOLD``, quietly applying a loyalty rate
    to the wrong subscribers.
    """
    if isinstance(value, list | tuple | set | frozenset):
        return [str(v).upper() for v in value]
    return None


def compare(operator: str, actual: Any, values: list[Any]) -> bool:
    """Evaluate one comparison. Pure, total, and never raises on bad data."""
    name = _normalise_operator(operator)

    if name == "EXISTS":
        return actual not in (None, "") and actual != []
    if name == "NOT_EXISTS":
        return actual in (None, "") or actual == []

    if actual is None:
        # An absent fact cannot satisfy a positive comparison. Treating it as a
        # match would price usage on a rule that was never meant for it.
        return name in {"NOT_EQUALS", "NOT_IN", "NOT_CONTAINS"}

    members = _as_list(actual)
    wanted = [str(v).upper() for v in values]

    if members is not None:
        # List-valued facts: set semantics throughout.
        match name:
            case "CONTAINS" | "IN":
                return any(v in members for v in wanted)
            case "NOT_CONTAINS" | "NOT_IN":
                return not any(v in members for v in wanted)
            case "EQUALS":
                return sorted(members) == sorted(wanted)
            case "NOT_EQUALS":
                return sorted(members) != sorted(wanted)
            case _:
                return False

    text = str(actual).upper()
    match name:
        case "EQUALS":
            return bool(wanted) and text == wanted[0]
        case "NOT_EQUALS":
            return not wanted or text != wanted[0]
        case "IN":
            return text in wanted
        case "NOT_IN":
            return text not in wanted
        case "CONTAINS":
            return bool(wanted) and wanted[0] in text
        case "NOT_CONTAINS":
            return not wanted or wanted[0] not in text
        case "STARTS_WITH":
            return bool(wanted) and text.startswith(wanted[0])

    left = _decimal(actual)
    rights = [_decimal(v) for v in values]
    if left is None or not rights or any(r is None for r in rights):
        return False
    match name:
        case "GREATER_THAN":
            return left > rights[0]
        case "GREATER_OR_EQUAL":
            return left >= rights[0]
        case "LESS_THAN":
            return left < rights[0]
        case "LESS_OR_EQUAL":
            return left <= rights[0]
        case "BETWEEN":
            return len(rights) >= 2 and rights[0] <= left <= rights[1]
    return False


def evaluate(rule: ExecutableRule, facts: dict[str, Any]) -> tuple[bool, str]:
    """Whether every condition on the rule holds. Returns ``(matched, why not)``.

    Conditions within a group are ANDed; groups are combined by the rule's own
    ``condition_logic``. The failure sentence names the first condition that did
    not hold, because "did not match" without a reason is unactionable.
    """
    predicates = rule.predicates or []
    if not predicates:
        return True, ""

    groups: dict[int, list[tuple[bool, str]]] = {}
    for predicate in predicates:
        index = int(predicate.get("group_index", 0))
        attribute = predicate.get("attribute", "?")
        operator = predicate.get("operator", "EQUALS")
        values = predicate.get("values") or []
        try:
            outcome = compare(operator, facts.get(attribute), values)
        except ConditionError as exc:
            groups.setdefault(index, []).append((False, str(exc)))
            continue
        if predicate.get("negate"):
            outcome = not outcome
        actual = facts.get(attribute)
        detail = (
            f"{attribute} {operator} {values if len(values) != 1 else values[0]} "
            f"(actual: {actual!r})"
        )
        groups.setdefault(index, []).append((outcome, detail))

    group_results = {index: all(o for o, _ in items) for index, items in groups.items()}
    matched = (
        any(group_results.values())
        if rule.condition_logic == "OR"
        else all(group_results.values())
    )
    if matched:
        return True, ""

    for index, items in groups.items():
        if not group_results[index]:
            for outcome, detail in items:
                if not outcome:
                    return False, detail
    return False, "a condition did not hold"


async def candidates_for(
    db: AsyncSession, *, snapshot_id: str, context: dict[str, Any], event_date: date
) -> list[ExecutableRule]:
    """Rules whose scope is compatible with this context (§12).

    One indexed query per *context*, not per CDR. A NULL on the rule side is a
    wildcard; a NULL on the context side means the dimension could not be
    resolved, so only rules that leave it open may apply — a rule demanding a
    tariff plan must not price a record whose tariff is unknown.
    """
    statement = select(ExecutableRule).where(
        ExecutableRule.snapshot_id == snapshot_id,
        ExecutableRule.effective_from <= event_date,
        or_(ExecutableRule.effective_to.is_(None), ExecutableRule.effective_to >= event_date),
    )

    for column in (
        "service_type", "product_code", "offer_code", "tariff_plan_code",
        "destination_zone", "origin_zone", "time_band", "account_type",
        "network_type", "rating_group", "roaming", "on_net",
    ):
        value = context.get(column)
        attribute = getattr(ExecutableRule, column)
        statement = (
            statement.where(attribute.is_(None))
            if value is None
            else statement.where(or_(attribute.is_(None), attribute == value))
        )

    return list(
        (
            await db.execute(
                statement.order_by(
                    ExecutableRule.stage_order,
                    ExecutableRule.specificity.desc(),
                    ExecutableRule.priority.desc(),
                    ExecutableRule.rule_version.desc(),
                )
            )
        ).scalars().all()
    )


@dataclass
class Verdict:
    """One rule's fate against one usage record — the §5.7 audit row."""

    rule: ExecutableRule
    status: str
    reason: str | None = None
    detail: str = ""

    @property
    def stage(self) -> str:
        return self.rule.execution_stage


@dataclass
class Resolution:
    """The outcome of resolving every stage for one usage record."""

    #: stage -> the single winning rule.
    selected: dict[str, ExecutableRule] = field(default_factory=dict)
    #: stage -> additional rules that also apply (stackable discounts, taxes).
    stacked: dict[str, list[ExecutableRule]] = field(default_factory=dict)
    verdicts: list[Verdict] = field(default_factory=list)
    #: Stages where the winner was not distinguishable. Non-empty means the
    #: record must be raised as AMBIGUOUS_RULE_MATCH rather than rated.
    ambiguous_stages: list[str] = field(default_factory=list)
    ambiguity_detail: str = ""

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.ambiguous_stages)

    def rules_for(self, stage: str) -> list[ExecutableRule]:
        winner = self.selected.get(stage)
        return ([winner] if winner else []) + self.stacked.get(stage, [])


def _rank(rule: ExecutableRule) -> tuple[int, int, int]:
    """The §15 ordering key: priority, then specificity, then version."""
    return (rule.priority, rule.specificity, rule.rule_version)


def _stackable(rule: ExecutableRule) -> bool:
    policy = str(rule.stacking_policy or "").upper()
    return policy in {"STACKABLE", "ADDITIVE", "CUMULATIVE"}


def resolve(candidates: list[ExecutableRule], facts: dict[str, Any]) -> Resolution:
    """Evaluate every candidate and pick the winners (§14, §15)."""
    resolution = Resolution()
    matched_by_stage: dict[str, list[ExecutableRule]] = {}

    # --- Evaluate -----------------------------------------------------------
    for rule in candidates:
        ok, why = evaluate(rule, facts)
        if not ok:
            resolution.verdicts.append(
                Verdict(rule, EvaluationStatus.REJECTED, RejectionReason.CONDITION_FAILED, why)
            )
            continue
        matched_by_stage.setdefault(rule.execution_stage, []).append(rule)

    # --- Resolve, stage by stage -------------------------------------------
    for stage in STAGE_ORDER:
        matches = matched_by_stage.get(stage)
        if not matches:
            continue

        ordered = sorted(matches, key=_rank, reverse=True)
        winner = ordered[0]

        # Ambiguity: indistinguishable on every tie-breaker there is (§15).
        peers = [r for r in ordered[1:] if _rank(r) == _rank(winner)]
        if peers and stage in SINGLE_RULE_STAGES:
            resolution.ambiguous_stages.append(stage)
            resolution.ambiguity_detail = (
                f"{stage}: {winner.rule_key} and "
                f"{', '.join(p.rule_key for p in peers)} are equally ranked "
                f"(priority {winner.priority}, specificity {winner.specificity}, "
                f"version {winner.rule_version}). No rule can be chosen without guessing."
            )
            for rule in ordered:
                resolution.verdicts.append(
                    Verdict(rule, EvaluationStatus.MATCHED, None, resolution.ambiguity_detail)
                )
            continue

        resolution.selected[stage] = winner
        resolution.verdicts.append(
            Verdict(
                winner,
                EvaluationStatus.SELECTED,
                None,
                _win_reason(winner, ordered[1] if len(ordered) > 1 else None),
            )
        )

        # --- The losers, and precisely why they lost ------------------------
        exclusive_taken = {winner.conflict_group} if winner.conflict_group else set()
        for rule in ordered[1:]:
            if stage not in SINGLE_RULE_STAGES and _stackable(rule):
                if rule.conflict_group and rule.conflict_group in exclusive_taken:
                    # Same exclusive group as a rule already applied: stackable
                    # or not, only one member of a group may take effect (§19).
                    resolution.verdicts.append(
                        Verdict(
                            rule,
                            EvaluationStatus.MATCHED_NOT_SELECTED,
                            RejectionReason.EXCLUSIVE_GROUP_TAKEN,
                            f"exclusive group '{rule.conflict_group}' already applied",
                        )
                    )
                    continue
                resolution.stacked.setdefault(stage, []).append(rule)
                if rule.conflict_group:
                    exclusive_taken.add(rule.conflict_group)
                resolution.verdicts.append(
                    Verdict(rule, EvaluationStatus.SELECTED, None, "stacked after the winner")
                )
                continue

            resolution.verdicts.append(
                Verdict(
                    rule,
                    EvaluationStatus.MATCHED_NOT_SELECTED,
                    _loss_reason(winner, rule),
                    _loss_detail(winner, rule),
                )
            )

    return resolution


def _win_reason(winner: ExecutableRule, runner_up: ExecutableRule | None) -> str:
    if runner_up is None:
        return "the only rule matching this record at this stage"
    if winner.priority != runner_up.priority:
        return f"highest priority ({winner.priority} vs {runner_up.priority})"
    if winner.specificity != runner_up.specificity:
        return (
            f"most specific match at equal priority "
            f"(specificity {winner.specificity} vs {runner_up.specificity})"
        )
    return f"highest version among equally ranked rules (v{winner.rule_version})"


def _loss_reason(winner: ExecutableRule, loser: ExecutableRule) -> str:
    if loser.priority < winner.priority:
        return RejectionReason.LOWER_PRIORITY
    if loser.specificity < winner.specificity:
        return RejectionReason.LOWER_SPECIFICITY
    if loser.rule_version < winner.rule_version:
        return RejectionReason.LOWER_VERSION
    return RejectionReason.NOT_STACKABLE


def _loss_detail(winner: ExecutableRule, loser: ExecutableRule) -> str:
    return (
        f"outranked by {winner.rule_key} "
        f"(priority {winner.priority} vs {loser.priority}, "
        f"specificity {winner.specificity} vs {loser.specificity})"
    )


def facts_for(row: Any) -> dict[str, Any]:
    """The values conditions are evaluated against, for one enriched record.

    ``subscriber_groups`` is included as a **list**, which is what makes
    ``subscriber_groups CONTAINS GOLD`` a membership test on one record rather
    than a reason to duplicate it.
    """
    timestamp = getattr(row, "event_timestamp", None)
    return {
        "service_type": row.service_type,
        "call_direction": row.call_direction,
        "product": row.product_code,
        "offer": row.offer_code,
        "tariff_plan": row.tariff_plan_code,
        "tariff_plan_id": row.tariff_plan_code,
        "subscriber_type": row.subscriber_type or row.account_type,
        "customer_segment": row.customer_segment,
        "subscriber_groups": list(row.subscriber_groups or []),
        "destination_zone": row.destination_zone,
        "destination_type": row.destination_type,
        "network_relation": row.network_relation,
        "origin_zone": row.origin_zone,
        "time_band": row.time_band,
        "day_type": row.day_type,
        "account_type": row.account_type,
        "roaming": row.roaming,
        "roaming_flag": row.roaming,
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
        "bundle_ids": list(row.bundle_ids or []),
        "offer_ids": list(row.offer_ids or []),
        "event_timestamp": timestamp.isoformat() if timestamp else None,
        "day_of_week": (
            ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")[timestamp.weekday()]
            if timestamp
            else None
        ),
    }
