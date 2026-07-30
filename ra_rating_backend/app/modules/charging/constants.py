"""Enumerations for the charging / prepaid / postpaid metadata catalogue.

Python enums rather than Postgres ENUM types, for the same reason the existing
catalogue does it: an operator adds a balance category or a proration method far
more often than anyone wants to take an exclusive lock for. The values reach the
UI through ``GET /api/rating/meta/enums``, so the two cannot drift.
"""

from __future__ import annotations

from enum import StrEnum

# --- Shared charging (plan §C.5) --------------------------------------------


class UnitDimension(StrEnum):
    """What a charging unit measures.

    A rate's unit must match its dimension — charging per MEGABYTE for a voice
    call is a modelling error, not a preference, and this is what lets the
    validator say so.
    """

    TIME = "TIME"
    VOLUME = "VOLUME"
    EVENT = "EVENT"
    MESSAGE = "MESSAGE"
    CURRENCY = "CURRENCY"


class PulseRoundMode(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    NEAREST = "NEAREST"


class ReleaseMode(StrEnum):
    """What a reservation gives back when a session ends."""

    #: Return only the quota the session did not consume. The normal case.
    UNUSED = "UNUSED"
    #: Return the whole reservation — used when the session is deemed not chargeable.
    ALL = "ALL"


class ReservationTimeout(StrEnum):
    RELEASE = "RELEASE"
    EXTEND = "EXTEND"
    TERMINATE = "TERMINATE"


# --- Prepaid (plan §C.6) ----------------------------------------------------


class BalanceCategory(StrEnum):
    MAIN = "MAIN"
    PROMOTIONAL = "PROMOTIONAL"
    VOICE = "VOICE"
    SMS = "SMS"
    DATA = "DATA"
    SHARED = "SHARED"


class ExpiryPolicy(StrEnum):
    #: Expires on a fixed date regardless of when it was topped up.
    CALENDAR = "CALENDAR"
    #: Expires N days after the last credit.
    ROLLING = "ROLLING"
    NONE = "NONE"


class ReservationStrategy(StrEnum):
    """How an OCS hands out quota."""

    #: One quota for the whole session, re-requested when exhausted.
    QUOTA = "QUOTA"
    #: Fixed-size increments, re-authorised at a threshold.
    INCREMENTAL = "INCREMENTAL"
    #: No reservation — charge on the terminate request only.
    NONE = "NONE"


# --- Postpaid (plan §C.7) ---------------------------------------------------


class BillingFrequency(StrEnum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"


class InvoiceComponentType(StrEnum):
    RECURRING = "RECURRING"
    USAGE = "USAGE"
    ONE_TIME = "ONE_TIME"
    CREDIT = "CREDIT"
    TAX = "TAX"
    ADJUSTMENT = "ADJUSTMENT"
    LATE_FEE = "LATE_FEE"


class ComponentSign(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class TriggerEvent(StrEnum):
    ACTIVATION = "ACTIVATION"
    SUSPENSION = "SUSPENSION"
    SIM_SWAP = "SIM_SWAP"
    MIGRATION = "MIGRATION"
    MANUAL = "MANUAL"


class ProrationMethod(StrEnum):
    DAILY = "DAILY"
    MONTHLY_30 = "MONTHLY_30"
    ACTUAL_DAYS = "ACTUAL_DAYS"
    NONE = "NONE"


class CreditBreachAction(StrEnum):
    NOTIFY = "NOTIFY"
    BAR_OUTGOING = "BAR_OUTGOING"
    BAR_ALL = "BAR_ALL"
    NONE = "NONE"


class AggregationDimension(StrEnum):
    SUBSCRIBER = "SUBSCRIBER"
    ACCOUNT = "ACCOUNT"
    GROUP = "GROUP"


class AggregationWindow(StrEnum):
    CYCLE = "CYCLE"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
