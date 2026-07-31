"""Every vocabulary translation between this platform and `canonical_rating`.

Collected in one module on purpose. These are the judgement calls in the whole
feature — which of thirty stages becomes which of seven, what a SUPERSEDED
version is called in a schema that has no such state — and they belong somewhere
a reviewer can read them end to end rather than scattered across the mappers.

The convention throughout: a value with no faithful target equivalent maps to
``None``, and the caller skips the row and logs it. There is no catch-all bucket.
"""

from __future__ import annotations

from app.modules.catalog.constants import CatalogStatus
from app.modules.rules.constants import DataType, Operator, RuleStatus
from app.modules.rules.vocabulary.values import ValueType

# --- Stages ------------------------------------------------------------------

#: The seven stages `chk_rating_rule_stage` permits.
TARGET_STAGES: frozenset[str] = frozenset(
    {"CLASSIFICATION", "ALLOWANCE", "BASE_RATE", "DISCOUNT", "SURCHARGE", "TAX", "ROUNDING"}
)

#: Platform stage code → target stage. Absent key = no faithful equivalent.
#:
#: The unmapped ones are unmapped for a reason, not by oversight:
#: ``MAXIMUM_CHARGE`` is a cap applied after discounts settle and the target has
#: no cap stage; ``BALANCE_RESERVATION`` / ``BALANCE_EXCEPTION`` /
#: ``SESSION_CONTROL`` are online-charging session mechanics; ``PRORATION``,
#: ``CREDIT_CHECK``, ``USAGE_AGGREGATION`` and ``INVOICE_COMPONENT`` are postpaid
#: billing steps with no usage-rating counterpart. Filing any of them under
#: SURCHARGE or CLASSIFICATION would put rules in a pipeline position that
#: changes what they mean.
STAGE_MAP: dict[str, str] = {
    # Common — classification
    "ELIGIBILITY": "CLASSIFICATION",
    "SERVICE_CLASSIFICATION": "CLASSIFICATION",
    "DESTINATION_CLASSIFICATION": "CLASSIFICATION",
    "TIME_BAND": "CLASSIFICATION",
    # Common — quantity and rate
    "QUANTITY": "BASE_RATE",
    "TARIFF_SELECTION": "BASE_RATE",
    "MINIMUM_CHARGE": "BASE_RATE",
    "PULSE": "BASE_RATE",
    "BASE_CHARGE": "BASE_RATE",
    # Common — allowance and adjustment
    "BUNDLE": "ALLOWANCE",
    "PROMOTION": "DISCOUNT",
    "DISCOUNT": "DISCOUNT",
    "SURCHARGE": "SURCHARGE",
    "TAX": "TAX",
    "ROUNDING": "ROUNDING",
    # Prepaid
    "BALANCE_SELECTION": "ALLOWANCE",
    "BALANCE_DEDUCTION": "ALLOWANCE",
    # Postpaid
    "BILLING_CYCLE_ASSIGNMENT": "CLASSIFICATION",
    "RECURRING_CHARGE": "BASE_RATE",
    "ONE_TIME_CHARGE": "BASE_RATE",
    "INVOICE_TAX": "TAX",
    "LATE_FEE": "SURCHARGE",
    "INVOICE_ROUNDING": "ROUNDING",
}


def target_stage(code: str) -> str | None:
    return STAGE_MAP.get(code)


# --- Rule status -------------------------------------------------------------

#: Platform has nine lifecycle states, the target six.
#:
#: ``PUBLISHED`` → ``ACTIVE`` is the one worth defending: the platform's own
#: exclusion constraint treats ACTIVE and PUBLISHED as the same thing — a version
#: occupying a live effective window (`ex_rule_version_active_window`,
#: `canonical/rule.py`). Mirroring it as anything else would contradict the
#: constraint that governs it.
STATUS_MAP: dict[str, str] = {
    RuleStatus.DRAFT: "DRAFT",
    RuleStatus.VALIDATED: "DRAFT",
    RuleStatus.REVIEWED: "PENDING_APPROVAL",
    RuleStatus.APPROVED: "PENDING_APPROVAL",
    RuleStatus.COMPILED: "PENDING_APPROVAL",
    RuleStatus.PUBLISHED: "ACTIVE",
    RuleStatus.ACTIVE: "ACTIVE",
    RuleStatus.SUPERSEDED: "INACTIVE",
    RuleStatus.RETIRED: "RETIRED",
}


def target_status(status: str) -> str | None:
    return STATUS_MAP.get(status)


# --- Catalogue status --------------------------------------------------------

#: `time_band` and `destination_prefix` allow only ACTIVE / INACTIVE, so RETIRED
#: folds into INACTIVE. Nothing is lost that the target could have held.
CATALOG_STATUS_MAP: dict[str, str] = {
    CatalogStatus.ACTIVE: "ACTIVE",
    CatalogStatus.INACTIVE: "INACTIVE",
    CatalogStatus.RETIRED: "INACTIVE",
}


def target_catalog_status(status: str) -> str:
    return CATALOG_STATUS_MAP.get(status, "INACTIVE")


# --- Value types -------------------------------------------------------------

#: `chk_condition_value_type`.
CONDITION_VALUE_TYPES: frozenset[str] = frozenset(
    {"STRING", "INTEGER", "DECIMAL", "BOOLEAN", "DATE", "TIMESTAMP", "ARRAY"}
)
#: `chk_action_value_type` — same set minus ARRAY, plus EXPRESSION.
ACTION_VALUE_TYPES: frozenset[str] = frozenset(
    {"STRING", "INTEGER", "DECIMAL", "BOOLEAN", "DATE", "TIMESTAMP", "EXPRESSION"}
)

#: Platform ValueType → target value type.
#:
#: MONEY collapses into DECIMAL because the target has no money type. The
#: currency does not vanish — it is carried on `rating_rule.currency_code` for
#: the rule as a whole. A per-action currency that differs from the rule's is not
#: representable; `mapping.py` logs that case.
VALUE_TYPE_MAP: dict[str, str] = {
    ValueType.STRING: "STRING",
    ValueType.NUMBER: "DECIMAL",
    ValueType.MONEY: "DECIMAL",
    ValueType.BOOLEAN: "BOOLEAN",
    ValueType.ENUM: "STRING",
    ValueType.REFERENCE: "STRING",
    ValueType.DATE: "DATE",
    ValueType.DATETIME: "TIMESTAMP",
    ValueType.LIST: "ARRAY",
    # RANGE is resolved from the attribute's own type by the caller — a BETWEEN
    # over dates and one over money are different target types.
}

#: Platform DataType (attribute registry) → `chk_attribute_data_type`.
DATA_TYPE_MAP: dict[str, str] = {
    DataType.STRING: "STRING",
    DataType.NUMBER: "DECIMAL",
    DataType.BOOLEAN: "BOOLEAN",
    DataType.ENUM: "STRING",
    DataType.REFERENCE: "STRING",
    DataType.DATETIME: "TIMESTAMP",
}


def target_condition_value_type(value_type: str, attribute_data_type: str | None) -> str | None:
    """Target value type for one stored condition value."""
    if value_type == ValueType.RANGE:
        # BETWEEN: the declared type is the *element* type, which lives on the
        # attribute. Defaulting to DECIMAL would type a date range as a number.
        if attribute_data_type is None:
            return None
        return DATA_TYPE_MAP.get(attribute_data_type)
    return VALUE_TYPE_MAP.get(value_type)


def target_action_value_type(value_type: str | None) -> str:
    """Target value type for an action parameter. ARRAY is not permitted there,
    so a list parameter is carried as its joined STRING form."""
    if value_type is None:
        return "STRING"
    mapped = VALUE_TYPE_MAP.get(value_type, "STRING")
    return "STRING" if mapped == "ARRAY" else mapped


# --- Operators ---------------------------------------------------------------

#: The target has no per-condition negation flag, so a negated predicate must be
#: expressed by inverting its operator. These five pairs invert exactly.
#: STARTS_WITH, CONTAINS and BETWEEN have no inverse in the operator vocabulary,
#: so a negated one cannot be mirrored — `mapping.py` skips the rule rather than
#: dropping the negation and storing the opposite of what the author wrote.
OPERATOR_INVERSE: dict[str, str] = {
    Operator.EQUALS: Operator.NOT_EQUALS,
    Operator.NOT_EQUALS: Operator.EQUALS,
    Operator.IN: Operator.NOT_IN,
    Operator.NOT_IN: Operator.IN,
    Operator.EXISTS: Operator.NOT_EXISTS,
    Operator.NOT_EXISTS: Operator.EXISTS,
    Operator.GREATER_THAN: Operator.LESS_OR_EQUAL,
    Operator.LESS_OR_EQUAL: Operator.GREATER_THAN,
    Operator.LESS_THAN: Operator.GREATER_OR_EQUAL,
    Operator.GREATER_OR_EQUAL: Operator.LESS_THAN,
}


def invert_operator(code: str) -> str | None:
    return OPERATOR_INVERSE.get(code)


# --- Charging mode -----------------------------------------------------------

#: `chk_rating_rule_account_scope` also allows HYBRID, which this platform has no
#: concept of; nothing maps onto it.
ACCOUNT_SCOPE_MAP: dict[str, str] = {
    "PREPAID": "PREPAID",
    "POSTPAID": "POSTPAID",
    "BOTH": "BOTH",
}


def target_account_scope(charging_mode: str) -> str:
    return ACCOUNT_SCOPE_MAP.get(charging_mode, "BOTH")


# --- Weekdays ----------------------------------------------------------------

#: Platform stores a *list* of weekday tokens; the target stores one `day_type`
#: per row. A band on a set of days that is not "all", "weekdays" or "weekend"
#: therefore becomes several target rows — one per day.
DAY_MAP: dict[str, str] = {
    "MON": "MONDAY",
    "TUE": "TUESDAY",
    "WED": "WEDNESDAY",
    "THU": "THURSDAY",
    "FRI": "FRIDAY",
    "SAT": "SATURDAY",
    "SUN": "SUNDAY",
}

_WEEKDAYS = frozenset({"MON", "TUE", "WED", "THU", "FRI"})
_WEEKEND = frozenset({"SAT", "SUN"})
_ALL_DAYS = _WEEKDAYS | _WEEKEND


def target_day_types(days: list[str] | None) -> list[str]:
    """Day tokens → the `day_type` values needed to cover them."""
    tokens = {str(d).upper()[:3] for d in (days or []) if d}
    known = tokens & _ALL_DAYS
    if not known or known == _ALL_DAYS:
        return ["ANY"]
    if known == _WEEKDAYS:
        return ["WEEKDAY"]
    if known == _WEEKEND:
        return ["WEEKEND"]
    # Any other combination: one explicit row per day, so the band's coverage is
    # exact rather than rounded to the nearest bucket.
    return [DAY_MAP[d] for d in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN") if d in known]
