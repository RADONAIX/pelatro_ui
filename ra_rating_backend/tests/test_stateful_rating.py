"""Stateful rating: bundles, tiers, promotions. Pure logic, no database."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.modules.balances import engine as balances
from app.modules.balances.models import BalanceBucket
from app.modules.rating import engine as rating
from app.modules.rules.constants import ActionType, ExecutionStage


class _Cdr:
    """CDR stand-in with only what the engine and balance code read."""

    def __init__(self, **kw):
        self.id = kw.get("id", "cdr-1")
        self.cdr_id = kw.get("cdr_id", "CDR1")
        self.duration_seconds = kw.get("duration_seconds")
        self.usage_volume = kw.get("usage_volume")
        self.service_type = kw.get("service_type", "VOICE")
        self.product_code = kw.get("product_code", "PREPAID_A")
        self.destination_zone = kw.get("destination_zone", "LOCAL_ONNET")
        self.time_band = kw.get("time_band", "PEAK")
        self.currency = kw.get("currency", "GBP")
        self.subscriber_id = kw.get("subscriber_id", "SUB1")
        self.account_id = kw.get("account_id", "ACC1")
        self.msisdn = kw.get("msisdn", "447700900001")
        self.event_date = kw.get("event_date", date(2026, 3, 10))
        self.event_timestamp = kw.get(
            "event_timestamp", datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
        )


class _Rule:
    """Executable-rule stand-in."""

    def __init__(self, stage, actions, key="R", currency="GBP"):
        self.execution_stage = stage
        self.actions = actions
        self.rule_key = key
        self.rule_name = key
        self.currency_code = currency
        self.stage_order = 0


def _rate(unit="SECOND", rate=0.10, per=60):
    return _Rule(
        ExecutionStage.BASE_CHARGE,
        [{"action_type": ActionType.SET_RATE.value,
          "params": {"rate": rate, "unit": unit, "per_units": per}, "resolved": {}}],
        key="RATE",
    )


def _bucket(allocated=300, consumed=0, unit="MINUTE", code="VOICE_300_MIN"):
    return BalanceBucket(
        id="bucket-1", owner_key="SUB1", bundle_code=code, service_type="VOICE",
        quota_unit=unit, shared=False, period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31), reset_period="MONTHLY",
        allocated=allocated, consumed=consumed, overflow=0, consumption_count=0,
    )


# --- Period boundaries ------------------------------------------------------


@pytest.mark.parametrize(
    "period,on,start,end",
    [
        ("MONTHLY", date(2026, 3, 15), date(2026, 3, 1), date(2026, 3, 31)),
        ("MONTHLY", date(2026, 2, 15), date(2026, 2, 1), date(2026, 2, 28)),
        ("DAILY", date(2026, 3, 15), date(2026, 3, 15), date(2026, 3, 15)),
        # 2026-03-11 is a Wednesday.
        ("WEEKLY", date(2026, 3, 11), date(2026, 3, 9), date(2026, 3, 15)),
    ],
)
def test_allowance_periods_follow_the_calendar(period, on, start, end):
    assert balances.period_bounds(period, on) == (start, end)


def test_a_non_resetting_allowance_has_no_period_boundary():
    start, end = balances.period_bounds("NONE", date(2026, 3, 15))
    assert start.year == 1970 and end.year == 2999


# --- Unit conversion --------------------------------------------------------


def test_a_call_is_measured_in_the_bundles_unit():
    """Bundles are quoted in minutes; CDRs carry seconds. Comparing them
    directly would be a 60x error in the customer's favour."""
    cdr = _Cdr(duration_seconds=180)
    assert balances.to_quota_unit(cdr, "MINUTE") == Decimal(3)
    assert balances.to_quota_unit(cdr, "SECOND") == Decimal(180)


def test_data_is_measured_in_the_bundles_unit():
    cdr = _Cdr(usage_volume=1024 * 1024 * 5, service_type="DATA")
    assert balances.to_quota_unit(cdr, "MEGABYTE") == Decimal(5)


def test_one_sms_record_is_one_message():
    assert balances.to_quota_unit(_Cdr(service_type="SMS"), "MESSAGE") == Decimal(1)


def test_covered_amount_is_converted_back_to_the_rates_unit():
    # 3 minutes covered, rate quoted per second -> 180 seconds.
    assert rating._from_bundle_unit(Decimal(3), "MINUTE", "SECOND") == Decimal(180)
    assert rating._from_bundle_unit(Decimal(1), "MEGABYTE", "BYTE") == Decimal(1024 * 1024)


# --- Consumption ------------------------------------------------------------


def test_a_bundle_covers_usage_until_it_runs_out():
    engine = balances.BalanceEngine(run_id="run-1")
    bucket = _bucket(allocated=5)

    first = engine.consume(bucket, _Cdr(duration_seconds=180))  # 3 minutes
    assert first.consumed == Decimal(3)
    assert first.overflow == Decimal(0)
    assert first.balance_after == Decimal(2)

    second = engine.consume(bucket, _Cdr(duration_seconds=300))  # 5 minutes
    # Only 2 were left, so 3 spill over.
    assert second.consumed == Decimal(2)
    assert second.overflow == Decimal(3)
    assert second.balance_after == Decimal(0)

    third = engine.consume(bucket, _Cdr(duration_seconds=60))
    assert third.consumed == Decimal(0)
    assert third.overflow == Decimal(1)


def test_every_consumption_is_logged_even_when_nothing_was_covered():
    """An exhausted bundle still has to explain why the call was charged."""
    engine = balances.BalanceEngine(run_id="run-1")
    bucket = _bucket(allocated=0)
    engine.consume(bucket, _Cdr(duration_seconds=60))
    assert len(engine.ledger) == 1
    assert float(engine.ledger[0].consumed) == 0
    assert float(engine.ledger[0].overflow) == 1


def test_consumption_is_order_dependent():
    """The same calls in a different order produce different coverage once the
    allowance runs out — which is why the rating pass orders by event time."""
    def run(durations):
        engine = balances.BalanceEngine(run_id="r")
        bucket = _bucket(allocated=5)
        return [
            engine.consume(bucket, _Cdr(duration_seconds=d)).consumed for d in durations
        ]

    assert run([240, 120]) == [Decimal(4), Decimal(1)]
    assert run([120, 240]) == [Decimal(2), Decimal(3)]


def test_a_shared_bundle_is_owned_by_the_account():
    """Getting this wrong gives every member of a family plan the full allowance."""
    engine = balances.BalanceEngine(run_id="r")
    cdr = _Cdr(subscriber_id="SUB1", account_id="ACC1")
    assert engine.owner_key(cdr, shared=True) == "ACC1"
    assert engine.owner_key(cdr, shared=False) == "SUB1"


def test_owner_falls_back_when_ids_are_missing():
    engine = balances.BalanceEngine(run_id="r")
    cdr = _Cdr(subscriber_id=None, account_id=None, msisdn="447700900001")
    assert engine.owner_key(cdr, shared=False) == "447700900001"


# --- Tier ladders -----------------------------------------------------------


def test_tiers_are_ordered_with_the_open_ended_rung_last():
    tiers = rating.parse_tiers("*:0.02, 100:0, 500:0.01")
    assert [str(x.up_to) for x in tiers] == ["100", "500", "None"]


def test_tier_pricing_splits_across_rungs():
    tiers = rating.parse_tiers("100:0, 500:0.01, *:0.02")
    total, split = rating.charge_across_tiers(Decimal(0), Decimal(600), tiers, Decimal(1))
    # 100 free + 400 at 0.01 + 100 at 0.02 = 0 + 4 + 2
    assert total == Decimal(6)
    assert len(split) == 3


def test_tier_position_carries_from_prior_usage():
    """The second gigabyte of the month must not be priced as the first."""
    tiers = rating.parse_tiers("100:0, *:0.01")
    first, _ = rating.charge_across_tiers(Decimal(0), Decimal(100), tiers, Decimal(1))
    second, _ = rating.charge_across_tiers(Decimal(100), Decimal(100), tiers, Decimal(1))
    assert first == Decimal(0)
    assert second == Decimal(1)


def test_usage_above_the_top_tier_is_reported_as_unpriced_not_free():
    tiers = rating.parse_tiers("100:0.01")  # no open-ended rung
    total, split = rating.charge_across_tiers(Decimal(0), Decimal(200), tiers, Decimal(1))
    assert total == Decimal(1)
    # The overflow is flagged with a negative rate marker, not silently dropped.
    assert split[-1][1] == Decimal(-1)
    assert split[-1][0] == Decimal(100)


def test_a_counter_returns_the_total_before_this_event():
    """Returning the new total would price every event one tier too high."""
    from app.modules.balances.models import UsageCounter

    engine = balances.BalanceEngine(run_id="r")
    counter = UsageCounter(
        owner_key="SUB1", counter_key="R", service_type="DATA", unit="BYTE",
        period_start=date(2026, 3, 1), period_end=date(2026, 3, 31), total=500,
    )
    before = engine.advance_counter(counter, Decimal(200))
    assert before == Decimal(500)
    assert float(counter.total) == 700


# --- The engine, end to end -------------------------------------------------


def test_a_bundle_reduces_the_charged_quantity():
    engine = balances.BalanceEngine(run_id="r")
    bucket = _bucket(allocated=10)
    cdr = _Cdr(duration_seconds=180)  # 3 minutes
    consumption = engine.consume(bucket, cdr)

    bundle_rule = _Rule(
        ExecutionStage.BUNDLE,
        [{"action_type": ActionType.CONSUME_BUNDLE.value,
          "params": {"bundle": "VOICE_300_MIN"}, "resolved": {}}],
        key="BUNDLE",
    )
    outcome = rating.rate_cdr(
        cdr,
        {ExecutionStage.BASE_CHARGE: _rate(), ExecutionStage.BUNDLE: bundle_rule},
        bundle=consumption,
    )
    # Fully covered, so nothing is charged.
    assert outcome.final_charge == Decimal("0.00")
    assert outcome.bundle_code == "VOICE_300_MIN"
    assert outcome.bundle_consumed == Decimal(3)


def test_only_the_overflow_is_charged_when_a_bundle_runs_out():
    engine = balances.BalanceEngine(run_id="r")
    bucket = _bucket(allocated=1)  # 1 minute left
    cdr = _Cdr(duration_seconds=180)  # 3 minutes
    consumption = engine.consume(bucket, cdr)

    bundle_rule = _Rule(
        ExecutionStage.BUNDLE,
        [{"action_type": ActionType.CONSUME_BUNDLE.value,
          "params": {"bundle": "VOICE_300_MIN"}, "resolved": {}}],
        key="BUNDLE",
    )
    outcome = rating.rate_cdr(
        cdr,
        {ExecutionStage.BASE_CHARGE: _rate(), ExecutionStage.BUNDLE: bundle_rule},
        bundle=consumption,
    )
    # 1 minute covered, 2 minutes charged at 0.10/60s = 0.20
    assert outcome.final_charge == Decimal("0.20")
    assert outcome.bundle_overflow == Decimal(2)


def test_a_data_session_minimum_floors_the_quantity():
    cdr = _Cdr(service_type="DATA", usage_volume=1000, duration_seconds=None)
    rule = _Rule(
        ExecutionStage.BASE_CHARGE,
        [
            {"action_type": ActionType.SET_RATE.value,
             "params": {"rate": 1, "unit": "KILOBYTE", "per_units": 1}, "resolved": {}},
            {"action_type": ActionType.SET_MINIMUM_QUANTITY.value,
             "params": {"quantity": 100}, "resolved": {}},
        ],
        key="DATA",
    )
    outcome = rating.rate_cdr(cdr, {ExecutionStage.BASE_CHARGE: rule})
    # ~0.98 KB of real usage is floored to the 100 KB session minimum.
    assert outcome.billable_quantity == Decimal(100)


def test_a_tiered_rate_prices_from_the_carried_position():
    cdr = _Cdr(service_type="DATA", usage_volume=100, duration_seconds=None)
    rule = _Rule(
        ExecutionStage.BASE_CHARGE,
        [{"action_type": ActionType.SET_TIERED_RATE.value,
          "params": {"tiers": "100:0, *:0.01", "unit": "BYTE", "per_units": 1},
          "resolved": {}}],
        key="TIERED",
    )
    free = rating.rate_cdr(cdr, {ExecutionStage.BASE_CHARGE: rule}, tier_start=Decimal(0))
    charged = rating.rate_cdr(cdr, {ExecutionStage.BASE_CHARGE: rule}, tier_start=Decimal(100))
    assert free.final_charge == Decimal("0.00")
    assert charged.final_charge == Decimal("1.00")


def test_a_free_usage_promotion_zeroes_the_charge():
    promo = _Rule(
        ExecutionStage.PROMOTION,
        [{"action_type": ActionType.APPLY_PROMOTION.value,
          "params": {"promotion": "WELCOME"},
          "resolved": {"promotion": {"code": "WELCOME", "promotion_type": "FREE_USAGE",
                                     "value": 100}}}],
        key="PROMO",
    )
    outcome = rating.rate_cdr(
        _Cdr(duration_seconds=180),
        {ExecutionStage.BASE_CHARGE: _rate(), ExecutionStage.PROMOTION: promo},
    )
    assert outcome.final_charge == Decimal("0.00")


def test_a_percentage_promotion_reduces_the_charge():
    promo = _Rule(
        ExecutionStage.PROMOTION,
        [{"action_type": ActionType.APPLY_PROMOTION.value,
          "params": {"promotion": "HALF"},
          "resolved": {"promotion": {"code": "HALF", "promotion_type": "PERCENTAGE",
                                     "value": 50}}}],
        key="PROMO",
    )
    outcome = rating.rate_cdr(
        _Cdr(duration_seconds=180),
        {ExecutionStage.BASE_CHARGE: _rate(), ExecutionStage.PROMOTION: promo},
    )
    # 3 minutes at 0.10 = 0.30, halved.
    assert outcome.final_charge == Decimal("0.15")


def test_a_bundle_rule_with_no_resolvable_balance_says_so_in_the_trace():
    bundle_rule = _Rule(
        ExecutionStage.BUNDLE,
        [{"action_type": ActionType.CONSUME_BUNDLE.value,
          "params": {"bundle": "MISSING"}, "resolved": {}}],
        key="BUNDLE",
    )
    outcome = rating.rate_cdr(
        _Cdr(duration_seconds=60),
        {ExecutionStage.BASE_CHARGE: _rate(), ExecutionStage.BUNDLE: bundle_rule},
        bundle=None,
    )
    labels = [s.label for s in outcome.trace]
    assert "Bundle rule matched but no balance" in labels
    # The call is still charged in full — silently zero-rating it would be leakage.
    assert outcome.final_charge > 0


# --- SMS and roaming --------------------------------------------------------


def test_sms_is_charged_per_message():
    rule = _Rule(
        ExecutionStage.BASE_CHARGE,
        [{"action_type": ActionType.SET_RATE.value,
          "params": {"rate": 0.05, "unit": "MESSAGE", "per_units": 1}, "resolved": {}}],
        key="SMS",
    )
    cdr = _Cdr(service_type="SMS", duration_seconds=None, usage_volume=None)
    assert rating.rate_cdr(cdr, {ExecutionStage.BASE_CHARGE: rule}).final_charge == Decimal("0.05")


def test_a_roaming_surcharge_is_added_before_tax():
    surcharge = _Rule(
        ExecutionStage.SURCHARGE,
        [{"action_type": ActionType.ADD_SURCHARGE.value,
          "params": {"percentage": 50}, "resolved": {}}],
        key="ROAM",
    )
    outcome = rating.rate_cdr(
        _Cdr(duration_seconds=180),
        {ExecutionStage.BASE_CHARGE: _rate(), ExecutionStage.SURCHARGE: surcharge},
    )
    # 0.30 + 50% = 0.45
    assert outcome.final_charge == Decimal("0.45")
