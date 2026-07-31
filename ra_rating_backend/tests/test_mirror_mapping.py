"""Mapping tests — pure, no database.

Every skip decision the mirror makes is a judgement about when the target schema
cannot hold what the platform means. Those are exactly the cases worth pinning
down, because the failure they prevent is silent: a mirrored rule that looks
plausible and prices differently from the real one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import pytest

from app.modules.mirror import mapping
from app.modules.mirror import valuemaps as vm
from app.modules.mirror.mapping import Unmappable

# --- Doubles -----------------------------------------------------------------


@dataclass
class Group:
    condition_group_id: str
    parent_group_id: str | None = None
    group_logic: str = "AND"
    negated_flag: bool = False
    sequence_number: int = 0


@dataclass
class Cond:
    rule_condition_id: str
    condition_group_id: str
    attribute_name: str = "destination_zone"
    operator_code: str = "EQUALS"
    comparison_value: str = "LOCAL"
    comparison_value_type: str = "STRING"
    comparison_values: list = field(default_factory=list)
    sequence_number: int = 0
    negated_flag: bool = False
    created_at: datetime | None = None


@dataclass
class Action:
    rule_action_id: str
    action_type: str = "SET_RATE"
    target_attribute: str | None = "charge"
    action_value: str | None = "0.15"
    action_value_type: str | None = "MONEY"
    currency_code: str | None = None
    unit_code: str | None = None
    execution_sequence: int = 0
    created_at: datetime | None = None


@dataclass
class Param:
    rule_parameter_id: str
    rule_action_id: str | None
    parameter_name: str = "rate"
    parameter_value: str = "0.15"
    parameter_value_type: str = "MONEY"
    sequence_number: int = 0
    created_at: datetime | None = None


@dataclass
class Rule:
    rule_id: str = "r1"
    rule_key: str = "VOICE|PEAK"
    rule_name: str = "Peak on-net voice"
    description: str = "desc"
    charging_mode: str = "PREPAID"
    created_by: str | None = "user-1"


@dataclass
class Version:
    rule_version_id: str = "v1"
    version_number: int = 1
    priority: int = 100
    stop_processing: bool = False
    status: str = "ACTIVE"
    effective_from: date = date(2026, 1, 1)
    effective_to: date | None = None
    currency_code: str | None = "GHS"
    condition_logic: str = "AND"
    stacking_policy_id: str | None = "sp1"
    created_by: str | None = "user-1"
    created_at: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    approved_by: str | None = None
    approved_at: datetime | None = None


def _rule_row(**over: Any) -> dict[str, Any]:
    version = Version(**over.pop("version", {}))
    return mapping.map_rule(
        Rule(**over.pop("rule", {})),
        version,
        stage_code=over.pop("stage_code", "BASE_CHARGE"),
        rule_type_code=over.pop("rule_type_code", "BASE_TARIFF"),
        allows_multiple=over.pop("allows_multiple", False),
    )


# --- Rule header -------------------------------------------------------------


def test_the_target_row_is_keyed_on_the_version_not_the_logical_rule():
    # The target has version_no and an effective window; one row per version is
    # what makes a historical rating result re-explainable against it.
    assert _rule_row()["rule_id"] == "v1"


def test_an_inclusive_end_date_becomes_an_exclusive_timestamp():
    # `chk_rating_rule_dates` demands effective_to > effective_from, and the
    # platform's end date is inclusive. A same-day window is the sharp case.
    row = _rule_row(version={"effective_from": date(2026, 3, 1),
                             "effective_to": date(2026, 3, 1)})
    assert row["effective_from"] == datetime(2026, 3, 1, tzinfo=UTC)
    assert row["effective_to"] == datetime(2026, 3, 2, tzinfo=UTC)


def test_a_stage_outside_the_targets_seven_is_refused():
    with pytest.raises(Unmappable, match="stage"):
        _rule_row(stage_code="SESSION_CONTROL")


def test_published_mirrors_as_active():
    # The platform's own exclusion constraint treats ACTIVE and PUBLISHED as the
    # same live window, so the mirror must not disagree with it.
    assert _rule_row(version={"status": "PUBLISHED"})["status"] == "ACTIVE"


def test_superseded_mirrors_as_inactive():
    assert _rule_row(version={"status": "SUPERSEDED"})["status"] == "INACTIVE"


def test_stop_processing_wins_over_the_stacking_policy():
    row = _rule_row(version={"stop_processing": True}, allows_multiple=True)
    assert row["match_strategy"] == "FIRST_MATCH"


def test_a_stackable_rule_is_all_matches():
    assert _rule_row(allows_multiple=True)["match_strategy"] == "ALL_MATCHES"


def test_match_strategy_is_only_ever_first_match_or_all_matches():
    """The target knows exactly two strategies — nothing else may be written.

    Exhaustive over both mappers' whole input space: every stacking policy,
    stop_processing and multiplicity combination must land on one of the two.
    """
    allowed = {"FIRST_MATCH", "ALL_MATCHES"}
    # Legacy: every policy the platform has, plus an unknown one for the default.
    for policy in ("EXCLUSIVE", "STACKABLE", "OVERRIDE", "SOMETHING_ELSE"):
        row = mapping.map_legacy_rule(LegacyRule(stacking_policy=policy))
        assert row["match_strategy"] in allowed, policy
    from app.modules.mirror import targetvocab
    assert set(targetvocab.LEGACY_STRATEGY_MAP.values()) == allowed
    # Canonical: all four flag combinations.
    for stop in (False, True):
        for multiple in (False, True):
            row = _rule_row(version={"stop_processing": stop}, allows_multiple=multiple)
            assert row["match_strategy"] in allowed, (stop, multiple)


def test_source_system_code_is_never_written():
    # It FKs to a table outside the ten this feature may populate.
    assert _rule_row()["source_system_code"] is None


def test_a_malformed_currency_is_dropped_rather_than_truncated():
    assert _rule_row(version={"currency_code": "GHSX"})["currency_code"] is None


# --- Condition-group flattening ----------------------------------------------


def _flatten(logic: str, groups: list[Group], conds: list[Cond]) -> dict[str, int]:
    return mapping.flatten_condition_groups(
        condition_logic=logic, groups=groups, conditions=conds
    )


def test_a_single_and_group_collapses_to_group_one():
    groups = [Group("g1")]
    conds = [Cond("c1", "g1", sequence_number=0), Cond("c2", "g1", sequence_number=1)]
    assert _flatten("AND", groups, conds) == {"c1": 1, "c2": 1}


def test_a_single_or_group_gives_each_predicate_its_own_group():
    groups = [Group("g1", group_logic="OR")]
    conds = [Cond("c1", "g1", sequence_number=0), Cond("c2", "g1", sequence_number=1)]
    assert _flatten("AND", groups, conds) == {"c1": 1, "c2": 2}


def test_an_or_of_ands_maps_onto_the_integer_column():
    groups = [
        Group("root", group_logic="OR"),
        Group("a", parent_group_id="root", sequence_number=0),
        Group("b", parent_group_id="root", sequence_number=1),
    ]
    conds = [Cond("c1", "a"), Cond("c2", "b")]
    assert _flatten("AND", groups, conds) == {"c1": 1, "c2": 2}


def test_nesting_deeper_than_two_levels_is_refused():
    groups = [
        Group("root", group_logic="OR"),
        Group("a", parent_group_id="root"),
        Group("deep", parent_group_id="a"),
    ]
    with pytest.raises(Unmappable, match="deeper than two levels"):
        _flatten("AND", groups, [Cond("c1", "deep")])


def test_a_negated_group_is_refused():
    # The target has no negation column anywhere. Dropping the NOT would store
    # the exact opposite of what the author wrote.
    groups = [Group("g1", negated_flag=True)]
    with pytest.raises(Unmappable, match="negated"):
        _flatten("AND", groups, [Cond("c1", "g1")])


def test_and_of_ands_flattens_to_one_group():
    groups = [Group("g1", sequence_number=0), Group("g2", sequence_number=1)]
    conds = [Cond("c1", "g1"), Cond("c2", "g2")]
    assert _flatten("AND", groups, conds) == {"c1": 1, "c2": 1}


# --- Conditions --------------------------------------------------------------


def _conds(conds: list[Cond], index: dict[str, int]) -> list[dict[str, Any]]:
    return mapping.map_conditions(rule_id="v1", conditions=conds, group_index=index)


def test_a_negated_predicate_is_expressed_by_inverting_its_operator():
    # Inverted at platform level (EQUALS → NOT_EQUALS), then translated into
    # the target's short codes (→ NEQ).
    rows = _conds([Cond("c1", "g1", operator_code="EQUALS", negated_flag=True)], {"c1": 1})
    assert rows[0]["operator_code"] == "NEQ"


def test_operators_are_stored_in_the_targets_short_codes():
    rows = _conds([Cond("c1", "g1", operator_code="EQUALS")], {"c1": 1})
    assert rows[0]["operator_code"] == "EQ"


def test_a_negated_operator_with_no_inverse_is_refused():
    with pytest.raises(Unmappable, match="no inverse"):
        _conds([Cond("c1", "g1", operator_code="CONTAINS", negated_flag=True)], {"c1": 1})


def test_an_in_list_is_carried_as_a_json_array():
    cond = Cond(
        "c1", "g1", operator_code="IN",
        comparison_value_type="LIST", comparison_values=["A", "B"],
    )
    row = _conds([cond], {"c1": 1})[0]
    assert row["value_type"] == "ARRAY"
    assert row["comparison_value"] == '["A", "B"]'


def test_between_populates_both_value_columns():
    cond = Cond(
        "c1", "g1", attribute_name="duration_seconds", operator_code="BETWEEN",
        comparison_value_type="RANGE", comparison_values=[Decimal("1"), Decimal("60")],
    )
    row = _conds([cond], {"c1": 1})[0]
    assert (row["comparison_value"], row["comparison_value_to"]) == ("1", "60")


def test_between_without_two_values_is_refused():
    cond = Cond("c1", "g1", operator_code="BETWEEN",
                comparison_value_type="RANGE", comparison_values=[Decimal("1")])
    with pytest.raises(Unmappable, match="two values"):
        _conds([cond], {"c1": 1})


def test_sequence_numbers_are_unique_within_a_group():
    # `uq_rule_condition_sequence` is (rule_id, condition_group, sequence_no) and
    # platform sequence numbers restart per group.
    conds = [Cond("c1", "g1", sequence_number=0), Cond("c2", "g2", sequence_number=0)]
    rows = _conds(conds, {"c1": 1, "c2": 1})
    assert {r["sequence_no"] for r in rows} == {1, 2}


# --- Actions -----------------------------------------------------------------


def test_the_rate_and_the_unit_split_across_the_targets_two_actions():
    # The target separates "the monetary rate" (SET_RATE) from "the charging
    # unit" (SET_RATING_UNIT), where the platform holds both on one action.
    action = Action("a1")
    params = [Param("p1", "a1", "rate"), Param("p2", "a1", "unit", "MINUTE", "STRING")]
    rows = mapping.map_actions(rule_id="v1", actions=[action], parameters=params)
    assert [(r["action_type"], r["parameter_name"]) for r in rows] == [
        ("SET_RATE", "rate"),
        ("SET_RATING_UNIT", "unit_seconds"),
    ]
    assert [r["sequence_no"] for r in rows] == [1, 2]
    assert [r["parameter_value"] for r in rows] == ["0.15", "60"]


def test_an_action_with_no_parameters_still_emits_a_row():
    # Otherwise the mirrored rule would match and then do nothing. The promoted
    # principal value (action_value) recovers the rate parameter.
    rows = mapping.map_actions(rule_id="v1", actions=[Action("a1")], parameters=[])
    assert len(rows) == 1
    assert rows[0]["parameter_name"] == "rate"
    assert rows[0]["parameter_value"] == "0.15"


def test_money_maps_to_decimal_not_to_a_money_type_the_target_lacks():
    rows = mapping.map_actions(rule_id="v1", actions=[Action("a1")], parameters=[])
    assert rows[0]["value_type"] == "DECIMAL"


def test_an_unknown_action_is_refused():
    with pytest.raises(Unmappable, match="no equivalent"):
        mapping.map_actions(
            rule_id="v1", actions=[Action("a1", action_type="NOT_A_REAL_ACTION")],
            parameters=[],
        )


def test_tax_catalogue_reference_becomes_numeric_percentage_only():
    action = Action("a1", action_type="APPLY_TAX", action_value=None)
    params = [Param("p1", "a1", "tax_rule", "INDIA_GST_18", "REFERENCE")]

    rows = mapping.map_actions(
        rule_id="v1",
        actions=[action],
        parameters=params,
        tax_rates={"INDIA_GST_18": Decimal("18.0000")},
    )

    assert [(row["parameter_name"], row["parameter_value"], row["value_type"]) for row in rows] == [
        ("percentage", "18", "DECIMAL")
    ]


def test_unresolved_tax_catalogue_reference_refuses_the_rule():
    action = Action("a1", action_type="APPLY_TAX", action_value=None)
    params = [Param("p1", "a1", "tax_rule", "MISSING_TAX", "REFERENCE")]

    with pytest.raises(Unmappable, match="without a resolved percentage"):
        mapping.map_actions(rule_id="v1", actions=[action], parameters=params)


# --- Legacy rule model -------------------------------------------------------
#
# The path the authoring UI actually writes. `RULE_COMPILE_SOURCE` defaults to
# LEGACY, so a default deployment's whole estate arrives through here.


@dataclass
class LegacyCond:
    id: str
    attribute: str = "tariff_plan"
    operator: str = "EQUALS"
    values: list = field(default_factory=lambda: ["PREPAID_A_VOICE"])
    negate: bool = False
    sequence: int = 0
    group_index: int = 0
    created_at: datetime | None = None


@dataclass
class LegacyAction:
    id: str
    action_type: str = "SET_RATE"
    params: dict = field(default_factory=lambda: {"rate": "1", "unit": "SECOND"})
    sequence: int = 0
    created_at: datetime | None = None


@dataclass
class LegacyRule:
    id: str = "lr1"
    rule_key: str = "PREPAID_PEAK_VOICE"
    name: str = "Prepaid Peak Voice"
    description: str = "Base tariff"
    rule_type: str = "BASE_TARIFF"
    execution_stage: str = "BASE_CHARGE"
    service_type: str | None = None
    version: int = 1
    priority: int = 100
    stacking_policy: str = "EXCLUSIVE"
    condition_logic: str = "AND"
    status: str = "DRAFT"
    currency_code: str | None = "INR"
    effective_from: date = date(2026, 7, 30)
    effective_to: date | None = date(2026, 7, 31)
    created_by: str | None = "user-1"
    created_at: datetime = datetime(2026, 7, 30, tzinfo=UTC)
    approved_by: str | None = None
    approved_at: datetime | None = None
    conditions: list = field(default_factory=lambda: [LegacyCond("c1")])
    actions: list = field(default_factory=lambda: [LegacyAction("a1")])


def test_a_legacy_rule_maps_onto_the_target():
    row = mapping.map_legacy_rule(LegacyRule())
    assert row["rule_id"] == "lr1"
    assert row["rule_stage"] == "BASE_RATE"
    # Stored in the target's own five-type vocabulary, not the platform's.
    assert row["rule_type"] == "USAGE_RATE"
    assert row["version_no"] == 1
    assert row["status"] == "DRAFT"
    assert row["currency_code"] == "INR"
    # Legacy predates the prepaid/postpaid split.
    assert row["account_scope"] == "BOTH"
    assert row["effective_to"] == datetime(2026, 8, 1, tzinfo=UTC)


def test_legacy_stacking_policy_becomes_a_match_strategy():
    # The target knows exactly two strategies; EXCLUSIVE and OVERRIDE both
    # reduce to FIRST_MATCH, STACKABLE alone is ALL_MATCHES.
    assert mapping.map_legacy_rule(LegacyRule())["match_strategy"] == "FIRST_MATCH"
    assert (
        mapping.map_legacy_rule(LegacyRule(stacking_policy="STACKABLE"))["match_strategy"]
        == "ALL_MATCHES"
    )
    assert (
        mapping.map_legacy_rule(LegacyRule(stacking_policy="OVERRIDE"))["match_strategy"]
        == "FIRST_MATCH"
    )


def test_a_tiered_action_makes_the_rule_tiered_usage_rate():
    rule = LegacyRule(actions=[LegacyAction("a1", action_type="SET_TIERED_RATE",
                                            params={"tiers": "60:0.5,*:0.3"})])
    assert mapping.map_legacy_rule(rule)["rule_type"] == "TIERED_USAGE_RATE"


def test_a_bundle_rule_becomes_free_unit():
    rule = LegacyRule(rule_type="BUNDLE", execution_stage="BUNDLE",
                      actions=[LegacyAction("a1", action_type="CONSUME_BUNDLE",
                                            params={"bundle": "VOICE100"})])
    row = mapping.map_legacy_rule(rule)
    assert row["rule_type"] == "FREE_UNIT"
    assert row["rule_stage"] == "ALLOWANCE"


def test_a_legacy_condition_recovers_its_type_from_the_attribute_registry():
    # Legacy conditions carry no declared value type — the gap the canonical
    # model was built to close. It is recovered, never guessed.
    rows = mapping.map_legacy_conditions(LegacyRule())
    assert rows[0]["attribute_name"] == "tariff_plan"
    assert rows[0]["value_type"] == "STRING"
    assert rows[0]["comparison_value"] == "PREPAID_A_VOICE"
    assert rows[0]["condition_group"] == 1


def test_legacy_rule_header_service_type_is_the_first_target_condition():
    rule = LegacyRule(
        service_type="VOICE",
        conditions=[
            LegacyCond(
                "c1",
                attribute="destination_zone",
                values=["LOCAL_ONNET"],
            )
        ],
    )

    rows = mapping.map_legacy_conditions(rule)

    assert [
        (
            row["sequence_no"],
            row["attribute_name"],
            row["operator_code"],
            row["value_type"],
            row["comparison_value"],
        )
        for row in rows
    ] == [
        (1, "service_type", "EQ", "STRING", "VOICE"),
        (2, "destination_zone", "EQ", "STRING", "LOCAL"),
    ]


@pytest.mark.parametrize(
    "source_zone", ["LOCAL", "LOCAL_ONNET", "LOCAL_OFFNET", "ONNET", "OFFNET"]
)
def test_local_onnet_and_offnet_zones_mirror_as_local(source_zone: str):
    rule = LegacyRule(
        conditions=[
            LegacyCond("c1", attribute="destination_zone", values=[source_zone])
        ]
    )

    row = mapping.map_legacy_conditions(rule)[0]

    assert row["comparison_value"] == "LOCAL"


def test_service_type_is_repeated_in_every_or_group():
    rule = LegacyRule(
        service_type="SMS",
        condition_logic="OR",
        conditions=[
            LegacyCond("c1", group_index=0),
            LegacyCond("c2", group_index=1, sequence=1),
        ],
    )

    rows = mapping.map_legacy_conditions(rule)

    assert [
        (row["condition_group"], row["sequence_no"], row["attribute_name"])
        for row in rows
    ] == [
        (1, 1, "service_type"),
        (1, 2, "tariff_plan"),
        (2, 1, "service_type"),
        (2, 2, "tariff_plan"),
    ]


def test_canonical_rule_header_service_type_is_materialised():
    rows = mapping.map_conditions(
        rule_id="v1",
        conditions=[Cond("c1", "g1", comparison_value="LOCAL_OFFNET")],
        group_index={"c1": 1},
        service_type="VOICE",
    )

    assert [
        (row["sequence_no"], row["attribute_name"], row["comparison_value"])
        for row in rows
    ] == [
        (1, "service_type", "VOICE"),
        (2, "destination_zone", "LOCAL"),
    ]


def test_legacy_groups_collapse_under_and_and_separate_under_or():
    conds = [LegacyCond("c1", group_index=0), LegacyCond("c2", group_index=1, sequence=1)]
    assert [
        r["condition_group"] for r in mapping.map_legacy_conditions(
            LegacyRule(condition_logic="AND", conditions=conds)
        )
    ] == [1, 1]
    assert [
        r["condition_group"] for r in mapping.map_legacy_conditions(
            LegacyRule(condition_logic="OR", conditions=conds)
        )
    ] == [1, 2]


def test_an_unknown_legacy_attribute_is_refused():
    with pytest.raises(Unmappable, match="not a known rating attribute"):
        mapping.map_legacy_conditions(LegacyRule(conditions=[LegacyCond("c1", attribute="nope")]))


def test_a_legacy_params_dict_explodes_across_the_targets_action_types():
    action = LegacyAction(
        "a1", params={"rate": "1", "unit": "SECOND", "currency": "INR", "per_units": "2"}
    )
    rows = mapping.map_legacy_actions(LegacyRule(actions=[action]))
    # Currency lives on rating_rule; the action stores one concrete base-unit
    # block instead of separate unit/per_units rows.
    assert [(r["action_type"], r["parameter_name"]) for r in rows] == [
        ("SET_RATE", "rate"),
        ("SET_RATING_UNIT", "unit_seconds"),
    ]
    assert [r["sequence_no"] for r in rows] == [1, 2]
    assert [r["parameter_value"] for r in rows] == ["1.00", "60"]
    assert len({r["action_id"] for r in rows}) == 2
    by_name = {r["parameter_name"]: r["value_type"] for r in rows}
    assert by_name["rate"] == "DECIMAL"
    assert by_name["unit_seconds"] == "INTEGER"


def test_a_connection_fee_becomes_a_fixed_surcharge():
    action = LegacyAction("a1", action_type="SET_CONNECTION_FEE",
                          params={"amount": "0.05", "currency": "INR"})
    rows = mapping.map_legacy_actions(LegacyRule(actions=[action]))
    assert {r["action_type"] for r in rows} == {"APPLY_FIXED_SURCHARGE"}


def test_a_legacy_tax_reference_becomes_numeric_percentage_only():
    action = LegacyAction(
        "a1", action_type="APPLY_TAX", params={"tax_rule": "INDIA_GST_18"}
    )

    rows = mapping.map_legacy_actions(
        LegacyRule(actions=[action]),
        tax_rates={"INDIA_GST_18": Decimal("18.0000")},
    )

    assert [
        (row["action_type"], row["parameter_name"], row["parameter_value"], row["value_type"])
        for row in rows
    ] == [("APPLY_PERCENT_TAX", "percentage", "18", "DECIMAL")]


def test_a_zero_charge_becomes_a_zero_rate():
    action = LegacyAction("a1", action_type="SET_ZERO_CHARGE", params={})
    rows = mapping.map_legacy_actions(LegacyRule(actions=[action]))
    assert rows == [
        {
            "action_id": "a1:1", "rule_id": "lr1", "sequence_no": 1,
            "action_type": "SET_RATE", "parameter_name": "rate",
            "parameter_value": "0.00", "value_type": "DECIMAL", "created_at": None,
        }
    ]


def test_an_action_outside_the_targets_registry_refuses_the_rule():
    action = LegacyAction("a1", action_type="SELECT_TARIFF",
                          params={"tariff_plan": "PREPAID_A"})
    with pytest.raises(Unmappable, match="no equivalent"):
        mapping.map_legacy_actions(LegacyRule(actions=[action]))


def test_a_negated_legacy_condition_inverts_its_operator():
    rows = mapping.map_legacy_conditions(
        LegacyRule(conditions=[LegacyCond("c1", operator="EQUALS", negate=True)])
    )
    assert rows[0]["operator_code"] == "NEQ"


def test_legacy_attributes_translate_into_the_targets_names_and_types():
    conds = [
        LegacyCond("c1", attribute="duration_seconds", operator="GREATER_THAN",
                   values=[60], sequence=0),
        LegacyCond("c2", attribute="bundle", values=["VOICE100"], sequence=1),
    ]
    rows = mapping.map_legacy_conditions(LegacyRule(conditions=conds))
    assert (rows[0]["attribute_name"], rows[0]["operator_code"]) == ("duration_sec", "GT")
    assert rows[0]["value_type"] == "INTEGER"
    assert rows[1]["attribute_name"] == "bundle_code"


def test_usage_volume_rescales_from_bytes_to_kilobytes():
    conds = [LegacyCond("c1", attribute="usage_volume", operator="GREATER_THAN",
                        values=[2048])]
    row = mapping.map_legacy_conditions(LegacyRule(conditions=conds))[0]
    assert row["attribute_name"] == "volume_kb"
    assert row["comparison_value"] == "2"
    assert row["value_type"] == "DECIMAL"


def test_boolean_values_are_stored_uppercase_like_the_targets_own_rows():
    conds = [LegacyCond("c1", attribute="roaming", values=[False])]
    row = mapping.map_legacy_conditions(LegacyRule(conditions=conds))[0]
    assert row["comparison_value"] == "FALSE"
    assert row["value_type"] == "BOOLEAN"


def test_a_legacy_rule_on_an_unmappable_stage_is_refused():
    with pytest.raises(Unmappable, match="stage"):
        mapping.map_legacy_rule(LegacyRule(execution_stage="MAXIMUM_CHARGE"))


# --- Target-native authoring (2026-07) ---------------------------------------
#
# The UI now offers the operator's own vocabulary directly. A rule authored in
# it must round-trip into the mirror byte-for-byte — no renaming, no rescaling.


def test_a_natively_authored_rule_round_trips_unchanged():
    rule = LegacyRule(
        rule_type="PERCENTAGE_DISCOUNT", execution_stage="DISCOUNT",
        conditions=[LegacyCond("c1", attribute="volume_kb",
                               operator="GREATER_THAN", values=[100])],
        actions=[LegacyAction("a1", action_type="APPLY_PERCENT_DISCOUNT",
                              params={"percentage": "10"})],
    )
    row = mapping.map_legacy_rule(rule)
    assert (row["rule_type"], row["rule_stage"]) == ("PERCENTAGE_DISCOUNT", "DISCOUNT")
    cond = mapping.map_legacy_conditions(rule)[0]
    # volume_kb authored directly already states kilobytes — NO ÷1024, unlike
    # the usage_volume (bytes) alias.
    assert (cond["attribute_name"], cond["operator_code"], cond["comparison_value"]) == (
        "volume_kb", "GT", "100"
    )
    action = mapping.map_legacy_actions(rule)[0]
    assert (action["action_type"], action["parameter_name"], action["parameter_value"],
            action["value_type"]) == ("APPLY_PERCENT_DISCOUNT", "percentage", "10", "DECIMAL")


def test_a_native_free_unit_rule_round_trips():
    rule = LegacyRule(
        rule_type="FREE_UNIT", execution_stage="BUNDLE",
        conditions=[LegacyCond("c1", attribute="bundle_code", values=["VOICE100"])],
        actions=[LegacyAction("a1", action_type="CONSUME_ALLOWANCE",
                              params={"bundle_code": "VOICE100"})],
    )
    assert mapping.map_legacy_rule(rule)["rule_type"] == "FREE_UNIT"
    assert mapping.map_legacy_conditions(rule)[0]["attribute_name"] == "bundle_code"
    action = mapping.map_legacy_actions(rule)[0]
    assert (action["action_type"], action["parameter_name"]) == (
        "CONSUME_ALLOWANCE", "balance_type"
    )


def test_voice_rate_pulse_and_rounding_match_the_target_sample_shape():
    rule = LegacyRule(
        actions=[
            LegacyAction(
                "a1",
                action_type="SET_RATE",
                params={"rate": "0.5", "unit": "SECOND", "currency": "INR"},
            ),
            LegacyAction(
                "a2",
                action_type="SET_PULSE",
                params={"initial_seconds": "60"},
                sequence=1,
            ),
            LegacyAction(
                "a3",
                action_type="SET_ROUNDING",
                params={"mode": "CEILING"},
                sequence=2,
            ),
        ]
    )

    assert [
        (row["action_type"], row["parameter_name"], row["parameter_value"])
        for row in mapping.map_legacy_actions(rule)
    ] == [
        ("SET_RATE", "rate", "0.50"),
        ("SET_RATING_UNIT", "unit_seconds", "60"),
        ("SET_PULSE", "pulse_seconds", "60"),
        ("SET_ROUNDING", "pulse_rounding", "CEILING"),
    ]


def test_count_and_kilobyte_units_match_the_target_sample_shape():
    count = LegacyRule(
        actions=[
            LegacyAction(
                "a1", action_type="SET_RATE", params={"rate": "0.2", "unit": "MESSAGE"}
            )
        ]
    )
    volume = LegacyRule(
        actions=[
            LegacyAction(
                "a1", action_type="SET_RATE", params={"rate": "0.1", "unit": "KILOBYTE"}
            ),
            LegacyAction(
                "a2", action_type="SET_ROUNDING", params={"mode": "CEILING"}, sequence=1
            ),
        ]
    )

    assert [
        (row["parameter_name"], row["parameter_value"])
        for row in mapping.map_legacy_actions(count)
    ] == [("rate", "0.20"), ("unit_count", "1")]
    assert [
        (row["parameter_name"], row["parameter_value"])
        for row in mapping.map_legacy_actions(volume)
    ] == [
        ("rate", "0.10"),
        ("unit_kb", "1024"),
        ("volume_rounding", "CEILING"),
    ]


def test_allowance_and_free_quantity_match_the_target_parameter_names():
    rule = LegacyRule(
        actions=[
            LegacyAction(
                "a1",
                action_type="CONSUME_ALLOWANCE",
                params={"bundle_code": "VOICE_MINUTES"},
            ),
            LegacyAction(
                "a2",
                action_type="SET_FREE_QUANTITY",
                params={"quantity": "100"},
                sequence=1,
            ),
        ]
    )

    assert [
        (row["action_type"], row["parameter_name"], row["parameter_value"])
        for row in mapping.map_legacy_actions(rule)
    ] == [
        ("CONSUME_ALLOWANCE", "balance_type", "VOICE_MINUTES"),
        ("SET_FREE_QUANTITY", "maximum_units", "100"),
    ]


def test_native_rule_types_and_actions_exist_in_both_registries():
    # The UI offers what the legacy registries hold; the mirror seeds what
    # targetvocab holds. These must agree or an authored rule fails its FK.
    from app.modules.mirror import targetvocab
    from app.modules.rules.constants import ACTION_SPECS, ATTRIBUTE_BY_KEY, RULE_TYPE_STAGE

    for rule_type in ("USAGE_RATE", "TIERED_USAGE_RATE", "PERCENTAGE_DISCOUNT",
                      "FREE_UNIT", "PERCENTAGE_TAX"):
        assert rule_type in RULE_TYPE_STAGE
    legacy_actions = {s.type for s in ACTION_SPECS}
    assert legacy_actions >= targetvocab.TARGET_ACTION_TYPES
    for name in ("duration_sec", "volume_kb", "bundle_code", "bundle_remaining",
                 "tax_country", "tax_exempt", "offer_code", "destination_country",
                 "destination_operator"):
        assert name in ATTRIBUTE_BY_KEY
        assert name in targetvocab.TARGET_ATTRIBUTE_NAMES


# --- Time bands --------------------------------------------------------------


@dataclass
class Band:
    code: str = "PEAK"
    name: str = "Peak"
    days: list = field(default_factory=lambda: ["MON", "TUE", "WED", "THU", "FRI"])
    start_time: time = time(8, 0)
    end_time: time = time(20, 0)
    timezone: str = "Africa/Accra"
    priority: int = 100
    status: str = "ACTIVE"
    created_at: datetime = datetime(2026, 1, 1, tzinfo=UTC)


def test_weekday_bands_collapse_to_one_row():
    rows = mapping.map_time_band(Band())
    assert len(rows) == 1
    assert rows[0]["day_type"] == "WEEKDAY"
    assert (rows[0]["start_second"], rows[0]["end_second"]) == (28_800, 72_000)


def test_a_weekend_band_uses_the_weekend_day_type():
    assert mapping.map_time_band(Band(days=["SAT", "SUN"]))[0]["day_type"] == "WEEKEND"


def test_an_arbitrary_day_set_becomes_one_row_per_day():
    rows = mapping.map_time_band(Band(days=["MON", "WED"]))
    assert [r["day_type"] for r in rows] == ["MONDAY", "WEDNESDAY"]


def test_a_band_that_wraps_midnight_is_split_in_two():
    # `chk_time_band_seconds` requires start < end, so 22:00→06:00 cannot be one
    # row. Two rows keep the coverage exact.
    rows = mapping.map_time_band(Band(days=[], start_time=time(22, 0), end_time=time(6, 0)))
    assert [(r["start_second"], r["end_second"]) for r in rows] == [
        (79_200, 86_400),
        (0, 21_600),
    ]


def test_a_retired_band_mirrors_as_inactive():
    # The target allows only ACTIVE / INACTIVE.
    assert mapping.map_time_band(Band(status="RETIRED"))[0]["status"] == "INACTIVE"


# --- Destination prefixes ----------------------------------------------------


@dataclass
class Zone:
    code: str = "LOCAL"
    zone_type: str = "NATIONAL"
    country_code: str | None = "GH"
    status: str = "ACTIVE"


@dataclass
class Prefix:
    prefix: str = "233"
    created_at: datetime = datetime(2026, 1, 1, tzinfo=UTC)


def test_the_zone_is_denormalised_onto_the_prefix():
    row = mapping.map_destination_prefix(Prefix(), Zone())
    assert row["zone_code"] == "LOCAL"
    assert row["destination_type"] == "NATIONAL"
    assert row["country_code"] == "GH"


def test_a_prefix_with_no_zone_is_refused():
    with pytest.raises(Unmappable, match="zone"):
        mapping.map_destination_prefix(Prefix(), None)


# --- Value maps --------------------------------------------------------------


def test_every_mapped_stage_is_one_the_target_permits():
    assert set(vm.STAGE_MAP.values()) <= vm.TARGET_STAGES


def test_every_mapped_status_is_one_the_target_permits():
    allowed = {"DRAFT", "PENDING_APPROVAL", "ACTIVE", "INACTIVE", "REJECTED", "RETIRED"}
    assert set(vm.STATUS_MAP.values()) <= allowed


def test_every_platform_status_has_a_mapping():
    from app.modules.rules.constants import RuleStatus

    assert {str(s) for s in RuleStatus} <= {str(k) for k in vm.STATUS_MAP}


def test_condition_value_types_stay_inside_the_check_constraint():
    assert set(vm.VALUE_TYPE_MAP.values()) <= vm.CONDITION_VALUE_TYPES


def test_operator_inversion_is_symmetric():
    for code, inverse in vm.OPERATOR_INVERSE.items():
        assert vm.OPERATOR_INVERSE[inverse] == code
