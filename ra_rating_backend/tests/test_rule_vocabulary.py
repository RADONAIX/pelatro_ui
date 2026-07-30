"""The rule vocabulary must stay internally consistent.

These are the checks that catch a half-finished edit to ``rules/constants.py``:
the UI builder is generated from these tables, so an attribute with no valid
operators or an action whose stage doesn't exist would ship a broken form.
"""

from __future__ import annotations

from app.modules.rules.constants import (
    ACTION_BY_TYPE,
    ACTION_SPECS,
    ALLOWED_TRANSITIONS,
    ATTRIBUTE_BY_KEY,
    OPERATOR_ARITY,
    OPERATOR_LABELS,
    OPERATORS_BY_TYPE,
    REQUIRED_ACTION_FOR_TYPE,
    RULE_ATTRIBUTES,
    RULE_TYPE_STAGE,
    STAGE_ORDER,
    ActionType,
    DataType,
    Operator,
    RuleStatus,
    RuleType,
)


def test_every_operator_has_a_label_and_arity():
    for op in Operator:
        assert op.value in OPERATOR_LABELS, f"{op} has no label"
        assert op.value in OPERATOR_ARITY, f"{op} has no arity"


def test_operator_arity_bounds_are_sane():
    for op, (low, high) in OPERATOR_ARITY.items():
        assert low >= 0, op
        assert high is None or high >= low, op


def test_every_data_type_allows_at_least_one_operator():
    for dt in DataType:
        assert OPERATORS_BY_TYPE.get(dt.value), f"{dt} has no operators"


def test_attributes_are_unique_and_well_formed():
    assert len(ATTRIBUTE_BY_KEY) == len(RULE_ATTRIBUTES), "duplicate attribute key"
    for attr in RULE_ATTRIBUTES:
        assert attr.data_type in {d.value for d in DataType}, attr.key
        assert OPERATORS_BY_TYPE[attr.data_type], attr.key
        assert attr.specificity > 0, attr.key
        if attr.data_type == DataType.ENUM:
            assert attr.values, f"{attr.key} is ENUM but lists no values"
        if attr.data_type == DataType.REFERENCE:
            assert attr.reference, f"{attr.key} is REFERENCE but names no catalogue"


def test_reference_attributes_point_at_a_real_catalogue():
    from app.modules.catalog.router import ENTITY_BY_SLUG

    for attr in RULE_ATTRIBUTES:
        if attr.reference:
            assert attr.reference in ENTITY_BY_SLUG, (
                f"{attr.key} references unknown catalogue '{attr.reference}'"
            )


def test_actions_are_unique_and_staged():
    assert len(ACTION_BY_TYPE) == len(ACTION_SPECS), "duplicate action type"
    for spec in ACTION_SPECS:
        assert spec.stage in STAGE_ORDER, f"{spec.type} has unknown stage {spec.stage}"
        keys = [p.key for p in spec.params]
        assert len(keys) == len(set(keys)), f"{spec.type} has duplicate params"


def test_action_reference_params_point_at_a_real_catalogue():
    from app.modules.catalog.router import ENTITY_BY_SLUG

    for spec in ACTION_SPECS:
        for p in spec.params:
            if p.reference:
                assert p.reference in ENTITY_BY_SLUG, (
                    f"{spec.type}.{p.key} references unknown catalogue '{p.reference}'"
                )
            if p.data_type == DataType.ENUM:
                assert p.values, f"{spec.type}.{p.key} is ENUM but lists no values"


def test_every_action_type_enum_member_has_a_spec():
    for action in ActionType:
        assert action.value in ACTION_BY_TYPE, f"{action} has no ActionSpec"


def test_every_rule_type_maps_to_a_stage():
    for rt in RuleType:
        assert rt.value in RULE_TYPE_STAGE, f"{rt} has no execution stage"
        assert RULE_TYPE_STAGE[rt.value] in STAGE_ORDER


def test_required_actions_reference_known_action_types():
    for rule_type, actions in REQUIRED_ACTION_FOR_TYPE.items():
        assert rule_type in RULE_TYPE_STAGE, rule_type
        for a in actions:
            assert a in ACTION_BY_TYPE, f"{rule_type} requires unknown action {a}"


def test_lifecycle_graph_is_closed():
    """Every status is a node, and every transition target is a real status."""
    statuses = {s.value for s in RuleStatus}
    assert set(ALLOWED_TRANSITIONS) == statuses, "a status is missing from the transition map"
    for source, targets in ALLOWED_TRANSITIONS.items():
        for target in targets:
            assert target in statuses, f"{source} -> unknown status {target}"
            assert target != source, f"{source} has a self-transition"


def test_retired_is_terminal():
    assert ALLOWED_TRANSITIONS[RuleStatus.RETIRED] == ()
