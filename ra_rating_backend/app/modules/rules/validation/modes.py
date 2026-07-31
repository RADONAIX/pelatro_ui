"""Mode coherence — the tier that keeps one engine from becoming two.

The whole "prepaid and postpaid share one rule model" claim rests on a single
mechanism: ``rule_stage.applies_to`` scopes each stage to a charging mode, and
``pipeline_for(mode)`` derives the sequence. That mechanism is only worth
anything if nothing can be stored outside its own pipeline. Without this tier a
``BOTH`` rule can carry ``DEDUCT_BALANCE``, compile into the common pipeline, and
then do nothing at all on a postpaid event — silently, because there is no error
anywhere and the rule looks correct on screen.

Four things are checked, and each of them corresponds to a way the model can be
made incoherent:

1. **Type against mode.** A ``MONTHLY_RENTAL`` rule declared ``PREPAID``.
2. **Action against pipeline.** An action whose stage is not in the rule's own
   pipeline — the ``BOTH`` + ``DEDUCT_BALANCE`` case above.
3. **Condition attribute against mode.** A prepaid rule conditioning on a
   billing cycle, which no prepaid event carries.
4. **Execution mode against stage.** An ``ONLINE`` rule that assembles an
   invoice line, or an ``OFFLINE`` rule that reserves session quota. Both are
   authorable and neither can ever fire.

Every message names the mode, the stage and the fix, because the author who hits
one of these has usually picked the wrong rule type rather than made a typo.
"""

from __future__ import annotations

from app.modules.rules.canonical.draft import CanonicalDraft
from app.modules.rules.validation.issues import Issue, error, warn
from app.modules.rules.vocabulary.actions import ACTION_BY_CODE
from app.modules.rules.vocabulary.attributes import (
    ATTRIBUTE_MODE_SCOPE,
    CANONICAL_ATTRIBUTE_BY_KEY,
)
from app.modules.rules.vocabulary.modes import ChargingMode, ExecutionMode
from app.modules.rules.vocabulary.stages import STAGE_BY_CODE, stage_codes_for
from app.modules.rules.vocabulary.types import RULE_TYPE_BY_CODE

#: Stages that only mean anything inside a live session: they read and write
#: reservation state against a sub-100 ms budget. A rule at one of these can
#: never fire in a bill run, so declaring it OFFLINE makes it dead configuration.
_ONLINE_ONLY_STAGES: frozenset[str] = frozenset(
    {"BALANCE_RESERVATION", "SESSION_CONTROL"}
)

#: Stages that only mean anything in a bill run: they need a whole billing period
#: of usage, which no single session has. An ONLINE rule here is equally dead.
_OFFLINE_ONLY_STAGES: frozenset[str] = frozenset(
    {
        "BILLING_CYCLE_ASSIGNMENT", "USAGE_AGGREGATION", "RECURRING_CHARGE",
        "ONE_TIME_CHARGE", "PRORATION", "INVOICE_COMPONENT", "INVOICE_TAX",
        "LATE_FEE", "INVOICE_ROUNDING",
    }
)


def check(draft: CanonicalDraft) -> list[Issue]:
    mode = draft.charging_mode
    if mode not in set(ChargingMode):
        return [
            error(
                "unknown_charging_mode",
                f"'{mode}' is not a charging mode.",
                "charging_mode",
                hint=f"One of: {', '.join(m.value for m in ChargingMode)}.",
            )
        ]

    issues: list[Issue] = []
    pipeline = stage_codes_for(mode)

    issues.extend(_type_against_mode(draft, mode))
    issues.extend(_actions_against_pipeline(draft, mode, pipeline))
    issues.extend(_attributes_against_mode(draft, mode))
    issues.extend(_execution_mode_against_stages(draft))
    return issues


def _type_against_mode(draft: CanonicalDraft, mode: str) -> list[Issue]:
    spec = RULE_TYPE_BY_CODE.get(draft.rule_type_code)
    if spec is None:
        return [
            error(
                "unknown_rule_type",
                f"'{draft.rule_type_code}' is not a rule type.",
                "rule_type",
            )
        ]
    if spec.charging_mode not in (ChargingMode.BOTH, mode):
        return [
            error(
                "rule_type_wrong_mode",
                f"{spec.name} is a {spec.charging_mode.lower()} rule type, but this "
                f"rule is declared {mode}.",
                "rule_type",
                hint=f"Change the charging mode to {spec.charging_mode}, or pick a "
                     "rule type that applies to this mode.",
            )
        ]
    if mode == ChargingMode.BOTH and spec.rule_category != "COMMON":
        return [
            error(
                "both_requires_common_type",
                f"{spec.name} is a {spec.rule_category.lower()} rule type, so it "
                "cannot be correct for both prepaid and postpaid.",
                "rule_type",
                hint="BOTH means the same rule text is right either way. Pick the "
                     "mode this rule actually belongs to.",
            )
        ]
    return []


def _actions_against_pipeline(
    draft: CanonicalDraft, mode: str, pipeline: frozenset[str]
) -> list[Issue]:
    """Every action must sit at a stage the rule's pipeline actually runs.

    This is the check the plan calls I5. The failure it prevents is not a crash:
    an out-of-pipeline action compiles cleanly and then never executes, so the
    rule shows green and charges nothing.
    """
    issues: list[Issue] = []
    for index, action in enumerate(draft.actions):
        spec = ACTION_BY_CODE.get(action.action_type)
        if spec is None:
            issues.append(
                error(
                    "unknown_action",
                    f"'{action.action_type}' is not a known action.",
                    f"actions[{index}].action_type",
                )
            )
            continue
        if spec.stage_code in pipeline:
            continue
        stage = STAGE_BY_CODE.get(spec.stage_code)
        belongs_to = stage.applies_to.lower() if stage else "another"
        issues.append(
            error(
                "action_outside_pipeline",
                f"{spec.label} runs at the {spec.stage_code} stage, which is a "
                f"{belongs_to} stage — a {mode} rule never reaches it.",
                f"actions[{index}].action_type",
                hint=(
                    "A rule marked BOTH may only use common stages. Set the "
                    f"charging mode to {belongs_to.upper()} if that is what this "
                    "rule is."
                    if mode == ChargingMode.BOTH
                    else f"Only {mode} and common actions can be used here."
                ),
            )
        )
    return issues


def _attributes_against_mode(draft: CanonicalDraft, mode: str) -> list[Issue]:
    """A condition on data the mode's events do not carry never matches.

    A warning rather than an error: an operator running a converged product may
    legitimately enrich a prepaid event with account data we do not know about,
    and blocking that would be us asserting more than we know.
    """
    issues: list[Issue] = []
    for index, cond in enumerate(draft.conditions):
        scope = ATTRIBUTE_MODE_SCOPE.get(cond.attribute)
        if scope is None or mode in scope:
            continue
        attr = CANONICAL_ATTRIBUTE_BY_KEY.get(cond.attribute)
        label = attr.label if attr else cond.attribute
        issues.append(
            warn(
                "attribute_outside_mode",
                f"{label} is a {'/'.join(s.lower() for s in scope)} attribute, and "
                f"this rule is {mode}. The condition is unlikely ever to match.",
                f"conditions[{index}].attribute",
                hint="Remove the condition, or change the rule's charging mode.",
            )
        )
    return issues


def _execution_mode_against_stages(draft: CanonicalDraft) -> list[Issue]:
    """An online engine must not carry rules it can never fire, and vice versa.

    This is what lets the compiler emit two snapshots from one rule set: without
    it, an online charging snapshot loads four thousand invoice rules that will
    not fire once, and the p95 selection budget goes with them.
    """
    execution_mode = draft.behaviour.execution_mode
    if execution_mode == ExecutionMode.BOTH:
        return []

    issues: list[Issue] = []
    for index, action in enumerate(draft.actions):
        spec = ACTION_BY_CODE.get(action.action_type)
        if spec is None:
            continue
        stage = spec.stage_code
        if execution_mode == ExecutionMode.OFFLINE and stage in _ONLINE_ONLY_STAGES:
            issues.append(
                error(
                    "offline_rule_at_online_stage",
                    f"{spec.label} only means anything inside a live session, but "
                    "this rule is marked OFFLINE, so it will never run.",
                    f"actions[{index}].action_type",
                    hint="Set the execution mode to ONLINE.",
                )
            )
        elif execution_mode == ExecutionMode.ONLINE and stage in _OFFLINE_ONLY_STAGES:
            issues.append(
                error(
                    "online_rule_at_offline_stage",
                    f"{spec.label} runs at {stage}, which happens in a bill run, but "
                    "this rule is marked ONLINE, so it will never run.",
                    f"actions[{index}].action_type",
                    hint="Set the execution mode to OFFLINE.",
                )
            )
    return issues
