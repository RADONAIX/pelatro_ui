"""The typed-value codec.

Most of these tests are about one thing: money is `Decimal` from the vendor's cell
to the numeric column, and anything that would quietly lose precision raises
instead. The legacy file importer did ``float(cleaned)``, so these are the
regression tests for a defect that already shipped.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.modules.rules.canonical import units
from app.modules.rules.canonical.valuetypes import (
    SCALE,
    TypedValue,
    ValueError_,
    decode,
    encode,
    encode_list,
    encode_range,
    format_decimal,
    parse_bool,
    parse_date,
    parse_datetime,
    parse_decimal,
    quantize,
)
from app.modules.rules.vocabulary.values import ValueType

# --- Money and numbers ------------------------------------------------------


def test_decimal_parsing_never_routes_through_float():
    """``Decimal(0.1)`` is 0.1000000000000000055511151231257827. Six decimal places
    times a few hundred million CDRs is a gap nobody can explain."""
    assert parse_decimal("0.1") == Decimal("0.1")
    assert parse_decimal(0.1) == Decimal("0.1")
    assert str(parse_decimal(0.1)) == "0.1"


def test_money_requires_a_currency():
    with pytest.raises(ValueError_) as exc:
        encode("0.05", ValueType.MONEY, field="rate")
    assert "currency" in str(exc.value).lower()


def test_money_with_a_currency_is_stored_as_decimal():
    value = encode("0.012345", ValueType.MONEY, currency="gbp", field="rate")
    assert value.numeric == Decimal("0.012345")
    assert isinstance(value.numeric, Decimal)
    assert value.currency_code == "GBP"
    assert value.text == "0.012345"


def test_currency_must_be_three_letters():
    with pytest.raises(ValueError_):
        encode("1.00", ValueType.MONEY, currency="POUNDS", field="rate")


def test_precision_beyond_six_decimals_is_rejected_not_truncated():
    """Silently rounding a vendor's rate makes the stored value disagree with their
    document, and the disagreement is invisible until a reconciliation run."""
    with pytest.raises(ValueError_) as exc:
        encode("0.0000005", ValueType.MONEY, currency="GBP", field="rate")
    assert "six decimal" in str(exc.value)


def test_exactly_six_decimals_is_accepted():
    value = encode("0.000001", ValueType.MONEY, currency="GBP")
    assert value.numeric == SCALE


def test_oversized_amounts_are_rejected():
    with pytest.raises(ValueError_):
        quantize(Decimal("999999999999999999"))


def test_currency_symbols_and_whitespace_are_tolerated():
    assert parse_decimal("£1234.56") == Decimal("1234.56")
    assert parse_decimal(" 1234.56 ") == Decimal("1234.56")


def test_us_convention_separators_are_understood():
    assert parse_decimal("£1,234.56") == Decimal("1234.56")
    assert parse_decimal("1,234,567.89") == Decimal("1234567.89")


def test_european_convention_separators_are_understood():
    """A comma decimal point read as a thousands separator is a 100x error, and it
    would sail through a validator that only checks the result is a number."""
    assert parse_decimal("1.234,56") == Decimal("1234.56")
    assert parse_decimal("1,50") == Decimal("1.50")
    assert parse_decimal("0,012345") == Decimal("0.012345")


def test_genuinely_ambiguous_separators_are_rejected_not_guessed():
    """``1,234`` is 1234 under one convention and 1.234 under the other. There is no
    correct guess, so the row is rejected and says why."""
    with pytest.raises(ValueError_) as exc:
        parse_decimal("1,234")
    assert "ambiguous" in str(exc.value)
    assert "1000x" in str(exc.value)


def test_percentage_suffix_is_stripped():
    assert parse_decimal("15%") == Decimal("15")


def test_booleans_are_not_silently_numbers():
    """A boolean arriving where a number was expected means the mapping is wrong;
    accepting it as 1 hides that."""
    with pytest.raises(ValueError_):
        parse_decimal(True)


def test_non_finite_values_are_rejected():
    with pytest.raises(ValueError_):
        parse_decimal("NaN")
    with pytest.raises(ValueError_):
        parse_decimal("Infinity")


def test_money_scale_divides_minor_unit_sources():
    """Oracle BRM ships amounts in hundred-thousandths. Scaling happens once, in
    the codec, rather than in each adapter with its own rounding."""
    value = encode("1200", ValueType.MONEY, currency="GBP", money_scale=100000)
    assert value.numeric == Decimal("0.012")


def test_format_decimal_never_uses_exponent_notation():
    """``Decimal.normalize()`` renders 60 as 6E+1 — correct, and useless in a
    column a human reads to check a charge."""
    assert format_decimal(Decimal("60")) == "60"
    assert format_decimal(Decimal("60.000000")) == "60"
    assert format_decimal(Decimal("0.000001")) == "0.000001"
    assert format_decimal(Decimal("0")) == "0"
    assert "E" not in format_decimal(Decimal("1000000000"))


# --- Other primitives -------------------------------------------------------


def test_boolean_vocabulary():
    for token in ("yes", "Y", "TRUE", "1", "on", "enabled"):
        assert parse_bool(token) is True
    for token in ("no", "N", "FALSE", "0", "off", "disabled"):
        assert parse_bool(token) is False
    with pytest.raises(ValueError_):
        parse_bool("maybe")


def test_date_formats_cover_what_vendors_actually_send():
    for token in ("2026-03-01", "01/03/2026", "01-03-2026", "01.03.2026", "20260301"):
        assert parse_date(token) == date(2026, 3, 1)


def test_excel_datetime_in_a_date_cell_is_accepted():
    assert parse_date("2026-03-01 00:00:00") == date(2026, 3, 1)


def test_unparseable_date_names_the_expected_format():
    with pytest.raises(ValueError_) as exc:
        parse_date("the first of March")
    assert "YYYY-MM-DD" in str(exc.value)


def test_naive_timestamps_use_the_source_timezone_not_the_server():
    """A vendor in Europe/London exporting 00:30 is not the same instant as 00:30
    wherever this process runs. Assuming otherwise misplaces an hour of usage twice
    a year, in both directions."""
    london = parse_datetime("2026-07-01T00:30:00", timezone="Europe/London")
    assert london == datetime(2026, 6, 30, 23, 30, tzinfo=UTC)
    naive_utc = parse_datetime("2026-07-01T00:30:00")
    assert naive_utc == datetime(2026, 7, 1, 0, 30, tzinfo=UTC)


def test_unknown_timezone_is_rejected():
    with pytest.raises(ValueError_):
        parse_datetime("2026-07-01T00:30:00", timezone="Middle/Earth")


def test_enum_values_are_checked_against_the_allowed_set():
    assert encode("peak", ValueType.ENUM, allowed=("PEAK", "OFF_PEAK")).text == "PEAK"
    with pytest.raises(ValueError_) as exc:
        encode("SHOULDER", ValueType.ENUM, allowed=("PEAK", "OFF_PEAK"))
    assert "PEAK" in str(exc.value)


def test_reference_codes_are_upper_cased():
    assert encode("local_onnet", ValueType.REFERENCE).text == "LOCAL_ONNET"


def test_empty_values_are_rejected_with_a_useful_message():
    for value_type in (ValueType.STRING, ValueType.REFERENCE, ValueType.ENUM):
        with pytest.raises(ValueError_):
            encode("   ", value_type, allowed=("A",))


# --- Lists and ranges -------------------------------------------------------


def test_list_encoding_types_every_element():
    value = encode_list(["1.5", "2.5"], ValueType.NUMBER, field="values")
    assert value.value_type == ValueType.LIST
    assert value.elements == ("1.5", "2.5")


def test_list_deduplicates_without_failing():
    """A vendor listing a zone twice is untidy, not wrong — but the stored set is
    deduplicated so the specificity score is not inflated by the repetition."""
    value = encode_list(["LOCAL", "LOCAL", "INTL"], ValueType.REFERENCE)
    assert value.elements == ("LOCAL", "INTL")


def test_empty_list_is_rejected():
    with pytest.raises(ValueError_):
        encode_list([], ValueType.STRING)


def test_range_bounds_must_be_ordered():
    ok = encode_range("1", "10", ValueType.NUMBER)
    assert ok.elements == ("1", "10")
    with pytest.raises(ValueError_) as exc:
        encode_range("10", "1", ValueType.NUMBER)
    assert "upper bound" in str(exc.value)


def test_list_elements_keep_decimal_precision_as_strings():
    """JSONB has no exact decimal type, so elements are stored as strings. Storing
    them as JSON numbers would route through float and undo the codec."""
    value = encode_list(["0.000001", "0.000002"], ValueType.NUMBER)
    assert all(isinstance(e, str) for e in value.elements)
    assert value.elements == ("0.000001", "0.000002")


# --- Decode / round-trip ----------------------------------------------------


def test_decode_returns_decimal_for_money():
    encoded = encode("1.25", ValueType.MONEY, currency="GBP")
    assert decode(encoded) == Decimal("1.25")
    assert isinstance(decode(encoded), Decimal)


def test_decode_round_trips_every_scalar_type():
    cases = (
        (ValueType.STRING, "abc", "abc"),
        (ValueType.NUMBER, "42", Decimal("42")),
        (ValueType.BOOLEAN, "yes", True),
        (ValueType.ENUM, "peak", "PEAK"),
        (ValueType.REFERENCE, "local", "LOCAL"),
        (ValueType.DATE, "2026-03-01", date(2026, 3, 1)),
    )
    for value_type, raw, expected in cases:
        encoded = encode(raw, value_type, allowed=("PEAK",) if value_type == "ENUM" else ())
        assert decode(encoded) == expected, value_type


def test_typed_value_with_ref_is_immutable():
    original = encode("LOCAL", ValueType.REFERENCE)
    updated = original.with_ref("abc-123")
    assert original.resolved_ref_id is None
    assert updated.resolved_ref_id == "abc-123"
    assert isinstance(updated, TypedValue)


# --- Units ------------------------------------------------------------------


def test_unit_registry_agrees_with_the_live_engine():
    """A 1024-vs-1000 disagreement between the codec and the engine is a 2.4%
    revenue error at gigabyte scale, always in the customer's favour."""
    from app.modules.rating.engine import _UNIT_SCALE

    for code, factor in _UNIT_SCALE.items():
        spec = units.UNIT_BY_CODE.get(code)
        assert spec is not None, f"engine knows unit {code}, the registry does not"
        assert spec.factor == factor, f"{code}: registry {spec.factor} vs engine {factor}"


def test_unit_aliases_resolve():
    assert units.resolve_unit("MB") == ("MEGABYTE", "MB")
    assert units.resolve_unit("MEGABYTE") == ("MEGABYTE", None)
    assert units.resolve_unit(None) == (None, None)


def test_unknown_unit_is_rejected_rather_than_guessed():
    """Guessing a unit risks a 60x or 1024x error, so the row is rejected."""
    resolved, raw = units.resolve_unit("FURLONG")
    assert resolved is None and raw == "FURLONG"
    with pytest.raises(ValueError_) as exc:
        encode("1", ValueType.NUMBER, unit="FURLONG")
    assert "60x or 1024x" in str(exc.value)


def test_dimension_mismatch_is_detectable():
    assert units.same_dimension("SECOND", "MINUTE") is True
    assert units.same_dimension("SECOND", "MEGABYTE") is False
    assert units.same_dimension("SECOND", None) is False


def test_conversion_is_exact_and_refuses_cross_dimension():
    assert units.convert(Decimal(1), "MINUTE", "SECOND") == Decimal(60)
    assert units.convert(Decimal(1), "MEGABYTE", "KILOBYTE") == Decimal(1024)
    with pytest.raises(ValueError):
        units.convert(Decimal(1), "SECOND", "MEGABYTE")
    with pytest.raises(KeyError):
        units.convert(Decimal(1), "SECOND", "FURLONG")


def test_every_dimension_has_a_base_unit_with_factor_one():
    for dimension, code in units.BASE_UNIT.items():
        spec = units.UNIT_BY_CODE[code]
        assert spec.dimension == dimension
        assert spec.factor == Decimal(1)
