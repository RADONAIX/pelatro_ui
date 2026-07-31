"""The canonical rule-type registry — the three families from the spec.

A rule type is the answer to "what kind of rule is this", and it decides one
thing that matters more than any other: **which stage the rule runs at**. Get
that wrong and the rule fires in the wrong pass of the charging sequence, which
is why the type is a table with an FK rather than a free-text column.

Each type also carries ``required_action_types`` — the validator's contract. A
BASE_TARIFF rule with no SET_RATE is the single most common authoring mistake and
silently produces a zero expected charge; the contract is what catches it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.rules.vocabulary.modes import ChargingMode, RuleCategory


@dataclass(frozen=True, slots=True)
class RuleTypeSpec:
    code: str
    name: str
    rule_category: str
    stage_code: str
    #: Which charging modes may use this type.
    charging_mode: str = ChargingMode.BOTH
    #: A matching rule MUST carry one of these actions.
    required_action_types: tuple[str, ...] = ()
    #: Set when this type is an operator-facing synonym for another one. Both are
    #: offered in the UI; both compile to identical behaviour.
    alias_of: str | None = None
    #: True for the eleven types that predate the canonical model.
    legacy: bool = False
    description: str = ""


_T = RuleTypeSpec
_C = RuleCategory
_M = ChargingMode


# --- Common (17) ------------------------------------------------------------

_COMMON: tuple[RuleTypeSpec, ...] = (
    _T("PRODUCT_ELIGIBILITY", "Product eligibility", _C.COMMON, "ELIGIBILITY",
       required_action_types=("SET_ELIGIBILITY",)),
    _T("SERVICE_CLASSIFICATION", "Service classification", _C.COMMON,
       "SERVICE_CLASSIFICATION", required_action_types=("SET_SERVICE_CLASS",)),
    _T("DESTINATION_CLASSIFICATION", "Destination classification", _C.COMMON,
       "DESTINATION_CLASSIFICATION", required_action_types=("SET_DESTINATION_ZONE",)),
    _T("TIME_BAND", "Time band", _C.COMMON, "TIME_BAND",
       required_action_types=("SET_TIME_BAND",)),
    _T("MINIMUM_QUANTITY", "Minimum quantity", _C.COMMON, "QUANTITY",
       required_action_types=("SET_MINIMUM_QUANTITY",)),
    _T("TARIFF_SELECTION", "Tariff selection", _C.COMMON, "TARIFF_SELECTION", legacy=True,
       required_action_types=("SELECT_TARIFF",)),
    _T("BASE_TARIFF", "Base tariff", _C.COMMON, "BASE_CHARGE", legacy=True,
       required_action_types=("SET_RATE", "SET_TIERED_RATE", "SET_ZERO_CHARGE")),
    _T("ZERO_RATE", "Zero rate", _C.COMMON, "BASE_CHARGE", legacy=True,
       required_action_types=("SET_ZERO_CHARGE",)),
    _T("PULSE", "Pulse", _C.COMMON, "PULSE", legacy=True,
       required_action_types=("SET_PULSE",)),
    _T("MINIMUM_CHARGE", "Minimum charge", _C.COMMON, "MINIMUM_CHARGE", legacy=True,
       required_action_types=("SET_MINIMUM_CHARGE",)),
    # Stage stays BASE_CHARGE until the R4 cut-over moves it (plan D12), so the
    # type and its action agree at every point in the migration.
    _T("MAXIMUM_CHARGE", "Maximum charge", _C.COMMON, "BASE_CHARGE",
       required_action_types=("SET_MAXIMUM_CHARGE",)),
    _T("BUNDLE", "Bundle", _C.COMMON, "BUNDLE", legacy=True,
       required_action_types=("CONSUME_BUNDLE",)),
    _T("PROMOTION", "Promotion", _C.COMMON, "PROMOTION", legacy=True,
       required_action_types=("APPLY_PROMOTION",)),
    _T("DISCOUNT", "Discount", _C.COMMON, "DISCOUNT", legacy=True,
       required_action_types=("APPLY_DISCOUNT",)),
    _T("SURCHARGE", "Surcharge", _C.COMMON, "SURCHARGE", legacy=True,
       required_action_types=("ADD_SURCHARGE",)),
    _T("TAX", "Tax", _C.COMMON, "TAX", legacy=True,
       required_action_types=("APPLY_TAX",)),
    _T("ROUNDING", "Rounding", _C.COMMON, "ROUNDING", legacy=True,
       required_action_types=("APPLY_ROUNDING",)),
    # --- Canonical-rating synonyms (2026-07) ------------------------------
    # The operator's canonical_rating model names the same families
    # differently. Same stages, same behaviour — aliases, not new code paths.
    # Contracts mirror `legacy.REQUIRED_ACTION_FOR_TYPE` exactly; the parity
    # test holds them together.
    _T("USAGE_RATE", "Usage rate", _C.COMMON, "BASE_CHARGE",
       required_action_types=("SET_RATE",), alias_of="BASE_TARIFF",
       description="The operator's name for a base tariff."),
    _T("TIERED_USAGE_RATE", "Tiered usage rate", _C.COMMON, "BASE_CHARGE",
       required_action_types=("SET_TIERED_RATE",), alias_of="BASE_TARIFF",
       description="A base tariff priced against cumulative usage tiers."),
    _T("PERCENTAGE_DISCOUNT", "Percentage discount", _C.COMMON, "DISCOUNT",
       required_action_types=("APPLY_PERCENT_DISCOUNT", "APPLY_FIXED_DISCOUNT",
                              "APPLY_DISCOUNT"),
       alias_of="DISCOUNT"),
    _T("FREE_UNIT", "Free unit", _C.COMMON, "BUNDLE",
       required_action_types=("CONSUME_ALLOWANCE", "SET_FREE_QUANTITY",
                              "CONSUME_BUNDLE"),
       alias_of="BUNDLE",
       description="Free units drawn from an allowance before charging."),
    _T("PERCENTAGE_TAX", "Percentage tax", _C.COMMON, "TAX",
       required_action_types=("APPLY_PERCENT_TAX", "APPLY_FIXED_TAX", "APPLY_TAX"),
       alias_of="TAX"),
)


# --- Prepaid (10) -----------------------------------------------------------

_PREPAID: tuple[RuleTypeSpec, ...] = (
    _T("BALANCE_BUCKET_PRIORITY", "Balance bucket priority", _C.PREPAID,
       "BALANCE_SELECTION", _M.PREPAID, ("SELECT_BALANCE_BUCKET",),
       description="Which bucket is consumed first for this kind of usage."),
    _T("BALANCE_PREFERENCE", "Main vs promotional balance", _C.PREPAID,
       "BALANCE_SELECTION", _M.PREPAID, ("SELECT_BALANCE_BUCKET",),
       description="Prefer promotional credit over main balance, or the reverse."),
    _T("SESSION_RESERVATION", "Session reservation", _C.PREPAID,
       "BALANCE_RESERVATION", _M.PREPAID, ("RESERVE_BALANCE",)),
    _T("RESERVATION_RELEASE", "Reservation release", _C.PREPAID,
       "BALANCE_RESERVATION", _M.PREPAID, ("RELEASE_RESERVATION",)),
    _T("BALANCE_DEDUCTION", "Balance deduction", _C.PREPAID,
       "BALANCE_DEDUCTION", _M.PREPAID, ("DEDUCT_BALANCE",)),
    _T("INSUFFICIENT_BALANCE", "Insufficient balance handling", _C.PREPAID,
       "BALANCE_EXCEPTION", _M.PREPAID,
       ("ALLOW_PARTIAL_USAGE", "ALLOW_NEGATIVE_BALANCE", "STOP_SERVICE")),
    _T("ZERO_BALANCE", "Zero balance handling", _C.PREPAID,
       "BALANCE_EXCEPTION", _M.PREPAID, ("STOP_SERVICE", "ALLOW_NEGATIVE_BALANCE")),
    _T("NEGATIVE_BALANCE", "Negative balance handling", _C.PREPAID,
       "BALANCE_EXCEPTION", _M.PREPAID, ("ALLOW_NEGATIVE_BALANCE",)),
    _T("PARTIAL_SESSION_CHARGING", "Partial session charging", _C.PREPAID,
       "BALANCE_EXCEPTION", _M.PREPAID, ("ALLOW_PARTIAL_USAGE",)),
    _T("BUNDLE_CONSUMPTION_ORDER", "Bundle consumption order", _C.PREPAID,
       "BUNDLE", _M.PREPAID, ("CONSUME_BUNDLE",),
       description="Which bundle is drawn down first when several match."),
)


# --- Postpaid (12) ----------------------------------------------------------

_POSTPAID: tuple[RuleTypeSpec, ...] = (
    _T("BILLING_CYCLE_ASSIGNMENT", "Billing cycle assignment", _C.POSTPAID,
       "BILLING_CYCLE_ASSIGNMENT", _M.POSTPAID, ("ASSIGN_BILLING_CYCLE",)),
    # The operator's word for base rating on a postpaid account. Same stage, same
    # actions, same implementation — an alias, not a second code path.
    _T("USAGE_RATING", "Usage rating", _C.POSTPAID, "BASE_CHARGE", _M.POSTPAID,
       ("SET_RATE", "SET_TIERED_RATE", "SET_ZERO_CHARGE"), alias_of="BASE_TARIFF"),
    _T("USAGE_AGGREGATION", "Usage aggregation", _C.POSTPAID, "USAGE_AGGREGATION",
       _M.POSTPAID, ("AGGREGATE_USAGE",)),
    _T("MONTHLY_RENTAL", "Monthly rental", _C.POSTPAID, "RECURRING_CHARGE",
       _M.POSTPAID, ("ADD_RECURRING_CHARGE",)),
    _T("RECURRING_CHARGE", "Recurring charge", _C.POSTPAID, "RECURRING_CHARGE",
       _M.POSTPAID, ("ADD_RECURRING_CHARGE",)),
    _T("ONE_TIME_CHARGE", "One-time charge", _C.POSTPAID, "ONE_TIME_CHARGE",
       _M.POSTPAID, ("ADD_ONE_TIME_CHARGE",)),
    _T("PRORATION", "Proration", _C.POSTPAID, "PRORATION", _M.POSTPAID,
       ("APPLY_PRORATION",)),
    _T("CREDIT_LIMIT", "Credit limit", _C.POSTPAID, "CREDIT_CHECK", _M.POSTPAID,
       ("CHECK_CREDIT_LIMIT",)),
    _T("INVOICE_COMPONENT", "Invoice component", _C.POSTPAID, "INVOICE_COMPONENT",
       _M.POSTPAID, ("ADD_INVOICE_COMPONENT", "ADD_USAGE_CHARGE")),
    _T("INVOICE_TAX", "Invoice tax", _C.POSTPAID, "INVOICE_TAX", _M.POSTPAID,
       ("APPLY_TAX",)),
    _T("LATE_FEE", "Late fee", _C.POSTPAID, "LATE_FEE", _M.POSTPAID, ("ADD_LATE_FEE",)),
    _T("INVOICE_ROUNDING", "Invoice rounding", _C.POSTPAID, "INVOICE_ROUNDING",
       _M.POSTPAID, ("APPLY_ROUNDING",)),
)


CANONICAL_RULE_TYPES: tuple[RuleTypeSpec, ...] = (*_COMMON, *_PREPAID, *_POSTPAID)

RULE_TYPE_BY_CODE: dict[str, RuleTypeSpec] = {t.code: t for t in CANONICAL_RULE_TYPES}

#: Canonical replacement for the legacy ``constants.RULE_TYPE_STAGE``.
CANONICAL_RULE_TYPE_STAGE: dict[str, str] = {
    t.code: t.stage_code for t in CANONICAL_RULE_TYPES
}


def rule_types_for_mode(charging_mode: str) -> tuple[RuleTypeSpec, ...]:
    """Types offerable for a charging mode — what the wizard's Step 1 filters to."""
    if charging_mode == ChargingMode.BOTH:
        return tuple(t for t in CANONICAL_RULE_TYPES if t.rule_category == RuleCategory.COMMON)
    return tuple(
        t for t in CANONICAL_RULE_TYPES
        if t.charging_mode in (ChargingMode.BOTH, charging_mode)
    )
