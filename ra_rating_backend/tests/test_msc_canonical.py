"""MSC row → canonical usage (§6, §7)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.msc import canonical
from app.modules.msc.constants import SkipReason

BASE_ROW = {
    "id": 10461,
    "source_file": "BTKK20181025012901937755.dat.xml",
    "record_kind": "moCallRecord",
    "record_number": "1793821403",
    "served_msisdn": "915885519585F0",
    "served_imsi": "54061625669344F4",
    "calling_number": "A180519585F0",
    "called_number": "819077048088",
    "call_duration": "195",
    "call_reference": "03EA00EAB6B2",
    "answer_time_ts": datetime(2018, 10, 24, 18, 1, 59, tzinfo=UTC),
    "charged_party": "callingParty",
    "location_cell_identifier": "E7E2",
    "location_location_area_code": "0BC7",
    "lastmccmnc": "54F660",
    "type_of_subscribers": "home",
}


def transform(**overrides):
    return canonical.transform(
        {**BASE_ROW, **overrides}, source_system="MSC01", country_code="855"
    )


class TestIdentity:
    def test_the_usage_id_is_derived_from_file_and_record_number(self):
        usage = transform()
        assert usage.usage_id == "MSC01-BTKK20181025012901937755-1793821403"

    def test_the_same_source_row_always_produces_the_same_id(self):
        # §6. If this ever stops holding, every idempotency guarantee downstream
        # silently stops working while still appearing to succeed.
        assert transform().usage_id == transform().usage_id

    def test_a_different_record_number_is_a_different_usage(self):
        assert transform().usage_id != transform(record_number="9999").usage_id

    def test_a_long_file_name_stays_within_the_column_and_stays_unique(self):
        long_a = transform(source_file="X" * 90 + "A.dat.xml")
        long_b = transform(source_file="X" * 90 + "B.dat.xml")
        assert len(long_a.usage_id) <= 128
        assert long_a.usage_id != long_b.usage_id

    def test_a_row_with_no_record_number_is_rejected_not_guessed(self):
        with pytest.raises(canonical.TransformError, match="stable usage identity"):
            canonical.transform(
                {**BASE_ROW, "record_number": None, "id": None},
                source_system="MSC01",
                country_code="855",
            )


class TestDuplicateHash:
    def test_the_same_event_in_a_renamed_file_has_the_same_business_hash(self):
        # §7. A re-export under a new file name is a new usage_id and the same
        # event — which is exactly what a file-level check misses.
        original = transform()
        reexported = transform(source_file="RESENT20181025.dat.xml")
        assert original.usage_id != reexported.usage_id
        assert original.duplicate_hash == reexported.duplicate_hash

    def test_a_different_duration_is_a_different_event(self):
        assert transform().duplicate_hash != transform(call_duration="196").duplicate_hash


class TestDirection:
    def test_a_mobile_originated_call_puts_our_subscriber_first(self):
        usage = transform()
        assert usage.call_direction == "MO"
        assert usage.calling_number == usage.subscriber_msisdn
        assert usage.called_number != usage.subscriber_msisdn

    def test_a_mobile_terminated_call_puts_our_subscriber_second(self):
        # On an incoming call the served subscriber is the *called* party. Get
        # this backwards and every incoming call is priced against the caller's
        # destination — a systematic, invisible mispricing.
        usage = transform(record_kind="mtCallRecord", calling_number="A170305053F2")
        assert usage.call_direction == "MT"
        assert usage.called_number == usage.subscriber_msisdn
        assert usage.calling_number == "855570305352" or usage.calling_number.startswith("855")

    def test_an_sms_reads_its_recipient_from_destination_number(self):
        usage = transform(
            record_kind="moSMSRecord",
            called_number="",
            call_duration=None,
            destination_number="819036484498",
            origination_time_ts=datetime(2018, 10, 24, 18, 0, tzinfo=UTC),
            answer_time_ts=None,
        )
        assert usage.service_type == "SMS"
        assert usage.called_number == "855963844489"
        assert usage.duration_seconds is None


class TestSkipping:
    def test_supplementary_service_actions_are_skipped_not_rated(self):
        result = canonical.transform(
            {**BASE_ROW, "record_kind": "ssActionRecord"},
            source_system="MSC01",
            country_code="855",
        )
        assert isinstance(result, canonical.SkippedRecord)
        assert result.reason == SkipReason.NOT_RATEABLE

    def test_transit_traffic_has_no_subscriber_of_ours(self):
        result = canonical.transform(
            {**BASE_ROW, "record_kind": "transitRecord"},
            source_system="MSC01",
            country_code="855",
        )
        assert isinstance(result, canonical.SkippedRecord)
        assert result.reason == SkipReason.NO_SERVED_SUBSCRIBER

    def test_an_unknown_record_kind_is_reported_not_dropped(self):
        result = canonical.transform(
            {**BASE_ROW, "record_kind": "somethingNew"},
            source_system="MSC01",
            country_code="855",
        )
        assert isinstance(result, canonical.SkippedRecord)
        assert result.reason == SkipReason.UNKNOWN_RECORD_KIND


class TestCharge:
    def test_the_actual_charge_is_absent_never_zero(self):
        # The MSC does not price calls. Defaulting to zero would classify every
        # unbilled call as correctly billed at nothing — the single most
        # expensive mistake this platform could make.
        assert transform().actual_charge is None


class TestValidation:
    def test_a_voice_record_with_no_duration_is_rejected(self):
        with pytest.raises(canonical.TransformError, match="duration"):
            transform(call_duration=None)

    def test_a_record_with_no_usable_time_is_rejected(self):
        with pytest.raises(canonical.TransformError, match="event time"):
            transform(answer_time_ts=None, seizure_time_ts=None, setup_time_ts=None)

    def test_a_record_with_no_served_subscriber_is_rejected(self):
        with pytest.raises(canonical.TransformError, match="served subscriber"):
            transform(served_msisdn="", served_imsi="")


class TestRoaming:
    def test_the_home_network_is_not_roaming(self):
        assert transform(lastmccmnc="54F660").roaming_flag is False

    def test_a_foreign_serving_network_is_roaming(self):
        assert transform(lastmccmnc="52003").roaming_flag is True
