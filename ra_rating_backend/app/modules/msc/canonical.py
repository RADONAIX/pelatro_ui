"""One raw MSC row → one canonical usage record (§6).

Pure: a mapping in, a ``CanonicalUsage`` out, no I/O. The whole vendor-specific
surface of this integration is here, so a second switch vendor is a second
module beside this one rather than a change to the pipeline.

**The identity rule (§6).** ``usage_id`` is derived from
``source_system + source_file + source_record_number`` and nothing else. It is
not a UUID and never regenerated: re-reading the same source row on a second run
must produce the same id, or every idempotency guarantee downstream — the unique
constraint on the final result, the duplicate check, the re-rate endpoint —
silently stops working while appearing to succeed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.modules.msc import decode
from app.modules.msc.constants import (
    HOME_MCCMNC_DEFAULT,
    HOME_SUBSCRIBER_VALUES,
    RECORD_KINDS,
    RecordKind,
    SkipReason,
)


class TransformError(Exception):
    """This row cannot become a usage record. Recorded per row, never fatal."""

    def __init__(self, message: str, *, field: str = "", code: str = "INVALID_USAGE_RECORD"):
        super().__init__(message)
        self.message = message
        self.field = field
        self.code = code


@dataclass
class CanonicalUsage:
    """The vendor-neutral usage model (§5.5)."""

    usage_id: str
    source_system: str
    source_file: str
    source_record_number: str
    service_type: str
    call_direction: str
    event_start_time: datetime
    event_date: date
    subscriber_msisdn: str | None = None
    subscriber_imsi: str | None = None
    calling_number: str | None = None
    called_number: str | None = None
    charged_party: str | None = None
    event_end_time: datetime | None = None
    duration_seconds: int | None = None
    cell_id: str | None = None
    location_area_code: str | None = None
    call_reference: str | None = None
    #: Absent on an MSC record — the switch does not price calls. Populated by
    #: the OCS correlation step (§21), not here.
    actual_charge: Decimal | None = None
    currency: str | None = None
    roaming_flag: bool = False
    serving_network: str | None = None
    duplicate_hash: str = ""
    #: Everything the switch said that the canonical model has no column for.
    #: Kept so a rule can reference a vendor field without a schema change.
    source_attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkippedRecord:
    """A source row that is real but carries no rateable usage."""

    source_file: str
    source_record_number: str
    record_kind: str
    reason: str


#: Characters that would make a usage_id awkward to pass through a URL path.
_ID_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _id_token(value: str) -> str:
    return _ID_UNSAFE.sub("_", str(value).strip())


def build_usage_id(source_system: str, source_file: str, record_number: str) -> str:
    """The deterministic usage identity (§6).

    Readable rather than hashed, so an analyst holding a usage_id can find the
    source row without a lookup table. Long file names are truncated with a
    short digest of the full name appended, which keeps the id inside the
    column width without ever colliding.
    """
    stem = str(source_file).split("/")[-1]
    for suffix in (".xml", ".dat", ".csv", ".gz"):
        while stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]

    token = _id_token(stem)
    if len(token) > 48:
        digest = hashlib.sha1(str(source_file).encode()).hexdigest()[:8]
        token = f"{token[:40]}_{digest}"
    return f"{_id_token(source_system)}-{token}-{_id_token(record_number)}"


def build_duplicate_hash(
    *,
    subscriber_msisdn: str | None,
    called_number: str | None,
    event_start_time: datetime | None,
    duration_seconds: int | None,
    call_reference: str | None,
) -> str:
    """The business identity of the event (§7).

    Distinct from ``usage_id``, which identifies a *row in a file*. The same
    call re-exported into a differently named file is a different usage_id and
    the same duplicate hash — which is exactly the case a file-level check
    misses and which double-counts revenue.
    """
    parts = [
        subscriber_msisdn or "",
        called_number or "",
        event_start_time.astimezone(UTC).isoformat() if event_start_time else "",
        str(duration_seconds if duration_seconds is not None else ""),
        call_reference or "",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _text(row: dict[str, Any], name: str) -> str | None:
    value = row.get(name)
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _event_time(row: dict[str, Any], spec: RecordKind) -> datetime | None:
    """The event start, preferring the loader's parsed column.

    Falls back to decoding the raw BCD field, because a loader that failed to
    parse one timestamp should cost us that one record's precision, not the
    record itself.
    """
    for column in spec.time_columns:
        value = row.get(column)
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        raw = _text(row, column.removesuffix("_ts"))
        if raw:
            try:
                return decode.decode_timestamp(raw)
            except decode.DecodeError:
                continue
    return None


def transform(
    row: dict[str, Any],
    *,
    source_system: str,
    country_code: str,
    home_mccmnc: str = HOME_MCCMNC_DEFAULT,
    default_currency: str | None = None,
) -> CanonicalUsage | SkippedRecord:
    """Map one raw MSC row onto the canonical model.

    Returns a ``SkippedRecord`` for kinds that carry no rateable usage, so the
    caller can account for every source row without treating "not chargeable"
    as an error.
    """
    kind = _text(row, "record_kind") or ""
    source_file = _text(row, "source_file") or ""
    record_number = _text(row, "record_number") or str(row.get("id") or "")

    spec = RECORD_KINDS.get(kind)
    if spec is None:
        return SkippedRecord(source_file, record_number, kind, SkipReason.UNKNOWN_RECORD_KIND)
    if not spec.rateable:
        return SkippedRecord(
            source_file, record_number, kind, spec.skip_reason or SkipReason.NOT_RATEABLE
        )

    if not source_file or not record_number:
        raise TransformError(
            "The source row has no file name or record number, so no stable "
            "usage identity can be derived.",
            field="source_record_number",
        )

    # --- Identity -----------------------------------------------------------
    usage_id = build_usage_id(source_system, source_file, record_number)

    # --- Parties ------------------------------------------------------------
    try:
        msisdn = decode.normalise_msisdn(_text(row, "served_msisdn"), country_code=country_code)
        imsi = decode.decode_imsi(_text(row, "served_imsi"))
        other_party = (
            decode.normalise_msisdn(
                _text(row, spec.other_party_column), country_code=country_code
            )
            if spec.other_party_column
            else None
        )
    except decode.DecodeError as exc:
        raise TransformError(str(exc), field="msisdn", code="INVALID_USAGE_RECORD") from exc

    if not msisdn and not imsi:
        raise TransformError(
            "The record identifies no served subscriber.",
            field="subscriber_msisdn",
            code="INVALID_USAGE_RECORD",
        )

    # Calling/called are stated from the network's point of view, so they do not
    # swap with direction: on a terminating record our subscriber is the called
    # party and the counterparty is the caller.
    if spec.direction == "MT":
        calling_number, called_number = other_party, msisdn
    else:
        calling_number, called_number = msisdn, other_party

    # --- Time and duration --------------------------------------------------
    event_start = _event_time(row, spec)
    if event_start is None:
        raise TransformError(
            "The record carries no usable event time.",
            field="event_start_time",
            code="INVALID_USAGE_RECORD",
        )

    duration: int | None = None
    if spec.has_duration:
        try:
            duration = decode.decode_duration(_text(row, "call_duration"))
        except decode.DecodeError as exc:
            raise TransformError(str(exc), field="duration_seconds") from exc
        if duration is None:
            raise TransformError(
                "A voice record with no duration cannot be rated.",
                field="duration_seconds",
            )

    event_end: datetime | None = row.get("release_time_ts")
    if isinstance(event_end, datetime) and event_end.tzinfo is None:
        event_end = event_end.replace(tzinfo=UTC)
    if event_end is None and duration is not None:
        event_end = event_start + timedelta(seconds=duration)

    # --- Network ------------------------------------------------------------
    serving = _text(row, "lastmccmnc") or _text(row, "firstmccmnc")
    subscriber_kind = _text(row, "type_of_subscribers")
    # Roaming is our subscriber on someone else's network. A visiting
    # subscriber (type_of_subscribers != home) is not our revenue at all, but
    # the serving-network comparison is what distinguishes the two.
    roaming = bool(
        serving
        and home_mccmnc
        and serving.upper() != home_mccmnc.upper()
        and (subscriber_kind is None or subscriber_kind in HOME_SUBSCRIBER_VALUES)
    )

    return CanonicalUsage(
        usage_id=usage_id,
        source_system=source_system,
        source_file=source_file,
        source_record_number=record_number,
        service_type=spec.service_type,
        call_direction=spec.direction,
        event_start_time=event_start,
        event_date=event_start.date(),
        event_end_time=event_end,
        subscriber_msisdn=msisdn,
        subscriber_imsi=imsi,
        calling_number=calling_number,
        called_number=called_number,
        charged_party=_text(row, "charged_party"),
        duration_seconds=duration,
        cell_id=_text(row, "location_cell_identifier"),
        location_area_code=_text(row, "location_location_area_code"),
        call_reference=_text(row, "call_reference"),
        # Deliberately None: sm_msc01 has no charged-amount column. Defaulting
        # it to zero would make every unbilled call look correctly billed at
        # nothing, which is the single most expensive mistake this platform
        # could make.
        actual_charge=None,
        currency=default_currency,
        roaming_flag=roaming,
        serving_network=serving,
        duplicate_hash=build_duplicate_hash(
            subscriber_msisdn=msisdn,
            called_number=called_number,
            event_start_time=event_start,
            duration_seconds=duration,
            call_reference=_text(row, "call_reference"),
        ),
        source_attributes={
            key: value
            for key, value in (
                ("record_kind", kind),
                ("record_type", _text(row, "record_type")),
                ("call_type", _text(row, "call_type")),
                ("charge_level", _text(row, "charge_level")),
                ("charge_indicator", _text(row, "additional_chg_info_charge_indicator")),
                ("charge_area_code", _text(row, "charge_area_code")),
                ("teleservice", _text(row, "basic_service_teleservice")),
                ("system_type", _text(row, "system_type")),
                ("recording_entity", _text(row, "recording_entity")),
                ("subscriber_category", _text(row, "subscriber_category")),
                ("type_of_subscribers", subscriber_kind),
                ("serving_network", serving),
                ("translated_number", _text(row, "translated_number")),
                ("source_row_id", str(row.get("id")) if row.get("id") is not None else None),
            )
            if value is not None
        },
    )
