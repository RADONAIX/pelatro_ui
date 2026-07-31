"""The ingestion kernel, against a real Postgres.

Three properties carry most of the value here, and each replaces a specific
legacy defect:

**Unchanged means unchanged.** A nightly full dump re-imported must cut zero new
versions. The legacy importers cut one per row per night, which buried the three
changes that mattered under four thousand that did not.

**Dry run writes nothing.** The legacy connector had no preview at all — it wrote
straight to live pricing — so a nightly delta could not be inspected before it
changed what subscribers were charged.

**A withdrawal is proposed, never applied.** A rule the vendor stopped exporting
keeps rating until a human says otherwise.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.core.config import settings
from app.modules.rules.canonical.draft import (
    CanonicalDraft,
    DraftAction,
    DraftBehaviour,
    DraftCondition,
    DraftConditionGroup,
    DraftParameter,
    DraftValidity,
    Provenance,
)
from app.modules.rules.canonical.lineage import RuleValidationIssue
from app.modules.rules.canonical.logic import RuleParameter
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.ingest import kernel, keys, reconcile
from app.modules.rules.ingest.reconcile import Decision, ImportMode
from app.modules.rules.vocabulary.modes import ChargingMode
from app.modules.rules.vocabulary.sync import sync_vocabulary
from app.modules.rules.vocabulary.values import ValueType

pytestmark = pytest.mark.asyncio

TENANT = settings.default_tenant_id
ACTOR = kernel.Actor("11111111-1111-1111-1111-111111111111", "Test Author")


async def _ready(db) -> None:
    present = (
        await db.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'ra_rule'")
        )
    ).scalar_one()
    if not present:
        pytest.skip("ra_rule is not migrated (run alembic upgrade head)")
    await sync_vocabulary(db)
    await db.flush()


def _tariff(name: str = "Kernel peak voice", rate: str = "0.012345", **overrides):
    defaults: dict = {
        "rule_name": name,
        "charging_mode": ChargingMode.PREPAID,
        "rule_type_code": "BASE_TARIFF",
        "service_type": "VOICE",
        "validity": DraftValidity(effective_from=date(2026, 1, 1), currency_code="GBP"),
        "root_group": DraftConditionGroup(
            conditions=(DraftCondition("service_type", "EQUALS", ("VOICE",)),)
        ),
        "actions": (
            DraftAction(
                "SET_RATE",
                parameters=(
                    DraftParameter("rate", rate, ValueType.MONEY, currency="GBP"),
                    DraftParameter("unit", "MINUTE", ValueType.ENUM),
                    DraftParameter("per_units", "1", ValueType.NUMBER),
                ),
            ),
        ),
        "behaviour": DraftBehaviour(priority=200),
    }
    defaults.update(overrides)
    return CanonicalDraft(**defaults)


async def _count_versions(db, rule_key: str) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(CanonicalRuleVersion)
                .join(
                    CanonicalRule,
                    CanonicalRule.rule_id == CanonicalRuleVersion.rule_id,
                )
                .where(CanonicalRule.rule_key == rule_key)
            )
        ).scalar_one()
    )


async def _a_source_system(db) -> str:
    """Reuse a registered source system, or register one for the test."""
    from app.modules.connectors.models import SourceSystem

    code = "TEST_KERNEL_SRC"
    existing = (
        await db.execute(select(SourceSystem).where(SourceSystem.code == code))
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            SourceSystem(
                code=code,
                name="Kernel test source",
                vendor="TEST",
                category="RULES",
                source_type="FILE",
                status="ACTIVE",
            )
        )
        await db.flush()
    return code


# --- Identity ---------------------------------------------------------------


def test_a_rename_keeps_the_rule_key():
    """The legacy key was slugged from the name, so a vendor rename forked a
    phantom logical rule while the original kept rating."""
    assert keys.derive(_tariff("Peak")) == keys.derive(_tariff("Peak Hours"))


def test_two_rules_with_the_same_name_and_different_predicates_do_not_collide():
    other_predicate = _tariff(
        "Peak",
        root_group=DraftConditionGroup(
            conditions=(DraftCondition("service_type", "EQUALS", ("SMS",)),)
        ),
    )
    assert keys.derive(_tariff("Peak")) != keys.derive(other_predicate)


def test_re_pricing_a_rule_keeps_its_identity():
    """Identity is what the rule *targets*, not what it charges — otherwise every
    price change forks the rule and its history restarts at version 1."""
    assert keys.derive(_tariff(rate="0.01")) == keys.derive(_tariff(rate="0.02"))


def test_a_vendors_own_key_wins_over_a_derived_one():
    keyed = _tariff(provenance=Provenance(channel="FILE", external_ref="TC-4471"))
    assert keys.derive(keyed, source_code="ERICSSON") == "ERICSSON:TC-4471"


def test_a_suggested_key_is_made_unique_rather_than_rejected():
    assert keys.suggest("Peak On-net") == "PEAK_ON_NET"
    assert keys.unique("PEAK", {"PEAK"}) == "PEAK_2"
    assert keys.unique("PEAK", {"PEAK", "PEAK_2"}) == "PEAK_3"


# --- Reconciliation ---------------------------------------------------------


def test_retired_identity_is_reintroduced_but_active_identity_stays_unchanged():
    retired = SimpleNamespace(
        rule_index={"PRICE": ("rule-id", "same-hash")},
        rule_statuses={"PRICE": "RETIRED"},
    )
    active = SimpleNamespace(
        rule_index={"PRICE": ("rule-id", "same-hash")},
        rule_statuses={"PRICE": "ACTIVE"},
    )

    assert reconcile.decide(None, "PRICE", retired, "same-hash").decision == (
        Decision.CHANGED
    )
    assert reconcile.decide(None, "PRICE", active, "same-hash").decision == (
        Decision.UNCHANGED
    )


async def test_importing_the_same_rule_twice_cuts_one_version(db_session):
    """The decision the whole behaviour hash exists for."""
    await _ready(db_session)
    draft = _tariff("Kernel unchanged check")
    key = keys.derive(draft)

    first = await kernel.ingest(db_session, [draft], actor=ACTOR)
    assert first.counts.get(Decision.NEW) == 1

    second = await kernel.ingest(db_session, [draft], actor=ACTOR)
    assert second.counts.get(Decision.UNCHANGED) == 1
    assert not second.counts.get(Decision.CHANGED)
    assert await _count_versions(db_session, key) == 1


async def test_reimporting_a_retired_rule_reintroduces_it_as_a_new_version(
    db_session,
):
    await _ready(db_session)
    draft = _tariff("Kernel retired reintroduction")
    key = keys.derive(draft)
    await kernel.ingest(db_session, [draft], actor=ACTOR)

    rule = (
        await db_session.execute(
            select(CanonicalRule).where(CanonicalRule.rule_key == key)
        )
    ).scalar_one()
    current = await db_session.get(CanonicalRuleVersion, rule.current_version_id)
    rule.status = "RETIRED"
    current.status = "RETIRED"
    await db_session.flush()

    result = await kernel.ingest(db_session, [draft], actor=ACTOR)
    await db_session.refresh(rule)

    assert result.counts.get(Decision.CHANGED) == 1
    assert await _count_versions(db_session, key) == 2
    assert rule.status == "DRAFT"


async def test_a_rate_change_of_one_millionth_is_caught(db_session):
    """The threshold has to be exact. A tolerance here is a tolerance on money."""
    await _ready(db_session)
    base = _tariff("Kernel precision check", rate="0.012345")
    key = keys.derive(base)

    await kernel.ingest(db_session, [base], actor=ACTOR)
    result = await kernel.ingest(
        db_session, [_tariff("Kernel precision check", rate="0.012346")], actor=ACTOR
    )
    assert result.counts.get(Decision.CHANGED) == 1
    assert await _count_versions(db_session, key) == 2


async def test_a_rename_is_an_update_not_a_new_rule(db_session):
    """A rename follows through to the rule row — but cuts no version, because a
    name is not behaviour and versioning for it buries the changes that are."""
    await _ready(db_session)
    draft = _tariff("Kernel rename before")
    key = keys.derive(draft)
    await kernel.ingest(db_session, [draft], actor=ACTOR)
    await kernel.ingest(db_session, [_tariff("Kernel rename after")], actor=ACTOR)

    rules = (
        (
            await db_session.execute(
                select(CanonicalRule).where(CanonicalRule.rule_key == key)
            )
        )
        .scalars()
        .all()
    )
    assert len(rules) == 1
    assert rules[0].rule_name == "Kernel rename after"
    assert await _count_versions(db_session, key) == 1


# --- Dry run ----------------------------------------------------------------


async def test_a_dry_run_writes_nothing(db_session):
    """Preview and commit are the same code path; only the commit differs."""
    await _ready(db_session)
    draft = _tariff("Kernel dry run check")
    key = keys.derive(draft)

    result = await kernel.ingest(db_session, [draft], actor=ACTOR, dry_run=True)
    assert result.counts.get(Decision.NEW) == 1
    assert result.dry_run

    await _ready(db_session)
    assert await _count_versions(db_session, key) == 0


# --- Validation -------------------------------------------------------------


async def test_a_mode_incoherent_rule_is_rejected_not_written(db_session):
    await _ready(db_session)
    bad = _tariff(
        "Kernel incoherent",
        charging_mode=ChargingMode.BOTH,
        rule_type_code="TAX",
        actions=(
            DraftAction(
                "DEDUCT_BALANCE",
                parameters=(
                    DraftParameter("balance_type", "MAIN", ValueType.REFERENCE),
                ),
            ),
        ),
    )
    result = await kernel.ingest(db_session, [bad], actor=ACTOR)
    assert result.counts.get(Decision.REJECTED) == 1
    assert not result.valid
    assert await _count_versions(db_session, keys.derive(bad)) == 0


async def test_one_bad_row_does_not_cost_the_good_ones(db_session):
    """A 40,000-row dump with one malformed record must still land 39,999."""
    await _ready(db_session)
    good = _tariff("Kernel batch good")
    bad = _tariff("Kernel batch bad", actions=())

    result = await kernel.ingest(db_session, [good, bad], actor=ACTOR)
    assert result.counts.get(Decision.NEW) == 1
    assert result.counts.get(Decision.REJECTED) == 1
    assert await _count_versions(db_session, keys.derive(good)) == 1


async def test_failures_are_grouped_by_cause_not_listed_by_row(db_session):
    """Four hundred rows failing for one reason is one fix, and an operations
    team that sees four hundred lines concludes the import is unusable."""
    await _ready(db_session)
    broken = [
        _tariff(
            f"Kernel grouped {n}",
            actions=(),
            root_group=DraftConditionGroup(
                conditions=(DraftCondition("service_type", "EQUALS", (f"VOICE{n}",)),)
            ),
        )
        for n in range(3)
    ]
    result = await kernel.ingest(db_session, broken, actor=ACTOR)
    codes = {r["code"]: r["count"] for r in result.reasons}
    assert codes.get("no_actions") == 3


async def test_validation_issues_are_persisted_against_the_version(db_session):
    """The catalogue's Validation column has to be drillable: "which rules failed
    which check" is a query, not a re-validation script."""
    await _ready(db_session)
    broad = _tariff("Kernel no conditions", root_group=DraftConditionGroup())
    result = await kernel.ingest(db_session, [broad], actor=ACTOR)
    version_id = result.records[0].rule_version_id
    assert version_id is not None

    issues = (
        (
            await db_session.execute(
                select(RuleValidationIssue.code).where(
                    RuleValidationIssue.rule_version_id == version_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert "no_conditions" in issues
    # A rule that is merely broad is a WARNING, so it still saved.
    assert result.records[0].decision == Decision.NEW


# --- Withdrawals ------------------------------------------------------------


async def test_a_withdrawal_is_proposed_never_applied(db_session):
    """The failure this product exists to catch: a rule the vendor deleted that
    keeps rating, invisibly. It must surface — and it must not auto-retire."""
    await _ready(db_session)
    source = await _a_source_system(db_session)

    kept = _tariff(
        "Kernel withdrawal kept",
        provenance=Provenance(
            channel="CONNECTOR", source_system_code=source, external_ref="KEEP-1"
        ),
    )
    dropped = _tariff(
        "Kernel withdrawal dropped",
        rate="0.05",
        provenance=Provenance(
            channel="CONNECTOR", source_system_code=source, external_ref="DROP-1"
        ),
        root_group=DraftConditionGroup(
            conditions=(DraftCondition("service_type", "EQUALS", ("SMS",)),)
        ),
    )
    await kernel.ingest(
        db_session,
        [kept, dropped],
        actor=ACTOR,
        channel="CONNECTOR",
        source_system_code=source,
        import_mode=ImportMode.FULL,
    )

    second = await kernel.ingest(
        db_session,
        [kept],
        actor=ACTOR,
        channel="CONNECTOR",
        source_system_code=source,
        import_mode=ImportMode.FULL,
    )
    assert second.counts.get(Decision.WITHDRAWN) == 1

    # Proposed only: the rule is untouched and still rating.
    dropped_key = keys.derive(dropped, source_code=source)
    rule = (
        await db_session.execute(
            select(CanonicalRule).where(CanonicalRule.rule_key == dropped_key)
        )
    ).scalar_one()
    assert rule.status != "RETIRED"
    # And a batch containing a withdrawal can never auto-commit.
    assert not second.safe_to_auto_commit


async def test_a_delta_import_never_proposes_a_withdrawal(db_session):
    """Absence means nothing in a delta export, and treating it as a deletion
    would retire the estate on the first incremental feed."""
    await _ready(db_session)
    source = await _a_source_system(db_session)
    draft = _tariff(
        "Kernel delta check",
        provenance=Provenance(
            channel="CONNECTOR", source_system_code=source, external_ref="DELTA-1"
        ),
    )
    await kernel.ingest(
        db_session,
        [draft],
        actor=ACTOR,
        channel="CONNECTOR",
        source_system_code=source,
        import_mode=ImportMode.FULL,
    )
    result = await kernel.ingest(
        db_session,
        [],
        actor=ACTOR,
        channel="CONNECTOR",
        source_system_code=source,
        import_mode=ImportMode.DELTA,
    )
    assert not result.counts.get(Decision.WITHDRAWN)


# --- Auto-commit gate -------------------------------------------------------


async def test_a_batch_of_only_new_rules_is_safe_to_auto_commit(db_session):
    """New rules land as drafts and change no price until someone publishes them.
    Changed and withdrawn do not have that property, which is the whole gate."""
    await _ready(db_session)
    result = await kernel.ingest(
        db_session, [_tariff("Kernel autocommit new")], actor=ACTOR, dry_run=True
    )
    assert result.safe_to_auto_commit


async def test_a_batch_that_changes_a_rule_needs_a_human(db_session):
    await _ready(db_session)
    await kernel.ingest(db_session, [_tariff("Kernel autocommit base")], actor=ACTOR)
    result = await kernel.ingest(
        db_session,
        [_tariff("Kernel autocommit base", rate="0.99")],
        actor=ACTOR,
        dry_run=True,
    )
    assert result.counts.get(Decision.CHANGED) == 1
    assert not result.safe_to_auto_commit


# --- Money ------------------------------------------------------------------


async def test_money_survives_the_kernel_as_an_exact_decimal(db_session):
    await _ready(db_session)
    result = await kernel.ingest(
        db_session, [_tariff("Kernel money check", rate="0.012345")], actor=ACTOR
    )
    stored = (
        await db_session.execute(
            select(RuleParameter.parameter_value_numeric).where(
                RuleParameter.rule_version_id == result.records[0].rule_version_id,
                RuleParameter.parameter_name == "rate",
            )
        )
    ).scalar_one()
    assert stored == Decimal("0.012345")
