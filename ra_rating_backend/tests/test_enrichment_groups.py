"""Enrichment, and the rule that a subscriber's groups never multiply a CDR.

This is the requirement's central constraint (§2, §9, §29). A subscriber in 25
groups must produce ONE enriched record, ONE rating context and ONE result. The
alternative — a join that fans the CDR out per group — inflates both expected
revenue and reported leakage by the group count, and the damage looks like a
pricing defect rather than the modelling defect it is.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from app.modules.cdr import bulk_enrich
from app.modules.cdr.bulk_enrich import (
    BatchReference,
    DayType,
    EnrichmentStatus,
    NetworkRelation,
)
from app.modules.cdr.enrich import TimeBandEntry
from app.modules.cdr.models import SubscriberGroupMembership, SubscriberProduct
from tests.factories import FakeUsage

EVENT_DAY = date(2026, 7, 30)


def membership(code: str, *, frm=date(2020, 1, 1), to=None) -> SubscriberGroupMembership:
    return SubscriberGroupMembership(
        msisdn="233241234567", group_code=code, effective_from=frm, effective_to=to
    )


def subscription(**overrides) -> SubscriberProduct:
    defaults = {
        "subscriber_id": "SUB-1",
        "account_id": "ACC-1",
        "msisdn": "233241234567",
        "product_code": "SMART20",
        "tariff_plan_code": "SMART20",
        "account_type": "PREPAID",
        "effective_from": date(2020, 1, 1),
        "effective_to": None,
    }
    return SubscriberProduct(**{**defaults, **overrides})


def reference(*, groups=(), subscriptions=(), prefixes=None, holidays=None) -> BatchReference:
    ref = BatchReference()
    ref.prefixes = prefixes if prefixes is not None else [
        # Deliberately overlapping, shortest last — longest-prefix matching (§26)
        # must pick 23350, not 233.
        ("23350", "NATIONAL_MOBILE", "NATIONAL_MOBILE"),
        ("2335", "NATIONAL", "NATIONAL"),
        ("233", "NATIONAL", "NATIONAL"),
    ]
    ref.prefixes.sort(key=lambda e: len(e[0]), reverse=True)
    ref.home_prefixes = ["23324"]
    ref.time_bands = [
        TimeBandEntry(
            code="PEAK",
            days=frozenset({"MON", "TUE", "WED", "THU", "FRI"}),
            start=time(8, 0),
            end=time(20, 0),
            timezone="UTC",
            priority=10,
        )
    ]
    ref.holidays = holidays or {}
    ref.known_products = {"SMART20"}
    ref.known_tariffs = {"SMART20"}
    ref.product_account_type = {"SMART20": "PREPAID"}
    ref.product_segment = {"SMART20": "GOLD"}
    for m in groups:
        ref.memberships.setdefault(m.msisdn, []).append(m)
    for s in subscriptions:
        ref.subscriptions.setdefault(s.msisdn, []).append(s)
    return ref


class TestGroupsDoNotDuplicate:
    """§29: a subscriber in 25 groups gets ONE record."""

    def test_twenty_five_groups_produce_one_enriched_record(self):
        groups = [membership(f"GROUP_{i:02d}") for i in range(25)]
        ref = reference(groups=groups, subscriptions=[subscription()])

        result = bulk_enrich.assign(FakeUsage(), ref)

        # assign() returns ONE dict. There is no code path that can return a
        # list, which is the structural guarantee — not a runtime check.
        assert isinstance(result, dict)
        assert len(result["subscriber_groups"]) == 25
        assert result["usage_id"] == "MSC-10001"

    def test_the_groups_land_as_a_list_on_the_single_row(self):
        ref = reference(
            groups=[membership("GOLD"), membership("ACCRA"), membership("VOICE_BUNDLE")],
            subscriptions=[subscription()],
        )
        result = bulk_enrich.assign(FakeUsage(), ref)
        assert result["subscriber_groups"] == ["ACCRA", "GOLD", "VOICE_BUNDLE"]

    def test_group_count_does_not_change_the_rating_context(self):
        """§11: the context key must not include the group list.

        If it did, every distinct combination of groups would be its own
        context, the collapse from a million CDRs to a few thousand rule
        decisions would disappear, and the batch would stop being tractable.
        """
        few = bulk_enrich.assign(
            FakeUsage(),
            reference(groups=[membership("GOLD")], subscriptions=[subscription()]),
        )
        many = bulk_enrich.assign(
            FakeUsage(),
            reference(
                groups=[membership(f"G{i}") for i in range(25)],
                subscriptions=[subscription()],
            ),
        )
        assert few["context_hash"] == many["context_hash"]

    def test_duplicate_group_codes_are_collapsed(self):
        ref = reference(
            groups=[membership("GOLD"), membership("GOLD", frm=date(2021, 1, 1))],
            subscriptions=[subscription()],
        )
        assert bulk_enrich.assign(FakeUsage(), ref)["subscriber_groups"] == ["GOLD"]

    def test_the_group_list_is_ordered_deterministically(self):
        # An unordered array would make two identical situations produce two
        # different context keys.
        a = reference(
            groups=[membership("ZEBRA"), membership("ALPHA")], subscriptions=[subscription()]
        )
        b = reference(
            groups=[membership("ALPHA"), membership("ZEBRA")], subscriptions=[subscription()]
        )
        assert (
            bulk_enrich.assign(FakeUsage(), a)["subscriber_groups"]
            == bulk_enrich.assign(FakeUsage(), b)["subscriber_groups"]
        )


class TestEffectiveDating:
    """§8: enrichment is as-of the event, never 'the latest record'."""

    def test_a_group_that_had_expired_is_excluded(self):
        ref = reference(
            groups=[
                membership("GOLD"),
                membership("LAPSED", to=date(2025, 1, 1)),
            ],
            subscriptions=[subscription()],
        )
        assert bulk_enrich.assign(FakeUsage(), ref)["subscriber_groups"] == ["GOLD"]

    def test_a_group_that_had_not_started_is_excluded(self):
        ref = reference(
            groups=[membership("GOLD"), membership("FUTURE", frm=date(2030, 1, 1))],
            subscriptions=[subscription()],
        )
        assert bulk_enrich.assign(FakeUsage(), ref)["subscriber_groups"] == ["GOLD"]

    def test_the_tariff_in_force_at_the_event_is_used(self):
        # Rating a July call against an August tariff is a classic false
        # exception — the tariff changed, the call did not.
        old = subscription(tariff_plan_code="OLD_PLAN", effective_to=date(2026, 7, 29))
        new = subscription(tariff_plan_code="SMART20", effective_from=date(2026, 7, 30))
        ref = reference(subscriptions=[old, new])
        ref.known_tariffs = {"OLD_PLAN", "SMART20"}
        assert bulk_enrich.assign(FakeUsage(), ref)["tariff_plan_code"] == "SMART20"


class TestEnrichmentStatuses:
    """§10."""

    def test_a_fully_enriched_record_is_rateable(self):
        ref = reference(groups=[membership("GOLD")], subscriptions=[subscription()])
        result = bulk_enrich.assign(FakeUsage(), ref)
        assert result["enrichment_status"] == EnrichmentStatus.ENRICHED
        assert result["rateable"] is True

    def test_an_unknown_subscriber_is_reported(self):
        result = bulk_enrich.assign(FakeUsage(), reference())
        assert result["enrichment_status"] == EnrichmentStatus.SUBSCRIBER_NOT_FOUND
        assert result["rateable"] is False

    def test_a_missing_tariff_is_reported(self):
        ref = reference(subscriptions=[subscription(tariff_plan_code=None)])
        result = bulk_enrich.assign(FakeUsage(), ref)
        assert result["enrichment_status"] == EnrichmentStatus.TARIFF_NOT_FOUND
        assert result["rateable"] is False

    def test_two_live_tariffs_are_refused_not_resolved(self):
        # §10: which of two prices is correct is a data question with a real
        # answer. Picking one replaces it with a confident guess.
        ref = reference(
            subscriptions=[
                subscription(tariff_plan_code="SMART20"),
                subscription(tariff_plan_code="SMART50"),
            ]
        )
        result = bulk_enrich.assign(FakeUsage(), ref)
        assert result["enrichment_status"] == EnrichmentStatus.MULTIPLE_ACTIVE_TARIFFS
        assert result["tariff_plan_code"] is None
        assert result["rateable"] is False

    def test_an_unmatched_prefix_is_reported(self):
        ref = reference(prefixes=[], subscriptions=[subscription()])
        result = bulk_enrich.assign(FakeUsage(), ref)
        assert result["enrichment_status"] == EnrichmentStatus.PREFIX_NOT_FOUND


class TestLongestPrefix:
    """§26."""

    def test_the_longest_configured_prefix_wins(self):
        ref = reference(subscriptions=[subscription()])
        # 233501112222 matches 233, 2335 and 23350. Only 23350 is right.
        assert ref.destination("233501112222") == ("NATIONAL_MOBILE", "NATIONAL_MOBILE")

    def test_a_shorter_number_falls_back_to_the_shorter_prefix(self):
        ref = reference(subscriptions=[subscription()])
        assert ref.destination("2334000000")[0] == "NATIONAL"

    def test_an_unknown_number_matches_nothing(self):
        ref = reference(subscriptions=[subscription()])
        assert ref.destination("447700900123") == (None, None)


class TestNetworkRelation:
    def test_a_home_prefix_is_on_net(self):
        ref = reference(subscriptions=[subscription()])
        usage = FakeUsage(called_number="233241111111")
        result = bulk_enrich.assign(usage, ref)
        assert result["network_relation"] == NetworkRelation.ON_NET

    def test_another_operators_prefix_is_off_net(self):
        ref = reference(subscriptions=[subscription()])
        result = bulk_enrich.assign(FakeUsage(), ref)
        assert result["network_relation"] == NetworkRelation.OFF_NET

    def test_a_terminating_call_is_enriched_against_the_caller(self):
        ref = reference(subscriptions=[subscription()])
        usage = FakeUsage(
            call_direction="MT", calling_number="233501112222", called_number="233241234567"
        )
        result = bulk_enrich.assign(usage, ref)
        assert result["destination_zone"] == "NATIONAL_MOBILE"


class TestDayType:
    def test_a_weekday_is_a_weekday(self):
        # 2026-07-30 is a Thursday.
        assert reference().day_type(EVENT_DAY) == DayType.WEEKDAY

    def test_a_saturday_is_a_weekend(self):
        assert reference().day_type(date(2026, 8, 1)) == DayType.WEEKEND

    def test_a_public_holiday_overrides_the_weekday(self):
        # A holiday priced as an ordinary Tuesday puts a variance on every call
        # that day — a large, alarming and entirely artificial spike.
        ref = reference(holidays={EVENT_DAY: DayType.HOLIDAY})
        assert ref.day_type(EVENT_DAY) == DayType.HOLIDAY


@pytest.mark.asyncio
class TestGroupAggregationInSql:
    """§9: the aggregation itself, against Postgres."""

    async def test_group_membership_aggregates_to_one_row_per_subscriber(self, db_session):
        msisdn = "233900000001"
        for index in range(25):
            db_session.add(
                SubscriberGroupMembership(
                    msisdn=msisdn,
                    group_code=f"AGG_TEST_{index:02d}",
                    effective_from=date(2020, 1, 1),
                )
            )
        await db_session.flush()

        counts = await bulk_enrich.group_counts(db_session, {msisdn}, EVENT_DAY)

        # ONE row back, carrying a count of 25 — not 25 rows.
        assert list(counts) == [msisdn]
        assert counts[msisdn] == 25
        await db_session.rollback()
