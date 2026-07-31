"""Pure rule logic: specificity scoring, key derivation, RBAC resolution."""

from __future__ import annotations

from app.core.rbac import (
    DEFAULT_ROLE_PERMISSIONS,
    RatingPermKey,
    RoleSlug,
    deny_all,
    has_permission,
    normalize,
)
from app.modules.rules.constants import Operator
from app.modules.rules.schemas import ConditionIn
from app.modules.rules.service import compute_specificity, derive_rule_key


def _cond(attribute: str, operator: Operator, *values) -> ConditionIn:
    return ConditionIn(attribute=attribute, operator=operator, values=list(values))


# --- Rule key ---------------------------------------------------------------


def test_derive_rule_key_normalises_punctuation_and_case():
    assert derive_rule_key("Peak On-net Voice") == "PEAK_ON_NET_VOICE"
    assert derive_rule_key("  spaced   out  ") == "SPACED_OUT"
    assert derive_rule_key("!!!") == "RULE"


def test_derive_rule_key_is_bounded():
    assert len(derive_rule_key("x" * 500)) <= 72


# --- Specificity ------------------------------------------------------------


def test_narrower_rule_outscores_broader_rule():
    """The 4-level fallback must emerge from scoring, not hand-set priorities."""
    exact = compute_specificity([
        _cond("service_type", Operator.EQUALS, "VOICE"),
        _cond("product", Operator.EQUALS, "PREPAID_A"),
        _cond("destination_zone", Operator.EQUALS, "LOCAL_ONNET"),
        _cond("time_band", Operator.EQUALS, "PEAK"),
    ])
    product_level = compute_specificity([
        _cond("service_type", Operator.EQUALS, "VOICE"),
        _cond("product", Operator.EQUALS, "PREPAID_A"),
    ])
    service_level = compute_specificity([_cond("service_type", Operator.EQUALS, "VOICE")])
    global_default = compute_specificity([])

    assert exact > product_level > service_level > global_default == 0


def test_set_membership_scores_below_exact_match():
    exact = compute_specificity([_cond("destination_zone", Operator.EQUALS, "LOCAL_ONNET")])
    one_of_four = compute_specificity([
        _cond("destination_zone", Operator.IN, "A", "B", "C", "D")
    ])
    assert one_of_four < exact


def test_wider_in_list_scores_no_higher_than_a_narrow_one():
    two = compute_specificity([_cond("destination_zone", Operator.IN, "A", "B")])
    ten = compute_specificity([_cond("destination_zone", Operator.IN, *"ABCDEFGHIJ")])
    assert ten <= two


def test_negative_and_existence_operators_barely_narrow_anything():
    not_in = compute_specificity([_cond("destination_zone", Operator.NOT_IN, "A", "B")])
    exists = compute_specificity([_cond("destination_zone", Operator.EXISTS)])
    exact = compute_specificity([_cond("destination_zone", Operator.EQUALS, "A")])
    assert exists == 0
    assert 0 < not_in < exact


def test_unknown_attributes_are_ignored_not_scored():
    assert compute_specificity([_cond("not_a_real_attribute", Operator.EQUALS, "x")]) == 0


# --- RBAC -------------------------------------------------------------------


def test_maker_checker_analyst_cannot_approve():
    analyst = DEFAULT_ROLE_PERMISSIONS[RoleSlug.ANALYST]
    assert has_permission(analyst, RatingPermKey.RULES, "edit")
    assert not has_permission(analyst, RatingPermKey.APPROVALS, "edit")


def test_ra_lead_can_approve():
    lead = DEFAULT_ROLE_PERMISSIONS[RoleSlug.RA_LEAD]
    assert has_permission(lead, RatingPermKey.APPROVALS, "edit")


def test_viewer_cannot_edit_anything():
    viewer = DEFAULT_ROLE_PERMISSIONS[RoleSlug.VIEWER]
    assert not any(has_permission(viewer, k, "edit") for k in RatingPermKey)


def test_normalize_fills_missing_keys_from_role_defaults():
    partial = {RatingPermKey.RULES.value: {"view": True, "edit": False}}
    filled = normalize(partial, RoleSlug.ANALYST)
    assert set(filled) == {k.value for k in RatingPermKey}
    # The explicitly stored entry wins over the default (which allows edit).
    assert filled[RatingPermKey.RULES.value] == {"view": True, "edit": False}


def test_unknown_role_denies_everything():
    perms = normalize(None, "some_new_role")
    assert perms == deny_all()
    assert not any(has_permission(perms, k, "view") for k in RatingPermKey)


def test_every_default_matrix_covers_every_key():
    for role, matrix in DEFAULT_ROLE_PERMISSIONS.items():
        assert set(matrix) == {k.value for k in RatingPermKey}, role
