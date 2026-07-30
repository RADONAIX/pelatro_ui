"""The rule vocabulary: statuses, stages, operators, attributes and actions.

This module is the single source of truth for what a rule can express. The UI's
visual condition/action builder is *generated* from these tables (served by
``GET /api/rating/meta/*``) rather than hard-coding a parallel list — that is
the only way a 10,000-rule estate stays consistent between what the builder
offers and what the engine can execute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RuleStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    COMPILED = "COMPILED"
    PUBLISHED = "PUBLISHED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    RETIRED = "RETIRED"


#: Legal lifecycle moves. Phase 1 implements the DRAFT/VALIDATED/RETIRED subset
#: plus versioning; Phase 2 turns on review → approve → compile → publish. The
#: full map lives here now so the state machine never has to be redefined.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    RuleStatus.DRAFT: (RuleStatus.VALIDATED, RuleStatus.RETIRED),
    RuleStatus.VALIDATED: (RuleStatus.DRAFT, RuleStatus.REVIEWED, RuleStatus.RETIRED),
    RuleStatus.REVIEWED: (RuleStatus.DRAFT, RuleStatus.APPROVED, RuleStatus.RETIRED),
    RuleStatus.APPROVED: (RuleStatus.COMPILED, RuleStatus.DRAFT, RuleStatus.RETIRED),
    RuleStatus.COMPILED: (RuleStatus.PUBLISHED, RuleStatus.APPROVED),
    RuleStatus.PUBLISHED: (RuleStatus.ACTIVE, RuleStatus.SUPERSEDED, RuleStatus.RETIRED),
    RuleStatus.ACTIVE: (RuleStatus.SUPERSEDED, RuleStatus.RETIRED),
    RuleStatus.SUPERSEDED: (RuleStatus.RETIRED,),
    RuleStatus.RETIRED: (),
}

#: A rule stops being editable the moment it is approved — after that a change
#: means a NEW VERSION, which is what makes the audit trail trustworthy.
EDITABLE_STATUSES: frozenset[str] = frozenset({RuleStatus.DRAFT, RuleStatus.VALIDATED})


class ExecutionStage(StrEnum):
    """Charging pipeline position. Ordering here IS the rating sequence (§13)."""

    QUANTITY = "QUANTITY"
    TARIFF_SELECTION = "TARIFF_SELECTION"
    MINIMUM_CHARGE = "MINIMUM_CHARGE"
    PULSE = "PULSE"
    BASE_CHARGE = "BASE_CHARGE"
    BUNDLE = "BUNDLE"
    PROMOTION = "PROMOTION"
    DISCOUNT = "DISCOUNT"
    SURCHARGE = "SURCHARGE"
    TAX = "TAX"
    ROUNDING = "ROUNDING"


STAGE_ORDER: tuple[str, ...] = (
    ExecutionStage.QUANTITY,
    ExecutionStage.TARIFF_SELECTION,
    ExecutionStage.MINIMUM_CHARGE,
    ExecutionStage.PULSE,
    ExecutionStage.BASE_CHARGE,
    ExecutionStage.BUNDLE,
    ExecutionStage.PROMOTION,
    ExecutionStage.DISCOUNT,
    ExecutionStage.SURCHARGE,
    ExecutionStage.TAX,
    ExecutionStage.ROUNDING,
)


class RuleType(StrEnum):
    BASE_TARIFF = "BASE_TARIFF"
    TARIFF_SELECTION = "TARIFF_SELECTION"
    PULSE = "PULSE"
    MINIMUM_CHARGE = "MINIMUM_CHARGE"
    BUNDLE = "BUNDLE"
    PROMOTION = "PROMOTION"
    DISCOUNT = "DISCOUNT"
    SURCHARGE = "SURCHARGE"
    TAX = "TAX"
    ROUNDING = "ROUNDING"
    ZERO_RATE = "ZERO_RATE"


RULE_TYPE_STAGE: dict[str, str] = {
    RuleType.BASE_TARIFF: ExecutionStage.BASE_CHARGE,
    RuleType.TARIFF_SELECTION: ExecutionStage.TARIFF_SELECTION,
    RuleType.PULSE: ExecutionStage.PULSE,
    RuleType.MINIMUM_CHARGE: ExecutionStage.MINIMUM_CHARGE,
    RuleType.BUNDLE: ExecutionStage.BUNDLE,
    RuleType.PROMOTION: ExecutionStage.PROMOTION,
    RuleType.DISCOUNT: ExecutionStage.DISCOUNT,
    RuleType.SURCHARGE: ExecutionStage.SURCHARGE,
    RuleType.TAX: ExecutionStage.TAX,
    RuleType.ROUNDING: ExecutionStage.ROUNDING,
    RuleType.ZERO_RATE: ExecutionStage.BASE_CHARGE,
}


class StackingPolicy(StrEnum):
    #: Only the single highest-priority winner in the conflict group applies.
    EXCLUSIVE = "EXCLUSIVE"
    #: Every match applies, in priority order.
    STACKABLE = "STACKABLE"
    #: Replaces anything a lower-priority rule already set at this stage.
    OVERRIDE = "OVERRIDE"


class Operator(StrEnum):
    EQUALS = "EQUALS"
    NOT_EQUALS = "NOT_EQUALS"
    IN = "IN"
    NOT_IN = "NOT_IN"
    GREATER_THAN = "GREATER_THAN"
    LESS_THAN = "LESS_THAN"
    GREATER_OR_EQUAL = "GREATER_OR_EQUAL"
    LESS_OR_EQUAL = "LESS_OR_EQUAL"
    BETWEEN = "BETWEEN"
    STARTS_WITH = "STARTS_WITH"
    CONTAINS = "CONTAINS"
    EXISTS = "EXISTS"
    NOT_EXISTS = "NOT_EXISTS"


OPERATOR_LABELS: dict[str, str] = {
    Operator.EQUALS: "Equals",
    Operator.NOT_EQUALS: "Not equals",
    Operator.IN: "In",
    Operator.NOT_IN: "Not in",
    Operator.GREATER_THAN: "Greater than",
    Operator.LESS_THAN: "Less than",
    Operator.GREATER_OR_EQUAL: "Greater than or equal",
    Operator.LESS_OR_EQUAL: "Less than or equal",
    Operator.BETWEEN: "Between",
    Operator.STARTS_WITH: "Starts with",
    Operator.CONTAINS: "Contains",
    Operator.EXISTS: "Exists",
    Operator.NOT_EXISTS: "Does not exist",
}

#: (min, max) number of values each operator takes. ``None`` = unbounded.
OPERATOR_ARITY: dict[str, tuple[int, int | None]] = {
    Operator.EQUALS: (1, 1),
    Operator.NOT_EQUALS: (1, 1),
    Operator.IN: (1, None),
    Operator.NOT_IN: (1, None),
    Operator.GREATER_THAN: (1, 1),
    Operator.LESS_THAN: (1, 1),
    Operator.GREATER_OR_EQUAL: (1, 1),
    Operator.LESS_OR_EQUAL: (1, 1),
    Operator.BETWEEN: (2, 2),
    Operator.STARTS_WITH: (1, 1),
    Operator.CONTAINS: (1, 1),
    Operator.EXISTS: (0, 0),
    Operator.NOT_EXISTS: (0, 0),
}


class DataType(StrEnum):
    STRING = "STRING"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    ENUM = "ENUM"
    #: Points at a canonical metadata entity; values are that entity's `code`.
    REFERENCE = "REFERENCE"
    DATETIME = "DATETIME"


_COMMON = (Operator.EXISTS, Operator.NOT_EXISTS)
_SET = (Operator.IN, Operator.NOT_IN)
_EQ = (Operator.EQUALS, Operator.NOT_EQUALS)
_ORD = (
    Operator.GREATER_THAN,
    Operator.LESS_THAN,
    Operator.GREATER_OR_EQUAL,
    Operator.LESS_OR_EQUAL,
    Operator.BETWEEN,
)
_TEXT = (Operator.STARTS_WITH, Operator.CONTAINS)

OPERATORS_BY_TYPE: dict[str, tuple[str, ...]] = {
    DataType.STRING: (*_EQ, *_SET, *_TEXT, *_COMMON),
    DataType.NUMBER: (*_EQ, *_ORD, *_SET, *_COMMON),
    DataType.BOOLEAN: (*_EQ, *_COMMON),
    DataType.ENUM: (*_EQ, *_SET, *_COMMON),
    DataType.REFERENCE: (*_EQ, *_SET, *_COMMON),
    DataType.DATETIME: (*_EQ, *_ORD, *_COMMON),
}


@dataclass(frozen=True)
class RuleAttribute:
    """One selectable left-hand side in the condition builder."""

    key: str
    label: str
    data_type: str
    group: str
    #: Canonical metadata slug supplying the value list (REFERENCE types).
    reference: str | None = None
    #: Fixed value list (ENUM types).
    values: tuple[str, ...] = ()
    #: Weight added to a rule's specificity score when this attribute is bound.
    #: Narrower dimensions score higher, so an exact product+destination rule
    #: beats a service-wide default without anyone hand-tuning priorities.
    specificity: int = 10
    description: str = ""


_NETWORK_TYPES = ("2G", "3G", "4G", "5G", "VOLTE", "WIFI")
_DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

RULE_ATTRIBUTES: tuple[RuleAttribute, ...] = (
    # --- Service & product ---------------------------------------------------
    RuleAttribute("service_type", "Service Type", DataType.ENUM, "Service",
                  values=("VOICE", "SMS", "DATA", "MMS", "ROAMING", "DIGITAL"),
                  specificity=40,
                  description="The kind of usage the CDR represents."),
    RuleAttribute("product", "Product", DataType.REFERENCE, "Service",
                  reference="products", specificity=50,
                  description="Subscriber's rated product at the time of the event."),
    RuleAttribute("offer", "Offer", DataType.REFERENCE, "Service",
                  reference="offers", specificity=55,
                  description="Commercial offer attached to the product."),
    RuleAttribute("tariff_plan", "Tariff Plan", DataType.REFERENCE, "Service",
                  reference="tariff-plans", specificity=55),
    RuleAttribute("rating_group", "Rating Group", DataType.REFERENCE, "Service",
                  reference="rating-groups", specificity=30),
    RuleAttribute("account_type", "Account Type", DataType.ENUM, "Service",
                  values=("PREPAID", "POSTPAID", "HYBRID"), specificity=20),

    # --- Destination ---------------------------------------------------------
    RuleAttribute("destination_zone", "Destination Zone", DataType.REFERENCE, "Destination",
                  reference="destination-zones", specificity=45,
                  description="Zone the called/destination number resolves to."),
    RuleAttribute("origin_zone", "Origin Zone", DataType.REFERENCE, "Destination",
                  reference="destination-zones", specificity=25),
    RuleAttribute("called_number", "Called Number", DataType.STRING, "Destination",
                  specificity=60),
    RuleAttribute("calling_number", "Calling Number", DataType.STRING, "Destination",
                  specificity=60),
    RuleAttribute("country_code", "Country Code", DataType.STRING, "Destination",
                  specificity=25),
    RuleAttribute("on_net", "On-net", DataType.BOOLEAN, "Destination", specificity=20),

    # --- Time ----------------------------------------------------------------
    RuleAttribute("time_band", "Time Band", DataType.REFERENCE, "Time",
                  reference="time-bands", specificity=35,
                  description="PEAK / OFF_PEAK / WEEKEND band the event falls in."),
    RuleAttribute("day_of_week", "Day of Week", DataType.ENUM, "Time",
                  values=_DAYS, specificity=20),
    RuleAttribute("event_timestamp", "Event Timestamp", DataType.DATETIME, "Time",
                  specificity=15),

    # --- Network / roaming ---------------------------------------------------
    RuleAttribute("roaming", "Roaming", DataType.BOOLEAN, "Network", specificity=30),
    RuleAttribute("network_type", "Network Type", DataType.ENUM, "Network",
                  values=_NETWORK_TYPES, specificity=20),
    RuleAttribute("visited_operator", "Visited Operator", DataType.STRING, "Network",
                  specificity=40, description="Roaming partner PLMN (MCC+MNC)."),
    RuleAttribute("apn", "APN", DataType.STRING, "Network", specificity=35,
                  description="Access point name — data sessions only."),

    # --- Usage ---------------------------------------------------------------
    RuleAttribute("duration_seconds", "Duration (seconds)", DataType.NUMBER, "Usage",
                  specificity=15),
    RuleAttribute("usage_volume", "Usage Volume", DataType.NUMBER, "Usage",
                  specificity=15, description="Bytes for data, messages for SMS."),
    RuleAttribute("currency", "Currency", DataType.REFERENCE, "Usage",
                  reference="currencies", specificity=10),

    # --- Subscriber ----------------------------------------------------------
    RuleAttribute("subscriber_id", "Subscriber ID", DataType.STRING, "Subscriber",
                  specificity=70),
    RuleAttribute("msisdn", "MSISDN", DataType.STRING, "Subscriber", specificity=70),
    RuleAttribute("imsi", "IMSI", DataType.STRING, "Subscriber", specificity=70),

    # --- Rating-assurance dimensions -----------------------------------------
    # Added, never substituted: every attribute above keeps its key, weight and
    # meaning, so no existing rule changes behaviour or specificity.
    RuleAttribute("call_direction", "Call Direction", DataType.ENUM, "Service",
                  values=("MO", "MT", "FORWARDED"), specificity=35,
                  description="Who initiated the event. An incoming call and an "
                              "outgoing call are the same service priced differently."),
    RuleAttribute("subscriber_type", "Subscriber Type", DataType.ENUM, "Subscriber",
                  values=("PREPAID", "POSTPAID", "HYBRID"), specificity=20,
                  description="Charging relationship at the time of the event."),
    RuleAttribute("customer_segment", "Customer Segment", DataType.STRING, "Subscriber",
                  specificity=30,
                  description="The single segment the subscriber belongs to. For "
                              "membership of several groups use Subscriber Groups."),
    RuleAttribute("subscriber_groups", "Subscriber Groups", DataType.STRING, "Subscriber",
                  specificity=35,
                  description="Every group the subscriber held at the time of the "
                              "event, as a list. Use CONTAINS / NOT_CONTAINS — this "
                              "is a membership test on ONE usage record and never "
                              "multiplies it into one result per group."),
    RuleAttribute("destination_type", "Destination Type", DataType.STRING, "Destination",
                  specificity=45,
                  description="What it costs to reach the destination, as opposed "
                              "to where the destination is."),
    RuleAttribute("network_relation", "Network Relation", DataType.ENUM, "Destination",
                  values=("ON_NET", "OFF_NET", "INTERNATIONAL"), specificity=30),
    RuleAttribute("day_type", "Day Type", DataType.ENUM, "Time",
                  values=("WEEKDAY", "WEEKEND", "HOLIDAY"), specificity=25,
                  description="From the holiday calendar, not the weekday number: "
                              "a public holiday priced as a Tuesday puts a variance "
                              "on every call that day."),
)

ATTRIBUTE_BY_KEY: dict[str, RuleAttribute] = {a.key: a for a in RULE_ATTRIBUTES}


class ActionType(StrEnum):
    SET_RATE = "SET_RATE"
    #: A rate ladder over cumulative usage in the period — "first 1 GB free,
    #: next 5 GB at X". Stateful: needs a per-subscriber usage counter.
    SET_TIERED_RATE = "SET_TIERED_RATE"
    #: A floor on billable *quantity*, not charge. A data session billed at a
    #: minimum of 100 KB is a quantity rule; a minimum call charge is not.
    SET_MINIMUM_QUANTITY = "SET_MINIMUM_QUANTITY"
    SET_PULSE = "SET_PULSE"
    SET_MINIMUM_CHARGE = "SET_MINIMUM_CHARGE"
    SET_MAXIMUM_CHARGE = "SET_MAXIMUM_CHARGE"
    SET_CONNECTION_FEE = "SET_CONNECTION_FEE"
    APPLY_DISCOUNT = "APPLY_DISCOUNT"
    APPLY_PROMOTION = "APPLY_PROMOTION"
    CONSUME_BUNDLE = "CONSUME_BUNDLE"
    APPLY_TAX = "APPLY_TAX"
    APPLY_ROUNDING = "APPLY_ROUNDING"
    SELECT_TARIFF = "SELECT_TARIFF"
    ADD_SURCHARGE = "ADD_SURCHARGE"
    SET_ZERO_CHARGE = "SET_ZERO_CHARGE"


@dataclass(frozen=True)
class ActionParam:
    key: str
    label: str
    data_type: str
    required: bool = True
    reference: str | None = None
    values: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class ActionSpec:
    type: str
    label: str
    stage: str
    params: tuple[ActionParam, ...] = field(default_factory=tuple)
    description: str = ""


_CURRENCY = ActionParam("currency", "Currency", DataType.REFERENCE, False, reference="currencies")

ACTION_SPECS: tuple[ActionSpec, ...] = (
    ActionSpec(
        ActionType.SET_RATE, "Set rate", ExecutionStage.BASE_CHARGE,
        (
            ActionParam("rate", "Rate", DataType.NUMBER, True,
                        description="Charge per `per_units` of `unit`."),
            ActionParam("unit", "Unit", DataType.ENUM, True,
                        values=("SECOND", "MINUTE", "MESSAGE", "BYTE", "KILOBYTE",
                                "MEGABYTE", "EVENT")),
            ActionParam("per_units", "Per units", DataType.NUMBER, False,
                        description="Defaults to 1. e.g. £0.10 per 60 SECOND."),
            _CURRENCY,
        ),
        "The base tariff: how much a unit of usage costs.",
    ),
    ActionSpec(
        ActionType.SET_TIERED_RATE, "Set tiered rate", ExecutionStage.BASE_CHARGE,
        (
            ActionParam("tiers", "Tiers", DataType.STRING, True,
                        description=(
                            "Ladder as up_to:rate, cheapest first — "
                            "e.g. 1073741824:0, 5368709120:0.00000001, *:0.00000002. "
                            "Use * for the final open-ended tier."
                        )),
            ActionParam("unit", "Unit", DataType.ENUM, True,
                        values=("SECOND", "MINUTE", "MESSAGE", "BYTE", "KILOBYTE",
                                "MEGABYTE", "GIGABYTE", "EVENT")),
            ActionParam("per_units", "Per units", DataType.NUMBER, False),
            _CURRENCY,
        ),
        "Prices usage against cumulative consumption in the period. Stateful.",
    ),
    ActionSpec(
        ActionType.SET_MINIMUM_QUANTITY, "Set minimum quantity", ExecutionStage.QUANTITY,
        (
            ActionParam("quantity", "Minimum quantity", DataType.NUMBER, True,
                        description="In the rate's unit. e.g. a 100 KB data session floor."),
        ),
        "Floors billable quantity before the rate is applied.",
    ),
    ActionSpec(
        ActionType.SET_PULSE, "Set pulse", ExecutionStage.PULSE,
        (
            ActionParam("initial_seconds", "Initial pulse", DataType.NUMBER, True,
                        description="First chargeable block, e.g. 60."),
            ActionParam("subsequent_seconds", "Subsequent pulse", DataType.NUMBER, False,
                        description="Block size after the first. Defaults to the initial pulse."),
        ),
        "Rounds billable quantity UP to whole pulses before the rate is applied.",
    ),
    ActionSpec(
        ActionType.SET_MINIMUM_CHARGE, "Set minimum charge", ExecutionStage.MINIMUM_CHARGE,
        (ActionParam("amount", "Amount", DataType.NUMBER), _CURRENCY),
        "Floor applied to the calculated charge (minimum call charge).",
    ),
    ActionSpec(
        ActionType.SET_MAXIMUM_CHARGE, "Set maximum charge", ExecutionStage.BASE_CHARGE,
        (ActionParam("amount", "Amount", DataType.NUMBER), _CURRENCY),
        "Cap applied to the calculated charge.",
    ),
    ActionSpec(
        ActionType.SET_CONNECTION_FEE, "Set connection fee", ExecutionStage.BASE_CHARGE,
        (ActionParam("amount", "Amount", DataType.NUMBER), _CURRENCY),
        "One-off setup charge added per call.",
    ),
    ActionSpec(
        ActionType.APPLY_DISCOUNT, "Apply discount", ExecutionStage.DISCOUNT,
        (
            ActionParam("discount", "Discount", DataType.REFERENCE, False,
                        reference="discounts",
                        description="Catalogue discount, or set percentage/amount instead."),
            ActionParam("percentage", "Percentage", DataType.NUMBER, False),
            ActionParam("amount", "Amount", DataType.NUMBER, False),
            _CURRENCY,
        ),
    ),
    ActionSpec(
        ActionType.APPLY_PROMOTION, "Apply promotion", ExecutionStage.PROMOTION,
        (ActionParam("promotion", "Promotion", DataType.REFERENCE, True, reference="promotions"),),
    ),
    ActionSpec(
        ActionType.CONSUME_BUNDLE, "Consume bundle", ExecutionStage.BUNDLE,
        (
            ActionParam("bundle", "Bundle", DataType.REFERENCE, True, reference="bundles"),
            ActionParam("consume_order", "Consume order", DataType.NUMBER, False,
                        description="Lower consumes first when several bundles match."),
        ),
        "Draws billable quantity from a bundle before it is charged. Stateful (Phase 6).",
    ),
    ActionSpec(
        ActionType.APPLY_TAX, "Apply tax", ExecutionStage.TAX,
        (ActionParam("tax_rule", "Tax rule", DataType.REFERENCE, True, reference="tax-rules"),),
    ),
    ActionSpec(
        ActionType.APPLY_ROUNDING, "Apply rounding", ExecutionStage.ROUNDING,
        (
            ActionParam("rounding_rule", "Rounding rule", DataType.REFERENCE, False,
                        reference="rounding-rules"),
            ActionParam("mode", "Mode", DataType.ENUM, False,
                        values=("HALF_UP", "HALF_EVEN", "CEILING", "FLOOR", "TRUNCATE")),
            ActionParam("decimals", "Decimals", DataType.NUMBER, False),
        ),
    ),
    ActionSpec(
        ActionType.SELECT_TARIFF, "Select tariff", ExecutionStage.TARIFF_SELECTION,
        (ActionParam("tariff_plan", "Tariff plan", DataType.REFERENCE, True,
                     reference="tariff-plans"),),
        "Routes the CDR to a specific tariff plan before base charging.",
    ),
    ActionSpec(
        ActionType.ADD_SURCHARGE, "Add surcharge", ExecutionStage.SURCHARGE,
        (
            ActionParam("amount", "Amount", DataType.NUMBER, False),
            ActionParam("percentage", "Percentage", DataType.NUMBER, False),
            _CURRENCY,
        ),
    ),
    ActionSpec(
        ActionType.SET_ZERO_CHARGE, "Set zero charge", ExecutionStage.BASE_CHARGE,
        (),
        "Forces the expected charge to zero — free calls, promotional numbers, emergency.",
    ),
)

ACTION_BY_TYPE: dict[str, ActionSpec] = {a.type: a for a in ACTION_SPECS}

#: Rule types that MUST carry at least one action of a matching stage, checked by
#: the structural validator. A BASE_TARIFF rule with no SET_RATE is the single
#: most common authoring mistake and silently produces a zero expected charge.
REQUIRED_ACTION_FOR_TYPE: dict[str, tuple[str, ...]] = {
    RuleType.BASE_TARIFF: (
        ActionType.SET_RATE,
        ActionType.SET_TIERED_RATE,
        ActionType.SET_ZERO_CHARGE,
    ),
    RuleType.PULSE: (ActionType.SET_PULSE,),
    RuleType.MINIMUM_CHARGE: (ActionType.SET_MINIMUM_CHARGE,),
    RuleType.TAX: (ActionType.APPLY_TAX,),
    RuleType.ROUNDING: (ActionType.APPLY_ROUNDING,),
    RuleType.DISCOUNT: (ActionType.APPLY_DISCOUNT,),
    RuleType.PROMOTION: (ActionType.APPLY_PROMOTION,),
    RuleType.BUNDLE: (ActionType.CONSUME_BUNDLE,),
    RuleType.SURCHARGE: (ActionType.ADD_SURCHARGE,),
    RuleType.TARIFF_SELECTION: (ActionType.SELECT_TARIFF,),
    RuleType.ZERO_RATE: (ActionType.SET_ZERO_CHARGE,),
}


class ConditionLogic(StrEnum):
    AND = "AND"
    OR = "OR"


class ValidationSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"
