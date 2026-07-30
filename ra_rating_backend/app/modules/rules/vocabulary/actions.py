"""The canonical action registry — what a rule can *do* when it matches.

Superset of the legacy ``constants.ACTION_SPECS``: the fifteen existing actions
keep their codes, stages and parameter names (asserted by
``test_canonical_vocabulary.py``), and thirty-odd more cover the prepaid and
postpaid semantics the legacy vocabulary has no way to express.

Two additions over the legacy shape are worth explaining:

``target_attribute`` — what the action writes (``charge``, ``quantity``,
``balance``, ``invoice_line`` …). The legacy model left this implicit in the
action code, which meant nothing could answer "which rules affect the charge?"
without a hard-coded list.

``value_param`` — which parameter is the action's *principal* value, promoted to
``rule_action.action_value``/``action_value_numeric``. A rate change is then an
indexed numeric comparison rather than a JSONB dig, which is what makes
"show me every rule whose rate moved" a query instead of a script.

**``stage_code`` is a default, not a constraint.** When a rule fires is decided by
its *rule type's* stage; an action's stage is where it normally belongs, used to
infer a type from a tariff sheet that carries no type column. The two legitimately
differ: ``APPLY_TAX`` sits at ``TAX`` for a usage rule and at ``INVOICE_TAX`` for a
postpaid invoice rule — same semantics, different point in the sequence, and
inventing ``APPLY_INVOICE_TAX`` would be two implementations of one idea. The
invariant that *is* enforced is mode coherence: every action a rule uses must sit
at a stage inside ``pipeline(rule.charging_mode)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.rules.vocabulary.modes import RuleCategory
from app.modules.rules.vocabulary.values import UnitDimension, ValueType


@dataclass(frozen=True, slots=True)
class ParamSpec:
    key: str
    label: str
    value_type: str
    required: bool = True
    #: Catalogue slug for REFERENCE params.
    reference: str | None = None
    #: Allowed values for ENUM params.
    values: tuple[str, ...] = ()
    #: For NUMBER/MONEY params measured in something.
    unit_dimension: str | None = None
    #: Ordered parameters (rate tiers) persist as multiple rows under one name.
    repeatable: bool = False
    description: str = ""


@dataclass(frozen=True, slots=True)
class ActionSpec:
    code: str
    label: str
    stage_code: str
    category: str
    #: What the action writes. None for actions that only classify context.
    target_attribute: str | None = None
    #: Key of the parameter promoted to rule_action.action_value.
    value_param: str | None = None
    params: tuple[ParamSpec, ...] = field(default_factory=tuple)
    description: str = ""


_P = ParamSpec
_A = ActionSpec

_CURRENCY = _P("currency", "Currency", ValueType.REFERENCE, False, reference="currencies")
_TIME_UNITS = ("SECOND", "MINUTE", "MESSAGE", "BYTE", "KILOBYTE", "MEGABYTE", "EVENT")
_TIERED_UNITS = (*_TIME_UNITS[:6], "GIGABYTE", "EVENT")
_ROUND_MODES = ("HALF_UP", "HALF_EVEN", "CEILING", "FLOOR", "TRUNCATE")


# --- Common: classification -------------------------------------------------
# These four exist because the spec lists product eligibility, service
# classification, destination classification and time band as *rules*. A rule
# needs an action, and "classify" is a different verb from "charge".

_CLASSIFICATION: tuple[ActionSpec, ...] = (
    _A("SET_ELIGIBILITY", "Set eligibility", "ELIGIBILITY", RuleCategory.COMMON,
       target_attribute="eligible", value_param="eligible",
       params=(
           _P("eligible", "Eligible", ValueType.BOOLEAN),
           _P("reason_code", "Reason code", ValueType.STRING, False,
              description="Surfaced on the exception when a CDR is ruled ineligible."),
       ),
       description="Decides whether these rules may charge this event at all."),
    _A("SET_SERVICE_CLASS", "Set service class", "SERVICE_CLASSIFICATION", RuleCategory.COMMON,
       target_attribute="rating_group", value_param="rating_group",
       params=(
           _P("rating_group", "Rating group", ValueType.REFERENCE, reference="rating-groups"),
           _P("service_type", "Service type", ValueType.ENUM, False,
              values=("VOICE", "SMS", "DATA", "MMS", "ROAMING", "DIGITAL")),
       )),
    _A("SET_DESTINATION_ZONE", "Set destination zone", "DESTINATION_CLASSIFICATION",
       RuleCategory.COMMON, target_attribute="destination_zone", value_param="destination_zone",
       params=(
           _P("destination_zone", "Destination zone", ValueType.REFERENCE,
              reference="destination-zones"),
       ),
       description="Overrides prefix-table resolution for a specific case."),
    _A("SET_TIME_BAND", "Set time band", "TIME_BAND", RuleCategory.COMMON,
       target_attribute="time_band", value_param="time_band",
       params=(_P("time_band", "Time band", ValueType.REFERENCE, reference="time-bands"),)),
)


# --- Common: quantity, rate, adjustment (the legacy fifteen) ----------------

_LEGACY: tuple[ActionSpec, ...] = (
    _A("SET_MINIMUM_QUANTITY", "Set minimum quantity", "QUANTITY", RuleCategory.COMMON,
       target_attribute="quantity", value_param="quantity",
       params=(
           _P("quantity", "Minimum quantity", ValueType.NUMBER,
              unit_dimension=UnitDimension.VOLUME,
              description="In the rate's unit — e.g. a 100 KB data session floor."),
       ),
       description="Floors billable quantity before the rate is applied."),
    _A("SELECT_TARIFF", "Select tariff", "TARIFF_SELECTION", RuleCategory.COMMON,
       target_attribute="tariff_plan", value_param="tariff_plan",
       params=(_P("tariff_plan", "Tariff plan", ValueType.REFERENCE, reference="tariff-plans"),),
       description="Routes the CDR to a tariff plan before base charging."),
    _A("SET_MINIMUM_CHARGE", "Set minimum charge", "MINIMUM_CHARGE", RuleCategory.COMMON,
       target_attribute="charge", value_param="amount",
       params=(_P("amount", "Amount", ValueType.MONEY), _CURRENCY),
       description="Floor applied to the calculated charge (minimum call charge)."),
    _A("SET_PULSE", "Set pulse", "PULSE", RuleCategory.COMMON,
       target_attribute="quantity", value_param="initial_seconds",
       params=(
           _P("initial_seconds", "Initial pulse", ValueType.NUMBER,
              unit_dimension=UnitDimension.TIME),
           _P("subsequent_seconds", "Subsequent pulse", ValueType.NUMBER, False,
              unit_dimension=UnitDimension.TIME,
              description="Defaults to the initial pulse."),
           _P("pulse_profile", "Pulse profile", ValueType.REFERENCE, False,
              reference="pulse-profiles",
              description="Use a catalogue profile instead of literal values."),
       ),
       description="Rounds billable quantity UP to whole pulses."),
    _A("SET_RATE", "Set rate", "BASE_CHARGE", RuleCategory.COMMON,
       target_attribute="charge", value_param="rate",
       params=(
           _P("rate", "Rate", ValueType.MONEY, description="Charge per `per_units` of `unit`."),
           _P("unit", "Unit", ValueType.ENUM, values=_TIME_UNITS),
           _P("per_units", "Per units", ValueType.NUMBER, False,
              description="Defaults to 1 — e.g. 0.10 per 60 SECOND."),
           _CURRENCY,
       ),
       description="The base tariff: how much a unit of usage costs."),
    _A("SET_TIERED_RATE", "Set tiered rate", "BASE_CHARGE", RuleCategory.COMMON,
       target_attribute="charge", value_param=None,
       params=(
           _P("tiers", "Tiers", ValueType.STRING, repeatable=True,
              description="Ladder as up_to:rate, cheapest first; * for the open tier."),
           _P("unit", "Unit", ValueType.ENUM, values=_TIERED_UNITS),
           _P("per_units", "Per units", ValueType.NUMBER, False),
           _CURRENCY,
       ),
       description="Prices usage against cumulative consumption in the period. Stateful."),
    _A("SET_MAXIMUM_CHARGE", "Set maximum charge", "BASE_CHARGE", RuleCategory.COMMON,
       target_attribute="charge", value_param="amount",
       params=(_P("amount", "Amount", ValueType.MONEY), _CURRENCY),
       description=(
           "Cap applied to the calculated charge. Stays on BASE_CHARGE until the "
           "R4 cut-over moves it to the MAXIMUM_CHARGE stage with a parity report."
       )),
    _A("SET_CONNECTION_FEE", "Set connection fee", "BASE_CHARGE", RuleCategory.COMMON,
       target_attribute="charge", value_param="amount",
       params=(_P("amount", "Amount", ValueType.MONEY), _CURRENCY),
       description="One-off setup charge added per call."),
    _A("SET_ZERO_CHARGE", "Set zero charge", "BASE_CHARGE", RuleCategory.COMMON,
       target_attribute="charge",
       description="Forces the expected charge to zero — free numbers, emergency calls."),
    _A("CONSUME_BUNDLE", "Consume bundle", "BUNDLE", RuleCategory.COMMON,
       target_attribute="quantity", value_param="bundle",
       params=(
           _P("bundle", "Bundle", ValueType.REFERENCE, reference="bundles"),
           _P("consume_order", "Consume order", ValueType.NUMBER, False,
              description="Lower consumes first. Prefer a bundle_priority row."),
       ),
       description="Draws billable quantity from a bundle before it is charged."),
    _A("APPLY_PROMOTION", "Apply promotion", "PROMOTION", RuleCategory.COMMON,
       target_attribute="charge", value_param="promotion",
       params=(_P("promotion", "Promotion", ValueType.REFERENCE, reference="promotions"),)),
    _A("APPLY_DISCOUNT", "Apply discount", "DISCOUNT", RuleCategory.COMMON,
       target_attribute="charge", value_param="percentage",
       params=(
           _P("discount", "Discount", ValueType.REFERENCE, False, reference="discounts",
              description="Catalogue discount, or set percentage/amount instead."),
           _P("percentage", "Percentage", ValueType.NUMBER, False),
           _P("amount", "Amount", ValueType.MONEY, False),
           _CURRENCY,
       )),
    _A("ADD_SURCHARGE", "Add surcharge", "SURCHARGE", RuleCategory.COMMON,
       target_attribute="charge", value_param="amount",
       params=(
           _P("amount", "Amount", ValueType.MONEY, False),
           _P("percentage", "Percentage", ValueType.NUMBER, False),
           _CURRENCY,
       )),
    # `tax_rule` is optional so a tariff sheet stating "VAT 20%" inline has
    # somewhere to go. A catalogue rule is still the better modelling — change
    # the rate once and every rule follows — but refusing the inline form means
    # an operator must build a tax catalogue before they can import a tariff,
    # which is a worse first hour than a rule that states its own rate.
    _A("APPLY_TAX", "Apply tax", "TAX", RuleCategory.COMMON,
       target_attribute="tax", value_param="tax_rule",
       params=(
           _P("tax_rule", "Tax rule", ValueType.REFERENCE, False, reference="tax-rules",
              description="A catalogue tax rule. Preferred: the rate lives in one "
                          "place and every rule that references it follows a change."),
           _P("rate_percent", "Rate %", ValueType.NUMBER, False,
              description="An inline percentage, for a sheet that states the rate "
                          "rather than naming a catalogue rule."),
           _P("inclusive", "Tax inclusive", ValueType.BOOLEAN, False,
              description="Tick when the rated amount already includes this tax."),
       )),
    _A("APPLY_ROUNDING", "Apply rounding", "ROUNDING", RuleCategory.COMMON,
       target_attribute="charge", value_param=None,
       params=(
           _P("rounding_rule", "Rounding rule", ValueType.REFERENCE, False,
              reference="rounding-rules"),
           _P("mode", "Mode", ValueType.ENUM, False, values=_ROUND_MODES),
           _P("decimals", "Decimals", ValueType.NUMBER, False),
       )),
)


# --- Prepaid ----------------------------------------------------------------

_PREPAID: tuple[ActionSpec, ...] = (
    _A("SELECT_BALANCE_BUCKET", "Select balance bucket", "BALANCE_SELECTION",
       RuleCategory.PREPAID, target_attribute="balance", value_param="balance_type",
       params=(
           _P("balance_type", "Balance type", ValueType.REFERENCE, reference="balance-types"),
           _P("balance_bucket", "Balance bucket", ValueType.REFERENCE, False,
              reference="balance-buckets"),
           _P("consume_order", "Consume order", ValueType.NUMBER, False),
           _P("fallback_balance_type", "Fallback balance type", ValueType.REFERENCE, False,
              reference="balance-types"),
       ),
       description="Which bucket pays: main, promotional, or a specific bundle bucket."),
    _A("DEDUCT_BALANCE", "Deduct balance", "BALANCE_DEDUCTION", RuleCategory.PREPAID,
       target_attribute="balance", value_param="balance_type",
       params=(
           _P("balance_type", "Balance type", ValueType.REFERENCE, reference="balance-types"),
           _P("amount_source", "Amount source", ValueType.ENUM,
              values=("CHARGE", "QUANTITY"),
              description="Deduct the money charged, or the quantity consumed."),
           _P("unit", "Unit", ValueType.ENUM, False, values=_TIERED_UNITS),
           _P("allow_partial", "Allow partial", ValueType.BOOLEAN, False),
       )),
    _A("RESERVE_BALANCE", "Reserve balance", "BALANCE_RESERVATION", RuleCategory.PREPAID,
       target_attribute="reservation", value_param="quota_amount",
       params=(
           _P("balance_type", "Balance type", ValueType.REFERENCE, reference="balance-types"),
           _P("quota_amount", "Quota amount", ValueType.NUMBER),
           _P("unit", "Unit", ValueType.ENUM, False, values=_TIERED_UNITS),
           _P("validity_seconds", "Validity (seconds)", ValueType.NUMBER, False,
              unit_dimension=UnitDimension.TIME),
           _P("reservation_policy", "Reservation policy", ValueType.REFERENCE, False,
              reference="reservation-policies"),
           _P("ocs_profile", "OCS profile", ValueType.REFERENCE, False, reference="ocs-profiles"),
       ),
       description="Online quota reservation at session start or interim update."),
    _A("RELEASE_RESERVATION", "Release reservation", "BALANCE_RESERVATION",
       RuleCategory.PREPAID, target_attribute="reservation", value_param="release_mode",
       params=(
           _P("release_mode", "Release mode", ValueType.ENUM, values=("UNUSED", "ALL")),
           _P("grace_seconds", "Grace (seconds)", ValueType.NUMBER, False,
              unit_dimension=UnitDimension.TIME),
       )),
    _A("ALLOW_PARTIAL_USAGE", "Allow partial usage", "BALANCE_EXCEPTION",
       RuleCategory.PREPAID, target_attribute="quantity", value_param="min_chargeable_quantity",
       params=(
           _P("min_chargeable_quantity", "Minimum chargeable quantity", ValueType.NUMBER),
           _P("unit", "Unit", ValueType.ENUM, False, values=_TIERED_UNITS),
           _P("round_mode", "Round mode", ValueType.ENUM, False, values=_ROUND_MODES),
       ),
       description="Charge for what the balance could cover instead of failing the session."),
    _A("ALLOW_NEGATIVE_BALANCE", "Allow negative balance", "BALANCE_EXCEPTION",
       RuleCategory.PREPAID, target_attribute="balance", value_param="limit_amount",
       params=(_P("limit_amount", "Limit amount", ValueType.MONEY), _CURRENCY)),
    _A("STOP_SERVICE", "Stop service", "SESSION_CONTROL", RuleCategory.PREPAID,
       target_attribute="session", value_param="action",
       params=(
           _P("action", "Action", ValueType.ENUM,
              values=("TERMINATE", "REDIRECT", "THROTTLE")),
           _P("redirect_target", "Redirect target", ValueType.STRING, False),
           _P("notify", "Notify", ValueType.BOOLEAN, False),
       )),
)


# --- Postpaid ---------------------------------------------------------------

_POSTPAID: tuple[ActionSpec, ...] = (
    _A("ASSIGN_BILLING_CYCLE", "Assign billing cycle", "BILLING_CYCLE_ASSIGNMENT",
       RuleCategory.POSTPAID, target_attribute="billing_cycle", value_param="billing_cycle",
       params=(_P("billing_cycle", "Billing cycle", ValueType.REFERENCE,
                  reference="billing-cycles"),)),
    _A("AGGREGATE_USAGE", "Aggregate usage", "USAGE_AGGREGATION", RuleCategory.POSTPAID,
       target_attribute="usage_total", value_param="aggregation_profile",
       params=(
           _P("aggregation_profile", "Aggregation profile", ValueType.REFERENCE,
              reference="usage-aggregation-profiles"),
           _P("dimension", "Dimension", ValueType.ENUM, False,
              values=("SUBSCRIBER", "ACCOUNT", "GROUP")),
           _P("window", "Window", ValueType.ENUM, False,
              values=("CYCLE", "DAY", "WEEK", "MONTH")),
       )),
    _A("ADD_RECURRING_CHARGE", "Add recurring charge", "RECURRING_CHARGE",
       RuleCategory.POSTPAID, target_attribute="invoice_line", value_param="amount",
       params=(
           _P("recurring_charge", "Recurring charge", ValueType.REFERENCE, False,
              reference="recurring-charges",
              description="Catalogue charge, or set an amount directly."),
           _P("amount", "Amount", ValueType.MONEY, False),
           _CURRENCY,
           _P("advance_flag", "Charged in advance", ValueType.BOOLEAN, False),
           _P("invoice_component", "Invoice component", ValueType.REFERENCE, False,
              reference="invoice-components"),
       ),
       description="Monthly rental and any other cyclic charge."),
    _A("ADD_ONE_TIME_CHARGE", "Add one-time charge", "ONE_TIME_CHARGE",
       RuleCategory.POSTPAID, target_attribute="invoice_line", value_param="amount",
       params=(
           _P("one_time_charge", "One-time charge", ValueType.REFERENCE, False,
              reference="one-time-charges"),
           _P("amount", "Amount", ValueType.MONEY, False),
           _CURRENCY,
           _P("trigger_event", "Trigger event", ValueType.ENUM, False,
              values=("ACTIVATION", "SUSPENSION", "SIM_SWAP", "MIGRATION", "MANUAL")),
           _P("invoice_component", "Invoice component", ValueType.REFERENCE, False,
              reference="invoice-components"),
       )),
    _A("APPLY_PRORATION", "Apply proration", "PRORATION", RuleCategory.POSTPAID,
       target_attribute="invoice_line", value_param="proration_profile",
       params=(
           _P("proration_profile", "Proration profile", ValueType.REFERENCE,
              reference="proration-profiles"),
           _P("method", "Method", ValueType.ENUM, False,
              values=("DAILY", "MONTHLY_30", "ACTUAL_DAYS", "NONE")),
           _P("basis", "Basis", ValueType.ENUM, False,
              values=("ACTIVATION", "CEASE", "PLAN_CHANGE")),
       )),
    _A("CHECK_CREDIT_LIMIT", "Check credit limit", "CREDIT_CHECK", RuleCategory.POSTPAID,
       target_attribute="credit_status", value_param="credit_limit_profile",
       params=(
           _P("credit_limit_profile", "Credit limit profile", ValueType.REFERENCE,
              reference="credit-limit-profiles"),
           _P("breach_action", "Breach action", ValueType.ENUM, False,
              values=("NOTIFY", "BAR_OUTGOING", "BAR_ALL", "NONE")),
           _P("warning_threshold_pct", "Warning threshold %", ValueType.NUMBER, False),
       )),
    _A("ADD_INVOICE_COMPONENT", "Add invoice component", "INVOICE_COMPONENT",
       RuleCategory.POSTPAID, target_attribute="invoice_line", value_param="amount",
       params=(
           _P("invoice_component", "Invoice component", ValueType.REFERENCE,
              reference="invoice-components"),
           _P("amount", "Amount", ValueType.MONEY, False),
           _CURRENCY,
           _P("sign", "Sign", ValueType.ENUM, False, values=("DEBIT", "CREDIT")),
       )),
    _A("ADD_USAGE_CHARGE", "Add usage charge", "INVOICE_COMPONENT", RuleCategory.POSTPAID,
       target_attribute="invoice_line", value_param="invoice_component",
       params=(
           _P("invoice_component", "Invoice component", ValueType.REFERENCE,
              reference="invoice-components"),
           _P("amount_source", "Amount source", ValueType.ENUM, False,
              values=("RATED_CHARGE", "AGGREGATED_USAGE")),
           _CURRENCY,
       ),
       description="Places already-rated usage onto the invoice as a line."),
    _A("ADD_LATE_FEE", "Add late fee", "LATE_FEE", RuleCategory.POSTPAID,
       target_attribute="invoice_line", value_param="amount",
       params=(
           _P("late_fee_profile", "Late fee profile", ValueType.REFERENCE, False,
              reference="late-fee-profiles"),
           _P("amount", "Amount", ValueType.MONEY, False),
           _P("percentage", "Percentage", ValueType.NUMBER, False),
           _CURRENCY,
           _P("grace_days", "Grace days", ValueType.NUMBER, False),
       )),
)


CANONICAL_ACTIONS: tuple[ActionSpec, ...] = (
    *_CLASSIFICATION, *_LEGACY, *_PREPAID, *_POSTPAID,
)

ACTION_BY_CODE: dict[str, ActionSpec] = {a.code: a for a in CANONICAL_ACTIONS}

#: Every catalogue slug any action parameter points at. The reference resolver
#: needs a model for each of these; the ones the prepaid/postpaid phases add are
#: listed here from R1 so a missing model is a startup-time failure, not a
#: confusing "'MONTHLY' does not exist" at authoring time.
REFERENCED_CATALOGUES: frozenset[str] = frozenset(
    p.reference
    for a in CANONICAL_ACTIONS
    for p in a.params
    if p.reference
)


def actions_for_mode(charging_mode: str) -> tuple[ActionSpec, ...]:
    """The action palette a rule of this charging mode may use.

    This is what stops the wizard offering ``ADD_INVOICE_COMPONENT`` to someone
    authoring a prepaid rule, and what the mode-coherence validator enforces on
    everything arriving from an import.
    """
    from app.modules.rules.vocabulary.stages import stage_codes_for

    allowed = stage_codes_for(charging_mode)
    return tuple(a for a in CANONICAL_ACTIONS if a.stage_code in allowed)
