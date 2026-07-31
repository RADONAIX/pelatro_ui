"""What each MSC record kind means, and which of them are rateable.

An MSC dumps every event it handled, not just the chargeable ones. Rating all of
them would invent revenue that was never billable; dropping the unrateable ones
without recording *why* would make the source and the platform disagree on
volume with no way to reconcile. So every kind is classified explicitly, and the
non-rateable ones are counted as skipped rather than silently discarded.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CallDirection(StrEnum):
    MO = "MO"
    MT = "MT"
    #: A call forwarded on the served subscriber's behalf — charged to them, but
    #: neither originated nor terminated by them.
    FORWARDED = "FORWARDED"


class SkipReason(StrEnum):
    #: Supplementary-service actions (USSD, balance enquiry, call-barring
    #: changes). Real events, no rateable usage.
    NOT_RATEABLE = "NOT_RATEABLE"
    #: Traffic passing through the switch between two other networks. No served
    #: subscriber, so nothing of ours to assure.
    NO_SERVED_SUBSCRIBER = "NO_SERVED_SUBSCRIBER"
    UNKNOWN_RECORD_KIND = "UNKNOWN_RECORD_KIND"


@dataclass(frozen=True)
class RecordKind:
    """How one MSC record kind maps onto the canonical usage model."""

    kind: str
    service_type: str
    direction: str
    #: Source columns holding the event start, most authoritative first. For a
    #: call this is answer time, not seizure: charging begins when the callee
    #: answers, and a 40-second ring on an unanswered call is not billable.
    time_columns: tuple[str, ...]
    #: The column naming the *other* party. For a mobile-terminated record the
    #: served subscriber is the called party, so the counterparty is the caller.
    other_party_column: str | None
    has_duration: bool
    rateable: bool = True
    skip_reason: str | None = None


RECORD_KINDS: dict[str, RecordKind] = {
    "moCallRecord": RecordKind(
        kind="moCallRecord",
        service_type="VOICE",
        direction=CallDirection.MO,
        time_columns=("answer_time_ts", "seizure_time_ts", "setup_time_ts"),
        other_party_column="called_number",
        has_duration=True,
    ),
    "mtCallRecord": RecordKind(
        kind="mtCallRecord",
        service_type="VOICE",
        direction=CallDirection.MT,
        time_columns=("answer_time_ts", "seizure_time_ts", "setup_time_ts"),
        other_party_column="calling_number",
        has_duration=True,
    ),
    "forwardCallRecord": RecordKind(
        kind="forwardCallRecord",
        service_type="VOICE",
        direction=CallDirection.FORWARDED,
        time_columns=("answer_time_ts", "seizure_time_ts"),
        other_party_column="called_number",
        has_duration=True,
    ),
    "moSMSRecord": RecordKind(
        kind="moSMSRecord",
        service_type="SMS",
        direction=CallDirection.MO,
        time_columns=("origination_time_ts",),
        # An SMS names its recipient in destination_number; called_number is
        # empty on every one of these records.
        other_party_column="destination_number",
        has_duration=False,
    ),
    "mtSMSRecord": RecordKind(
        kind="mtSMSRecord",
        service_type="SMS",
        direction=CallDirection.MT,
        time_columns=("delivery_time_ts",),
        # A terminating SMS records no originator on this switch, so there is no
        # counterparty to enrich against. Destination-based rules cannot apply,
        # which is correct: an incoming SMS is normally free.
        other_party_column=None,
        has_duration=False,
    ),
    "ssActionRecord": RecordKind(
        kind="ssActionRecord",
        service_type="SS",
        direction=CallDirection.MO,
        time_columns=("ss_action_time_ts",),
        other_party_column=None,
        has_duration=False,
        rateable=False,
        skip_reason=SkipReason.NOT_RATEABLE,
    ),
    "transitRecord": RecordKind(
        kind="transitRecord",
        service_type="VOICE",
        direction=CallDirection.MO,
        time_columns=("answer_time_ts",),
        other_party_column="called_number",
        has_duration=True,
        rateable=False,
        skip_reason=SkipReason.NO_SERVED_SUBSCRIBER,
    ),
}

#: Source columns the canonical transform reads. Listed explicitly so the SELECT
#: names its columns instead of using ``*`` — the source table has 137 of them,
#: and pulling all of them for 10 lakh rows is wasted I/O on every batch.
SOURCE_COLUMNS: tuple[str, ...] = (
    "id",
    "source_file",
    "record_kind",
    "record_number",
    "record_type",
    "served_imsi",
    "served_msisdn",
    "calling_number",
    "called_number",
    "destination_number",
    "translated_number",
    "call_reference",
    "call_duration",
    "call_type",
    "charged_party",
    "charge_level",
    "charge_area_code",
    "additional_chg_info_charge_indicator",
    "type_of_subscribers",
    "subscriber_category",
    "location_location_area_code",
    "location_cell_identifier",
    "recording_entity",
    "system_type",
    "basic_service_teleservice",
    "firstmccmnc",
    "lastmccmnc",
    "answer_time_ts",
    "seizure_time_ts",
    "release_time_ts",
    "setup_time_ts",
    "delivery_time_ts",
    "origination_time_ts",
    "ss_action_time_ts",
)

#: The switch's own home network code (MCC+MNC, as it writes it). A CDR whose
#: serving network differs is a roaming event.
HOME_MCCMNC_DEFAULT = "54F660"

#: ``type_of_subscribers`` values that mean the served party is one of ours.
HOME_SUBSCRIBER_VALUES = frozenset({"home", "HOME"})
