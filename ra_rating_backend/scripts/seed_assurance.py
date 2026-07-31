"""Seed the §28 worked example, end to end.

Creates exactly the scenario in the requirement:

    MSC-10001  233241234567 -> 233501112222, 195s, PEAK weekday
    PREPAID / SMART20, groups [GOLD, ACCRA, VOICE_BUNDLE]
    R100 general off-net 0.12/min  priority 100
    R200 SMART20 off-net peak 0.10/min  priority 200   <- should win
    R300 gold customer 0.11/min  priority 180
    B400 bundle covering 120s, D500 10% discount, T100 15% tax, RD10 rounding
    pulse 60/30
    => expected 0.16

Idempotent: every write is keyed and re-running changes nothing. Run with

    python -m scripts.seed_assurance

Then compile and activate a snapshot, and POST /api/rating/execute.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionFactory
from app.core.logging import configure_logging, get_logger
from app.modules.balances.models import BalanceBucket
from app.modules.catalog import models as cm
from app.modules.cdr.models import (
    CdrBatch,
    CdrEnriched,
    CdrLanding,
    HolidayCalendar,
    NetworkPrefix,
    SubscriberGroupMembership,
    SubscriberProduct,
)
from app.modules.msc.canonical import build_duplicate_hash, build_usage_id
from app.modules.rules import service as rule_svc
from app.modules.rules.constants import ActionType, Operator, RuleStatus, RuleType
from app.modules.rules.models import Rule, RuleAction, RuleCondition, RuleSet

log = get_logger("seed.assurance")

MSISDN = "233241234567"
CALLED = "233501112222"
EVENT_TIME = datetime(2026, 7, 30, 10, 2, 10, tzinfo=UTC)
EVENT_DAY = date(2026, 7, 30)
FROM = date(2020, 1, 1)


async def _upsert(db: AsyncSession, model, code: str, **fields):
    row = (await db.execute(select(model).where(model.code == code))).scalar_one_or_none()
    if row is None:
        row = model(code=code, **fields)
        db.add(row)
        await db.flush()
    return row


async def seed_reference(db: AsyncSession) -> None:
    """Catalogue, prefixes, time bands, holidays."""
    await _upsert(
        db, cm.Currency, "GHS", name="Ghanaian cedi", symbol="GH₵", decimals=2,
        status="ACTIVE", source_system="SEED",
    )
    await _upsert(
        db, cm.Product, "SMART20", name="Smart 20", product_type="POSTPAID_PLAN",
        account_type="PREPAID", currency_code="GHS", effective_from=FROM,
        status="ACTIVE", source_system="SEED", service_types=["VOICE"],
        # Segment is modelled in the attributes bag, which is where the
        # catalogue module already keeps non-core product facts.
        attributes={"segment": "GOLD"},
    )
    await _upsert(
        db, cm.TariffPlan, "SMART20", name="Smart 20 voice", service_type="VOICE",
        currency_code="GHS", effective_from=FROM, status="ACTIVE", source_system="SEED",
    )

    # Overlapping prefixes, so longest-prefix matching (§26) is actually
    # exercised: 233501112222 must resolve to 23350, not 233.
    national = await _upsert(
        db, cm.DestinationZone, "NATIONAL_MOBILE", name="National mobile",
        zone_type="NATIONAL_MOBILE", country_code="GH", status="ACTIVE", source_system="SEED",
    )
    generic = await _upsert(
        db, cm.DestinationZone, "NATIONAL", name="National", zone_type="NATIONAL",
        country_code="GH", status="ACTIVE", source_system="SEED",
    )
    for prefix, zone in (("233", generic), ("2335", generic), ("23350", national)):
        existing = (
            await db.execute(
                select(cm.DestinationPrefix).where(cm.DestinationPrefix.prefix == prefix)
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(cm.DestinationPrefix(prefix=prefix, zone_id=zone.id))

    # Our own network, so on-net/off-net is a real decision.
    existing = (
        await db.execute(select(NetworkPrefix).where(NetworkPrefix.prefix == "23324"))
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            NetworkPrefix(
                prefix="23324", operator_name="Home network", is_home_network=True,
                country_code="GH",
            )
        )

    await _upsert(
        db, cm.TimeBand, "PEAK", name="Peak", days=["MON", "TUE", "WED", "THU", "FRI"],
        start_time="08:00", end_time="20:00", timezone="UTC", priority=10,
        status="ACTIVE", source_system="SEED",
    )
    await _upsert(
        db, cm.TimeBand, "OFF_PEAK", name="Off peak",
        days=["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
        start_time="20:00", end_time="08:00", timezone="UTC", priority=5,
        status="ACTIVE", source_system="SEED",
    )

    # The 15% tax and the 120-second bundle from §28. Both are catalogue
    # entities the rule actions resolve against, so the rate lives in one place
    # rather than being repeated in every rule that applies it.
    await _upsert(
        db, cm.TaxRule, "GH_VAT_15", name="Ghana VAT 15%", tax_type="VAT",
        jurisdiction="GH", rate_percent=Decimal("15"), inclusive=False,
        effective_from=FROM, status="ACTIVE", source_system="SEED",
    )
    await _upsert(
        db, cm.BundleDefinition, "B400", name="Voice bundle 120 seconds",
        service_type="VOICE", quota_unit="SECOND", quota_value=Decimal(120),
        reset_period="MONTHLY", shared=False, effective_from=FROM,
        status="ACTIVE", source_system="SEED",
    )

    holiday = date(2026, 12, 25)
    existing = (
        await db.execute(
            select(HolidayCalendar).where(HolidayCalendar.holiday_date == holiday)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            HolidayCalendar(
                country_code="GH", holiday_date=holiday, name="Christmas Day",
                day_type="HOLIDAY",
            )
        )
    await db.flush()


async def seed_subscriber(db: AsyncSession) -> None:
    """One subscriber, one tariff, and the groups that must NOT multiply the CDR."""
    existing = (
        await db.execute(
            select(SubscriberProduct).where(SubscriberProduct.msisdn == MSISDN)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            SubscriberProduct(
                subscriber_id="SUB-10001", account_id="ACC-10001", msisdn=MSISDN,
                product_code="SMART20", tariff_plan_code="SMART20", account_type="PREPAID",
                effective_from=FROM, source_system="SEED",
            )
        )

    # §2: six groups, ONE rating result. The seed deliberately uses more groups
    # than the worked example needs, so a fan-out defect shows up as a wrong
    # count rather than passing unnoticed.
    for code, kind in (
        ("PREPAID", "SEGMENT"),
        ("GOLD", "LOYALTY"),
        ("ACCRA", "GEOGRAPHY"),
        ("EMPLOYEE", "STAFF"),
        ("VOICE_BUNDLE", "BUNDLE"),
        ("WEEKEND_PROMOTION", "PROMOTION"),
    ):
        await db.execute(
            pg_insert(SubscriberGroupMembership)
            .values(
                msisdn=MSISDN, subscriber_id="SUB-10001", group_code=code,
                group_type=kind, effective_from=FROM, source_system="SEED",
            )
            .on_conflict_do_nothing(
                index_elements=["msisdn", "group_code", "effective_from"]
            )
        )

    # The 120-second allowance from §28. Read-only during assurance (§18).
    bucket = (
        await db.execute(
            select(BalanceBucket).where(
                BalanceBucket.owner_key == "ACC-10001",
                BalanceBucket.bundle_code == "B400",
            )
        )
    ).scalar_one_or_none()
    if bucket is None:
        db.add(
            BalanceBucket(
                owner_key="ACC-10001", owner_type="ACCOUNT", subscriber_id="SUB-10001",
                account_id="ACC-10001", msisdn=MSISDN, bundle_code="B400",
                service_type="VOICE", quota_unit="SECOND",
                allocated=Decimal(120), consumed=Decimal(0), overflow=Decimal(0),
                period_start=FROM, period_end=date(2030, 1, 1), shared=False,
                source_system="SEED",
            )
        )
    await db.flush()


async def seed_usage(db: AsyncSession) -> str:
    """The one CDR from §28, as canonical usage."""
    batch = (
        await db.execute(
            select(CdrBatch).where(
                CdrBatch.source_system == "MSC01", CdrBatch.filename == "FILE100.dat.xml"
            )
        )
    ).scalar_one_or_none()
    if batch is None:
        batch = CdrBatch(
            filename="FILE100.dat.xml", source_system="MSC01", cdr_type="MSC",
            status="READY", event_date=EVENT_DAY, created_by=None,
        )
        db.add(batch)
        await db.flush()

    usage_id = build_usage_id("MSC01", "FILE100.dat.xml", "3563")
    existing = (
        await db.execute(select(CdrEnriched).where(CdrEnriched.usage_id == usage_id))
    ).scalar_one_or_none()
    if existing is not None:
        # Re-running the seed must not create a second copy of the same call.
        existing.processing_status = "PENDING"
        await db.flush()
        return usage_id

    duplicate_hash = build_duplicate_hash(
        subscriber_msisdn=MSISDN, called_number=CALLED, event_start_time=EVENT_TIME,
        duration_seconds=195, call_reference="03EA00EAB6B2",
    )
    landing = CdrLanding(
        batch_id=batch.id, row_number=3563, record_hash=duplicate_hash,
        payload={
            "record_kind": "moCallRecord", "record_number": "3563",
            "served_msisdn": MSISDN, "called_number": CALLED, "call_duration": "195",
        },
        status="LOADED",
    )
    db.add(landing)
    await db.flush()

    db.add(
        CdrEnriched(
            batch_id=batch.id, landing_id=landing.id, usage_id=usage_id,
            source_file="FILE100.dat.xml", source_record_number="3563",
            duplicate_hash=duplicate_hash, cdr_id=usage_id,
            msisdn=MSISDN, imsi="620010123456789", service_type="VOICE",
            call_direction="MO", event_timestamp=EVENT_TIME, event_date=EVENT_DAY,
            duration_seconds=Decimal(195), calling_number=MSISDN, called_number=CALLED,
            call_reference="03EA00EAB6B2", currency="GHS", source_system="MSC01",
            # No actual charge: an MSC record does not carry one (§21).
            actual_charge=None,
            enrichment_status="PENDING", processing_status="PENDING", quality_status="OK",
        )
    )
    await db.flush()
    return usage_id


_RULES = [
    {
        "rule_key": "R100_GENERAL_OFFNET",
        "name": "General prepaid off-net voice rate",
        "rule_type": RuleType.BASE_TARIFF,
        "priority": 100,
        "conditions": [
            ("service_type", Operator.EQUALS, ["VOICE"]),
            ("account_type", Operator.EQUALS, ["PREPAID"]),
        ],
        "actions": [
            (ActionType.SET_RATE,
             {"rate": 0.12, "unit": "SECOND", "per_units": 60, "currency": "GHS"}),
        ],
    },
    {
        # The winner: most specific AND highest priority.
        "rule_key": "R200_SMART20_OFFNET_PEAK",
        "name": "SMART20 off-net peak rate",
        "rule_type": RuleType.BASE_TARIFF,
        "priority": 200,
        "conditions": [
            ("service_type", Operator.EQUALS, ["VOICE"]),
            ("account_type", Operator.EQUALS, ["PREPAID"]),
            ("tariff_plan", Operator.EQUALS, ["SMART20"]),
            ("destination_zone", Operator.EQUALS, ["NATIONAL_MOBILE"]),
            ("time_band", Operator.EQUALS, ["PEAK"]),
        ],
        "actions": [
            (ActionType.SET_RATE,
             {"rate": 0.10, "unit": "SECOND", "per_units": 60, "currency": "GHS"}),
        ],
    },
    {
        "rule_key": "R300_GOLD_CUSTOMER",
        "name": "Gold customer voice rate",
        "rule_type": RuleType.BASE_TARIFF,
        "priority": 180,
        "conditions": [
            ("service_type", Operator.EQUALS, ["VOICE"]),
            # Group membership as a CONTAINS on the list — one record, not six.
            ("subscriber_groups", Operator.CONTAINS, ["GOLD"]),
        ],
        "actions": [
            (ActionType.SET_RATE,
             {"rate": 0.11, "unit": "SECOND", "per_units": 60, "currency": "GHS"}),
        ],
    },
    {
        "rule_key": "RD10_VOICE_PULSE",
        "name": "Voice pulse 60/30",
        "rule_type": RuleType.PULSE,
        "priority": 400,
        "conditions": [("service_type", Operator.EQUALS, ["VOICE"])],
        "actions": [
            (ActionType.SET_PULSE, {"initial_seconds": 60, "subsequent_seconds": 30}),
        ],
    },
    {
        # §18: the allowance reduces the chargeable quantity. It never produces
        # a separate charge of its own.
        "rule_key": "B400_VOICE_BUNDLE",
        "name": "Voice bundle 120 seconds",
        "rule_type": RuleType.BUNDLE,
        "priority": 450,
        "conditions": [
            ("service_type", Operator.EQUALS, ["VOICE"]),
            ("subscriber_groups", Operator.CONTAINS, ["VOICE_BUNDLE"]),
        ],
        "actions": [(ActionType.CONSUME_BUNDLE, {"bundle": "B400"})],
    },
    {
        "rule_key": "T100_VOICE_VAT",
        "name": "Voice VAT 15%",
        "rule_type": RuleType.TAX,
        "priority": 200,
        "conditions": [("service_type", Operator.EQUALS, ["VOICE"])],
        "actions": [(ActionType.APPLY_TAX, {"tax_rule": "GH_VAT_15"})],
    },
    {
        "rule_key": "D500_GOLD_DISCOUNT",
        "name": "Gold 10% voice discount",
        "rule_type": RuleType.DISCOUNT,
        "priority": 300,
        "conditions": [
            ("service_type", Operator.EQUALS, ["VOICE"]),
            ("subscriber_groups", Operator.CONTAINS, ["GOLD"]),
        ],
        "actions": [(ActionType.APPLY_DISCOUNT, {"percentage": 10})],
    },
]


async def seed_rules(db: AsyncSession) -> None:
    rule_set = (
        await db.execute(select(RuleSet).where(RuleSet.code == "GH_VOICE_ASSURANCE_2026"))
    ).scalar_one_or_none()
    if rule_set is None:
        rule_set = RuleSet(
            code="GH_VOICE_ASSURANCE_2026",
            name="Ghana voice assurance 2026",
            description="Worked example rules for the rating assurance batch (§28).",
            source_system="SEED_ASSURANCE",
        )
        db.add(rule_set)
        await db.flush()

    for spec in _RULES:
        existing = await rule_svc.latest_version(db, spec["rule_key"])
        if existing is not None:
            # Specificity is derived from the attribute registry, so a rule
            # seeded before an attribute was registered carries a stale score.
            # Recomputing on every run keeps the stored value and the registry
            # in agreement — a drifted score silently changes which rule wins.
            recomputed = rule_svc.compute_specificity(existing.conditions)
            if existing.specificity != recomputed:
                log.info(
                    "specificity_refreshed",
                    rule_key=existing.rule_key,
                    was=existing.specificity,
                    now=recomputed,
                )
                existing.specificity = recomputed
            continue
        rule = Rule(
            rule_key=spec["rule_key"],
            version=1,
            name=spec["name"],
            description=spec["name"],
            rule_type=spec["rule_type"].value,
            execution_stage=rule_svc.RULE_TYPE_STAGE[spec["rule_type"]],
            service_type="VOICE",
            category="Voice assurance",
            rule_set_id=rule_set.id,
            priority=spec["priority"],
            effective_from=FROM,
            currency_code="GHS",
            status=RuleStatus.DRAFT.value,
            source_system="SEED_ASSURANCE",
            owner="Rating Assurance",
            change_comment="Seeded §28 worked example.",
        )
        rule.conditions = [
            RuleCondition(
                sequence=i, group_index=0, attribute=attribute,
                operator=operator.value, values=values,
            )
            for i, (attribute, operator, values) in enumerate(spec["conditions"])
        ]
        rule.actions = [
            RuleAction(sequence=i, action_type=action.value, params=params)
            for i, (action, params) in enumerate(spec["actions"])
        ]
        # Computed, never author-set — two hand-tuned knobs for one job is how a
        # rule estate becomes unpredictable.
        rule.specificity = rule_svc.compute_specificity(rule.conditions)
        db.add(rule)
        await db.flush()
        await rule_svc.record_audit(
            db, rule, action="created", actor_id=None, actor_name="seed",
            to_status=rule.status, comment="Seeded §28 worked example.",
        )


async def main() -> None:
    configure_logging(level="INFO", json_logs=False)
    async with SessionFactory() as db:
        await seed_reference(db)
        await seed_subscriber(db)
        usage_id = await seed_usage(db)
        await seed_rules(db)
        await db.commit()
    log.info("assurance_seed_complete", usage_id=usage_id)
    print(f"Seeded the §28 worked example. usage_id = {usage_id}")
    print("Next: approve the rules, compile and activate a snapshot, then")
    print("      POST /api/rating/execute")


if __name__ == "__main__":
    asyncio.run(main())
