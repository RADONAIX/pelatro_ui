"""The canonical vocabulary must be internally coherent, and must agree with the
legacy vocabulary everywhere the two overlap.

The second half matters more than it looks. During R1-R4 both registries exist:
``rules/constants.py`` drives the live rating path, ``rules/vocabulary/`` drives
the canonical model. If they drift, a rule authored through the new path rates
differently from the same rule authored through the old one — and that divergence
would show up as a reconciliation exception nobody could explain. These tests are
what make "strict superset" a fact rather than an intention.
"""

from __future__ import annotations

import pytest

from app.modules.rules import constants as legacy
from app.modules.rules.vocabulary import (
    CANONICAL_ACTIONS,
    CANONICAL_ATTRIBUTE_BY_KEY,
    CANONICAL_ATTRIBUTES,
    CANONICAL_RULE_TYPES,
    CANONICAL_STAGES,
    ChargingMode,
    RuleCategory,
    actions_for_mode,
    attributes_for_mode,
    pipeline_for,
    rule_types_for_mode,
    stage_codes_for,
)
from app.modules.rules.vocabulary.actions import ACTION_BY_CODE
from app.modules.rules.vocabulary.aliases import (
    ACTION_ALIASES,
    ATTRIBUTE_ALIASES,
    CHARGING_MODE_ALIASES,
    RULE_TYPE_ALIASES,
    resolve_action,
    resolve_attribute,
    resolve_charging_mode,
)
from app.modules.rules.vocabulary.policies import CANONICAL_STACKING_POLICIES
from app.modules.rules.vocabulary.stages import STAGE_BY_CODE
from app.modules.rules.vocabulary.types import RULE_TYPE_BY_CODE
from app.modules.rules.vocabulary.values import (
    CURRENCY_BEARING_TYPES,
    NUMERIC_VALUE_TYPES,
    ValueType,
)

# --- Stages -----------------------------------------------------------------


def test_stage_codes_and_orders_are_unique():
    codes = [s.code for s in CANONICAL_STAGES]
    orders = [s.execution_order for s in CANONICAL_STAGES]
    assert len(set(codes)) == len(codes), "duplicate stage code"
    assert len(set(orders)) == len(orders), "duplicate execution_order"


def test_stage_orders_are_gap_numbered():
    """Steps of 10 keep room to insert a stage without renumbering — and
    renumbering silently reorders a live charging pipeline."""
    for stage in CANONICAL_STAGES:
        assert stage.execution_order % 10 == 0, f"{stage.code} is not gap-numbered"


def test_stages_are_declared_in_execution_order():
    orders = [s.execution_order for s in CANONICAL_STAGES]
    assert orders == sorted(orders), "declaration order should match execution order"


def test_every_stage_belongs_to_a_known_category():
    valid = {c.value for c in RuleCategory}
    for stage in CANONICAL_STAGES:
        assert stage.applies_to in valid, stage.code


def test_mode_pipelines_are_ordered_and_correctly_scoped():
    for mode, forbidden in (
        (ChargingMode.PREPAID, RuleCategory.POSTPAID),
        (ChargingMode.POSTPAID, RuleCategory.PREPAID),
    ):
        pipeline = pipeline_for(mode)
        orders = [s.execution_order for s in pipeline]
        assert orders == sorted(orders), f"{mode} pipeline is out of order"
        assert not [s for s in pipeline if s.applies_to == forbidden], (
            f"{mode} pipeline leaks {forbidden} stages"
        )


def test_both_mode_sees_only_common_stages():
    """A rule declared valid for prepaid AND postpaid cannot reach a stage that
    only exists in one of them — that is what BOTH means."""
    assert all(
        s.applies_to == RuleCategory.COMMON for s in pipeline_for(ChargingMode.BOTH)
    )


def test_unknown_charging_mode_is_rejected():
    with pytest.raises(KeyError):
        pipeline_for("SOMETIMES")


# --- Rule types -------------------------------------------------------------


def test_every_rule_type_names_a_real_stage():
    for spec in CANONICAL_RULE_TYPES:
        assert spec.stage_code in STAGE_BY_CODE, f"{spec.code} → {spec.stage_code}"


def test_rule_type_stage_is_reachable_from_its_charging_mode():
    """A postpaid type may not sit on a prepaid stage. This is the invariant that
    keeps one engine honest: the type decides the stage, the mode decides the
    pipeline, and the two must agree."""
    for spec in CANONICAL_RULE_TYPES:
        reachable = stage_codes_for(spec.charging_mode)
        assert spec.stage_code in reachable, (
            f"{spec.code} runs at {spec.stage_code}, unreachable for {spec.charging_mode}"
        )


def test_rule_type_category_agrees_with_its_stage_category():
    """A PREPAID-category type on a COMMON stage is fine (BUNDLE_CONSUMPTION_ORDER
    is one); a COMMON-category type on a mode-specific stage is not."""
    for spec in CANONICAL_RULE_TYPES:
        stage = STAGE_BY_CODE[spec.stage_code]
        if spec.rule_category == RuleCategory.COMMON:
            assert stage.applies_to == RuleCategory.COMMON, spec.code


def test_every_required_action_exists_and_is_mode_coherent():
    """The type's contract must be satisfiable. An action whose stage is outside
    the type's pipeline could never fire, so the contract would be unmeetable."""
    for spec in CANONICAL_RULE_TYPES:
        reachable = stage_codes_for(spec.charging_mode)
        for code in spec.required_action_types:
            action = ACTION_BY_CODE.get(code)
            assert action is not None, f"{spec.code} requires unknown action {code}"
            assert action.stage_code in reachable, (
                f"{spec.code} requires {code} at {action.stage_code}, "
                f"unreachable for {spec.charging_mode}"
            )


def test_every_rule_type_has_a_contract():
    for spec in CANONICAL_RULE_TYPES:
        assert spec.required_action_types, (
            f"{spec.code} requires no action — a rule of this type could match every "
            "CDR and change nothing, silently"
        )


def test_alias_targets_exist_and_are_not_themselves_aliases():
    for spec in CANONICAL_RULE_TYPES:
        if spec.alias_of is None:
            continue
        target = RULE_TYPE_BY_CODE.get(spec.alias_of)
        assert target is not None, f"{spec.code} aliases unknown {spec.alias_of}"
        assert target.alias_of is None, f"{spec.code} aliases an alias"
        assert target.stage_code == spec.stage_code, (
            f"{spec.code} and its alias target run at different stages"
        )


def test_rule_types_offered_per_mode_are_usable():
    for mode in (ChargingMode.PREPAID, ChargingMode.POSTPAID, ChargingMode.BOTH):
        offered = rule_types_for_mode(mode)
        assert offered, mode
        reachable = stage_codes_for(mode)
        for spec in offered:
            assert spec.stage_code in reachable, f"{mode} offers unusable {spec.code}"


# --- Actions ----------------------------------------------------------------


def test_every_action_names_a_real_stage():
    for action in CANONICAL_ACTIONS:
        assert action.stage_code in STAGE_BY_CODE, f"{action.code} → {action.stage_code}"


def test_action_codes_are_unique():
    codes = [a.code for a in CANONICAL_ACTIONS]
    assert len(set(codes)) == len(codes), "duplicate action code"


def test_action_value_param_exists():
    for action in CANONICAL_ACTIONS:
        if action.value_param is None:
            continue
        keys = {p.key for p in action.params}
        assert action.value_param in keys, (
            f"{action.code} promotes '{action.value_param}', which is not one of its params"
        )


def test_action_params_are_well_formed():
    valid_types = {v.value for v in ValueType}
    for action in CANONICAL_ACTIONS:
        keys = [p.key for p in action.params]
        assert len(set(keys)) == len(keys), f"{action.code} has a duplicate param"
        for param in action.params:
            assert param.value_type in valid_types, f"{action.code}.{param.key}"
            if param.value_type == ValueType.ENUM:
                assert param.values, f"{action.code}.{param.key} is ENUM with no values"
            if param.value_type == ValueType.REFERENCE:
                assert param.reference, f"{action.code}.{param.key} names no catalogue"


def test_money_params_can_carry_a_currency():
    """A MONEY parameter with no way to state its currency is an amount nobody can
    reconcile. Either the action offers a currency param, or the value is a
    percentage — never a bare amount."""
    for action in CANONICAL_ACTIONS:
        money = [p for p in action.params if p.value_type in CURRENCY_BEARING_TYPES]
        if not money:
            continue
        keys = {p.key for p in action.params}
        assert "currency" in keys, (
            f"{action.code} has money params {[p.key for p in money]} but no currency"
        )


def test_action_palette_per_mode_is_non_empty_and_scoped():
    for mode in (ChargingMode.PREPAID, ChargingMode.POSTPAID, ChargingMode.BOTH):
        palette = actions_for_mode(mode)
        assert palette, mode
        reachable = stage_codes_for(mode)
        for action in palette:
            assert action.stage_code in reachable


def test_prepaid_and_postpaid_palettes_do_not_overlap_on_mode_specific_actions():
    """An operator authoring a prepaid rule must never be offered
    ADD_INVOICE_COMPONENT, and vice versa."""
    prepaid = {a.code for a in actions_for_mode(ChargingMode.PREPAID)}
    postpaid = {a.code for a in actions_for_mode(ChargingMode.POSTPAID)}
    common = {a.code for a in actions_for_mode(ChargingMode.BOTH)}
    assert prepaid & postpaid == common, (
        "prepaid and postpaid palettes should share exactly the common actions"
    )
    assert "ADD_INVOICE_COMPONENT" not in prepaid
    assert "DEDUCT_BALANCE" not in postpaid


# --- Attributes -------------------------------------------------------------


def test_attribute_keys_are_unique():
    assert len(CANONICAL_ATTRIBUTE_BY_KEY) == len(CANONICAL_ATTRIBUTES)


def test_attributes_are_well_formed():
    for attr in CANONICAL_ATTRIBUTES:
        assert attr.specificity > 0, attr.key
        assert legacy.OPERATORS_BY_TYPE.get(attr.data_type), attr.key
        if attr.data_type == legacy.DataType.ENUM:
            assert attr.values, f"{attr.key} is ENUM but lists no values"
        if attr.data_type == legacy.DataType.REFERENCE:
            assert attr.reference, f"{attr.key} is REFERENCE but names no catalogue"


def test_attribute_mode_scope_refers_to_real_attributes():
    from app.modules.rules.vocabulary.attributes import ATTRIBUTE_MODE_SCOPE

    for key in ATTRIBUTE_MODE_SCOPE:
        assert key in CANONICAL_ATTRIBUTE_BY_KEY, key


def test_attributes_offered_per_mode_exclude_the_other_world():
    prepaid = {a.key for a in attributes_for_mode(ChargingMode.PREPAID)}
    postpaid = {a.key for a in attributes_for_mode(ChargingMode.POSTPAID)}
    assert "billing_cycle" not in prepaid
    assert "invoice_type" not in prepaid
    assert "balance_type" not in postpaid
    assert "session_type" not in postpaid
    # charging_mode itself is askable either way.
    assert "charging_mode" in prepaid and "charging_mode" in postpaid


# --- Aliases ----------------------------------------------------------------


def test_action_aliases_resolve_to_real_actions():
    for alias, target in ACTION_ALIASES.items():
        assert target in ACTION_BY_CODE, f"{alias} → unknown {target}"
        assert alias not in ACTION_BY_CODE, (
            f"{alias} is both an alias and a real action code"
        )


def test_rule_type_aliases_resolve_to_real_types():
    for alias, target in RULE_TYPE_ALIASES.items():
        assert target in RULE_TYPE_BY_CODE, f"{alias} → unknown {target}"
        assert alias not in RULE_TYPE_BY_CODE, (
            f"{alias} is both an alias and a real rule type"
        )


def test_attribute_aliases_resolve_to_real_attributes():
    for alias, target in ATTRIBUTE_ALIASES.items():
        assert target in CANONICAL_ATTRIBUTE_BY_KEY, f"{alias} → unknown {target}"
        assert alias not in CANONICAL_ATTRIBUTE_BY_KEY, (
            f"{alias} is both an alias and a real attribute"
        )


def test_charging_mode_aliases_resolve_to_real_modes():
    valid = {m.value for m in ChargingMode}
    for alias, target in CHARGING_MODE_ALIASES.items():
        assert target in valid, f"{alias} → unknown {target}"
        assert alias not in valid


def test_resolution_reports_what_it_substituted():
    """Lineage records only the substitutions that actually happened, so an
    already-canonical token must report no alias."""
    assert resolve_action("SET_MINIMUM") == ("SET_MINIMUM_CHARGE", "SET_MINIMUM")
    assert resolve_action("SET_RATE") == ("SET_RATE", None)
    assert resolve_action("set_minimum") == ("SET_MINIMUM_CHARGE", "set_minimum")
    assert resolve_attribute("product_id") == ("product", "product_id")
    assert resolve_attribute("product") == ("product", None)
    assert resolve_charging_mode("HYBRID") == ("BOTH", "HYBRID")
    assert resolve_charging_mode(None) == (None, None)
    assert resolve_charging_mode("") == ("", None)


def test_hybrid_means_both_for_a_rule_but_stays_hybrid_for_a_subscriber():
    """`account_type` describes a subscriber holding both balances; a rule's
    charging_mode describes text valid either way. Different statements, and
    collapsing them would lose the distinction."""
    assert resolve_charging_mode("HYBRID")[0] == ChargingMode.BOTH
    assert "HYBRID" in CANONICAL_ATTRIBUTE_BY_KEY["account_type"].values


# --- Stacking policies ------------------------------------------------------


def test_stacking_policies_cover_the_legacy_enum():
    canonical = {p.code for p in CANONICAL_STACKING_POLICIES}
    assert {p.value for p in legacy.StackingPolicy} <= canonical


def test_stacking_policy_flags_match_their_meaning():
    by_code = {p.code: p for p in CANONICAL_STACKING_POLICIES}
    assert by_code["EXCLUSIVE"].allows_multiple is False
    assert by_code["STACKABLE"].allows_multiple is True
    assert by_code["OVERRIDE"].overrides_lower is True


# --- Value types ------------------------------------------------------------


def test_numeric_value_types_include_money():
    assert ValueType.MONEY in NUMERIC_VALUE_TYPES
    assert ValueType.NUMBER in NUMERIC_VALUE_TYPES
    assert ValueType.STRING not in NUMERIC_VALUE_TYPES


def test_legacy_data_types_are_all_representable():
    """Every legacy DataType must map onto a canonical ValueType, or the backfill
    would have nowhere to put existing conditions."""
    canonical = {v.value for v in ValueType}
    for data_type in legacy.DataType:
        assert data_type.value in canonical, data_type


# --- Parity with the legacy vocabulary --------------------------------------


def test_legacy_stages_survive_with_their_relative_order():
    canonical_order = [
        s.code for s in sorted(CANONICAL_STAGES, key=lambda s: s.execution_order)
        if s.code in set(legacy.STAGE_ORDER)
    ]
    assert canonical_order == list(legacy.STAGE_ORDER), (
        "the canonical registry reordered an existing stage — every compiled "
        "snapshot's stage_order derives from the legacy tuple's positions"
    )


def test_legacy_stage_tuple_is_untouched():
    """R1 must not extend STAGE_ORDER: the compiler derives
    executable_rules.stage_order from index positions in it, so appending or
    prepending renumbers every snapshot ever compiled."""
    assert len(legacy.STAGE_ORDER) == 11
    assert legacy.STAGE_ORDER[0] == legacy.ExecutionStage.QUANTITY
    assert legacy.STAGE_ORDER[-1] == legacy.ExecutionStage.ROUNDING


def test_legacy_rule_types_keep_their_stage():
    for code, stage in legacy.RULE_TYPE_STAGE.items():
        spec = RULE_TYPE_BY_CODE.get(code)
        assert spec is not None, f"canonical registry dropped legacy type {code}"
        assert spec.stage_code == stage, (
            f"{code} moved from {stage} to {spec.stage_code} — a stage move needs a "
            "parity report, not a quiet edit"
        )


def test_legacy_rule_type_contracts_are_preserved():
    for code, required in legacy.REQUIRED_ACTION_FOR_TYPE.items():
        spec = RULE_TYPE_BY_CODE[code]
        assert set(required) == set(spec.required_action_types), code


def test_legacy_actions_keep_their_code_and_stage():
    for spec in legacy.ACTION_SPECS:
        action = ACTION_BY_CODE.get(spec.type)
        assert action is not None, f"canonical registry dropped legacy action {spec.type}"
        assert action.stage_code == spec.stage, (
            f"{spec.type} moved from {spec.stage} to {action.stage_code}"
        )


def test_legacy_action_params_are_a_subset_with_matching_requiredness():
    """Canonical may *add* an optional parameter (a pulse profile, an invoice
    component) and may *relax* a mandatory one. It may not drop a parameter,
    rename one, or make an optional parameter mandatory — any of those breaks a
    rule that imports today.

    Relaxing is safe in the one direction that matters: every rule written
    against the legacy spec already supplies the value, so widening what is
    accepted cannot invalidate anything that exists. `APPLY_TAX.tax_rule` was
    relaxed exactly so a sheet stating "VAT 20%" inline has somewhere to go
    without an operator first building a tax catalogue. What stops a relaxed
    parameter becoming a hole is the semantic tier, which requires one of
    `tax_rule` or `rate_percent` — a structural check cannot express "one of
    these two", which is why the requirement moved rather than disappeared."""
    for spec in legacy.ACTION_SPECS:
        canonical = ACTION_BY_CODE[spec.type]
        canonical_params = {p.key: p for p in canonical.params}
        for param in spec.params:
            assert param.key in canonical_params, f"{spec.type} lost param {param.key}"
            if not param.required:
                assert not canonical_params[param.key].required, (
                    f"{spec.type}.{param.key} became mandatory — rules that import "
                    "today have no value for it"
                )
        for key, param in canonical_params.items():
            if key not in {p.key for p in spec.params}:
                assert not param.required, (
                    f"{spec.type}.{key} is new and mandatory — existing rules have no value for it"
                )


def test_legacy_attributes_are_preserved_verbatim():
    """Imported rather than retyped, so this asserts the import, not a transcription."""
    for attr in legacy.RULE_ATTRIBUTES:
        assert CANONICAL_ATTRIBUTE_BY_KEY[attr.key] is attr


def test_canonical_registry_is_a_strict_superset():
    assert len(CANONICAL_STAGES) > len(legacy.STAGE_ORDER)
    assert len(CANONICAL_RULE_TYPES) > len(legacy.RULE_TYPE_STAGE)
    assert len(CANONICAL_ACTIONS) > len(legacy.ACTION_SPECS)
    assert len(CANONICAL_ATTRIBUTES) > len(legacy.RULE_ATTRIBUTES)
