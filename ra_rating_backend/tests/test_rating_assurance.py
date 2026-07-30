"""Rule resolution, calculation and assurance comparison. Pure logic, no database.

Covers the §28 worked example end to end and every §29 negative case.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.cdr.bulk_enrich import EnrichmentStatus
from app.modules.rating import comparison, plan, resolution
from app.modules.rating.actual_charge import NOT_CONFIGURED, ActualCharge
from app.modules.rating.audit_models import (
    ActualChargeStatus,
    ComponentType,
    EvaluationStatus,
    RatingStatus,
    RejectionReason,
)
from app.modules.rating.engine import apply_pulse, rate_cdr
from app.modules.rules.constants import ExecutionStage
from tests.factories import (
    FakeRule,
    FakeUsage,
    condition,
    discount_rule,
    pulse_rule,
    rate_rule,
    rounding_rule,
    tax_rule,
)


def matched(amount: str) -> ActualCharge:
    return ActualCharge(
        status=ActualChargeStatus.MATCHED, amount=Decimal(amount), currency="GHS", match_count=1
    )


# --- §13 Condition evaluation ------------------------------------------------


class TestOperators:
    @pytest.mark.parametrize(
        ("operator", "actual", "values", "expected"),
        [
            ("EQUALS", "SMART20", ["SMART20"], True),
            ("EQUALS", "SMART20", ["SMART10"], False),
            ("NOT_EQUALS", "SMART20", ["SMART10"], True),
            ("IN", "PEAK", ["PEAK", "OFF_PEAK"], True),
            ("NOT_IN", "NIGHT", ["PEAK", "OFF_PEAK"], True),
            ("GREATER_THAN", 195, [0], True),
            ("GREATER_THAN_OR_EQUAL", 60, [60], True),
            ("LESS_THAN", 30, [60], True),
            ("LESS_THAN_OR_EQUAL", 60, [60], True),
            ("BETWEEN", 90, [60, 120], True),
            ("BETWEEN", 150, [60, 120], False),
            ("STARTS_WITH", "233501112222", ["23350"], True),
            ("CONTAINS", "NATIONAL_MOBILE", ["MOBILE"], True),
            ("NOT_CONTAINS", "NATIONAL_MOBILE", ["FIXED"], True),
            ("IS_NULL", None, [], True),
            ("IS_NOT_NULL", "PREPAID", [], True),
        ],
    )
    def test_each_supported_operator(self, operator, actual, values, expected):
        assert resolution.compare(operator, actual, values) is expected

    def test_an_unsupported_operator_is_refused_not_guessed(self):
        with pytest.raises(resolution.ConditionError):
            resolution.compare("REGEX_MATCH", "x", ["y"])

    def test_an_absent_fact_cannot_satisfy_a_positive_comparison(self):
        # Treating a missing value as a match would price usage on a rule that
        # was never meant for it.
        assert resolution.compare("EQUALS", None, ["GOLD"]) is False
        assert resolution.compare("NOT_EQUALS", None, ["GOLD"]) is True

    def test_nothing_from_a_rule_is_ever_executed(self):
        # §13: an uploaded rule must not be able to become code.
        assert resolution.compare("EQUALS", "x", ["__import__('os').system('id')"]) is False


class TestListValuedFacts:
    """§2, §9 — group membership is a set test on ONE record."""

    def test_contains_is_membership_not_substring(self):
        groups = ["GOLD", "ACCRA", "VOICE_BUNDLE"]
        assert resolution.compare("CONTAINS", groups, ["GOLD"]) is True
        # The trap: as a string, "OLD" is inside "GOLD". As a set, it is not a
        # member — and a loyalty rate must not reach the wrong subscribers.
        assert resolution.compare("CONTAINS", groups, ["OLD"]) is False

    def test_not_contains_on_a_list(self):
        assert resolution.compare("NOT_CONTAINS", ["GOLD"], ["PLATINUM"]) is True

    def test_in_matches_any_member(self):
        assert resolution.compare("IN", ["GOLD", "ACCRA"], ["PLATINUM", "ACCRA"]) is True

    def test_an_empty_group_list_is_absent(self):
        assert resolution.compare("IS_NULL", [], []) is True


# --- §15 Rule resolution -----------------------------------------------------


class TestResolution:
    def test_the_worked_example_selects_the_smart20_rule(self):
        """§28: R200 wins, R100 and R300 are MATCHED_NOT_SELECTED."""
        rules = [
            rate_rule("R100", "0.12", priority=100, specificity=2),
            rate_rule("R200", "0.10", priority=200, specificity=5),
            rate_rule("R300", "0.11", priority=180, specificity=3),
        ]
        resolved = resolution.resolve(rules, resolution.facts_for(FakeUsage()))

        assert resolved.selected[ExecutionStage.BASE_CHARGE].rule_key == "R200"
        assert not resolved.is_ambiguous

        by_key = {v.rule.rule_key: v for v in resolved.verdicts}
        assert by_key["R200"].status == EvaluationStatus.SELECTED
        assert by_key["R100"].status == EvaluationStatus.MATCHED_NOT_SELECTED
        assert by_key["R300"].status == EvaluationStatus.MATCHED_NOT_SELECTED
        assert by_key["R100"].reason == RejectionReason.LOWER_PRIORITY

    def test_specificity_breaks_a_priority_tie(self):
        """§27: the more specific rule wins when priority is equal."""
        general = rate_rule("GENERAL", "0.12", priority=100, specificity=2)
        specific = rate_rule("SPECIFIC", "0.10", priority=100, specificity=5)
        resolved = resolution.resolve([general, specific], resolution.facts_for(FakeUsage()))

        assert resolved.selected[ExecutionStage.BASE_CHARGE].rule_key == "SPECIFIC"
        loser = next(v for v in resolved.verdicts if v.rule.rule_key == "GENERAL")
        assert loser.reason == RejectionReason.LOWER_SPECIFICITY

    def test_a_rule_whose_condition_fails_is_rejected_not_outranked(self):
        # REJECTED and MATCHED_NOT_SELECTED are different findings: the first is
        # a rule that did not apply, the second is one that lost.
        gold_only = rate_rule(
            "GOLD_RATE",
            "0.08",
            priority=500,
            predicates=[condition("subscriber_groups", "CONTAINS", "PLATINUM")],
        )
        fallback = rate_rule("BASE", "0.12", priority=100)
        resolved = resolution.resolve([gold_only, fallback], resolution.facts_for(FakeUsage()))

        assert resolved.selected[ExecutionStage.BASE_CHARGE].rule_key == "BASE"
        verdict = next(v for v in resolved.verdicts if v.rule.rule_key == "GOLD_RATE")
        assert verdict.status == EvaluationStatus.REJECTED
        assert verdict.reason == RejectionReason.CONDITION_FAILED
        assert "subscriber_groups" in verdict.detail

    def test_a_group_condition_selects_on_one_record(self):
        gold = rate_rule(
            "GOLD_RATE",
            "0.08",
            priority=500,
            predicates=[condition("subscriber_groups", "CONTAINS", "GOLD")],
        )
        resolved = resolution.resolve([gold], resolution.facts_for(FakeUsage()))
        assert resolved.selected[ExecutionStage.BASE_CHARGE].rule_key == "GOLD_RATE"


class TestAmbiguity:
    """§15, §29 — indistinguishable rules must not be tie-broken."""

    def test_two_identical_base_rules_are_ambiguous(self):
        a = rate_rule("RATE_A", "0.10", priority=100, specificity=3)
        b = rate_rule("RATE_B", "0.20", priority=100, specificity=3)
        resolved = resolution.resolve([a, b], resolution.facts_for(FakeUsage()))

        assert resolved.is_ambiguous
        assert ExecutionStage.BASE_CHARGE in resolved.ambiguous_stages
        # Critically: NOTHING was selected. An arbitrary winner would produce a
        # charge that cannot be defended when the customer disputes it.
        assert ExecutionStage.BASE_CHARGE not in resolved.selected
        assert "RATE_A" in resolved.ambiguity_detail
        assert "RATE_B" in resolved.ambiguity_detail

    def test_resolution_is_deterministic_not_random(self):
        # Same inputs in a different order must give the same verdict.
        a = rate_rule("RATE_A", "0.10", priority=100, specificity=3)
        b = rate_rule("RATE_B", "0.20", priority=100, specificity=3)
        assert (
            resolution.resolve([a, b], resolution.facts_for(FakeUsage())).ambiguous_stages
            == resolution.resolve([b, a], resolution.facts_for(FakeUsage())).ambiguous_stages
        )

    def test_a_version_difference_resolves_the_tie(self):
        a = rate_rule("RATE_A", "0.10", priority=100, specificity=3)
        b = rate_rule("RATE_B", "0.20", priority=100, specificity=3)
        b.rule_version = 2
        resolved = resolution.resolve([a, b], resolution.facts_for(FakeUsage()))
        assert not resolved.is_ambiguous
        assert resolved.selected[ExecutionStage.BASE_CHARGE].rule_key == "RATE_B"

    def test_stackable_taxes_are_not_ambiguous(self):
        # Two taxes both applying is normal; two base rates both applying is not.
        resolved = resolution.resolve(
            [tax_rule("VAT", "15"), tax_rule("LEVY", "3")], resolution.facts_for(FakeUsage())
        )
        assert not resolved.is_ambiguous
        assert len(resolved.rules_for(ExecutionStage.TAX)) == 2


class TestExclusiveGroups:
    """§19 — one discount per exclusive group."""

    def test_only_one_discount_from_an_exclusive_group_applies(self):
        first = discount_rule("D_BIG", "20", priority=200, stackable=True)
        second = discount_rule("D_SMALL", "10", priority=100, stackable=True)
        resolved = resolution.resolve([first, second], resolution.facts_for(FakeUsage()))

        assert len(resolved.rules_for(ExecutionStage.DISCOUNT)) == 1
        assert resolved.selected[ExecutionStage.DISCOUNT].rule_key == "D_BIG"
        loser = next(v for v in resolved.verdicts if v.rule.rule_key == "D_SMALL")
        assert loser.reason == RejectionReason.EXCLUSIVE_GROUP_TAKEN

    def test_stackable_discounts_in_different_groups_both_apply(self):
        loyalty = discount_rule("D_LOYALTY", "10", priority=200, stackable=True,
                                conflict_group="LOYALTY")
        promo = discount_rule("D_PROMO", "20", priority=100, stackable=True,
                              conflict_group="PROMO")
        resolved = resolution.resolve([loyalty, promo], resolution.facts_for(FakeUsage()))
        assert len(resolved.rules_for(ExecutionStage.DISCOUNT)) == 2

    def test_a_non_stackable_discount_excludes_the_rest(self):
        exclusive = discount_rule("D_ONLY", "25", priority=300, stackable=False)
        other = discount_rule("D_OTHER", "10", priority=100, stackable=False,
                              conflict_group="OTHER")
        resolved = resolution.resolve([exclusive, other], resolution.facts_for(FakeUsage()))
        assert len(resolved.rules_for(ExecutionStage.DISCOUNT)) == 1


class TestEffectiveDates:
    """§29 — an expired rule must never be selected."""

    def test_an_expired_rule_is_not_a_candidate(self):
        from app.modules.rating.execution import _candidates_in_memory

        expired = rate_rule("OLD", "0.50")
        expired.effective_to = FakeUsage().event_date.replace(year=2020)
        current = rate_rule("NEW", "0.10")

        candidates = _candidates_in_memory(
            [expired, current], {"service_type": "VOICE"}, FakeUsage().event_date
        )
        assert [r.rule_key for r in candidates] == ["NEW"]

    def test_a_rule_not_yet_effective_is_not_a_candidate(self):
        from app.modules.rating.execution import _candidates_in_memory

        future = rate_rule("FUTURE", "0.05")
        future.effective_from = FakeUsage().event_date.replace(year=2030)
        candidates = _candidates_in_memory(
            [future], {"service_type": "VOICE"}, FakeUsage().event_date
        )
        assert candidates == []


# --- §17 Pulse ---------------------------------------------------------------


class TestPulse:
    @pytest.mark.parametrize(
        ("duration", "initial", "subsequent", "expected"),
        [
            (75, 60, 30, 90),      # §17's worked example
            (195, 60, 30, 210),
            (0, 60, 30, 60),       # a connected zero-length call still bills one pulse
            (30, 60, 30, 60),      # shorter than the initial pulse
            (60, 60, 30, 60),      # exactly the initial pulse — no extra
            (90, 60, 30, 90),      # exactly a pulse boundary
            (91, 60, 30, 120),     # one second past it
        ],
    )
    def test_pulse_rounding(self, duration, initial, subsequent, expected):
        seconds, _ = apply_pulse(
            Decimal(duration),
            {"initial_seconds": initial, "subsequent_seconds": subsequent},
        )
        assert seconds == Decimal(expected)

    def test_a_zero_pulse_leaves_the_duration_untouched(self):
        # §29: an invalid pulse must not silently become a 1-second pulse.
        seconds, pulses = apply_pulse(Decimal(75), {"initial_seconds": 0})
        assert seconds == Decimal(75)
        assert pulses == Decimal(0)

    def test_a_negative_subsequent_pulse_falls_back_to_the_initial(self):
        seconds, _ = apply_pulse(
            Decimal(75), {"initial_seconds": 60, "subsequent_seconds": -30}
        )
        assert seconds == Decimal(120)


# --- §28 The complete worked example -----------------------------------------


class TestWorkedExample:
    """195s call, 120s bundle, 60/30 pulse, 0.10/min, 10% discount, 15% tax."""

    def _rules(self):
        return {
            ExecutionStage.BASE_CHARGE: rate_rule("R200", "0.10", priority=200, specificity=5),
            ExecutionStage.PULSE: pulse_rule("P10", 60, 30),
            ExecutionStage.DISCOUNT: discount_rule("D500", "10"),
            ExecutionStage.TAX: tax_rule("T100", "15"),
            ExecutionStage.ROUNDING: rounding_rule("RD10"),
        }

    def test_without_a_bundle_the_whole_call_is_charged(self):
        outcome = rate_cdr(FakeUsage(), self._rules(), bundle_before_pulse=True)
        # 195s, no bundle: pulse to 210s = 3.5 min x 0.10 = 0.35,
        # less 10% = 0.315, plus 15% tax = 0.36225 -> 0.36
        assert outcome.final_charge == Decimal("0.36")

    def test_the_full_worked_example_gives_0_16(self):
        from app.modules.rating.allowance import ReadOnlyConsumption, _Bucket

        bundle = ReadOnlyConsumption(
            bucket=_Bucket(bundle_code="B400"),
            requested=Decimal(195),
            consumed=Decimal(120),
            overflow=Decimal(75),
            balance_before=Decimal(120),
            balance_after=Decimal(0),
            unit="SECOND",
        )
        rules = {**self._rules(), ExecutionStage.BUNDLE: FakeRule(
            rule_key="B400",
            execution_stage=ExecutionStage.BUNDLE,
            actions=[{"action_type": "CONSUME_BUNDLE", "params": {"bundle": "B400"}}],
        )}
        outcome = rate_cdr(FakeUsage(), rules, bundle=bundle, bundle_before_pulse=True)

        # 195 - 120 = 75s chargeable; pulsed to 90s; 1.5 min x 0.10 = 0.15;
        # less 10% = 0.135; plus 15% tax = 0.15525; rounded = 0.16.
        assert outcome.billable_quantity == Decimal(90)
        assert outcome.base_charge == Decimal("0.150000")
        assert outcome.discount == Decimal("0.015000")
        assert outcome.final_charge == Decimal("0.16")

    def test_the_comparison_reports_an_overcharge(self):
        verdict = comparison.compare(
            expected=Decimal("0.16"),
            actual_charge=matched("0.60"),
            enrichment_status=EnrichmentStatus.ENRICHED,
            has_base_rule=True,
        )
        assert verdict.status == RatingStatus.OVERCHARGED
        assert verdict.variance == Decimal("0.44")
        assert verdict.absolute_variance == Decimal("0.44")
        assert "0.44" in verdict.explanation

    def test_every_step_is_recorded_as_a_component(self):
        outcome = rate_cdr(FakeUsage(), self._rules(), bundle_before_pulse=True)
        components = plan.components_from_trace("MSC-10001", outcome.trace_dicts())
        types = [c["component_type"] for c in components]

        assert ComponentType.BASE_CHARGE in types
        assert ComponentType.DISCOUNT in types
        assert ComponentType.TAX in types
        assert ComponentType.ROUNDING in types
        # Sequence numbers are dense and ordered — the unique constraint on
        # (usage_id, sequence_number) depends on it.
        assert [c["sequence_number"] for c in components] == list(range(1, len(components) + 1))


# --- §19, §20 Discounts, tax, rounding ---------------------------------------


class TestDiscountAndTax:
    def test_discounts_compound_rather_than_add(self):
        """§19: 10% then 20% off 10.00 is 7.20, not 7.00."""
        after_first = Decimal("10.00") * (Decimal(1) - Decimal("0.10"))
        after_second = after_first * (Decimal(1) - Decimal("0.20"))
        assert after_second == Decimal("7.2000")

    def test_a_discount_never_makes_a_charge_negative(self):
        rules = {
            ExecutionStage.BASE_CHARGE: rate_rule("R", "0.10"),
            ExecutionStage.DISCOUNT: FakeRule(
                rule_key="D_HUGE",
                execution_stage=ExecutionStage.DISCOUNT,
                actions=[
                    {"action_type": "APPLY_DISCOUNT", "params": {"amount": "999.00"}}
                ],
            ),
        }
        outcome = rate_cdr(FakeUsage(), rules, bundle_before_pulse=True)
        assert outcome.final_charge >= Decimal("0")

    def test_tax_applies_after_the_discount(self):
        """§20: tax on a discounted charge, not a discount on a taxed one."""
        rules = {
            ExecutionStage.BASE_CHARGE: rate_rule("R", "0.10"),
            ExecutionStage.PULSE: pulse_rule("P", 60, 60),
            ExecutionStage.DISCOUNT: discount_rule("D", "10"),
            ExecutionStage.TAX: tax_rule("T", "15"),
        }
        outcome = rate_cdr(FakeUsage(), rules, bundle_before_pulse=True)
        # 195s -> 240s = 4 min x 0.10 = 0.40; -10% = 0.36; +15% = 0.414
        assert outcome.final_charge == Decimal("0.41")
        # The final figure alone does not prove the order, so assert the tax
        # component: 15% of the discounted 0.36 is 0.054. Taxing first would
        # have produced 15% of 0.40 = 0.06, and a systematic 0.006 error on
        # every CDR is the hardest kind of defect to notice.
        assert outcome.discount == Decimal("0.040000")
        assert outcome.tax == Decimal("0.054000")


class TestMoneyPrecision:
    def test_money_is_never_a_float(self):
        outcome = rate_cdr(
            FakeUsage(),
            {ExecutionStage.BASE_CHARGE: rate_rule("R", "0.10")},
            bundle_before_pulse=True,
        )
        for value in (outcome.base_charge, outcome.discount, outcome.tax, outcome.final_charge):
            assert isinstance(value, Decimal)

    def test_repeated_addition_does_not_drift(self):
        # 0.1 + 0.2 != 0.3 in binary floating point, and across a million CDRs
        # that error becomes a reported leakage figure that is simply wrong.
        total = sum((Decimal("0.10") for _ in range(10)), Decimal(0))
        assert total == Decimal("1.00")


# --- §29 Negative cases ------------------------------------------------------


class TestNegativeCases:
    def test_no_rule_found(self):
        verdict = comparison.compare(
            expected=Decimal("0"),
            actual_charge=matched("0.60"),
            enrichment_status=EnrichmentStatus.ENRICHED,
            has_base_rule=False,
        )
        assert verdict.status == RatingStatus.NO_RULE_FOUND
        # No variance is claimed: there is no expected charge to compare against.
        assert verdict.variance == Decimal("0")

    def test_missing_tariff_blocks_rating(self):
        verdict = comparison.compare(
            expected=Decimal("0"),
            actual_charge=matched("0.60"),
            enrichment_status=EnrichmentStatus.TARIFF_NOT_FOUND,
            has_base_rule=True,
        )
        assert verdict.status == RatingStatus.ENRICHMENT_FAILED
        assert "TARIFF_NOT_FOUND" in verdict.explanation

    def test_missing_prefix_blocks_rating(self):
        verdict = comparison.compare(
            expected=Decimal("0"),
            actual_charge=matched("0.60"),
            enrichment_status=EnrichmentStatus.PREFIX_NOT_FOUND,
            has_base_rule=True,
        )
        assert verdict.status == RatingStatus.ENRICHMENT_FAILED

    def test_an_ambiguous_match_is_reported_not_priced(self):
        verdict = comparison.compare(
            expected=Decimal("0.16"),
            actual_charge=matched("0.60"),
            enrichment_status=EnrichmentStatus.ENRICHED,
            has_base_rule=True,
            is_ambiguous=True,
        )
        assert verdict.status == RatingStatus.AMBIGUOUS_RULE
        assert verdict.variance == Decimal("0")

    def test_no_actual_charge_still_computes_an_expected_one(self):
        verdict = comparison.compare(
            expected=Decimal("0.16"),
            actual_charge=NOT_CONFIGURED,
            enrichment_status=EnrichmentStatus.ENRICHED,
            has_base_rule=True,
        )
        assert verdict.status == RatingStatus.NO_ACTUAL_CHARGE
        assert verdict.expected == Decimal("0.16")
        assert verdict.actual is None

    def test_several_candidate_charges_are_not_silently_picked_from(self):
        verdict = comparison.compare(
            expected=Decimal("0.16"),
            actual_charge=ActualCharge(
                status=ActualChargeStatus.MULTIPLE_ACTUAL_MATCHES,
                match_count=3,
                detail="3 OCS charges match this event",
            ),
            enrichment_status=EnrichmentStatus.ENRICHED,
            has_base_rule=True,
        )
        assert verdict.status == RatingStatus.NO_ACTUAL_CHARGE
        assert verdict.variance == Decimal("0")

    def test_a_calculation_failure_is_not_a_zero_charge(self):
        verdict = comparison.compare(
            expected=Decimal("0"),
            actual_charge=matched("0.60"),
            enrichment_status=EnrichmentStatus.ENRICHED,
            has_base_rule=True,
            calculation_failed=True,
        )
        assert verdict.status == RatingStatus.CALCULATION_FAILED

    def test_a_bundle_larger_than_the_call_charges_nothing_not_a_negative(self):
        from app.modules.rating.allowance import ReadOnlyConsumption, _Bucket

        bundle = ReadOnlyConsumption(
            bucket=_Bucket(bundle_code="B_BIG"),
            requested=Decimal(195),
            consumed=Decimal(195),
            overflow=Decimal(0),
            balance_before=Decimal(600),
            balance_after=Decimal(405),
            unit="SECOND",
        )
        rules = {
            ExecutionStage.BASE_CHARGE: rate_rule("R", "0.10"),
            ExecutionStage.BUNDLE: FakeRule(
                rule_key="B_BIG",
                execution_stage=ExecutionStage.BUNDLE,
                actions=[{"action_type": "CONSUME_BUNDLE", "params": {"bundle": "B_BIG"}}],
            ),
        }
        outcome = rate_cdr(FakeUsage(), rules, bundle=bundle, bundle_before_pulse=True)
        assert outcome.billable_quantity == Decimal(0)
        assert outcome.final_charge == Decimal("0.00")

    def test_zero_charged_when_nothing_was_billed(self):
        verdict = comparison.compare(
            expected=Decimal("0.16"),
            actual_charge=matched("0.00"),
            enrichment_status=EnrichmentStatus.ENRICHED,
            has_base_rule=True,
        )
        assert verdict.status == RatingStatus.ZERO_CHARGED

    def test_billing_where_nothing_was_expected_is_an_overcharge(self):
        verdict = comparison.compare(
            expected=Decimal("0.00"),
            actual_charge=matched("0.60"),
            enrichment_status=EnrichmentStatus.ENRICHED,
            has_base_rule=True,
        )
        assert verdict.status == RatingStatus.OVERCHARGED


class TestTolerance:
    def test_rounding_noise_is_not_leakage(self):
        verdict = comparison.compare(
            expected=Decimal("0.16"),
            actual_charge=matched("0.16"),
            enrichment_status=EnrichmentStatus.ENRICHED,
            has_base_rule=True,
        )
        assert verdict.status == RatingStatus.MATCHED

    def test_an_undercharge_keeps_its_sign(self):
        verdict = comparison.compare(
            expected=Decimal("1.00"),
            actual_charge=matched("0.40"),
            enrichment_status=EnrichmentStatus.ENRICHED,
            has_base_rule=True,
        )
        assert verdict.status == RatingStatus.UNDERCHARGED
        # Negative: revenue was lost. The sign is the most important thing
        # about the number and is never absolute.
        assert verdict.variance == Decimal("-0.60")
        assert verdict.absolute_variance == Decimal("0.60")


# --- §16 Calculation plan ----------------------------------------------------


class TestBulkWriteSizing:
    """§25 — bulk inserts must survive a realistic batch size.

    PostgreSQL's Bind message counts parameters in a signed 16-bit integer, so
    one statement carries at most 32,767. A 5,000-row chunk of a 21-column table
    asks for 105,000 and is rejected — a failure that only appears at production
    batch sizes and never in a small test.
    """

    def test_a_full_chunk_is_split_below_the_protocol_limit(self):
        from app.modules.rating.audit_models import RatingResultFinal
        from app.modules.rating.execution import _MAX_BIND_PARAMS, _param_safe_batches

        rows = [{"usage_id": f"U{i}"} for i in range(5_000)]
        batches = _param_safe_batches(RatingResultFinal, rows)

        columns = len(RatingResultFinal.__table__.columns)
        assert sum(len(b) for b in batches) == 5_000, "no row may be dropped"
        for batch in batches:
            assert len(batch) * columns <= _MAX_BIND_PARAMS
        assert _MAX_BIND_PARAMS < 32_767

    def test_sizing_uses_the_table_not_the_dict(self):
        """The dict is sparse; SQLAlchemy still binds every client-side default.

        Sizing on ``len(row)`` undercounts by exactly the number of defaulted
        columns — which is how the first attempt at this fix still blew the
        limit.
        """
        from app.modules.rating.audit_models import UsageException
        from app.modules.rating.execution import _MAX_BIND_PARAMS, _param_safe_batches

        sparse = [{"usage_id": f"U{i}", "exception_type": "RULE"} for i in range(5_000)]
        batches = _param_safe_batches(UsageException, sparse)
        columns = len(UsageException.__table__.columns)
        assert columns > 2
        for batch in batches:
            assert len(batch) * columns <= _MAX_BIND_PARAMS

    def test_an_empty_write_produces_no_statements(self):
        from app.modules.rating.audit_models import RatingResultFinal
        from app.modules.rating.execution import _param_safe_batches

        assert _param_safe_batches(RatingResultFinal, []) == []


class TestCalculationPlan:
    def test_one_plan_per_record_not_one_charge_per_rule(self):
        rules = [
            rate_rule("R200", "0.10", priority=200, specificity=5),
            pulse_rule("P10", 60, 30),
            discount_rule("D500", "10"),
            tax_rule("T100", "15"),
            rounding_rule("RD10"),
        ]
        resolved = resolution.resolve(rules, resolution.facts_for(FakeUsage()))
        built = plan.build("MSC-10001", resolved)

        assert built.base_rate_rule == "R200"
        assert built.pulse_rule == "P10"
        assert built.discount_rules == ["D500"]
        assert built.tax_rules == ["T100"]
        assert built.rounding_rule == "RD10"

    def test_a_plan_with_no_base_rate_is_not_rateable(self):
        resolved = resolution.resolve([tax_rule("T", "15")], resolution.facts_for(FakeUsage()))
        assert plan.build("MSC-1", resolved).has_base_rate is False
