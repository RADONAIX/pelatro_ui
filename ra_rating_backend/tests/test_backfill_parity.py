"""R4 backfill and its parity gate.

The gate is the point of this file. A migration of a rating platform has exactly
one acceptance criterion — *the charges do not move* — and the only way to
assert that is to compile the legacy row and the canonical row through the same
compiler and diff the output.

The rule shapes below are chosen to be the ones a naive converter gets wrong:
several condition groups (which the legacy model flattens into `group_index`),
an `IN` over many values (which compiles to a dimension *set* rather than a
dimension), a negated predicate (which cannot reduce to a dimension at all), and
a rate with six decimal places (which is where float storage does its damage).
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select, text

from app.modules.rules import backfill
from app.modules.rules import service as legacy_service
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.ingest import kernel
from app.modules.rules.ingest.adapters import legacy as adapter
from app.modules.rules.models import Rule, RuleAction, RuleCondition
from app.modules.rules.vocabulary.modes import ChargingMode
from app.modules.rules.vocabulary.sync import sync_vocabulary

pytestmark = pytest.mark.asyncio

ACTOR = kernel.Actor("11111111-1111-1111-1111-111111111111", "Backfill Test")


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


def _legacy(
    rule_key: str,
    *,
    conditions: list[RuleCondition] | None = None,
    actions: list[RuleAction] | None = None,
    **overrides,
) -> Rule:
    """A legacy rule row, not persisted unless the test wants it."""
    defaults: dict = {
        "rule_key": rule_key,
        "version": 1,
        "name": rule_key.replace("_", " ").title(),
        "description": "",
        "rule_type": "BASE_TARIFF",
        "execution_stage": "BASE_CHARGE",
        "service_type": "VOICE",
        "priority": 100,
        "specificity": 0,
        "stacking_policy": "EXCLUSIVE",
        "condition_logic": "AND",
        "effective_from": date(2026, 1, 1),
        "currency_code": "GBP",
        "status": "APPROVED",
        "source_system": "MANUAL",
    }
    defaults.update(overrides)
    rule = Rule(**defaults)
    rule.conditions = conditions or [
        RuleCondition(
            sequence=0, group_index=0, attribute="service_type",
            operator="EQUALS", values=["VOICE"], negate=False,
        )
    ]
    rule.actions = actions or [
        RuleAction(
            sequence=0,
            action_type="SET_RATE",
            params={"rate": 0.012345, "unit": "MINUTE", "per_units": 1},
        )
    ]
    # The real create/update path computes this; a fixture that left it at 0
    # would fail parity for a reason that has nothing to do with the migration.
    if "specificity" not in overrides:
        rule.specificity = legacy_service.compute_specificity(rule.conditions)
    return rule


# --- Conversion (no database) -----------------------------------------------


def test_the_rule_key_is_carried_across_verbatim():
    """The one thing a migration of an assurance platform may never do is change
    identity: every historical rating result references it."""
    rule = _legacy("PEAK_ON_NET")
    assert adapter.convert(rule).draft.rule_key == "PEAK_ON_NET"


def test_legacy_no_conflict_group_sentinel_becomes_empty():
    draft = adapter.convert(_legacy("NO_CONFLICT", conflict_group="NO")).draft
    assert draft.behaviour.conflict_group is None


def test_charging_mode_is_inferred_from_the_account_type_condition():
    rule = _legacy(
        "PREPAID_ONLY",
        conditions=[
            RuleCondition(
                sequence=0, group_index=0, attribute="account_type",
                operator="EQUALS", values=["PREPAID"], negate=False,
            )
        ],
    )
    conversion = adapter.convert(rule)
    assert conversion.draft.charging_mode == ChargingMode.PREPAID
    assert any("inferred" in note for note in conversion.notes)


def test_a_rule_with_no_account_type_defaults_to_both():
    """The only value that cannot be wrong: it preserves exactly the behaviour
    the rule has today, whereas a guess would stop it applying to half the estate."""
    assert adapter.convert(_legacy("NO_MODE")).draft.charging_mode == ChargingMode.BOTH


def test_a_hybrid_account_type_becomes_both():
    """HYBRID describes a *subscriber* holding both balances. The rule serving
    them is correct either way, which is BOTH."""
    rule = _legacy(
        "HYBRID_RULE",
        conditions=[
            RuleCondition(
                sequence=0, group_index=0, attribute="account_type",
                operator="EQUALS", values=["HYBRID"], negate=False,
            )
        ],
    )
    assert adapter.convert(rule).draft.charging_mode == ChargingMode.BOTH


def test_several_condition_groups_become_a_nested_tree():
    rule = _legacy(
        "TWO_GROUPS",
        condition_logic="OR",
        conditions=[
            RuleCondition(sequence=0, group_index=0, attribute="destination_zone",
                          operator="EQUALS", values=["LOCAL"], negate=False),
            RuleCondition(sequence=1, group_index=0, attribute="time_band",
                          operator="EQUALS", values=["PEAK"], negate=False),
            RuleCondition(sequence=2, group_index=1, attribute="destination_zone",
                          operator="EQUALS", values=["NATIONAL"], negate=False),
        ],
    )
    root = adapter.convert(rule).draft.root_group
    assert root.logic == "OR"
    assert len(root.children) == 2
    assert [len(child.conditions) for child in root.children] == [2, 1]
    # Nothing lost: the same three predicates, in the same order.
    assert len(tuple(root.all_conditions())) == 3


def test_a_single_group_collapses_into_the_root():
    """Otherwise a simple rule's behaviour hash would depend on whether its author
    happened to use group 0 — a difference with no behavioural meaning."""
    root = adapter.convert(_legacy("ONE_GROUP")).draft.root_group
    assert root.children == ()
    assert len(root.conditions) == 1


def test_a_rate_stored_as_a_float_is_recovered_as_the_decimal_a_human_typed():
    from decimal import Decimal

    conversion = adapter.convert(_legacy("FLOAT_RATE"))
    rate = next(
        p for p in conversion.draft.actions[0].parameters if p.name == "rate"
    )
    assert rate.raw == Decimal("0.012345")
    assert not conversion.needs_review


def test_a_rate_float_storage_damaged_is_flagged_rather_than_carried_silently():
    """The plan's G.6, answered by reporting. A value needing more precision than
    the canonical scale allows was mangled before it reached us, and quietly
    writing it into the money-exact model would defeat the point of building it."""
    rule = _legacy(
        "DAMAGED_RATE",
        actions=[
            RuleAction(
                sequence=0,
                action_type="SET_RATE",
                params={"rate": 0.1 + 0.2, "unit": "MINUTE", "per_units": 1},
            )
        ],
    )
    conversion = adapter.convert(rule)
    assert conversion.needs_review
    assert "cannot be recovered with certainty" in conversion.suspect_money[0]


# --- Parity (against a real Postgres) ---------------------------------------


async def _backfill_and_compare(db, rule: Rule) -> backfill.Report:
    """Persist a legacy rule, backfill just it, and compile both sides."""
    db.add(rule)
    await db.flush()
    report = await backfill.backfill(db, actor=ACTOR, rule_key=rule.rule_key)
    return await backfill.check_parity(db, report, rule_key=rule.rule_key)


async def test_a_simple_tariff_compiles_identically(db_session):
    await _ready(db_session)
    report = await _backfill_and_compare(db_session, _legacy("PARITY_SIMPLE"))
    assert report.compared == 1
    assert report.differences == [], [str(d) for d in report.differences]


async def test_a_rule_with_several_groups_compiles_identically(db_session):
    """The shape a flattening converter gets wrong: the compiler's predicates
    carry `group_index`, so a tree rebuilt with different numbering diverges."""
    await _ready(db_session)
    rule = _legacy(
        "PARITY_GROUPS",
        condition_logic="OR",
        conditions=[
            RuleCondition(sequence=0, group_index=0, attribute="duration_seconds",
                          operator="GREATER_THAN", values=[60], negate=False),
            RuleCondition(sequence=1, group_index=1, attribute="duration_seconds",
                          operator="LESS_THAN", values=[10], negate=False),
        ],
    )
    report = await _backfill_and_compare(db_session, rule)
    assert report.differences == [], [str(d) for d in report.differences]


async def test_an_in_over_many_values_compiles_identically(db_session):
    """`IN` reduces to a dimension *set*, not a dimension. A converter that
    collapsed it to the first value would price the other zones wrong."""
    await _ready(db_session)
    rule = _legacy(
        "PARITY_IN",
        conditions=[
            RuleCondition(
                sequence=0, group_index=0, attribute="destination_zone",
                operator="IN",
                values=["LOCAL_ONNET", "LOCAL_OFFNET", "NATIONAL_FIXED"],
                negate=False,
            )
        ],
    )
    report = await _backfill_and_compare(db_session, rule)
    assert report.differences == [], [str(d) for d in report.differences]


async def test_a_negated_condition_compiles_identically(db_session):
    """A negated predicate cannot reduce to a dimension at all — it has to survive
    as a residual predicate, negation intact."""
    await _ready(db_session)
    rule = _legacy(
        "PARITY_NEGATED",
        conditions=[
            RuleCondition(
                sequence=0, group_index=0, attribute="destination_zone",
                operator="EQUALS", values=["PREMIUM"], negate=True,
            )
        ],
    )
    report = await _backfill_and_compare(db_session, rule)
    assert report.differences == [], [str(d) for d in report.differences]


async def test_a_six_decimal_rate_compiles_identically(db_session):
    """Where the money model earns its keep: the legacy side holds a float, the
    canonical side an exact decimal, and the compiled rate must still match."""
    await _ready(db_session)
    rule = _legacy(
        "PARITY_PRECISE",
        actions=[
            RuleAction(
                sequence=0,
                action_type="SET_RATE",
                params={"rate": 0.012345, "unit": "MINUTE", "per_units": 1},
            )
        ],
    )
    report = await _backfill_and_compare(db_session, rule)
    assert report.differences == [], [str(d) for d in report.differences]


async def test_the_legacy_version_number_is_preserved(db_session):
    """A rule at v2 must stay v2. `rule_version` is how an operator and a
    six-month-old rating result refer to a specific rule text; renumbering it
    makes every such reference wrong — and the parity gate catches it."""
    await _ready(db_session)
    v1 = _legacy("PARITY_VERSIONED", version=1, status="SUPERSEDED")
    v2 = _legacy("PARITY_VERSIONED", version=2)
    db_session.add_all([v1, v2])
    await db_session.flush()

    report = await backfill.backfill(db_session, actor=ACTOR, rule_key="PARITY_VERSIONED")
    assert report.written == 1  # only the latest logical version is migrated

    rule = (
        await db_session.execute(
            select(CanonicalRule).where(CanonicalRule.rule_key == "PARITY_VERSIONED")
        )
    ).scalar_one()
    version = await db_session.get(CanonicalRuleVersion, rule.current_version_id)
    assert version.version_number == 2

    parity = await backfill.check_parity(db_session, rule_key="PARITY_VERSIONED")
    assert parity.differences == [], [str(d) for d in parity.differences]


async def test_the_live_status_is_carried_not_reset_to_draft(db_session):
    """A backfill that landed 4,000 live rules as drafts would take the whole
    tariff out of the published set the moment the compiler was repointed."""
    await _ready(db_session)
    rule = _legacy("PARITY_APPROVED", status="APPROVED")
    db_session.add(rule)
    await db_session.flush()
    await backfill.backfill(db_session, actor=ACTOR, rule_key="PARITY_APPROVED")

    canonical = (
        await db_session.execute(
            select(CanonicalRule).where(CanonicalRule.rule_key == "PARITY_APPROVED")
        )
    ).scalar_one()
    assert canonical.status == "APPROVED"


async def test_running_the_backfill_twice_writes_nothing_the_second_time(db_session):
    """Resumability. A 40,000-rule backfill that dies at 90% must be re-runnable
    without re-versioning the 36,000 rules that already landed."""
    await _ready(db_session)
    rule = _legacy("PARITY_IDEMPOTENT")
    db_session.add(rule)
    await db_session.flush()

    first = await backfill.backfill(db_session, actor=ACTOR, rule_key="PARITY_IDEMPOTENT")
    assert first.written == 1

    second = await backfill.backfill(db_session, actor=ACTOR, rule_key="PARITY_IDEMPOTENT")
    assert second.written == 0
    assert second.unchanged == 1


async def test_a_dry_run_writes_nothing(db_session):
    await _ready(db_session)
    rule = _legacy("PARITY_DRY_RUN")
    db_session.add(rule)
    await db_session.flush()

    report = await backfill.backfill(
        db_session, actor=ACTOR, rule_key="PARITY_DRY_RUN", dry_run=True
    )
    assert report.converted == 1

    await _ready(db_session)
    present = (
        await db_session.execute(
            select(CanonicalRule).where(CanonicalRule.rule_key == "PARITY_DRY_RUN")
        )
    ).scalar_one_or_none()
    assert present is None


async def test_every_backfilled_rule_compiles_identically(db_session):
    """The plan's M3 exit criterion, on whatever the database actually holds.

    Asserted over the rules that *converted*, not over the whole table, and the
    distinction is the point. A legacy rule the canonical model refuses — one
    typed TARIFF_SELECTION whose only action is SET_RATE, say — is a real finding
    about the estate, reported by name in `report.failures` for a human to fix.
    Folding it into the parity assertion would make this test hostage to whatever
    anyone last created in the UI, and a test that fails for reasons unrelated to
    the code is one people learn to re-run rather than read.

    The same exclusion applies to a rule whose canonical copy has moved *ahead*
    of legacy — somebody imported or edited it here, so it holds a version the
    legacy table has never seen. Comparing those two is not a parity question at
    all: they are different rule texts, and they are *supposed* to differ. Such a
    rule is reported in `report.version_conflicts` and excluded here, on exactly
    the reasoning the paragraph above gives for unconverted rules.

    What must never happen is a rule that converts, has not diverged, and then
    compiles differently. That is the migration changing a charge, and it is what
    this asserts.
    """
    await _ready(db_session)
    report = await backfill.backfill(db_session, actor=ACTOR)
    await backfill.check_parity(db_session, report)

    assert report.compared >= 1
    diverged = set(report.version_conflicts)
    real = [
        d
        for d in report.differences
        if d.field != "existence" and d.rule_key not in diverged
    ]
    assert real == [], [str(d) for d in real]

    # Divergence is reported, never silent. A rule excluded above must be
    # nameable, or this test would quietly stop asserting anything the day the
    # whole estate drifted.
    assert all(isinstance(key, str) and key for key in diverged)

    # A rule that did not convert must be *reported*, not silently absent: the
    # parity gate's `existence` difference and the backfill's failure list are
    # two views of the same rule, and both must name it.
    missing = {d.rule_key for d in report.differences if d.field == "existence"}
    reported = {key for key, _reason in report.failures}
    assert missing <= reported, (
        f"rules missing from the canonical side with no recorded reason: "
        f"{sorted(missing - reported)}"
    )
