"""Test data factories for the rating assurance layer.

Deliberately builds *lightweight stand-ins* for the compiled rule and the
enriched usage row rather than persisting real ones. The resolution, plan and
comparison services take plain objects and read plain attributes, so the pure
logic — which is where the pricing correctness lives — is testable with no
database, no snapshot compile and no fixtures to keep in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.modules.rules.constants import ActionType, ExecutionStage


@dataclass
class FakeRule:
    """Stands in for a compiled ``ExecutableRule``."""

    rule_key: str
    execution_stage: str = ExecutionStage.BASE_CHARGE
    priority: int = 100
    specificity: int = 1
    rule_version: int = 1
    conflict_group: str | None = None
    stacking_policy: str = "EXCLUSIVE"
    condition_logic: str = "AND"
    predicates: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    effective_from: date = date(2020, 1, 1)
    effective_to: date | None = None
    currency_code: str = "GHS"
    rule_name: str = ""
    id: str = ""
    # Scope columns. None means "applies to any value" (§5.2).
    service_type: str | None = None
    product_code: str | None = None
    offer_code: str | None = None
    tariff_plan_code: str | None = None
    destination_zone: str | None = None
    origin_zone: str | None = None
    time_band: str | None = None
    account_type: str | None = None
    network_type: str | None = None
    rating_group: str | None = None
    roaming: bool | None = None
    on_net: bool | None = None
    dimension_sets: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = self.id or f"rule-{self.rule_key}"
        self.rule_name = self.rule_name or self.rule_key


@dataclass
class FakeUsage:
    """Stands in for an enriched ``CdrEnriched`` row."""

    usage_id: str = "MSC-10001"
    id: str = "cdr-1"
    msisdn: str = "233241234567"
    imsi: str | None = "620010123456789"
    subscriber_id: str | None = "SUB-1"
    account_id: str | None = "ACC-1"
    service_type: str = "VOICE"
    call_direction: str = "MO"
    event_timestamp: datetime = datetime(2026, 7, 30, 10, 2, 10, tzinfo=UTC)
    event_date: date = date(2026, 7, 30)
    event_end_time: datetime | None = None
    duration_seconds: Decimal | None = Decimal("195")
    usage_volume: Decimal | None = None
    calling_number: str | None = "233241234567"
    called_number: str | None = "233501112222"
    actual_charge: Decimal | None = None
    currency: str | None = "GHS"
    subscriber_type: str | None = "PREPAID"
    account_type: str | None = "PREPAID"
    customer_segment: str | None = "GOLD"
    subscriber_groups: list[str] = field(
        default_factory=lambda: ["ACCRA", "GOLD", "VOICE_BUNDLE"]
    )
    product_code: str | None = "SMART20"
    offer_code: str | None = None
    tariff_plan_code: str | None = "SMART20"
    network_relation: str | None = "OFF_NET"
    destination_type: str | None = "NATIONAL_MOBILE"
    destination_zone: str | None = "NATIONAL_MOBILE"
    origin_zone: str | None = "ACCRA"
    time_band: str | None = "PEAK"
    day_type: str | None = "WEEKDAY"
    roaming: bool = False
    on_net: bool | None = False
    network_type: str | None = None
    rating_group: str | None = None
    bundle_ids: list[str] = field(default_factory=list)
    offer_ids: list[str] = field(default_factory=list)
    context_key: str | None = "VOICE|SMART20|*|SMART20|NATIONAL_MOBILE|ACCRA|PEAK|PREPAID|N|*|*|N"
    context_hash: str | None = "hash-1"
    enrichment_status: str = "ENRICHED"
    enrichment_error: str | None = None
    processing_status: str = "PENDING"
    quality_status: str = "OK"
    source_system: str = "MSC01"
    source_file: str | None = "FILE100.dat.xml"
    source_record_number: str | None = "3563"
    call_reference: str | None = "03EA00EAB6B2"
    cell_id: str | None = "E7E2"
    location_area_code: str | None = "0BC7"
    charged_party: str | None = "callingParty"
    duplicate_hash: str | None = None
    serving_network: str | None = "54F660"


# --- Rule builders for the §28 worked example -------------------------------


def rate_rule(
    key: str,
    rate: str,
    *,
    priority: int = 100,
    specificity: int = 2,
    unit: str = "SECOND",
    per_units: int = 60,
    conflict_group: str = "VOICE_BASE_RATE",
    predicates: list[dict[str, Any]] | None = None,
    **scope: Any,
) -> FakeRule:
    """A base-rate rule quoted per ``per_units`` of ``unit``."""
    return FakeRule(
        rule_key=key,
        execution_stage=ExecutionStage.BASE_CHARGE,
        priority=priority,
        specificity=specificity,
        conflict_group=conflict_group,
        predicates=predicates or [],
        actions=[
            {
                "action_type": ActionType.SET_RATE.value,
                "params": {
                    "rate": rate,
                    "unit": unit,
                    "per_units": per_units,
                    "currency": "GHS",
                },
            }
        ],
        **scope,
    )


def pulse_rule(key: str, initial: int, subsequent: int) -> FakeRule:
    return FakeRule(
        rule_key=key,
        execution_stage=ExecutionStage.PULSE,
        actions=[
            {
                "action_type": ActionType.SET_PULSE.value,
                "params": {"initial_seconds": initial, "subsequent_seconds": subsequent},
            }
        ],
    )


def discount_rule(
    key: str,
    percentage: str,
    *,
    priority: int = 100,
    stackable: bool = False,
    conflict_group: str | None = "VOICE_DISCOUNT",
    predicates: list[dict[str, Any]] | None = None,
) -> FakeRule:
    return FakeRule(
        rule_key=key,
        execution_stage=ExecutionStage.DISCOUNT,
        priority=priority,
        conflict_group=conflict_group,
        stacking_policy="STACKABLE" if stackable else "EXCLUSIVE",
        predicates=predicates or [],
        actions=[
            {
                "action_type": ActionType.APPLY_DISCOUNT.value,
                "params": {"percentage": percentage},
            }
        ],
    )


def tax_rule(key: str, percent: str, *, inclusive: bool = False) -> FakeRule:
    return FakeRule(
        rule_key=key,
        execution_stage=ExecutionStage.TAX,
        stacking_policy="STACKABLE",
        actions=[
            {
                "action_type": ActionType.APPLY_TAX.value,
                "params": {},
                "resolved": {
                    "tax_rule": {"rate_percent": percent, "inclusive": inclusive}
                },
            }
        ],
    )


def rounding_rule(key: str, mode: str = "HALF_UP", decimals: int = 2) -> FakeRule:
    return FakeRule(
        rule_key=key,
        execution_stage=ExecutionStage.ROUNDING,
        actions=[
            {
                "action_type": ActionType.APPLY_ROUNDING.value,
                "params": {"mode": mode, "decimals": decimals},
            }
        ],
    )


def condition(attribute: str, operator: str, *values: Any, group: int = 0) -> dict[str, Any]:
    return {
        "attribute": attribute,
        "operator": operator,
        "values": list(values),
        "group_index": group,
        "negate": False,
    }
