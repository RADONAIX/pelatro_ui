"""The calculation plan (§16) and its recorded components (§5.8).

**One plan per usage record, not one charge per rule.** Each selected rule
contributes a *step* to a single ordered calculation — a bundle reduces the
chargeable quantity, a base rate prices what is left, a discount reduces the
amount, tax adds to it. Letting each rule compute its own final amount and
summing them would double-count the base charge into every downstream stage and
produce a number with no relationship to the tariff.

The ordering is the charging sequence and is not configurable per rule:

    quantity → pulse → bundle → base → minimum/maximum
    → promotion → discount → surcharge → tax → rounding

Tax after discount, always. Taxing before discounting yields a different figure,
and getting it backwards produces a small, consistent variance on every single
CDR — the hardest kind of defect to spot and the most expensive to leave running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.modules.compiler.models import ExecutableRule
from app.modules.rating.audit_models import ComponentType
from app.modules.rating.resolution import Resolution
from app.modules.rules.constants import ExecutionStage

#: Plan order. Independent of STAGE_ORDER so that the *charging* sequence can be
#: reasoned about here without being coupled to the compiler's stage numbering.
PLAN_ORDER: tuple[str, ...] = (
    ExecutionStage.QUANTITY,
    ExecutionStage.PULSE,
    ExecutionStage.BUNDLE,
    ExecutionStage.BASE_CHARGE,
    ExecutionStage.MINIMUM_CHARGE,
    ExecutionStage.PROMOTION,
    ExecutionStage.DISCOUNT,
    ExecutionStage.SURCHARGE,
    ExecutionStage.TAX,
    ExecutionStage.ROUNDING,
)


@dataclass
class CalculationPlan:
    """What will be applied to one usage record, in what order (§16)."""

    usage_id: str
    base_rate_rule: str | None = None
    pulse_rule: str | None = None
    bundle_rules: list[str] = field(default_factory=list)
    minimum_rule: str | None = None
    promotion_rules: list[str] = field(default_factory=list)
    discount_rules: list[str] = field(default_factory=list)
    surcharge_rules: list[str] = field(default_factory=list)
    tax_rules: list[str] = field(default_factory=list)
    rounding_rule: str | None = None

    @property
    def has_base_rate(self) -> bool:
        return self.base_rate_rule is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "usage_id": self.usage_id,
            "base_rate_rule": self.base_rate_rule,
            "pulse_rule": self.pulse_rule,
            "bundle_rules": self.bundle_rules,
            "minimum_rule": self.minimum_rule,
            "promotion_rules": self.promotion_rules,
            "discount_rules": self.discount_rules,
            "surcharge_rules": self.surcharge_rules,
            "tax_rules": self.tax_rules,
            "rounding_rule": self.rounding_rule,
        }


def _keys(rules: list[ExecutableRule]) -> list[str]:
    return [r.rule_key for r in rules]


def build(usage_id: str, resolution: Resolution) -> CalculationPlan:
    """Turn resolved rules into one ordered plan."""
    selected = resolution.selected

    def key_of(stage: str) -> str | None:
        rule = selected.get(stage)
        return rule.rule_key if rule else None

    return CalculationPlan(
        usage_id=usage_id,
        base_rate_rule=key_of(ExecutionStage.BASE_CHARGE),
        pulse_rule=key_of(ExecutionStage.PULSE),
        bundle_rules=_keys(resolution.rules_for(ExecutionStage.BUNDLE)),
        minimum_rule=key_of(ExecutionStage.MINIMUM_CHARGE),
        promotion_rules=_keys(resolution.rules_for(ExecutionStage.PROMOTION)),
        discount_rules=_keys(resolution.rules_for(ExecutionStage.DISCOUNT)),
        surcharge_rules=_keys(resolution.rules_for(ExecutionStage.SURCHARGE)),
        tax_rules=_keys(resolution.rules_for(ExecutionStage.TAX)),
        rounding_rule=key_of(ExecutionStage.ROUNDING),
    )


#: Engine trace stages mapped onto the persisted component vocabulary. The
#: engine speaks in charging stages; §5.8 asks for component types. One table,
#: so the two never drift.
_STAGE_TO_COMPONENT: dict[str, str] = {
    ExecutionStage.QUANTITY: ComponentType.BILLABLE_QUANTITY,
    ExecutionStage.PULSE: ComponentType.PULSE,
    ExecutionStage.BUNDLE: ComponentType.BUNDLE_DEDUCTION,
    ExecutionStage.BASE_CHARGE: ComponentType.BASE_CHARGE,
    ExecutionStage.MINIMUM_CHARGE: ComponentType.MINIMUM_CHARGE,
    ExecutionStage.PROMOTION: ComponentType.DISCOUNT,
    ExecutionStage.DISCOUNT: ComponentType.DISCOUNT,
    ExecutionStage.SURCHARGE: ComponentType.SURCHARGE,
    ExecutionStage.TAX: ComponentType.TAX,
    ExecutionStage.ROUNDING: ComponentType.ROUNDING,
}

#: Components measured in usage units rather than money.
_QUANTITY_COMPONENTS = frozenset(
    {ComponentType.BILLABLE_QUANTITY, ComponentType.PULSE, ComponentType.BUNDLE_DEDUCTION}
)


def components_from_trace(
    usage_id: str,
    trace: list[dict[str, Any]],
    *,
    run_id: str | None = None,
    rule_ids: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Turn the engine's trace into ``calculation_component`` rows (§5.8).

    Derived from the trace rather than computed a second time, so the stored
    components and the charge they explain cannot disagree — a separate
    re-computation is a second implementation, and two implementations of the
    same arithmetic eventually diverge.
    """
    rule_ids = rule_ids or {}
    rows: list[dict[str, Any]] = []
    running: Decimal | None = None
    sequence = 0

    for step in trace:
        stage = str(step.get("stage") or "")
        component = _STAGE_TO_COMPONENT.get(stage)
        if component is None:
            # CONTEXT and ASSURANCE narrate; they are not calculation steps.
            continue

        raw = step.get("value")
        value: Decimal | None
        try:
            value = Decimal(str(raw)) if raw not in (None, "") else None
        except Exception:
            value = None

        sequence += 1
        is_quantity = component in _QUANTITY_COMPONENTS
        input_amount = None if is_quantity else running
        adjustment = (
            None
            if is_quantity or value is None or running is None
            else value - running
        )

        rows.append(
            {
                "usage_id": usage_id,
                "run_id": run_id,
                "sequence_number": sequence,
                "component_type": component,
                "rule_key": step.get("rule_key"),
                "rule_id": rule_ids.get(step.get("rule_key") or ""),
                "input_quantity": value if is_quantity else None,
                "input_amount": input_amount,
                "adjustment_amount": adjustment,
                "output_amount": None if is_quantity else value,
                "calculation_detail": str(step.get("detail") or step.get("label") or ""),
            }
        )

        if not is_quantity and value is not None:
            running = value

    return rows
