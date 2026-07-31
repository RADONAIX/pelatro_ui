"""Source CDR → the common usage model (§9).

Per-vendor field maps live in ``CDR_PROFILES``. Adding a switch vendor is one
entry, not a new code path — which matters because a real estate carries a
different CDR layout per network element.

Everything here is pure: a dict in, a dict out. That keeps the whole
normalization surface unit-testable without a database.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

#: Timestamp layouts operators actually emit, most-specific first.
_TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y%m%d%H%M%S",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
)


class NormalizeError(Exception):
    def __init__(self, message: str, *, field: str = ""):
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass(frozen=True)
class CdrProfile:
    """How one source system's columns map onto the common model."""

    code: str
    label: str
    service_type: str
    #: canonical field -> the source column names to try, in order.
    fields: dict[str, tuple[str, ...]]
    #: Fields without which the record cannot be rated.
    required: tuple[str, ...] = ("cdr_id", "event_timestamp")
    #: Multiply the source volume by this to reach bytes (data CDRs).
    volume_multiplier: float = 1.0
    #: The source identity columns that make a record unique.
    identity: tuple[str, ...] = ("cdr_id",)
    notes: str = ""


MSC_VOICE = CdrProfile(
    code="MSC",
    label="MSC voice call record",
    service_type="VOICE",
    fields={
        "cdr_id": ("cdr_id", "record_id", "call_id", "callReference", "id"),
        "subscriber_id": ("subscriber_id", "subscriberId", "sub_id"),
        "account_id": ("account_id", "accountId", "billing_account"),
        "msisdn": ("msisdn", "callingNumber", "calling_number", "a_number", "anumber"),
        "imsi": ("imsi", "servedIMSI", "served_imsi"),
        "calling_number": ("calling_number", "callingNumber", "a_number", "msisdn"),
        "called_number": ("called_number", "calledNumber", "b_number", "bnumber", "dialled"),
        "event_timestamp": (
            "event_timestamp", "answerTime", "start_time", "startTime", "timestamp",
        ),
        "duration_seconds": ("duration_seconds", "duration", "callDuration", "seizure_duration"),
        "actual_charge": ("actual_charge", "charge", "chargeAmount", "billed_amount", "amount"),
        "actual_discount": ("actual_discount", "discount", "discountAmount"),
        "actual_tax": ("actual_tax", "tax", "taxAmount", "vat"),
        "currency": ("currency", "currencyCode", "ccy"),
        "network_type": ("network_type", "rat", "accessType"),
        "roaming": ("roaming", "roamingFlag", "is_roaming"),
        "visited_operator": ("visited_operator", "visitedPLMN", "vplmn"),
        "event_type": ("event_type", "callType", "recordType"),
    },
    required=("cdr_id", "event_timestamp", "called_number"),
    identity=("cdr_id", "calling_number", "event_timestamp"),
    notes="Mobile-originated voice from the switch.",
)

SMS_CDR = CdrProfile(
    code="SMS",
    label="SMS record",
    service_type="SMS",
    fields={
        "cdr_id": ("cdr_id", "record_id", "message_id", "id"),
        "subscriber_id": ("subscriber_id", "subscriberId"),
        "account_id": ("account_id", "accountId"),
        "msisdn": ("msisdn", "originator", "calling_number", "a_number"),
        "imsi": ("imsi", "servedIMSI"),
        "calling_number": ("calling_number", "originator", "a_number", "msisdn"),
        "called_number": ("called_number", "recipient", "b_number", "destination"),
        "event_timestamp": ("event_timestamp", "submitTime", "timestamp", "start_time"),
        "usage_volume": ("usage_volume", "message_count", "messages"),
        "actual_charge": ("actual_charge", "charge", "billed_amount", "amount"),
        "actual_tax": ("actual_tax", "tax", "vat"),
        "currency": ("currency", "currencyCode"),
        "roaming": ("roaming", "roamingFlag"),
        "event_type": ("event_type", "messageType"),
    },
    required=("cdr_id", "event_timestamp", "called_number"),
    identity=("cdr_id", "calling_number", "event_timestamp"),
)

DATA_CDR = CdrProfile(
    code="DATA",
    label="GGSN / PGW data session",
    service_type="DATA",
    fields={
        "cdr_id": ("cdr_id", "record_id", "session_id", "chargingId", "id"),
        "subscriber_id": ("subscriber_id", "subscriberId"),
        "account_id": ("account_id", "accountId"),
        "msisdn": ("msisdn", "servedMSISDN", "served_msisdn"),
        "imsi": ("imsi", "servedIMSI"),
        "event_timestamp": ("event_timestamp", "recordOpeningTime", "start_time", "timestamp"),
        "duration_seconds": ("duration_seconds", "duration", "sessionDuration"),
        "usage_volume": (
            "usage_volume", "total_volume", "totalVolume", "dataVolume", "bytes",
        ),
        "actual_charge": ("actual_charge", "charge", "billed_amount", "amount"),
        "actual_tax": ("actual_tax", "tax"),
        "currency": ("currency", "currencyCode"),
        "apn": ("apn", "accessPointName", "apn_ni"),
        "network_type": ("network_type", "rat", "ratType"),
        "roaming": ("roaming", "roamingFlag"),
        "rating_group": ("rating_group", "ratingGroup"),
    },
    required=("cdr_id", "event_timestamp"),
    identity=("cdr_id", "msisdn", "event_timestamp"),
    notes="Volume is expected in bytes; set volume_multiplier per source if not.",
)

ROAMING_TAP = CdrProfile(
    code="TAP",
    label="Roaming TAP record",
    service_type="ROAMING",
    fields={
        "cdr_id": ("cdr_id", "record_id", "callEventDetailId", "id"),
        "imsi": ("imsi", "servedIMSI"),
        "msisdn": ("msisdn", "servedMSISDN"),
        "calling_number": ("calling_number", "a_number", "msisdn"),
        "called_number": ("called_number", "dialledDigits", "b_number"),
        "event_timestamp": ("event_timestamp", "callEventStartTimeStamp", "timestamp"),
        "duration_seconds": ("duration_seconds", "totalCallEventDuration", "duration"),
        "usage_volume": ("usage_volume", "dataVolume"),
        "actual_charge": ("actual_charge", "charge", "chargeAmount", "totalCharge"),
        "actual_tax": ("actual_tax", "taxValue", "tax"),
        "currency": ("currency", "currencyCode"),
        "visited_operator": ("visited_operator", "sender", "vplmn", "recEntityId"),
        "event_type": ("event_type", "serviceType", "callTypeGroup"),
    },
    required=("cdr_id", "event_timestamp"),
    identity=("cdr_id", "imsi", "event_timestamp"),
    notes="Roaming always sets roaming=true regardless of the source flag.",
)

CDR_PROFILES: dict[str, CdrProfile] = {
    p.code: p for p in (MSC_VOICE, SMS_CDR, DATA_CDR, ROAMING_TAP)
}


# --- Coercion ---------------------------------------------------------------


def _pick(payload: dict[str, Any], names: tuple[str, ...]) -> Any:
    """First non-empty source column, matched case-insensitively."""
    lowered = {str(k).lower(): v for k, v in payload.items()}
    for name in names:
        value = payload.get(name)
        if value in (None, ""):
            value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def to_decimal(value: Any, field_name: str = "") -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    text = (
        str(value).replace(",", "").replace("£", "").replace("$", "").replace("€", "").strip()
    )
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError) as exc:
        raise NormalizeError(f"'{value}' is not a number.", field=field_name) from exc


def to_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().upper()
    if text in {"TRUE", "T", "Y", "YES", "1"}:
        return True
    if text in {"FALSE", "F", "N", "NO", "0"}:
        return False
    return None


def to_timestamp(value: Any, field_name: str = "event_timestamp") -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    # Epoch seconds are common in OCS exports.
    if text.isdigit() and len(text) == 10:
        return datetime.fromtimestamp(int(text), tz=UTC)
    normalised = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalised)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise NormalizeError(f"'{value}' is not a recognised timestamp.", field=field_name)


def normalise_number(msisdn: Any) -> str | None:
    """Strip formatting and international prefixes to bare digits.

    ``+44 7700 900123``, ``0044-7700-900123`` and ``00447700900123`` are the
    same number; leaving them different would fragment the prefix match and put
    the same destination in three different zones.
    """
    if msisdn in (None, ""):
        return None
    digits = "".join(ch for ch in str(msisdn) if ch.isdigit() or ch == "+")
    if digits.startswith("+"):
        digits = digits[1:]
    elif digits.startswith("00"):
        digits = digits[2:]
    return digits or None


def record_hash(profile: CdrProfile, normalized: dict[str, Any]) -> str:
    """Identity of the event, for duplicate detection.

    Hashes the identity fields only — not the charge — so a corrected resend of
    the same call is recognised as the same event rather than as new revenue.
    """
    parts = [profile.code]
    for name in profile.identity:
        value = normalized.get(name)
        parts.append(value.isoformat() if isinstance(value, datetime) else str(value or ""))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


# --- Normalization ----------------------------------------------------------


@dataclass
class NormalizedRecord:
    values: dict[str, Any] = field(default_factory=dict)
    record_hash: str = ""


def normalize(payload: dict[str, Any], profile: CdrProfile) -> NormalizedRecord:
    """Map one source record onto the common usage model. Raises NormalizeError."""
    out: dict[str, Any] = {}

    for canonical, sources in profile.fields.items():
        out[canonical] = _pick(payload, sources)

    out["service_type"] = profile.service_type
    out["event_timestamp"] = to_timestamp(out.get("event_timestamp"))
    out["event_date"] = out["event_timestamp"].date()

    for name in ("duration_seconds", "usage_volume", "actual_charge", "actual_discount",
                 "actual_tax"):
        out[name] = to_decimal(out.get(name), name)

    if out.get("usage_volume") is not None and profile.volume_multiplier != 1.0:
        out["usage_volume"] = out["usage_volume"] * profile.volume_multiplier

    out["roaming"] = to_bool(out.get("roaming"))
    if profile.code == "TAP":
        # A TAP record is by definition roaming, whatever the source flag says.
        out["roaming"] = True

    for name in ("msisdn", "calling_number", "called_number"):
        out[name] = normalise_number(out.get(name))
    out["imsi"] = normalise_number(out.get("imsi"))

    for name in ("currency", "network_type", "event_type", "apn", "rating_group",
                 "visited_operator"):
        value = out.get(name)
        out[name] = str(value).upper() if value not in (None, "") else None

    for name in ("cdr_id", "subscriber_id", "account_id"):
        value = out.get(name)
        out[name] = str(value).strip() if value not in (None, "") else None

    for name in profile.required:
        if out.get(name) in (None, ""):
            raise NormalizeError(f"'{name}' is required but missing.", field=name)

    if out.get("duration_seconds") is not None and out["duration_seconds"] < 0:
        raise NormalizeError("Duration cannot be negative.", field="duration_seconds")
    if out.get("usage_volume") is not None and out["usage_volume"] < 0:
        raise NormalizeError("Usage volume cannot be negative.", field="usage_volume")

    return NormalizedRecord(values=out, record_hash=record_hash(profile, out))


def event_date_of(records: list[NormalizedRecord]) -> date | None:
    """The batch's dominant event date, for partitioning and reporting."""
    if not records:
        return None
    counts: dict[date, int] = {}
    for record in records:
        day = record.values["event_date"]
        counts[day] = counts.get(day, 0) + 1
    return max(counts, key=lambda d: counts[d])
