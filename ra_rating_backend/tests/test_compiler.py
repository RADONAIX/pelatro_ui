"""Compiler and conflict detection. Pure logic — no database."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.compiler import compiler, conflicts
from app.modules.compiler.constants import MATCH_DIMENSIONS
from app.modules.rules.constants import ActionType, Operator, RuleStatus
from app.modules.rules.models import Rule, RuleAction, RuleCondition

TODAY = date(2026, 1, 1)


def make_rule(
    key: str,
    *,
    conditions: list[tuple[str, str, list]] | None = None,
    actions: list[tuple[str, dict]] | None = None,
    stage: str = "BASE_CHARGE",
    rule_type: str = "BASE_TARIFF",
    service: str = "VOICE",
    priority: int = 100,
    specificity: int = 50,
    stacking: str = "EXCLUSIVE",
    conflict_group: str | None = None,
    effective_from: date = TODAY,
    effective_to: date | None = None,
    status: str = RuleStatus.APPROVED.value,
    version: int = 1,
) -> Rule:
    rule = Rule(
        id=f"id-{key}",
        rule_key=key,
        version=version,
        name=key.replace("_", " ").title(),
        description="",
        rule_type=rule_type,
        execution_stage=stage,
        service_type=service,
        priority=priority,
        specificity=specificity,
        stacking_policy=stacking,
        conflict_group=conflict_group,
        condition_logic="AND",
        effective_from=effective_from,
        effective_to=effective_to,
        status=status,
    )
    rule.conditions = [
        RuleCondition(sequence=i, group_index=0, attribute=a, operator=op, values=v, negate=False)
        for i, (a, op, v) in enumerate(conditions or [])
    ]
    rule.actions = [
        RuleAction(sequence=i, action_type=at, params=p)
        for i, (at, p) in enumerate(actions or [(ActionType.SET_RATE.value, {"rate": 0.1})])
    ]
    return rule


RATE = (ActionType.SET_RATE.value, {"rate": 0.1, "unit": "SECOND", "per_units": 60})


# --- Condition compilation --------------------------------------------------


def test_equality_conditions_become_match_dimensions():
    rule = make_rule(
        "R",
        conditions=[
            ("destination_zone", Operator.EQUALS.value, ["LOCAL_ONNET"]),
            ("time_band", Operator.EQUALS.value, ["PEAK"]),
        ],
    )
    dims, sets, predicates = compiler._compile_conditions(rule)
    assert dims["destination_zone"] == "LOCAL_ONNET"
    assert dims["time_band"] == "PEAK"
    assert dims["service_type"] == "VOICE"
    assert not sets
    assert not predicates


def test_unconstrained_dimensions_are_wildcards():
    dims, _, _ = compiler._compile_conditions(make_rule("R"))
    wildcards = [col for _, col in MATCH_DIMENSIONS if col != "service_type"]
    assert all(dims[col] is None for col in wildcards)


def test_in_over_several_values_becomes_a_dimension_set():
    rule = make_rule(
        "R", conditions=[("destination_zone", Operator.IN.value, ["A", "B", "C"])]
    )
    dims, sets, _ = compiler._compile_conditions(rule)
    # The column stays wildcard so the join still matches; the set narrows it.
    assert dims["destination_zone"] is None
    assert sets["destination_zone"] == ["A", "B", "C"]


def test_in_over_one_value_collapses_to_equality():
    rule = make_rule("R", conditions=[("destination_zone", Operator.IN.value, ["A"])])
    dims, sets, _ = compiler._compile_conditions(rule)
    assert dims["destination_zone"] == "A"
    assert not sets


def test_non_dimension_conditions_stay_as_predicates():
    rule = make_rule(
        "R",
        conditions=[
            ("duration_seconds", Operator.GREATER_THAN.value, [60]),
            ("called_number", Operator.STARTS_WITH.value, ["44"]),
        ],
    )
    dims, _, predicates = compiler._compile_conditions(rule)
    assert dims["destination_zone"] is None
    assert {p["attribute"] for p in predicates} == {"duration_seconds", "called_number"}


def test_negated_conditions_are_never_reduced_to_a_dimension():
    rule = make_rule("R", conditions=[("destination_zone", Operator.EQUALS.value, ["A"])])
    rule.conditions[0].negate = True
    dims, _, predicates = compiler._compile_conditions(rule)
    assert dims["destination_zone"] is None
    assert predicates[0]["negate"] is True


def test_boolean_dimensions_are_coerced():
    for raw, expected in (("Y", True), (False, False), ("TRUE", True)):
        rule = make_rule("R", conditions=[("roaming", Operator.EQUALS.value, [raw])])
        dims, _, _ = compiler._compile_conditions(rule)
        assert dims["roaming"] is expected, raw


def test_an_unparseable_boolean_falls_back_to_a_predicate():
    rule = make_rule("R", conditions=[("roaming", Operator.EQUALS.value, ["maybe"])])
    dims, _, predicates = compiler._compile_conditions(rule)
    assert dims["roaming"] is None
    assert predicates


# --- Actions ----------------------------------------------------------------


def test_catalogue_values_are_inlined_at_compile_time():
    """A rate resolved now keeps rating at that rate even if the catalogue
    changes tomorrow — which is what makes an old charge reproducible."""
    rule = make_rule("R", actions=[(ActionType.APPLY_TAX.value, {"tax_rule": "VAT_STANDARD"})])
    references = {"tax_rules": {"VAT_STANDARD": "tax-id-1"}}
    payloads = {"tax_rules": {"VAT_STANDARD": {"code": "VAT_STANDARD", "rate_percent": 20.0}}}
    actions = compiler._compile_actions(rule, references, payloads)
    assert actions[0]["resolved"]["tax_rule"]["rate_percent"] == 20.0
    assert actions[0]["resolved"]["tax_rule_id"] == "tax-id-1"


def test_zero_charge_is_hoisted_ahead_of_other_actions():
    rule = make_rule(
        "R",
        actions=[RATE, (ActionType.SET_ZERO_CHARGE.value, {})],
    )
    actions = compiler._compile_actions(rule, {}, {})
    assert actions[0]["action_type"] == ActionType.SET_ZERO_CHARGE.value


def test_numbers_are_normalised_so_the_checksum_is_stable():
    rule_a = make_rule("A", actions=[(ActionType.SET_RATE.value, {"rate": 0.10})])
    rule_b = make_rule("B", actions=[(ActionType.SET_RATE.value, {"rate": ".1"})])
    a = compiler._compile_actions(rule_a, {}, {})
    b = compiler._compile_actions(rule_b, {}, {})
    assert a[0]["params"]["rate"] == b[0]["params"]["rate"]


# --- Checksum ---------------------------------------------------------------


def _compiled(rules: list[Rule]):
    return [compiler.compile_rule(r, "snap", {}, {}) for r in rules]


def test_the_same_rules_checksum_identically():
    rules = [make_rule("A"), make_rule("B")]
    assert compiler.checksum(_compiled(rules)) == compiler.checksum(_compiled(rules))


def test_rule_order_does_not_change_the_checksum():
    a = _compiled([make_rule("A"), make_rule("B")])
    b = _compiled([make_rule("B"), make_rule("A")])
    assert compiler.checksum(a) == compiler.checksum(b)


def test_changing_a_rate_changes_the_checksum():
    a = _compiled([make_rule("A", actions=[(ActionType.SET_RATE.value, {"rate": 0.1})])])
    b = _compiled([make_rule("A", actions=[(ActionType.SET_RATE.value, {"rate": 0.2})])])
    assert compiler.checksum(a) != compiler.checksum(b)


# --- Footprints and overlap -------------------------------------------------


def test_footprints_with_disjoint_dimensions_cannot_collide():
    a = conflicts.build_footprint(
        make_rule("A", conditions=[("destination_zone", Operator.EQUALS.value, ["ONNET"])])
    )
    b = conflicts.build_footprint(
        make_rule("B", conditions=[("destination_zone", Operator.EQUALS.value, ["OFFNET"])])
    )
    assert not conflicts.footprints_intersect(a, b)


def test_a_wildcard_intersects_anything():
    a = conflicts.build_footprint(make_rule("A"))
    b = conflicts.build_footprint(
        make_rule("B", conditions=[("destination_zone", Operator.EQUALS.value, ["ONNET"])])
    )
    assert conflicts.footprints_intersect(a, b)


def test_different_services_never_collide():
    a = conflicts.build_footprint(make_rule("A", service="VOICE"))
    b = conflicts.build_footprint(make_rule("B", service="SMS"))
    assert not conflicts.footprints_intersect(a, b)


def test_non_overlapping_validity_windows_do_not_collide():
    a = make_rule("A", effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30))
    b = make_rule("B", effective_from=date(2026, 7, 1))
    assert not conflicts.windows_overlap(a, b)


def test_open_ended_windows_overlap_everything_after_their_start():
    a = make_rule("A", effective_from=date(2026, 1, 1))
    b = make_rule("B", effective_from=date(2030, 1, 1))
    assert conflicts.windows_overlap(a, b)


# --- Conflict detection -----------------------------------------------------


def _codes(issues):
    return {i.code for i in issues}


#: Two rules that overlap but price differently — the case where the engine
#: genuinely has no basis to choose.
_RATE_A = (ActionType.SET_RATE.value, {"rate": 0.1})
_RATE_B = (ActionType.SET_RATE.value, {"rate": 0.2})


def test_equal_precedence_overlap_at_an_exclusive_stage_is_an_error():
    rules = [
        make_rule("A", priority=100, specificity=50, actions=[_RATE_A]),
        make_rule("B", priority=100, specificity=50, actions=[_RATE_B]),
    ]
    issues = conflicts.detect_conflicts(rules)
    ambiguous = [i for i in issues if i.code == "ambiguous_precedence"]
    assert ambiguous and ambiguous[0].severity == "ERROR"


def test_different_priority_resolves_the_ambiguity():
    rules = [
        make_rule("A", priority=200, actions=[_RATE_A]),
        make_rule("B", priority=100, actions=[_RATE_B]),
    ]
    assert "ambiguous_precedence" not in _codes(conflicts.detect_conflicts(rules))


def test_different_specificity_resolves_the_ambiguity():
    rules = [
        make_rule("A", specificity=90, actions=[_RATE_A]),
        make_rule("B", specificity=50, actions=[_RATE_B]),
    ]
    assert "ambiguous_precedence" not in _codes(conflicts.detect_conflicts(rules))


def test_a_residual_predicate_softens_the_overlap_to_a_warning():
    """One rule may be narrower than its footprint shows, so the collision is
    possible rather than certain."""
    rules = [
        make_rule(
            "A",
            conditions=[("duration_seconds", Operator.GREATER_THAN.value, [60])],
            actions=[_RATE_A],
        ),
        make_rule("B", actions=[_RATE_B]),
    ]
    ambiguous = [i for i in conflicts.detect_conflicts(rules) if i.code == "ambiguous_precedence"]
    assert ambiguous and ambiguous[0].severity == "WARNING"


def test_identical_rules_are_reported_as_duplicates_not_as_ambiguity():
    """Two rules that match the same and do the same have an unambiguous
    diagnosis: one of them is redundant."""
    issues = conflicts.detect_conflicts([make_rule("A"), make_rule("B")])
    assert "duplicate_rule" in _codes(issues)
    assert "ambiguous_precedence" not in _codes(issues)


def test_two_exclusive_promotions_that_can_both_match_are_flagged():
    rules = [
        make_rule("P1", rule_type="PROMOTION", stage="PROMOTION",
                  actions=[(ActionType.APPLY_PROMOTION.value, {"promotion": "X"})]),
        make_rule("P2", rule_type="PROMOTION", stage="PROMOTION",
                  actions=[(ActionType.APPLY_PROMOTION.value, {"promotion": "Y"})]),
    ]
    assert "multiple_exclusive_promotions" in _codes(conflicts.detect_conflicts(rules))


def test_a_shared_conflict_group_resolves_the_promotion_clash():
    rules = [
        make_rule("P1", rule_type="PROMOTION", stage="PROMOTION", conflict_group="G",
                  actions=[(ActionType.APPLY_PROMOTION.value, {"promotion": "X"})]),
        make_rule("P2", rule_type="PROMOTION", stage="PROMOTION", conflict_group="G",
                  priority=50,
                  actions=[(ActionType.APPLY_PROMOTION.value, {"promotion": "Y"})]),
    ]
    assert "multiple_exclusive_promotions" not in _codes(conflicts.detect_conflicts(rules))


def test_circular_tariff_selection_is_an_error():
    a = make_rule(
        "A", rule_type="TARIFF_SELECTION", stage="TARIFF_SELECTION",
        actions=[(ActionType.SELECT_TARIFF.value, {"tariff_plan": "PLAN_B"})],
    )
    a.tariff_plan_id = "PLAN_A"
    b = make_rule(
        "B", rule_type="TARIFF_SELECTION", stage="TARIFF_SELECTION",
        actions=[(ActionType.SELECT_TARIFF.value, {"tariff_plan": "PLAN_A"})],
    )
    b.tariff_plan_id = "PLAN_B"
    issues = conflicts.detect_conflicts([a, b])
    circular = [i for i in issues if i.code == "circular_tariff_selection"]
    assert circular and circular[0].severity == "ERROR"


def test_a_linear_tariff_chain_is_fine():
    a = make_rule("A", rule_type="TARIFF_SELECTION", stage="TARIFF_SELECTION",
                  actions=[(ActionType.SELECT_TARIFF.value, {"tariff_plan": "PLAN_B"})])
    a.tariff_plan_id = "PLAN_A"
    assert "circular_tariff_selection" not in _codes(conflicts.detect_conflicts([a]))


def test_only_approved_rules_are_selectable_for_a_snapshot():
    rules = [
        make_rule("A", status=RuleStatus.DRAFT.value),
        make_rule("B", status=RuleStatus.APPROVED.value),
        make_rule("C", status=RuleStatus.RETIRED.value),
    ]
    assert [r.rule_key for r in conflicts.selectable(rules)] == ["B"]


# --- Snapshot window --------------------------------------------------------


def test_snapshot_window_spans_every_rule():
    rules = [
        make_rule("A", effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30)),
        make_rule("B", effective_from=date(2026, 3, 1), effective_to=date(2026, 12, 31)),
    ]
    start, end = compiler.snapshot_window(rules)
    assert start == date(2026, 1, 1)
    assert end == date(2026, 12, 31)


def test_one_open_ended_rule_makes_the_snapshot_open_ended():
    rules = [
        make_rule("A", effective_to=date(2026, 6, 30)),
        make_rule("B", effective_to=None),
    ]
    _, end = compiler.snapshot_window(rules)
    assert end is None


# --- Root-cause inference ---------------------------------------------------


class _FakeCdr:
    def __init__(self, actual):
        self.actual_charge = actual


def _outcome(final, tax=Decimal("0"), discount=Decimal("0"), missing=()):
    from app.modules.rating.engine import RatingOutcome

    return RatingOutcome(
        final_charge=Decimal(str(final)),
        tax=Decimal(str(tax)),
        discount=Decimal(str(discount)),
        missing_stages=list(missing),
    )


def test_a_penny_variance_is_a_rounding_issue_not_a_component_fault():
    """Every component agreed; the two sides rounded differently. Blaming tax
    would send an analyst to check something that is correct."""
    from decimal import Decimal as D

    from app.modules.rating import assurance
    from app.modules.rating.constants import RootCause

    cause = assurance.infer_root_cause(
        _FakeCdr(D("0.34")), _outcome("0.35", tax="0.045"), D("0.01")
    )
    assert cause == RootCause.ROUNDING_ISSUE


def test_a_missing_tax_component_is_recognised():
    from decimal import Decimal as D

    from app.modules.rating import assurance
    from app.modules.rating.constants import RootCause

    # Expected 0.23 with 0.03 of tax; billed 0.20 — exactly the pre-tax charge.
    cause = assurance.infer_root_cause(
        _FakeCdr(D("0.20")), _outcome("0.23", tax="0.03"), D("0.03")
    )
    assert cause == RootCause.TAX_INCORRECT


def test_a_missing_discount_is_recognised():
    from decimal import Decimal as D

    from app.modules.rating import assurance
    from app.modules.rating.constants import RootCause

    # Expected 0.90 after a 0.10 discount; billed 1.00 — the undiscounted charge.
    cause = assurance.infer_root_cause(
        _FakeCdr(D("1.00")), _outcome("0.90", discount="0.10"), D("-0.10")
    )
    assert cause == RootCause.DISCOUNT_MISSING


def test_undercharge_and_overcharge_are_never_netted():
    """Two failures of opposite sign are two problems, not a clean book."""

    from app.modules.rating import assurance

    under = assurance.classify(_Cdr(actual="0.20"), _outcome("0.30"))
    over = assurance.classify(_Cdr(actual="0.40"), _outcome("0.30"))
    assert under.variance > 0 and over.variance < 0
    assert under.status == "UNDERCHARGED"
    assert over.status == "OVERCHARGED"


class _Cdr:
    """Minimal CDR stand-in for the classifier."""

    def __init__(self, actual=None, **kw):
        from decimal import Decimal as D

        self.actual_charge = D(actual) if actual is not None else None
        self.quality_status = kw.get("quality_status", "OK")
        self.currency = kw.get("currency")
        self.product_code = kw.get("product_code", "P")
        self.called_number = kw.get("called_number", "447")
        self.msisdn = kw.get("msisdn", "447")
        self.event_date = kw.get("event_date")


def test_rateable_usage_with_no_charge_is_unrated_and_leaks_the_full_amount():
    from app.modules.rating import assurance

    verdict = assurance.classify(_Cdr(actual=None), _outcome("0.23"))
    assert verdict.status == "UNRATED"
    assert verdict.variance == Decimal("0.23")


def test_free_usage_billed_free_is_clean():
    from app.modules.rating import assurance
    from app.modules.rating.engine import RatingOutcome

    outcome = RatingOutcome(zero_rated=True, final_charge=Decimal("0"))
    assert assurance.classify(_Cdr(actual="0"), outcome).status == "ZERO_CHARGE"


def test_free_usage_that_was_billed_is_an_overcharge():
    from app.modules.rating import assurance
    from app.modules.rating.engine import RatingOutcome

    outcome = RatingOutcome(zero_rated=True, final_charge=Decimal("0"))
    assert assurance.classify(_Cdr(actual="0.50"), outcome).status == "OVERCHARGED"
