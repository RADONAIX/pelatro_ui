"""The charging / prepaid / postpaid metadata catalogue.

Two claims are worth a test each. The catalogue must be *complete* with respect
to the rule vocabulary — every reference a rule can make must have somewhere to
resolve to, or the wizard offers a dropdown with no source behind it. And the
money columns must be exact, because a metadata table that stores £20.00 as a
float has the same defect the whole canonical model exists to remove.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.modules.catalog import service as svc
from app.modules.catalog.router import ENTITIES, ENTITY_BY_SLUG
from app.modules.charging import models as chm
from app.modules.rules.canonical.base import RULE_SCHEMA
from app.modules.rules.ingest.resolver import PENDING_CATALOGUES, REFERENCE_MODELS
from app.modules.rules.vocabulary.actions import (
    REFERENCED_CATALOGUES as ACTION_CATALOGUES,
)
from app.modules.rules.vocabulary.attributes import (
    REFERENCED_CATALOGUES as ATTRIBUTE_CATALOGUES,
)

TENANT = settings.default_tenant_id


# --- Completeness (no database needed) --------------------------------------


def test_every_catalogue_a_rule_can_reference_has_a_model():
    """The invariant that keeps the vocabulary and the store in step.

    A reference with no resolver model does not fail loudly — it produces
    "'MAIN' does not exist in the balance types catalogue", which sends an
    operator hunting for a data-entry mistake that was never made.
    """
    assert not PENDING_CATALOGUES, f"No model backs: {sorted(PENDING_CATALOGUES)}"


def test_every_referenced_catalogue_is_editable_in_the_ui():
    """A catalogue a rule can name but nobody can populate is a dead end."""
    referenced = ACTION_CATALOGUES | ATTRIBUTE_CATALOGUES
    missing = sorted(referenced - set(ENTITY_BY_SLUG))
    assert not missing, f"Referenced but not on the metadata catalogue: {missing}"


def test_resolver_and_router_agree_on_every_charging_slug():
    """The two registries name the same model for the same slug.

    They are maintained in different files, and a slug pointing at one model in
    the router and another in the resolver would give the UI a dropdown of
    entities the validator then rejects.
    """
    for slug, model in REFERENCE_MODELS.items():
        spec = ENTITY_BY_SLUG.get(slug)
        if spec is None:
            continue
        assert spec.model is model, f"{slug}: router has {spec.model}, resolver {model}"


def test_the_metadata_catalogue_is_grouped_for_the_screen():
    """Every entity declares which section of the Metadata Catalogue it sits in."""
    allowed = {"Commercial", "Charging", "Prepaid", "Postpaid", "Shared"}
    for spec in ENTITIES:
        assert spec.group in allowed, f"{spec.slug} has group {spec.group!r}"
    groups = {spec.group for spec in ENTITIES}
    assert {"Prepaid", "Postpaid"} <= groups


def test_charging_metadata_lives_in_the_rule_management_schema():
    """All of it in one schema, beside the rules that reference it."""
    for model in chm.CHARGING_MODELS:
        assert model.__table__.schema == RULE_SCHEMA, model.__tablename__


def test_the_bucket_definition_does_not_collide_with_subscriber_balances():
    """``rating.balance_buckets`` holds a subscriber's balance; this holds what a
    bucket grants. Same word, different things, and merging them would leave "how
    much does ADD_5GB give" answerable only by scanning subscriber rows."""
    from app.modules.balances.models import BalanceBucket

    assert BalanceBucket.__tablename__ != chm.BalanceBucketDefinition.__tablename__


# --- Persistence ------------------------------------------------------------

pytestmark_db = pytest.mark.asyncio


async def _require_schema(db) -> None:
    present = (
        await db.execute(
            text(
                "SELECT count(*) FROM pg_tables "
                "WHERE schemaname = :s AND tablename = 'balance_type'"
            ),
            {"s": RULE_SCHEMA},
        )
    ).scalar_one()
    if not present:
        pytest.skip("charging metadata is not migrated (run alembic upgrade head)")


@pytest.mark.asyncio
async def test_money_columns_hold_an_exact_decimal(db_session):
    """The defect this whole model exists to remove, checked at the metadata layer.

    A rental of £20.10 stored through a float round-trips as 20.099999999999998,
    and every invoice built from it is a penny out in a way nobody can reconstruct.
    """
    await _require_schema(db_session)
    await svc.create(
        db_session,
        chm.CreditLimitProfile,
        {
            "code": "TEST_CL_EXACT", "name": "Exactness check", "description": "",
            "status": "ACTIVE", "source_system": "MANUAL", "attributes": {},
            "limit_amount": Decimal("20.10"), "currency_code": "GBP",
            "warning_threshold_pct": 80, "breach_action": "NOTIFY", "grace_days": 0,
        },
        label="Credit limit profile",
        actor_id=None,
    )
    await db_session.flush()
    stored = (
        await db_session.execute(
            select(chm.CreditLimitProfile.limit_amount).where(
                chm.CreditLimitProfile.code == "TEST_CL_EXACT"
            )
        )
    ).scalar_one()
    assert isinstance(stored, Decimal)
    assert stored == Decimal("20.10")


@pytest.mark.asyncio
async def test_every_charging_table_accepts_a_row(db_session):
    """A model whose columns disagree with its migration fails here, not in
    production the first time an operator opens the screen."""
    await _require_schema(db_session)
    made: dict[str, str] = {}

    async def make(model, code, **columns):
        row = await svc.create(
            db_session, model,
            {"code": code, "name": code.title(), "description": "",
             "status": "ACTIVE", "source_system": "MANUAL", "attributes": {},
             **columns},
            label=model.__name__, actor_id=None,
        )
        await db_session.flush()
        made[model.__name__] = row.id
        return row

    unit = await make(chm.ChargingUnit, "TEST_MIN", dimension="TIME",
                      base_unit="SECOND", factor=Decimal(60), decimals=6)
    await make(chm.PulseProfile, "TEST_60_60", service_type="VOICE",
               initial_seconds=60, subsequent_seconds=60, round_mode="UP",
               min_chargeable_seconds=0, unit_code=unit.code)
    await make(chm.ChargeLimitProfile, "TEST_LIMITS", service_type="VOICE",
               min_charge=Decimal("0.05"), max_charge=Decimal("2.00"),
               min_quantity=None, max_quantity=None, currency_code="GBP",
               unit_code=None)
    ocs = await make(chm.OcsProfile, "TEST_OCS", vendor="Ericsson",
                     reservation_strategy="QUOTA", quota_unit="SECOND",
                     initial_quota=Decimal(300), subsequent_quota=Decimal(180),
                     quota_validity_seconds=600, redirect_on_exhaust=None,
                     connection={})
    policy = await make(chm.ReservationPolicy, "TEST_RES", ocs_profile_id=ocs.id,
                        initial_quota=Decimal(300), subsequent_quota=Decimal(180),
                        unit_code="SECOND", validity_seconds=600, threshold_pct=20,
                        release_mode="UNUSED", on_timeout="RELEASE")
    balance = await make(chm.BalanceType, "TEST_MAIN", category="MAIN",
                         unit_code=None, is_monetary=True, allows_negative=False,
                         expiry_policy="NONE")
    await make(chm.BalanceBucketDefinition, "TEST_5GB", balance_type_id=balance.id,
               unit_code="MEGABYTE", initial_amount=Decimal(5120),
               currency_code=None, validity_days=30, carry_over_flag=False,
               shared_flag=False)
    credit = await make(chm.CreditLimitProfile, "TEST_CL", limit_amount=Decimal(50),
                        currency_code="GBP", warning_threshold_pct=80,
                        breach_action="NOTIFY", grace_days=0)
    profile = await make(chm.ChargingProfile, "TEST_PROF", charging_mode="PREPAID",
                         ocs_profile_id=ocs.id, reservation_policy_id=policy.id,
                         credit_limit_profile_id=credit.id,
                         default_currency_code="GBP", rounding_rule_id=None,
                         negative_balance_allowed=False)
    await make(chm.BalancePriority, "TEST_PRIO", charging_profile_id=profile.id,
               balance_type_id=balance.id, service_type="VOICE",
               consumption_order=10)
    proration = await make(chm.ProrationProfile, "TEST_PRO", method="DAILY",
                           round_mode="HALF_UP", apply_on_activation=True,
                           apply_on_cease=True, apply_on_plan_change=True)
    cycle = await make(chm.BillingCycle, "TEST_CYCLE", frequency="MONTHLY",
                       cycle_start_day=1, bill_run_offset_days=2, timezone="UTC",
                       proration_profile_id=proration.id)
    component = await make(chm.InvoiceComponent, "TEST_COMP", component_type="USAGE",
                           gl_account="4000", tax_rule_id=None, display_order=10,
                           sign="DEBIT")
    await make(chm.RecurringCharge, "TEST_RENT", product_id=None, offer_id=None,
               amount=Decimal("20.00"), currency_code="GBP",
               billing_cycle_id=cycle.id, invoice_component_id=component.id,
               proration_profile_id=proration.id, advance_flag=True,
               effective_from=None, effective_to=None)
    await make(chm.OneTimeCharge, "TEST_OTC", trigger_event="ACTIVATION",
               amount=Decimal("10.00"), currency_code="GBP",
               invoice_component_id=component.id, refundable_flag=False)
    await make(chm.UsageAggregationProfile, "TEST_AGG", dimension="SUBSCRIBER",
               window="CYCLE", service_type="DATA", reset_policy="CYCLE",
               invoice_component_id=component.id)
    await make(chm.LateFeeProfile, "TEST_LATE", fee_amount=Decimal("5.00"),
               fee_percentage=None, currency_code="GBP", grace_days=14,
               max_occurrences=3, invoice_component_id=component.id)

    assert len(made) == len(chm.CHARGING_MODELS)


@pytest.mark.asyncio
async def test_a_rows_tenant_is_stamped_without_the_caller_supplying_one(db_session):
    """Tenancy is a column from day one, not a retrofit onto a populated estate."""
    await _require_schema(db_session)
    row = await svc.create(
        db_session, chm.BalanceType,
        {"code": "TEST_TENANT", "name": "Tenant check", "description": "",
         "status": "ACTIVE", "source_system": "MANUAL", "attributes": {},
         "category": "MAIN", "unit_code": None, "is_monetary": True,
         "allows_negative": False, "expiry_policy": "NONE"},
        label="Balance type", actor_id=None,
    )
    await db_session.flush()
    assert row.tenant_id == TENANT
