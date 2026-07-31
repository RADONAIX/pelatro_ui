"""Structured rule XML, read as a tree rather than as a table.

The failure this replaces was not "XML is unsupported" — the file parsed fine.
It was that the generic flattener writes every repeated sibling to the same key,
so a rule with two conditions arrived with one, a rule with two actions arrived
with the last, and the action landed in a column (`Actions.Action.Value`) that
the tabular mapper does not know. Every rule then failed with "the row produced
no actions", which is the *good* outcome: the bad outcome is the file where the
overwriting happened to leave something mappable, and a rule quietly imported
with half its conditions.

So the first test here is not about actions at all. It is that both conditions
survive.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.imports.parser import parse
from app.modules.rules.ingest import files
from app.modules.rules.ingest.adapters import xml_rules

EFFECTIVE = date(2026, 1, 1)


def _xml(body: str) -> bytes:
    return f"<Rules>{body}</Rules>".encode()


RULE = """
<Rule>
  <RuleKey>PREPAID_A_PEAK</RuleKey>
  <Name>Prepaid A peak on-net</Name>
  <ChargingMode>PREPAID</ChargingMode>
  <Service>ALL</Service>
  <EffectiveFrom>2026-01-01</EffectiveFrom>
  <Currency>GBP</Currency>
  <Conditions>
    <Condition><Field>destination_zone</Field><Operator>EQUALS</Operator>
      <Value>LOCAL_ONNET</Value></Condition>
    <Condition><Field>time_band</Field><Operator>EQUALS</Operator>
      <Value>PEAK</Value></Condition>
  </Conditions>
  <Actions>
    <Action><Type>SET_RATE</Type><Value>0.10</Value><Unit>SECOND</Unit>
      <PerUnits>60</PerUnits></Action>
    <Action><Type>APPLY_TAX</Type><TaxRule>VAT_20</TaxRule></Action>
  </Actions>
</Rule>
"""


def _parse(body: str = RULE):
    return files.parse_file(
        "tariff.xml", _xml(body), default_effective_from=EFFECTIVE
    )


# --- The defect this replaces ------------------------------------------------


def test_the_generic_flattener_really_does_lose_siblings():
    """Pinned so nobody 'simplifies' the XML path back onto it.

    This is not a criticism of the flattener — for a tabular XML export it is
    correct. It is a demonstration that the two shapes need different readers.
    """
    _, rows = parse("t.xml", _xml(RULE))
    row = rows[0]
    # Two conditions in, one column out — holding the *second* one.
    assert row["Conditions.Condition.Field"] == "time_band"
    assert "destination_zone" not in row.values()


# --- Reading the tree --------------------------------------------------------


def test_nesting_is_detected_rather_than_configured():
    assert xml_rules.looks_structured(_xml(RULE))


def test_a_flat_xml_export_still_goes_through_the_tabular_reader():
    """A vendor sending one element per rule with no nesting is not broken, and
    must not be rerouted onto a reader built for a shape they do not use."""
    flat = _xml(
        "<Rule><Name>Flat</Name><Service>VOICE</Service><Rate>0.01</Rate>"
        "<RateUnit>MINUTE</RateUnit><Currency>GBP</Currency></Rule>"
    )
    assert not xml_rules.looks_structured(flat)


def test_every_condition_survives():
    """The headline. Two conditions in, two conditions out."""
    parsed = _parse()
    assert not parsed.rejections
    conditions = parsed.drafts[0].conditions
    assert [c.attribute for c in conditions] == ["destination_zone", "time_band"]
    assert [c.values for c in conditions] == [("LOCAL_ONNET",), ("PEAK",)]


def test_every_action_survives_with_its_own_parameters():
    """And no action inherits a parameter from the one before it — the flattener
    gave APPLY_TAX the rate action's Unit."""
    actions = _parse().drafts[0].actions
    assert [a.action_type for a in actions] == ["SET_RATE", "APPLY_TAX"]

    rate = {p.name: p.raw for p in actions[0].parameters}
    assert rate["rate"] == Decimal("0.10")
    assert rate["unit"] == "SECOND"
    assert rate["per_units"] == Decimal("60")

    tax = {p.name: p.raw for p in actions[1].parameters}
    assert tax == {"tax_rule": "VAT_20"}
    assert "unit" not in tax


def test_a_bare_value_becomes_the_actions_principal_parameter():
    """Vendors write `<Value>0.10</Value>` far more often than `<Rate>`."""
    rate = next(
        p for p in _parse().drafts[0].actions[0].parameters if p.name == "rate"
    )
    assert rate.raw == Decimal("0.10")
    assert rate.value_type == "MONEY"


def test_the_header_fields_are_read():
    draft = _parse().drafts[0]
    assert draft.rule_key == "PREPAID_A_PEAK"
    assert draft.rule_name == "Prepaid A peak on-net"
    assert draft.charging_mode == "PREPAID"
    assert draft.validity.currency_code == "GBP"
    assert draft.validity.effective_from == EFFECTIVE


def test_the_rule_key_doubles_as_the_external_reference():
    """With it a nightly re-import updates rather than duplicates, and an XML
    export's own key is the best identity available."""
    assert _parse().drafts[0].provenance.external_ref == "PREPAID_A_PEAK"


def test_service_all_is_read_as_any():
    """A vendor writing "applies to all services" means our ANY. Rejecting it
    makes an operator hand-edit an export to satisfy our spelling."""
    parsed = _parse()
    assert parsed.drafts[0].service_type == "ANY"
    assert any("'ALL' read as 'ANY'" in note for note in parsed.notes)


def test_the_rule_type_is_inferred_from_the_principal_action():
    assert _parse().drafts[0].rule_type_code == "BASE_TARIFF"


def test_the_raw_payload_stays_nested():
    """The forensic record has to be readable. A flattened payload cannot answer
    "what did they actually send us?" for the very structure at issue."""
    raw = _parse().raw[0]
    conditions = raw["Conditions"]["Condition"]
    assert isinstance(conditions, list) and len(conditions) == 2
    assert conditions[0]["Field"] == "destination_zone"


# --- Tolerance and strictness ------------------------------------------------


def test_vendor_element_names_are_tolerated():
    """Every vendor names the containers differently, and the name carries no
    meaning — what matters is what is inside it."""
    body = """
    <TariffRule id="ERIC-1" name="Ericsson peak" mode="PREPAID" service="VOICE"
                validFrom="2026-01-01" ccy="GBP">
      <Criteria>
        <Criterion attribute="destination_zone" op="EQUALS" value="LOCAL_ONNET"/>
      </Criteria>
      <Effects>
        <Effect type="SET_RATE" value="0.02" unit="MINUTE"/>
      </Effects>
    </TariffRule>
    """
    parsed = files.parse_file(
        "eric.xml", _xml(body), default_effective_from=EFFECTIVE
    )
    assert not parsed.rejections
    draft = parsed.drafts[0]
    assert draft.rule_name == "Ericsson peak"
    assert draft.charging_mode == "PREPAID"
    assert [c.attribute for c in draft.conditions] == ["destination_zone"]
    assert draft.actions[0].action_type == "SET_RATE"


def test_namespaces_are_stripped():
    data = (
        b'<t:Rules xmlns:t="urn:vendor:tariff">'
        b"<t:Rule><t:Name>NS rule</t:Name><t:Service>VOICE</t:Service>"
        b"<t:Actions><t:Action><t:Type>SET_RATE</t:Type><t:Value>0.01</t:Value>"
        b"</t:Action></t:Actions></t:Rule></t:Rules>"
    )
    parsed = files.parse_file("ns.xml", data, default_effective_from=EFFECTIVE)
    assert not parsed.rejections
    assert parsed.drafts[0].rule_name == "NS rule"


def test_repeated_value_elements_become_an_in_list():
    body = """
    <Rule><Name>Zones</Name><Service>VOICE</Service>
      <Conditions><Condition><Field>destination_zone</Field><Operator>IN</Operator>
        <Value>LOCAL_ONNET</Value><Value>LOCAL_OFFNET</Value></Condition></Conditions>
      <Actions><Action><Type>SET_RATE</Type><Value>0.01</Value></Action></Actions>
    </Rule>
    """
    condition = files.parse_file(
        "z.xml", _xml(body), default_effective_from=EFFECTIVE
    ).drafts[0].conditions[0]
    assert condition.operator == "IN"
    assert condition.values == ("LOCAL_ONNET", "LOCAL_OFFNET")


def test_a_condition_with_no_attribute_is_rejected_by_element_not_row():
    body = """
    <Rule><Name>Broken</Name><Service>VOICE</Service>
      <Conditions><Condition><Operator>EQUALS</Operator><Value>X</Value></Condition></Conditions>
      <Actions><Action><Type>SET_RATE</Type><Value>0.01</Value></Action></Actions>
    </Rule>
    """
    parsed = files.parse_file("b.xml", _xml(body), default_effective_from=EFFECTIVE)
    assert not parsed.drafts
    assert parsed.rejections[0].column == "Condition"
    assert "no attribute" in parsed.rejections[0].message


def test_a_rule_with_no_actions_says_what_was_expected():
    body = """
    <Rule><Name>Empty</Name><Service>VOICE</Service>
      <Conditions><Condition><Field>destination_zone</Field><Value>LOCAL_ONNET</Value></Condition></Conditions>
    </Rule>
    """
    parsed = files.parse_file("e.xml", _xml(body), default_effective_from=EFFECTIVE)
    assert parsed.rejections[0].column == "Actions"
    assert "<Actions>" in parsed.rejections[0].message


def test_one_bad_rule_does_not_cost_the_good_ones():
    parsed = files.parse_file(
        "mixed.xml",
        _xml(RULE + "<Rule><Name>Broken</Name><Service>VOICE</Service></Rule>"),
        default_effective_from=EFFECTIVE,
    )
    assert len(parsed.drafts) == 1
    assert len(parsed.rejections) == 1


def test_an_unknown_nested_block_is_kept_whole_rather_than_flattened():
    body = """
    <Rule><Name>Extras</Name><Service>VOICE</Service>
      <VendorMeta><Region>EMEA</Region><Tier>2</Tier></VendorMeta>
      <Actions><Action><Type>SET_RATE</Type><Value>0.01</Value></Action></Actions>
    </Rule>
    """
    extras = files.parse_file(
        "x.xml", _xml(body), default_effective_from=EFFECTIVE
    ).drafts[0].provenance.unmapped
    assert extras["VendorMeta"] == {"Region": "EMEA", "Tier": "2"}


# --- The CSV columns that were missing ---------------------------------------


@pytest.mark.parametrize(
    ("header", "row", "expected_type", "expected_actions"),
    [
        (
            "Rule Name,Service,Charging Mode,Rounding Mode,Rounding Decimals,Effective From",
            "Round it,ALL,BOTH,HALF_UP,2,2026-01-01",
            "ROUNDING",
            ["APPLY_ROUNDING"],
        ),
        (
            "Rule Name,Service,Charging Mode,Rental,Currency,Effective From",
            "Monthly rental,ALL,POSTPAID,20.00,GBP,2026-01-01",
            "MONTHLY_RENTAL",
            ["ADD_RECURRING_CHARGE"],
        ),
        (
            "Rule Name,Service,Charging Mode,One Time Amount,Currency,Effective From",
            "Activation fee,ALL,POSTPAID,15.00,GBP,2026-01-01",
            "ONE_TIME_CHARGE",
            ["ADD_ONE_TIME_CHARGE"],
        ),
    ],
)
def test_the_action_columns_the_legacy_registry_lacks(
    header, row, expected_type, expected_actions
):
    """Rounding by mode, monthly rental and one-time charges had no column at
    all, so a sheet carrying them produced "the row produced no actions"."""
    parsed = files.parse_file(
        "t.csv", f"{header}\n{row}\n".encode(), default_effective_from=EFFECTIVE
    )
    assert not parsed.rejections, [r.message for r in parsed.rejections]
    draft = parsed.drafts[0]
    assert draft.rule_type_code == expected_type
    assert [a.action_type for a in draft.actions] == expected_actions


def test_a_modifier_does_not_decide_the_rule_type():
    """A rental row that also states rounding is a rental rule. Before this, the
    earliest-stage heuristic made it a ROUNDING rule."""
    csv = (
        b"Rule Name,Service,Charging Mode,Rounding Mode,Rounding Decimals,Rental,"
        b"Currency,Effective From\n"
        b"Rental,ALL,POSTPAID,HALF_UP,2,20.00,GBP,2026-01-01\n"
    )
    draft = files.parse_file(
        "t.csv", csv, default_effective_from=EFFECTIVE
    ).drafts[0]
    assert draft.rule_type_code == "MONTHLY_RENTAL"


def test_service_all_is_read_as_any_on_the_csv_path_too():
    csv = (
        b"Rule Name,Service,Charging Mode,Rate,Rate Unit,Currency,Effective From\n"
        b"Any service,ALL,BOTH,0.01,MINUTE,GBP,2026-01-01\n"
    )
    assert files.parse_file(
        "t.csv", csv, default_effective_from=EFFECTIVE
    ).drafts[0].service_type == "ANY"
