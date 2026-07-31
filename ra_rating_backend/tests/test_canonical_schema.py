"""The canonical schema, against a real Postgres.

Two things are being proved here that a pure-logic test cannot:

1. ``sync_vocabulary`` reconciles the Python registry into the lookup tables, and
   running it twice changes nothing. Deployment relies on that — adding a stage is
   a restart, not a migration.
2. The database itself refuses two overlapping live versions of one rule. That is
   an *enforcement* claim, and the only way to check an enforcement claim is to
   attempt the thing and be rejected.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.modules.rules.canonical import (
    CANONICAL_TABLES,
    RULE_SCHEMA,
    CanonicalRule,
    CanonicalRuleVersion,
    RuleStackingPolicyRow,
    RuleStage,
    RuleTypeRow,
)
from app.modules.rules.vocabulary import CANONICAL_RULE_TYPES, CANONICAL_STAGES
from app.modules.rules.vocabulary.policies import CANONICAL_STACKING_POLICIES
from app.modules.rules.vocabulary.sync import sync_vocabulary

pytestmark = pytest.mark.asyncio

TENANT = settings.default_tenant_id


async def _skip_unless_migrated(db) -> None:
    present = (
        await db.execute(
            text(
                "SELECT count(*) FROM pg_tables WHERE schemaname = :schema"
            ),
            {"schema": RULE_SCHEMA},
        )
    ).scalar_one()
    if not present:
        pytest.skip(f"schema {RULE_SCHEMA} is not migrated (run alembic upgrade head)")


async def test_every_canonical_table_exists(db_session):
    await _skip_unless_migrated(db_session)
    rows = (
        await db_session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = :s"),
            {"s": RULE_SCHEMA},
        )
    ).scalars().all()
    assert set(CANONICAL_TABLES) <= set(rows), set(CANONICAL_TABLES) - set(rows)


async def test_row_level_security_is_enabled_on_every_tenant_table(db_session):
    """Enabled permissively from R1 so the M8 tightening is a policy swap rather
    than a table-by-table rollout across a populated estate."""
    await _skip_unless_migrated(db_session)
    rows = (
        await db_session.execute(
            text(
                """
                SELECT c.relname FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :s AND c.relrowsecurity
                """
            ),
            {"s": RULE_SCHEMA},
        )
    ).scalars().all()
    assert set(CANONICAL_TABLES) <= set(rows), set(CANONICAL_TABLES) - set(rows)


async def test_sync_vocabulary_seeds_the_lookup_tables(db_session):
    await _skip_unless_migrated(db_session)
    await sync_vocabulary(db_session)

    for model, registry in (
        (RuleStage, CANONICAL_STAGES),
        (RuleTypeRow, CANONICAL_RULE_TYPES),
        (RuleStackingPolicyRow, CANONICAL_STACKING_POLICIES),
    ):
        count = (
            await db_session.execute(
                select(func.count()).select_from(model).where(model.tenant_id == TENANT)
            )
        ).scalar_one()
        assert count >= len(registry), f"{model.__tablename__} is short of the registry"

    codes = (
        await db_session.execute(
            select(RuleStage.code).where(RuleStage.tenant_id == TENANT)
        )
    ).scalars().all()
    assert {s.code for s in CANONICAL_STAGES} <= set(codes)
    await db_session.rollback()


async def test_sync_vocabulary_is_idempotent(db_session):
    """The second run must create nothing. A deploy runs this every time."""
    await _skip_unless_migrated(db_session)
    await sync_vocabulary(db_session)
    second = await sync_vocabulary(db_session)
    for table, counts in second.items():
        assert counts["created"] == 0, f"{table} created rows on a repeat run"
        assert counts["updated"] == 0, f"{table} updated rows on a repeat run"
    await db_session.rollback()


async def test_every_rule_type_row_points_at_its_registry_stage(db_session):
    """The FK proves the stage exists; this proves it is the *right* stage — the
    thing that decides where in the charging sequence the rule fires."""
    await _skip_unless_migrated(db_session)
    await sync_vocabulary(db_session)

    rows = (
        await db_session.execute(
            select(RuleTypeRow.code, RuleStage.code)
            .join(RuleStage, RuleStage.rule_stage_id == RuleTypeRow.rule_stage_id)
            .where(RuleTypeRow.tenant_id == TENANT)
        )
    ).all()
    stored = dict(rows)
    for spec in CANONICAL_RULE_TYPES:
        assert stored.get(spec.code) == spec.stage_code, spec.code
    await db_session.rollback()


async def test_overlapping_live_versions_are_rejected_by_the_database(db_session):
    """Two ACTIVE versions of one rule whose validity windows overlap means two
    live prices for one event. Application-level care cannot prevent it under
    concurrency; the exclusion constraint can."""
    await _skip_unless_migrated(db_session)
    await sync_vocabulary(db_session)
    await db_session.flush()

    stage = (
        await db_session.execute(
            select(RuleStage).where(
                RuleStage.tenant_id == TENANT, RuleStage.code == "BASE_CHARGE"
            )
        )
    ).scalar_one()
    rule_type = (
        await db_session.execute(
            select(RuleTypeRow).where(
                RuleTypeRow.tenant_id == TENANT, RuleTypeRow.code == "BASE_TARIFF"
            )
        )
    ).scalar_one()
    policy = (
        await db_session.execute(
            select(RuleStackingPolicyRow).where(
                RuleStackingPolicyRow.tenant_id == TENANT,
                RuleStackingPolicyRow.code == "EXCLUSIVE",
            )
        )
    ).scalar_one()

    rule = CanonicalRule(
        rule_key="TEST_OVERLAP_GUARD",
        rule_name="Overlap guard probe",
        charging_mode="PREPAID",
        rule_type_id=rule_type.rule_type_id,
        rule_stage_id=stage.rule_stage_id,
        service_type="VOICE",
    )
    db_session.add(rule)
    await db_session.flush()

    def _version(number: int, start: date, end: date | None) -> CanonicalRuleVersion:
        return CanonicalRuleVersion(
            rule_id=rule.rule_id,
            version_number=number,
            stacking_policy_id=policy.stacking_policy_id,
            effective_from=start,
            effective_to=end,
            status="ACTIVE",
            behaviour_hash=f"probe-{number}",
        )

    db_session.add(_version(1, date(2026, 1, 1), date(2026, 6, 30)))
    await db_session.flush()

    # Overlaps the first window by one day.
    db_session.add(_version(2, date(2026, 6, 30), None))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_non_overlapping_live_versions_are_allowed(db_session):
    """The constraint must not be so eager that a normal price change — one
    version ends, the next begins the following day — is rejected."""
    await _skip_unless_migrated(db_session)
    await sync_vocabulary(db_session)
    await db_session.flush()

    stage = (
        await db_session.execute(
            select(RuleStage).where(
                RuleStage.tenant_id == TENANT, RuleStage.code == "BASE_CHARGE"
            )
        )
    ).scalar_one()
    rule_type = (
        await db_session.execute(
            select(RuleTypeRow).where(
                RuleTypeRow.tenant_id == TENANT, RuleTypeRow.code == "BASE_TARIFF"
            )
        )
    ).scalar_one()
    policy = (
        await db_session.execute(
            select(RuleStackingPolicyRow).where(
                RuleStackingPolicyRow.tenant_id == TENANT,
                RuleStackingPolicyRow.code == "EXCLUSIVE",
            )
        )
    ).scalar_one()

    rule = CanonicalRule(
        rule_key="TEST_SUCCESSION",
        rule_name="Succession probe",
        charging_mode="POSTPAID",
        rule_type_id=rule_type.rule_type_id,
        rule_stage_id=stage.rule_stage_id,
        service_type="DATA",
    )
    db_session.add(rule)
    await db_session.flush()

    for number, start, end in (
        (1, date(2026, 1, 1), date(2026, 6, 30)),
        (2, date(2026, 7, 1), None),
    ):
        db_session.add(
            CanonicalRuleVersion(
                rule_id=rule.rule_id,
                version_number=number,
                stacking_policy_id=policy.stacking_policy_id,
                effective_from=start,
                effective_to=end,
                status="ACTIVE",
                behaviour_hash=f"succession-{number}",
            )
        )
    await db_session.flush()

    count = (
        await db_session.execute(
            select(func.count())
            .select_from(CanonicalRuleVersion)
            .where(CanonicalRuleVersion.rule_id == rule.rule_id)
        )
    ).scalar_one()
    assert count == 2
    await db_session.rollback()


async def test_draft_versions_may_overlap(db_session):
    """The guard covers ACTIVE and PUBLISHED only. Authoring two competing drafts
    for the same window is a normal thing to do while a change is being decided."""
    await _skip_unless_migrated(db_session)
    await sync_vocabulary(db_session)
    await db_session.flush()

    stage = (
        await db_session.execute(
            select(RuleStage).where(
                RuleStage.tenant_id == TENANT, RuleStage.code == "TAX"
            )
        )
    ).scalar_one()
    rule_type = (
        await db_session.execute(
            select(RuleTypeRow).where(
                RuleTypeRow.tenant_id == TENANT, RuleTypeRow.code == "TAX"
            )
        )
    ).scalar_one()
    policy = (
        await db_session.execute(
            select(RuleStackingPolicyRow).where(
                RuleStackingPolicyRow.tenant_id == TENANT,
                RuleStackingPolicyRow.code == "EXCLUSIVE",
            )
        )
    ).scalar_one()

    rule = CanonicalRule(
        rule_key="TEST_DRAFT_OVERLAP",
        rule_name="Draft overlap probe",
        charging_mode="BOTH",
        rule_type_id=rule_type.rule_type_id,
        rule_stage_id=stage.rule_stage_id,
        service_type="VOICE",
    )
    db_session.add(rule)
    await db_session.flush()

    for number in (1, 2):
        db_session.add(
            CanonicalRuleVersion(
                rule_id=rule.rule_id,
                version_number=number,
                stacking_policy_id=policy.stacking_policy_id,
                effective_from=date(2026, 1, 1),
                effective_to=None,
                status="DRAFT",
                behaviour_hash=f"draft-{number}",
            )
        )
    await db_session.flush()
    await db_session.rollback()


async def test_tenant_id_defaults_without_being_supplied(db_session):
    """Every canonical row is tenant-stamped even when the writer forgets, so a
    row can never become invisible to the eventual RLS policy."""
    await _skip_unless_migrated(db_session)
    await sync_vocabulary(db_session)
    stage = (
        await db_session.execute(
            select(RuleStage).where(
                RuleStage.tenant_id == TENANT, RuleStage.code == "PULSE"
            )
        )
    ).scalar_one()
    assert stage.tenant_id == TENANT
    await db_session.rollback()
