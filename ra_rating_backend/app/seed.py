"""Idempotent reference-data seed for the `rating` schema.

Run once after migrating:  ``python -m app.seed``

Everything is upserted by ``code``, so re-running is safe and never duplicates.
The sample rules reproduce the worked example from the product requirement
(125s call, £0.10/60s, 60s pulse, 10% discount, 15% VAT → £0.31) so the very
first thing a reviewer sees in the UI is a rule they can reason about.
"""

from __future__ import annotations

import asyncio
from datetime import date, time
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionFactory
from app.core.logging import configure_logging, get_logger
from app.core.rbac import DEFAULT_ROLE_PERMISSIONS
from app.modules.access.models import RatingRolePermission
from app.modules.catalog import models as cm
from app.modules.rules import service as rule_svc
from app.modules.rules.constants import ActionType, Operator, RuleStatus, RuleType
from app.modules.rules.models import Rule, RuleAction, RuleCondition, RuleSet, RuleTemplate
from app.modules.rules.vocabulary.sync import sync_vocabulary
from app.modules.tenancy import context as tenant_context

log = get_logger("seed")

_TODAY = date(2026, 1, 1)


async def _upsert(db: AsyncSession, model: type[Any], code: str, **fields: Any) -> Any:
    obj = (await db.execute(select(model).where(model.code == code))).scalar_one_or_none()
    if obj is None:
        obj = model(code=code, **fields)
        db.add(obj)
        await db.flush()
        return obj
    for key, value in fields.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def seed_permissions(db: AsyncSession) -> None:
    for role, matrix in DEFAULT_ROLE_PERMISSIONS.items():
        row = await db.get(RatingRolePermission, str(role))
        if row is None:
            db.add(RatingRolePermission(role_slug=str(role), permissions=dict(matrix)))
    await db.flush()


async def seed_catalog(db: AsyncSession) -> dict[str, Any]:
    refs: dict[str, Any] = {}

    for code, name, symbol, decimals in (
        ("GBP", "Pound Sterling", "£", 2),
        ("USD", "US Dollar", "$", 2),
        ("EUR", "Euro", "€", 2),
        ("INR", "Indian Rupee", "₹", 2),
    ):
        await _upsert(db, cm.Currency, code, name=name, symbol=symbol, decimals=decimals,
                      source_system="SEED")

    for code, name, mode, decimals in (
        ("ROUND_2DP_HALF_UP", "Round to 2dp (half up)", "HALF_UP", 2),
        ("ROUND_2DP_CEIL", "Round up to 2dp", "CEILING", 2),
        ("ROUND_4DP_HALF_UP", "Round to 4dp (half up)", "HALF_UP", 4),
    ):
        await _upsert(db, cm.RoundingRule, code, name=name, mode=mode, decimals=decimals,
                      source_system="SEED")

    for code, name, tax_type, rate, jurisdiction in (
        ("VAT_STANDARD", "VAT — standard rate", "VAT", Decimal("20.0000"), "GB"),
        ("VAT_REDUCED", "VAT — reduced rate", "VAT", Decimal("15.0000"), "GB"),
        ("VAT_ZERO", "VAT — zero rate", "VAT", Decimal("0.0000"), "GB"),
    ):
        await _upsert(db, cm.TaxRule, code, name=name, tax_type=tax_type, rate_percent=rate,
                      jurisdiction=jurisdiction, effective_from=_TODAY, source_system="SEED")

    for code, name, service_type, unit in (
        ("VOICE", "Voice call", "VOICE", "SECOND"),
        ("SMS", "Short message", "SMS", "MESSAGE"),
        ("DATA", "Data session", "DATA", "BYTE"),
    ):
        await _upsert(db, cm.ServiceDefinition, code, name=name, service_type=service_type,
                      usage_unit=unit, source_system="SEED")

    # --- Time bands ---------------------------------------------------------
    weekdays = ["MON", "TUE", "WED", "THU", "FRI"]
    await _upsert(db, cm.TimeBand, "PEAK", name="Peak", days=weekdays,
                  start_time=time(8, 0), end_time=time(19, 0), timezone="Europe/London",
                  priority=200, source_system="SEED",
                  description="Weekday business hours.")
    await _upsert(db, cm.TimeBand, "OFF_PEAK", name="Off-peak", days=weekdays,
                  start_time=time(19, 0), end_time=time(8, 0), timezone="Europe/London",
                  priority=100, source_system="SEED",
                  description="Weekday evenings and overnight — wraps midnight.")
    await _upsert(db, cm.TimeBand, "WEEKEND", name="Weekend", days=["SAT", "SUN"],
                  start_time=time(0, 0), end_time=time(23, 59), timezone="Europe/London",
                  priority=150, source_system="SEED")

    # --- Destination zones + prefixes ---------------------------------------
    zones = {
        "LOCAL_ONNET": ("On-net (same network)", "ONNET", "GB", ["4477", "4478"]),
        "LOCAL_OFFNET": ("Off-net (other UK mobile)", "OFFNET", "GB", ["4479", "4474"]),
        "NATIONAL_FIXED": ("UK fixed line", "NATIONAL", "GB", ["4420", "44161", "44131"]),
        "INTERNATIONAL": ("International", "INTERNATIONAL", None, ["001", "0033", "0049", "0091"]),
        "PREMIUM": ("Premium rate", "PREMIUM", "GB", ["4490", "4491"]),
        "EMERGENCY": ("Emergency services", "EMERGENCY", "GB", ["999", "112"]),
    }
    for code, (name, zone_type, country, prefixes) in zones.items():
        zone = await _upsert(db, cm.DestinationZone, code, name=name, zone_type=zone_type,
                             country_code=country, source_system="SEED")
        refs[f"zone:{code}"] = zone
        existing = {
            p for (p,) in (
                await db.execute(
                    select(cm.DestinationPrefix.prefix).where(
                        cm.DestinationPrefix.zone_id == zone.id
                    )
                )
            ).all()
        }
        for prefix in prefixes:
            if prefix not in existing:
                db.add(cm.DestinationPrefix(zone_id=zone.id, prefix=prefix))
    await db.flush()

    for code, name, service_type in (
        ("VOICE_STANDARD", "Standard voice", "VOICE"),
        ("VOICE_PREMIUM", "Premium voice", "VOICE"),
        ("SMS_STANDARD", "Standard SMS", "SMS"),
        ("DATA_STANDARD", "Standard data", "DATA"),
    ):
        await _upsert(db, cm.RatingGroup, code, name=name, service_type=service_type,
                      source_system="SEED")

    # --- Products, offers, tariff plans -------------------------------------
    prepaid = await _upsert(
        db, cm.Product, "PREPAID_A", name="Prepaid A", account_type="PREPAID",
        service_types=["VOICE", "SMS", "DATA"], currency_code="GBP",
        effective_from=_TODAY, source_system="SEED",
        description="Entry-level prepaid tariff.",
    )
    postpaid = await _upsert(
        db, cm.Product, "POSTPAID_A", name="Postpaid A", account_type="POSTPAID",
        service_types=["VOICE", "SMS", "DATA"], currency_code="GBP",
        effective_from=_TODAY, source_system="SEED",
        description="Standard postpaid tariff.",
    )
    refs["product:PREPAID_A"] = prepaid
    refs["product:POSTPAID_A"] = postpaid

    offer = await _upsert(db, cm.Offer, "PREPAID_A_LOYALTY", name="Prepaid A loyalty discount",
                          product_id=prepaid.id, exclusive=True, effective_from=_TODAY,
                          source_system="SEED")
    refs["offer:PREPAID_A_LOYALTY"] = offer

    plan = await _upsert(db, cm.TariffPlan, "PREPAID_A_VOICE", name="Prepaid A — voice",
                         product_id=prepaid.id, service_type="VOICE", currency_code="GBP",
                         effective_from=_TODAY, source_system="SEED")
    refs["plan:PREPAID_A_VOICE"] = plan

    await _upsert(db, cm.DiscountDefinition, "LOYALTY_10PC", name="Loyalty 10%",
                  discount_type="PERCENTAGE", value=Decimal("10"), pre_tax=True,
                  effective_from=_TODAY, source_system="SEED")
    await _upsert(db, cm.BundleDefinition, "VOICE_300_MIN", name="300 free minutes",
                  service_type="VOICE", quota_unit="MINUTE", quota_value=Decimal("300"),
                  reset_period="MONTHLY", shared=False, effective_from=_TODAY,
                  source_system="SEED")
    await _upsert(db, cm.Promotion, "WELCOME_FREE_ONNET", name="Welcome — free on-net calls",
                  promotion_type="FREE_USAGE", value=Decimal("100"), exclusive=True,
                  effective_from=_TODAY, source_system="SEED")

    return refs


async def seed_templates(db: AsyncSession) -> None:
    templates = [
        {
            "code": "VOICE_BASE_TARIFF",
            "name": "Voice — base tariff by destination and time band",
            "description": "Per-minute voice rate for one destination zone in one time band.",
            "service_type": "VOICE",
            "rule_type": RuleType.BASE_TARIFF.value,
            "payload": {
                "conditions": [
                    {"attribute": "service_type", "operator": Operator.EQUALS.value,
                     "values": ["VOICE"], "group_index": 0},
                    {"attribute": "destination_zone", "operator": Operator.EQUALS.value,
                     "values": ["LOCAL_ONNET"], "group_index": 0},
                    {"attribute": "time_band", "operator": Operator.EQUALS.value,
                     "values": ["PEAK"], "group_index": 0},
                ],
                "actions": [
                    {"action_type": ActionType.SET_RATE.value,
                     "params": {"rate": 0.10, "unit": "SECOND", "per_units": 60,
                                "currency": "GBP"}},
                ],
            },
        },
        {
            "code": "VOICE_PULSE_60",
            "name": "Voice — 60 second pulse",
            "description": "Rounds billable duration up to whole minutes.",
            "service_type": "VOICE",
            "rule_type": RuleType.PULSE.value,
            "payload": {
                "conditions": [
                    {"attribute": "service_type", "operator": Operator.EQUALS.value,
                     "values": ["VOICE"], "group_index": 0},
                ],
                "actions": [
                    {"action_type": ActionType.SET_PULSE.value,
                     "params": {"initial_seconds": 60, "subsequent_seconds": 60}},
                ],
            },
        },
        {
            "code": "SMS_FLAT_RATE",
            "name": "SMS — flat per-message rate",
            "description": "One price per message to a destination zone.",
            "service_type": "SMS",
            "rule_type": RuleType.BASE_TARIFF.value,
            "payload": {
                "conditions": [
                    {"attribute": "service_type", "operator": Operator.EQUALS.value,
                     "values": ["SMS"], "group_index": 0},
                ],
                "actions": [
                    {"action_type": ActionType.SET_RATE.value,
                     "params": {"rate": 0.05, "unit": "MESSAGE", "per_units": 1,
                                "currency": "GBP"}},
                ],
            },
        },
        {
            "code": "STANDARD_VAT",
            "name": "Tax — standard VAT",
            "description": "Applies the standard VAT rate to the discounted charge.",
            "service_type": "ANY",
            "rule_type": RuleType.TAX.value,
            "payload": {
                "conditions": [],
                "actions": [
                    {"action_type": ActionType.APPLY_TAX.value,
                     "params": {"tax_rule": "VAT_STANDARD"}},
                ],
            },
        },
        {
            "code": "EMERGENCY_ZERO_CHARGE",
            "name": "Emergency calls — zero charge",
            "description": "Never charge for calls to emergency services.",
            "service_type": "VOICE",
            "rule_type": RuleType.ZERO_RATE.value,
            "payload": {
                "conditions": [
                    {"attribute": "destination_zone", "operator": Operator.EQUALS.value,
                     "values": ["EMERGENCY"], "group_index": 0},
                ],
                "actions": [{"action_type": ActionType.SET_ZERO_CHARGE.value, "params": {}}],
            },
        },
    ]
    for tpl in templates:
        existing = (
            await db.execute(select(RuleTemplate).where(RuleTemplate.code == tpl["code"]))
        ).scalar_one_or_none()
        if existing is None:
            db.add(RuleTemplate(**tpl, is_system=True))
    await db.flush()


async def seed_rules(db: AsyncSession, refs: dict[str, Any]) -> None:
    """Three worked example rules, matching requirement §15.

    Together they rate a 125-second peak on-net call on PREPAID_A:
        pulse 60s -> 3 units x GBP 0.10 = 0.30 -> less 10% = 0.27
        -> plus 15% VAT = 0.3105 -> rounded 0.31
    """
    rule_set = (
        await db.execute(select(RuleSet).where(RuleSet.code == "PREPAID_A_VOICE_2026"))
    ).scalar_one_or_none()
    if rule_set is None:
        rule_set = RuleSet(
            code="PREPAID_A_VOICE_2026",
            name="Prepaid A voice tariff 2026",
            description="Baseline voice charging rules for the Prepaid A product.",
            source_system="SEED",
        )
        db.add(rule_set)
        await db.flush()

    product_id = refs["product:PREPAID_A"].id
    plan_id = refs["plan:PREPAID_A_VOICE"].id

    specs = [
        {
            "rule_key": "PREPAID_A_VOICE_ONNET_PEAK",
            "name": "Prepaid A — on-net peak voice rate",
            "description": "£0.10 per 60 seconds for on-net calls during the peak band.",
            "rule_type": RuleType.BASE_TARIFF,
            "priority": 500,
            "conditions": [
                ("service_type", Operator.EQUALS, ["VOICE"]),
                ("destination_zone", Operator.EQUALS, ["LOCAL_ONNET"]),
                ("time_band", Operator.EQUALS, ["PEAK"]),
                ("roaming", Operator.EQUALS, [False]),
            ],
            "actions": [
                (ActionType.SET_RATE,
                 {"rate": 0.10, "unit": "SECOND", "per_units": 60, "currency": "GBP"}),
            ],
        },
        {
            "rule_key": "PREPAID_A_VOICE_PULSE",
            "name": "Prepaid A — 60 second voice pulse",
            "description": "Billable duration rounds up to whole minutes.",
            "rule_type": RuleType.PULSE,
            "priority": 400,
            "conditions": [("service_type", Operator.EQUALS, ["VOICE"])],
            "actions": [
                (ActionType.SET_PULSE, {"initial_seconds": 60, "subsequent_seconds": 60}),
            ],
        },
        {
            "rule_key": "PREPAID_A_VOICE_VAT",
            "name": "Prepaid A — voice VAT",
            "description": "Reduced-rate VAT applied after any discount.",
            "rule_type": RuleType.TAX,
            "priority": 200,
            "conditions": [("service_type", Operator.EQUALS, ["VOICE"])],
            "actions": [(ActionType.APPLY_TAX, {"tax_rule": "VAT_REDUCED"})],
        },
    ]

    for spec in specs:
        if await rule_svc.latest_version(db, spec["rule_key"]) is not None:
            continue
        rule = Rule(
            rule_key=spec["rule_key"],
            version=1,
            name=spec["name"],
            description=spec["description"],
            rule_type=spec["rule_type"].value,
            execution_stage=rule_svc.RULE_TYPE_STAGE[spec["rule_type"]],
            service_type="VOICE",
            category="Voice tariff",
            rule_set_id=rule_set.id,
            product_id=product_id,
            tariff_plan_id=plan_id,
            priority=spec["priority"],
            effective_from=_TODAY,
            currency_code="GBP",
            status=RuleStatus.DRAFT.value,
            source_system="SEED",
            owner="Rating Assurance",
            change_comment="Seeded example rule.",
        )
        rule.conditions = [
            RuleCondition(sequence=i, group_index=0, attribute=attr,
                          operator=op.value, values=values)
            for i, (attr, op, values) in enumerate(spec["conditions"])
        ]
        rule.actions = [
            RuleAction(sequence=i, action_type=at.value, params=params)
            for i, (at, params) in enumerate(spec["actions"])
        ]
        rule.specificity = rule_svc.compute_specificity(rule.conditions)
        db.add(rule)
        await db.flush()
        await rule_svc.record_audit(
            db, rule, action="created", actor_id=None, actor_name="seed",
            to_status=rule.status, comment="Seeded example rule.",
        )


async def main() -> None:
    configure_logging(level="INFO", json_logs=False)
    async with SessionFactory() as db:
        # Bind a tenant before touching ra_rule. From migration 0013 the policies
        # are FORCEd, so an unbound connection sees no rows — the seeder would
        # find an empty vocabulary, write it again, and hit a unique violation on
        # rows it cannot see.
        async with tenant_context.scoped(db, settings.default_tenant_id):
            await _seed_all(db)
        await db.commit()
    log.info("seed_complete")


async def _seed_all(db: AsyncSession) -> None:
    await seed_permissions(db)
        # The canonical lookup tables mirror the Python vocabulary. Reconciled on
        # every run rather than frozen into a migration, so adding a stage or a
        # rule type is a restart instead of a schema change — and idempotent, so
        # running the seeder against a populated database is safe.
    counts = await sync_vocabulary(db)
    log.info("vocabulary_synced", **counts)
    refs = await seed_catalog(db)
    await seed_templates(db)
    await seed_rules(db, refs)


if __name__ == "__main__":
    asyncio.run(main())
