"""Enumerations shared by the canonical metadata and the rule model.

These are Python enums rather than Postgres ENUM types on purpose: a telecom
catalogue grows new service types and zone types regularly, and adding a value
to a Postgres enum needs a migration and an exclusive lock. A CHECK-free
VARCHAR + validation at the API boundary keeps the deploy cheap; the values are
published to the UI via ``GET /api/rating/meta/enums`` so the two never drift.
"""

from __future__ import annotations

from enum import StrEnum


class ServiceType(StrEnum):
    VOICE = "VOICE"
    SMS = "SMS"
    DATA = "DATA"
    MMS = "MMS"
    ROAMING = "ROAMING"
    DIGITAL = "DIGITAL"
    #: Matches any service — used by global default rules.
    ANY = "ANY"


class UsageUnit(StrEnum):
    SECOND = "SECOND"
    MINUTE = "MINUTE"
    MESSAGE = "MESSAGE"
    BYTE = "BYTE"
    KILOBYTE = "KILOBYTE"
    MEGABYTE = "MEGABYTE"
    EVENT = "EVENT"


class AccountType(StrEnum):
    PREPAID = "PREPAID"
    POSTPAID = "POSTPAID"
    HYBRID = "HYBRID"
    ANY = "ANY"


class ZoneType(StrEnum):
    ONNET = "ONNET"
    OFFNET = "OFFNET"
    LOCAL = "LOCAL"
    NATIONAL = "NATIONAL"
    INTERNATIONAL = "INTERNATIONAL"
    ROAMING = "ROAMING"
    PREMIUM = "PREMIUM"
    EMERGENCY = "EMERGENCY"


class TaxType(StrEnum):
    VAT = "VAT"
    GST = "GST"
    SALES = "SALES"
    EXCISE = "EXCISE"
    SURCHARGE = "SURCHARGE"


class RoundingMode(StrEnum):
    HALF_UP = "HALF_UP"
    HALF_EVEN = "HALF_EVEN"
    CEILING = "CEILING"
    FLOOR = "FLOOR"
    TRUNCATE = "TRUNCATE"


class DiscountType(StrEnum):
    PERCENTAGE = "PERCENTAGE"
    FIXED_AMOUNT = "FIXED_AMOUNT"
    RATE_OVERRIDE = "RATE_OVERRIDE"
    FREE_UNITS = "FREE_UNITS"


class ResetPeriod(StrEnum):
    NONE = "NONE"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    BILLING_CYCLE = "BILLING_CYCLE"


class CatalogStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    RETIRED = "RETIRED"


class Weekday(StrEnum):
    """Monday-first, to match ISO-8601."""

    MON = "MON"
    TUE = "TUE"
    WED = "WED"
    THU = "THU"
    FRI = "FRI"
    SAT = "SAT"
    SUN = "SUN"


#: Day-of-week tokens used by time bands.
WEEKDAYS: tuple[str, ...] = tuple(d.value for d in Weekday)
