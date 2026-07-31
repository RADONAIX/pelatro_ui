"""Spreadsheet row -> canonical rule. Pure logic, no database."""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.imports.constants import ALL_COLUMNS, COLUMN_BY_KEY, suggest_mapping
from app.modules.imports.mapper import RowError, map_row
from app.modules.imports.parser import parse

TODAY = date(2026, 1, 1)

TARIFF_SHEET = (
    "Rule Name,Service,Product,Destination,Time Band,Rate,Unit,Per,Currency,Tax,Valid From\n"
    "On-net peak,VOICE,PREPAID_A,LOCAL_ONNET,PEAK,0.10,SECOND,60,GBP,VAT_STANDARD,2026-01-01\n"
    "Off-net peak,VOICE,PREPAID_A,LOCAL_OFFNET,PEAK,0.15,SECOND,60,GBP,VAT_STANDARD,2026-01-01\n"
)


def _map(row: dict[str, str], mapping: dict[str, str | None] | None = None):
    return map_row(row, mapping or suggest_mapping(list(row)), default_effective_from=TODAY)


# --- Column catalogue -------------------------------------------------------


def test_column_keys_are_unique():
    keys = [c.key for c in ALL_COLUMNS]
    assert len(keys) == len(set(keys)), "duplicate import column key"


def test_condition_columns_are_real_rating_attributes():
    from app.modules.imports.constants import CONDITION_COLUMNS
    from app.modules.rules.constants import ATTRIBUTE_BY_KEY

    for col in CONDITION_COLUMNS:
        assert col.key in ATTRIBUTE_BY_KEY, f"{col.key} is not a rating attribute"


def test_action_groups_reference_real_actions_and_params():
    from app.modules.imports.constants import ACTION_GROUPS
    from app.modules.rules.constants import ACTION_BY_TYPE

    for group in ACTION_GROUPS:
        spec = ACTION_BY_TYPE.get(group.action_type)
        assert spec is not None, f"unknown action {group.action_type}"
        valid = {p.key for p in spec.params}
        for param_key, column_key in group.params:
            assert param_key in valid, f"{group.action_type} has no param '{param_key}'"
            assert column_key in COLUMN_BY_KEY, f"unknown column '{column_key}'"


# --- Heading auto-mapping ---------------------------------------------------


def test_headings_map_through_aliases_case_and_spacing():
    mapping = suggest_mapping(["Rule Name", "SERVICE", "destination", "Valid From", "Rate"])
    assert mapping == {
        "Rule Name": "name",
        "SERVICE": "service_type",
        "destination": "destination_zone",
        "Valid From": "effective_from",
        "Rate": "rate",
    }


def test_unrecognised_headings_are_reported_not_dropped():
    mapping = suggest_mapping(["name", "Vendor Internal Ref"])
    assert mapping["Vendor Internal Ref"] is None


# --- Row mapping ------------------------------------------------------------


def test_a_tariff_row_becomes_a_base_tariff_rule():
    _, rows = parse("tariff.csv", TARIFF_SHEET.encode())
    rule = _map(rows[0])

    assert rule["name"] == "On-net peak"
    assert rule["service_type"] == "VOICE"
    # No rule_type column — inferred from the presence of a rate.
    assert rule["rule_type"] == "BASE_TARIFF"
    assert rule["effective_from"] == "2026-01-01"

    conditions = {c["attribute"]: c for c in rule["conditions"]}
    assert conditions["destination_zone"]["values"] == ["LOCAL_ONNET"]
    assert conditions["time_band"]["values"] == ["PEAK"]
    # The product column is a predicate, not just a label.
    assert conditions["product"]["values"] == ["PREPAID_A"]

    actions = {a["action_type"]: a["params"] for a in rule["actions"]}
    assert actions["SET_RATE"] == {
        "rate": 0.10, "unit": "SECOND", "per_units": 60.0, "currency": "GBP",
    }
    assert actions["APPLY_TAX"] == {"tax_rule": "VAT_STANDARD"}


def test_comma_separated_cell_becomes_set_membership():
    rule = _map({"name": "Multi zone", "service_type": "VOICE",
                 "destination_zone": "LOCAL_ONNET, LOCAL_OFFNET", "rate": "0.1"})
    cond = next(c for c in rule["conditions"] if c["attribute"] == "destination_zone")
    assert cond["operator"] == "IN"
    assert cond["values"] == ["LOCAL_ONNET", "LOCAL_OFFNET"]


def test_single_cell_becomes_equality():
    rule = _map({"name": "One zone", "service_type": "VOICE",
                 "destination_zone": "LOCAL_ONNET", "rate": "0.1"})
    cond = next(c for c in rule["conditions"] if c["attribute"] == "destination_zone")
    assert cond["operator"] == "EQUALS"


def test_blank_cells_do_not_become_conditions():
    rule = _map({"name": "Sparse", "service_type": "VOICE",
                 "destination_zone": "", "time_band": "   ", "rate": "0.1"})
    attributes = {c["attribute"] for c in rule["conditions"]}
    assert "destination_zone" not in attributes
    assert "time_band" not in attributes


def test_boolean_cells_accept_operator_shorthand():
    for text, expected in (("Y", True), ("no", False), ("TRUE", True), ("0", False)):
        rule = _map({"name": "R", "service_type": "VOICE", "roaming": text, "rate": "0.1"})
        cond = next(c for c in rule["conditions"] if c["attribute"] == "roaming")
        assert cond["values"] == [expected], text


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-03-01", "2026-03-01"),
        ("01/03/2026", "2026-03-01"),
        ("01-03-2026", "2026-03-01"),
        ("2026-03-01 00:00:00", "2026-03-01"),
    ],
)
def test_date_formats_operators_actually_send(text, expected):
    rule = _map({"name": "R", "service_type": "VOICE", "rate": "0.1", "effective_from": text})
    assert rule["effective_from"] == expected


def test_numbers_tolerate_separators_and_currency_symbols():
    rule = _map({"name": "R", "service_type": "VOICE", "rate": "£1,234.50"})
    assert rule["actions"][0]["params"]["rate"] == 1234.50


def test_missing_effective_from_falls_back_to_the_batch_default():
    rule = _map({"name": "R", "service_type": "VOICE", "rate": "0.1"})
    assert rule["effective_from"] == TODAY.isoformat()


# --- Rule-type inference ----------------------------------------------------


@pytest.mark.parametrize(
    "cells,expected",
    [
        ({"rate": "0.1"}, "BASE_TARIFF"),
        ({"pulse_initial": "60"}, "PULSE"),
        ({"tax_rule": "VAT_STANDARD"}, "TAX"),
        ({"min_charge": "0.05"}, "MINIMUM_CHARGE"),
        ({"discount_percentage": "10"}, "DISCOUNT"),
        ({"zero_charge": "Y"}, "ZERO_RATE"),
    ],
)
def test_rule_type_is_inferred_from_the_action_columns_present(cells, expected):
    rule = _map({"name": "R", "service_type": "VOICE", **cells})
    assert rule["rule_type"] == expected


def test_an_explicit_rule_type_column_wins_over_inference():
    rule = _map({"name": "R", "service_type": "VOICE", "rate": "0.1", "rule_type": "ZERO_RATE"})
    assert rule["rule_type"] == "ZERO_RATE"


# --- Row-level failures -----------------------------------------------------


def test_a_row_with_no_action_columns_is_rejected():
    with pytest.raises(RowError, match="no actions"):
        _map({"name": "R", "service_type": "VOICE", "destination_zone": "LOCAL_ONNET"})


def test_missing_name_is_rejected_against_the_name_column():
    with pytest.raises(RowError) as exc:
        _map({"name": "", "service_type": "VOICE", "rate": "0.1"})
    assert exc.value.column == "name"


def test_unknown_service_type_is_rejected():
    with pytest.raises(RowError, match="not a valid service type"):
        _map({"name": "R", "service_type": "TELEPATHY", "rate": "0.1"})


def test_a_bad_enum_cell_names_the_allowed_values():
    with pytest.raises(RowError, match="Allowed:"):
        _map({"name": "R", "service_type": "VOICE", "rate": "0.1", "network_type": "6G"})


def test_a_bad_number_is_rejected_against_its_column():
    with pytest.raises(RowError) as exc:
        _map({"name": "R", "service_type": "VOICE", "rate": "free"})
    assert exc.value.column == "rate"


def test_a_bad_date_says_what_format_to_use():
    with pytest.raises(RowError, match="YYYY-MM-DD"):
        _map({"name": "R", "service_type": "VOICE", "rate": "0.1",
              "effective_from": "the first of March"})


# --- Parser -----------------------------------------------------------------


def test_csv_parses_headings_and_rows():
    headings, rows = parse("tariff.csv", TARIFF_SHEET.encode())
    assert headings[0] == "Rule Name"
    assert len(rows) == 2


def test_excel_bom_does_not_corrupt_the_first_heading():
    headings, _ = parse("t.csv", b"\xef\xbb\xbfname,service_type\nA,VOICE\n")
    assert headings[0] == "name"


def test_semicolon_delimited_export_is_sniffed():
    headings, rows = parse("t.csv", b"name;service_type;rate\nA;VOICE;0.1\n")
    assert headings == ["name", "service_type", "rate"]
    assert rows[0]["rate"] == "0.1"


def test_blank_rows_are_skipped():
    _, rows = parse("t.csv", b"name,service_type,rate\nA,VOICE,0.1\n,,\nB,VOICE,0.2\n")
    assert len(rows) == 2


def test_json_array_is_accepted():
    _, rows = parse("t.json", b'[{"name":"A","service_type":"VOICE","rate":"0.1"}]')
    assert rows[0]["name"] == "A"


def test_json_wrapped_in_a_rules_key_is_accepted():
    _, rows = parse("t.json", b'{"rules":[{"name":"A","service_type":"VOICE"}]}')
    assert rows[0]["name"] == "A"


def test_an_empty_file_is_refused():
    from app.core.errors import ValidationFailedError

    with pytest.raises(ValidationFailedError):
        parse("t.csv", b"")


# --- XML uploads ------------------------------------------------------------


class TestXmlParsing:
    """Vendor rule exports arrive as XML as often as CSV."""

    def test_repeated_elements_become_rows(self):
        from app.modules.imports.parser import parse

        xml = b"""<tariffs>
          <rule id="R1"><rule_key>K1</rule_key><rate currency="GBP">0.10</rate></rule>
          <rule id="R2"><rule_key>K2</rule_key><rate currency="GBP">0.05</rate></rule>
        </tariffs>"""
        _headings, rows = parse("rules.xml", xml)
        assert len(rows) == 2
        assert rows[0]["rule_key"] == "K1"
        # Attributes flatten alongside element text.
        assert rows[0]["rate.currency"] == "GBP"
        assert rows[0]["rate"] == "0.10"

    def test_namespaced_vendor_export_still_maps(self):
        from app.modules.imports.parser import parse

        xml = (
            b'<v:rules xmlns:v="urn:vendor">'
            b"<v:rule><v:rule_key>K1</v:rule_key></v:rule>"
            b"</v:rules>"
        )
        _, rows = parse("export.xml", xml)
        assert rows[0]["rule_key"] == "K1"

    def test_xml_without_extension_is_sniffed(self):
        from app.modules.imports.parser import parse

        _, rows = parse("upload", b"<r><rule><k>1</k></rule></r>")
        assert len(rows) == 1

    def test_empty_xml_is_a_clear_error(self):
        import pytest

        from app.core.errors import ValidationFailedError
        from app.modules.imports.parser import parse

        with pytest.raises(ValidationFailedError):
            parse("rules.xml", b"<rules></rules>")
