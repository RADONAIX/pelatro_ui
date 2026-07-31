"""Assurance statuses and root-cause categories (requirement §17, §18)."""

from __future__ import annotations

from enum import StrEnum


class AssuranceStatus(StrEnum):
    MATCHED = "MATCHED"
    UNDERCHARGED = "UNDERCHARGED"
    OVERCHARGED = "OVERCHARGED"
    #: The billing system produced no charge at all for rateable usage.
    UNRATED = "UNRATED"
    #: Billed zero where we expected zero — correct, and worth distinguishing
    #: from MATCHED so free traffic can be reported on separately.
    ZERO_CHARGE = "ZERO_CHARGE"
    NO_MATCHING_RULE = "NO_MATCHING_RULE"
    MULTIPLE_RULE_MATCH = "MULTIPLE_RULE_MATCH"
    PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
    BUNDLE_MISMATCH = "BUNDLE_MISMATCH"
    DISCOUNT_MISMATCH = "DISCOUNT_MISMATCH"
    TAX_MISMATCH = "TAX_MISMATCH"
    PULSE_MISMATCH = "PULSE_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    ENGINE_ERROR = "ENGINE_ERROR"


#: Statuses that represent a correct outcome. Everything else raises an
#: exception and counts towards revenue at risk.
CLEAN_STATUSES: frozenset[str] = frozenset(
    {AssuranceStatus.MATCHED, AssuranceStatus.ZERO_CHARGE}
)


class RootCause(StrEnum):
    INCORRECT_TARIFF = "INCORRECT_TARIFF"
    WRONG_PRODUCT_MAPPING = "WRONG_PRODUCT_MAPPING"
    WRONG_DESTINATION = "WRONG_DESTINATION"
    WRONG_PULSE = "WRONG_PULSE"
    BUNDLE_NOT_APPLIED = "BUNDLE_NOT_APPLIED"
    BUNDLE_OVER_CONSUMED = "BUNDLE_OVER_CONSUMED"
    DISCOUNT_MISSING = "DISCOUNT_MISSING"
    TAX_INCORRECT = "TAX_INCORRECT"
    ROUNDING_ISSUE = "ROUNDING_ISSUE"
    EXPIRED_RULE = "EXPIRED_RULE"
    REFERENCE_DATA_ERROR = "REFERENCE_DATA_ERROR"
    BILLING_DEPLOYMENT_ISSUE = "BILLING_DEPLOYMENT_ISSUE"
    NO_TARIFF_DEFINED = "NO_TARIFF_DEFINED"
    UNKNOWN = "UNKNOWN"


class ExceptionStatus(StrEnum):
    NEW = "NEW"
    ASSIGNED = "ASSIGNED"
    INVESTIGATING = "INVESTIGATING"
    ROOT_CAUSE_IDENTIFIED = "ROOT_CAUSE_IDENTIFIED"
    RESOLVED = "RESOLVED"
    REPROCESSED = "REPROCESSED"
    CLOSED = "CLOSED"


EXCEPTION_TRANSITIONS: dict[str, tuple[str, ...]] = {
    ExceptionStatus.NEW: (ExceptionStatus.ASSIGNED, ExceptionStatus.INVESTIGATING,
                          ExceptionStatus.CLOSED),
    ExceptionStatus.ASSIGNED: (ExceptionStatus.INVESTIGATING, ExceptionStatus.CLOSED),
    ExceptionStatus.INVESTIGATING: (ExceptionStatus.ROOT_CAUSE_IDENTIFIED,
                                    ExceptionStatus.RESOLVED, ExceptionStatus.CLOSED),
    ExceptionStatus.ROOT_CAUSE_IDENTIFIED: (ExceptionStatus.RESOLVED, ExceptionStatus.CLOSED),
    ExceptionStatus.RESOLVED: (ExceptionStatus.REPROCESSED, ExceptionStatus.CLOSED,
                               ExceptionStatus.INVESTIGATING),
    ExceptionStatus.REPROCESSED: (ExceptionStatus.CLOSED, ExceptionStatus.INVESTIGATING),
    ExceptionStatus.CLOSED: (ExceptionStatus.INVESTIGATING,),  # reopen
}


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


#: Below this absolute variance a difference is rounding noise, not leakage.
#: Expressed in minor units of the currency at comparison time.
DEFAULT_TOLERANCE = 0.005
