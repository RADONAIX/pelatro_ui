"""MSC wire-format decoding.

Every hex string here is a real value taken from ``msc_schema.sm_msc01``, and
the expected results were cross-checked against the loader's own parsed columns.
Synthetic examples would only prove the decoder is self-consistent.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.msc import decode


class TestTbcd:
    def test_it_swaps_each_nibble_pair(self):
        # 0x91 carries the digits 1 then 9 — low nibble first. Reading it as
        # "91" is the mistake that makes every number in the estate wrong.
        assert decode.decode_tbcd("91") == "19"

    def test_the_filler_nibble_terminates_the_number(self):
        # 0xF is padding for an odd digit count, not a digit.
        assert decode.decode_tbcd("5895433583F0") == "85593453380"

    def test_it_decodes_an_even_length_number_with_no_filler(self):
        assert decode.decode_tbcd("589556029009") == "855965200990"

    def test_an_odd_hex_length_is_rejected(self):
        with pytest.raises(decode.DecodeError, match="whole number of octets"):
            decode.decode_tbcd("915")

    def test_non_hexadecimal_is_rejected(self):
        with pytest.raises(decode.DecodeError, match="hexadecimal"):
            decode.decode_tbcd("ZZ")

    def test_an_empty_field_is_absent_not_empty(self):
        assert decode.decode_tbcd("") is None
        assert decode.decode_tbcd(None) is None


class TestAddress:
    @pytest.mark.parametrize(
        ("raw", "digits", "ton"),
        [
            ("915895433583F0", "85593453380", decode.TON_INTERNATIONAL),
            ("A170305053F2", "070305352", decode.TON_NATIONAL),
            ("819036484498", "0963844489", decode.TON_UNKNOWN),
            ("C15895433583F0", "85593453380", decode.TON_SUBSCRIBER),
        ],
    )
    def test_it_strips_the_ton_npi_octet(self, raw, digits, ton):
        assert decode.decode_address(raw) == (digits, ton)


class TestNormalisation:
    def test_national_and_international_forms_of_one_number_agree(self):
        # The same subscriber, written both ways on the same record. If these
        # normalised differently, prefix matching would put one call in two
        # destination zones.
        international = decode.normalise_msisdn("91589556029009", country_code="855")
        national = decode.normalise_msisdn("A19056029009", country_code="855")
        assert international == national == "855965200990"

    def test_it_strips_the_trunk_prefix_and_adds_the_country_code(self):
        assert decode.normalise_msisdn("819036484498", country_code="855") == "855963844489"

    def test_an_international_destination_keeps_its_own_country_code(self):
        # A Chinese number reached from Cambodia must not be prefixed with 855.
        assert decode.normalise_msisdn("91683195990950F9", country_code="855") == "8613599990059"

    def test_an_absent_number_stays_absent(self):
        assert decode.normalise_msisdn("", country_code="855") is None


class TestImsi:
    def test_an_imsi_has_no_ton_npi_octet(self):
        # 456 = Cambodia, 06 = the serving network. Stripping a leading octet
        # here — as an address decoder would — corrupts the MCC.
        assert decode.decode_imsi("54061625669344F4") == "456061526639444"


class TestTimestamp:
    def test_it_converts_local_time_to_utc(self):
        # 2018-10-25 01:01:59 +07:00. Storing the local digits as UTC would
        # shift the call seven hours and into the wrong time band.
        assert decode.decode_timestamp("1810250101592B0700") == datetime(
            2018, 10, 24, 18, 1, 59, tzinfo=UTC
        )

    def test_it_matches_the_loaders_own_parsed_column(self):
        # Cross-check against seizure_time_ts for source row id 10461.
        assert decode.decode_timestamp("1810241829392B0700") == datetime(
            2018, 10, 24, 11, 29, 39, tzinfo=UTC
        )

    def test_the_sign_may_be_a_literal_character(self):
        # Two loaders feed this table and they disagree on the sign encoding.
        assert decode.decode_timestamp("181025010159+0700") == decode.decode_timestamp(
            "1810250101592B0700"
        )

    def test_a_negative_offset_moves_the_other_way(self):
        assert decode.decode_timestamp("1810250101592D0500") == datetime(
            2018, 10, 25, 6, 1, 59, tzinfo=UTC
        )

    def test_a_timestamp_with_no_offset_is_read_as_utc(self):
        assert decode.decode_timestamp("181025010159") == datetime(
            2018, 10, 25, 1, 1, 59, tzinfo=UTC
        )

    def test_an_invalid_date_is_rejected(self):
        with pytest.raises(decode.DecodeError):
            decode.decode_timestamp("181325010159")


class TestDuration:
    def test_it_reads_whole_seconds(self):
        assert decode.decode_duration("195") == 195

    def test_absent_is_none_not_zero(self):
        # None means "the switch did not say"; zero means "a zero-length call".
        # Collapsing them would rate unanswered calls as free instead of
        # flagging them as unrateable.
        assert decode.decode_duration("") is None

    def test_a_negative_duration_is_rejected(self):
        with pytest.raises(decode.DecodeError, match="negative"):
            decode.decode_duration("-5")
