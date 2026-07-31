"""Bulk validate, approve and revert over an imported rule set.

The transitions are not what these tests are about — ``ALLOWED_TRANSITIONS``
already governs those and is already tested per rule. What is tested here is the
handful of properties that only matter once an action covers five hundred rules
at once, and each of them exists because the obvious implementation gets it
wrong:

* a bulk approval that skips the caller's own work rather than refusing outright,
* atomicity, so a tariff is never half-approved,
* validation errors that cannot be overridden,
* blockers grouped by cause rather than listed per rule,
* an audit trail that does not reveal whether a human clicked once or five
  hundred times.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.core.errors import ValidationFailedError
from app.modules.rules.canonical.lineage import CanonicalRuleAudit
from app.modules.rules.canonical.sets import CanonicalRuleSet, RuleSetMember
from app.modules.rules.constants import RuleStatus
from app.modules.rules.ingest import files, kernel, resolver
from app.modules.rules.ingest import sets as import_sets
from app.modules.rules.lifecycle import service as svc
from app.modules.rules.lifecycle.selectors import Selector, resolve
from app.modules.rules.vocabulary.modes import RuleSetType
from app.modules.rules.vocabulary.sync import sync_vocabulary

pytestmark = pytest.mark.asyncio

TENANT = settings.default_tenant_id
MAKER = kernel.Actor("11111111-1111-1111-1111-111111111111", "The Maker")
CHECKER = kernel.Actor("22222222-2222-2222-2222-222222222222", "The Checker")

HEADER = (
    "rule_key,name,charging_mode,rule_type,service_type,destination_zone,"
    "rate,currency,unit,effective_from\n"
)


def _csv(*rows: str) -> bytes:
    return (HEADER + "".join(r + "\n" for r in rows)).encode()


def _row(key: str, *, zone: str = "LOCAL_ONNET", rate: str = "0.01") -> str:
    return (
        f"{key},{key} rule,PREPAID,BASE_TARIFF,VOICE,{zone},{rate},GBP,MINUTE,"
        "2026-01-01"
    )


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


async def _import(db, data: bytes, *, actor=MAKER, with_set: bool = True):
    """Import a file the way the endpoint does, returning (rules, rule_set)."""
    parsed = files.parse_file("t.csv", data, default_effective_from=date(2026, 1, 1))
    rule_set = (
        await import_sets.resolve(
            db, tenant_id=TENANT, rule_set_id=None, create=True,
            source_system_id=None, source_code=None, filename="t.csv",
            actor_id=actor.id,
        )
        if with_set
        else None
    )
    result = await kernel.ingest(
        db, parsed.drafts, actor=actor, channel="FILE",
        raw_records=parsed.raw,
        rule_set_code=rule_set.code if rule_set else None,
        promote_clean=True,
    )
    rules = (
        await resolve(db, Selector(rule_set_id=rule_set.rule_set_id), tenant_id=TENANT)
        if rule_set
        else []
    )
    return rules, rule_set, result


# --- B1: the import becomes addressable -------------------------------------


async def test_an_import_produces_a_rule_set_containing_its_rules(db_session):
    """The link the whole feature turns on. Without it, "approve everything from
    that import" is a five-hundred-element array a client has to assemble."""
    await _ready(db_session)
    rules, rule_set, _ = await _import(
        db_session, _csv(_row("SET1"), _row("SET2"), _row("SET3"))
    )
    assert rule_set is not None
    assert rule_set.set_type == RuleSetType.VENDOR_IMPORT
    assert len(rules) == 3

    members = (
        await db_session.execute(
            select(RuleSetMember).where(
                RuleSetMember.rule_set_id == rule_set.rule_set_id
            )
        )
    ).scalars().all()
    assert len(members) == 3


async def test_two_imports_in_one_minute_do_not_share_a_set(db_session):
    """A collision would put the second import's rules in the first one's set —
    exactly the mistake this feature exists to make impossible."""
    await _ready(db_session)
    _, first, _ = await _import(db_session, _csv(_row("COL1")))
    _, second, _ = await _import(db_session, _csv(_row("COL2")))
    assert first.rule_set_id != second.rule_set_id
    assert first.code != second.code


async def test_an_import_can_decline_a_set(db_session):
    """A three-rule correction does not want a new set every time, and one that
    fired automatically would bury the sets that matter."""
    await _ready(db_session)
    _, rule_set, _ = await _import(
        db_session, _csv(_row("NOSET")), with_set=False
    )
    assert rule_set is None


# --- F.2: where a clean import lands ----------------------------------------


async def test_a_cleanly_validated_import_lands_validated(db_session):
    """The validation genuinely happened during ingestion. Landing at DRAFT would
    make the operator's first bulk action a re-validation that changes nothing."""
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("CLEAN")))
    assert rules[0].status == RuleStatus.VALIDATED


async def test_an_import_with_warnings_still_lands_as_draft(db_session):
    """A warning is precisely the case a human should read before the rule moves
    on, so it does not get the promotion."""
    await _ready(db_session)
    broad = (
        b"rule_key,name,charging_mode,rule_type,service_type,rate,currency,unit,"
        b"effective_from\nWARN,Warn rule,PREPAID,BASE_TARIFF,VOICE,0.01,GBP,"
        b"MINUTE,2026-01-01\n"
    )
    rules, _, _ = await _import(db_session, broad)
    # No conditions -> a warning, so no promotion.
    assert rules[0].status == RuleStatus.DRAFT


# --- Selectors ---------------------------------------------------------------


def test_a_selector_must_name_exactly_one_thing():
    """A bulk operation with no stated target, or two, is a request nobody can
    audit afterwards."""
    with pytest.raises(ValidationFailedError):
        Selector()
    with pytest.raises(ValidationFailedError):
        Selector(batch_id="a", rule_set_id="b")


async def test_a_batch_selector_finds_what_that_batch_wrote(db_session):
    await _ready(db_session)
    _, _, result = await _import(db_session, _csv(_row("BSEL1"), _row("BSEL2")))
    found = await resolve(db_session, Selector(batch_id=result.batch_id),
                          tenant_id=TENANT)
    assert {r.rule_name for r in found} == {"BSEL1 rule", "BSEL2 rule"}


async def test_a_preview_batch_cannot_be_acted_on(db_session):
    """A dry run rolls its own batch row back, so there is nothing to select —
    which is the right outcome, reached by the strongest possible means."""
    from app.core.errors import NotFoundError

    await _ready(db_session)
    parsed = files.parse_file("t.csv", _csv(_row("DRY")),
                              default_effective_from=date(2026, 1, 1))
    result = await kernel.ingest(
        db_session, parsed.drafts, actor=MAKER, channel="FILE", dry_run=True
    )
    await _ready(db_session)
    with pytest.raises(NotFoundError):
        await resolve(db_session, Selector(batch_id=result.batch_id), tenant_id=TENANT)


async def test_an_unknown_filter_is_refused_rather_than_ignored(db_session):
    """Silently dropping a filter would make a bulk action cover more rules than
    the operator asked for."""
    await _ready(db_session)
    with pytest.raises(ValidationFailedError, match="Unknown filter"):
        await resolve(
            db_session, Selector(filters={"not_a_filter": "x"}), tenant_id=TENANT
        )


# --- B3: approval ------------------------------------------------------------


async def _approve(db, rules, *, actor, **kwargs):
    return await svc.approve(
        db, rules, selector={}, actor_id=actor.id, actor_name=actor.name, **kwargs
    )


async def test_the_maker_cannot_approve_their_own_import(db_session):
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("MC1"), _row("MC2")))
    result = await _approve(db_session, rules, actor=MAKER)
    assert result.counts.get(svc.Outcome.SKIPPED_OWN_WORK) == 2
    assert result.applied == 0


async def test_a_checker_can_approve_the_makers_import(db_session):
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("CH1"), _row("CH2")))
    result = await _approve(db_session, rules, actor=CHECKER)
    assert result.applied == 2
    for rule in rules:
        assert rule.status == RuleStatus.APPROVED


async def test_the_makers_own_rules_are_skipped_not_the_whole_batch(db_session):
    """Refusing everything because the caller authored some of it would teach
    operators to import under a shared account, which costs more accountability
    than it buys."""
    await _ready(db_session)
    mine, _, _ = await _import(db_session, _csv(_row("MIX1")), actor=MAKER)
    theirs, _, _ = await _import(db_session, _csv(_row("MIX2")), actor=CHECKER)

    result = await _approve(db_session, [*mine, *theirs], actor=MAKER, atomic=False)
    assert result.counts.get(svc.Outcome.SKIPPED_OWN_WORK) == 1
    assert result.applied == 1


async def test_maker_checker_can_be_turned_off_for_a_one_person_deployment(db_session):
    """Hard-coding it on makes a single-analyst deployment unable to activate
    anything it imported."""
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("SOLO")))
    result = await _approve(
        db_session, rules, actor=MAKER, enforce_maker_checker=False
    )
    assert result.applied == 1


async def test_approval_is_atomic_by_default(db_session):
    """A half-approved tariff prices traffic wrong in a way that reads as an
    engine fault rather than an incomplete action."""
    await _ready(db_session)
    good, _, _ = await _import(db_session, _csv(_row("ATOM1")), actor=CHECKER)
    # A rule that cannot be approved from its current live state.
    good[0].status = RuleStatus.ACTIVE
    await db_session.flush()
    more, _, _ = await _import(db_session, _csv(_row("ATOM2")), actor=CHECKER)

    result = await _approve(db_session, [*good, *more], actor=MAKER)
    assert result.applied == 0, "one blocked rule must stop the whole set"
    assert more[0].status != RuleStatus.APPROVED
    # And the response must not claim otherwise. Reporting a rule as APPLIED
    # after an atomic run wrote nothing shows a UI a success that did not happen.
    assert result.counts.get(svc.Outcome.HELD_BY_ATOMIC) == 1


async def test_partial_approval_is_available_when_asked_for(db_session):
    await _ready(db_session)
    blocked, _, _ = await _import(db_session, _csv(_row("PART1")), actor=CHECKER)
    blocked[0].status = RuleStatus.ACTIVE
    await db_session.flush()
    ok, _, _ = await _import(db_session, _csv(_row("PART2")), actor=CHECKER)

    result = await _approve(db_session, [*blocked, *ok], actor=MAKER, atomic=False)
    assert result.applied == 1
    assert ok[0].status == RuleStatus.APPROVED


async def test_explicit_approval_reintroduces_a_retired_rule_as_a_new_version(
    db_session,
):
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("RESTORE")), actor=CHECKER)
    rule = rules[0]
    original_id = rule.current_version_id
    from app.modules.rules.canonical.rule import CanonicalRuleVersion

    original = await db_session.get(CanonicalRuleVersion, original_id)
    rule.status = RuleStatus.RETIRED
    original.status = RuleStatus.RETIRED
    await db_session.flush()

    result = await _approve(db_session, rules, actor=MAKER)
    await db_session.refresh(rule)

    assert result.applied == 1
    assert rule.status == RuleStatus.APPROVED
    assert rule.current_version_id != original_id
    assert original.status == RuleStatus.RETIRED


async def test_a_rule_with_validation_errors_cannot_be_approved(db_session):
    """The one blocker with no override. `force` exists on snapshot compilation
    because a rule *set* can carry warnings a human may accept; a structurally
    invalid rule is not in that category."""
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("BAD")), actor=CHECKER)
    from app.modules.rules.canonical.rule import CanonicalRuleVersion
    from app.modules.rules.vocabulary.modes import ValidationState

    version = await db_session.get(CanonicalRuleVersion, rules[0].current_version_id)
    version.validation_state = ValidationState.ERROR
    await db_session.flush()

    result = await _approve(db_session, rules, actor=MAKER)
    assert result.counts.get(svc.Outcome.BLOCKED_VALIDATION) == 1
    assert result.applied == 0


async def test_a_dry_run_changes_nothing(db_session):
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("DRUN")), actor=CHECKER)
    result = await _approve(db_session, rules, actor=MAKER, dry_run=True)
    assert result.outcomes[0].outcome == svc.Outcome.APPLIED
    assert rules[0].status != RuleStatus.APPROVED


async def test_approving_twice_reports_already_rather_than_failing(db_session):
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("TWICE")), actor=CHECKER)
    await _approve(db_session, rules, actor=MAKER)
    again = await _approve(db_session, rules, actor=MAKER)
    assert again.counts.get(svc.Outcome.ALREADY) == 1


# --- Audit -------------------------------------------------------------------


async def test_every_hop_is_audited_separately(db_session):
    """A rule's history should not reveal whether a human clicked once or five
    hundred times — only that it moved, and who moved it."""
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("AUD")), actor=CHECKER)
    await _approve(db_session, rules, actor=MAKER, comment="looks right")
    await db_session.flush()

    trail = (
        await db_session.execute(
            select(CanonicalRuleAudit)
            .where(CanonicalRuleAudit.rule_id == rules[0].rule_id)
            .order_by(CanonicalRuleAudit.created_at)
        )
    ).scalars().all()
    actions = [a.action for a in trail]
    # Landed VALIDATED, so two hops remain — each its own entry.
    assert actions[-2:] == ["status_reviewed", "status_approved"]
    assert all(a.actor_name == MAKER.name for a in trail[-2:])
    assert trail[-1].comment == "looks right"
    assert trail[-1].channel == "BULK"


# --- Reporting ---------------------------------------------------------------


async def test_blockers_are_grouped_by_cause(db_session):
    """Thirty-two rules failing one check is one fix. Shown as thirty-two lines
    it reads as a broken batch, and an operator who concludes that stops reading
    the report at all."""
    await _ready(db_session)
    rules, _, _ = await _import(
        db_session, _csv(_row("GRP1"), _row("GRP2"), _row("GRP3"))
    )
    result = await _approve(db_session, rules, actor=MAKER)  # all own work
    blocked = result.blocked()
    assert len(blocked) == 1
    assert blocked[0]["count"] == 3
    assert blocked[0]["code"] == "maker_is_checker"
    assert len(blocked[0]["examples"]) == 3


async def test_a_selection_containing_live_rules_is_flagged(db_session):
    """The signal the approver-role gate reads. New drafts change no price until
    activation; anything already approved does."""
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("LIVE")), actor=CHECKER)
    before = await _approve(db_session, rules, actor=MAKER, dry_run=True)
    assert not before.touched_live_pricing

    await _approve(db_session, rules, actor=MAKER)
    after = await svc.revert(
        db_session, rules, selector={}, actor_id=MAKER.id,
        actor_name=MAKER.name, dry_run=True,
    )
    assert after.touched_live_pricing


# --- Revert ------------------------------------------------------------------


async def test_revert_sends_an_approved_set_back_to_draft(db_session):
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("REV1"), _row("REV2")),
                                actor=CHECKER)
    await _approve(db_session, rules, actor=MAKER)
    result = await svc.revert(
        db_session, rules, selector={}, actor_id=MAKER.id, actor_name=MAKER.name
    )
    assert result.applied == 2
    for rule in rules:
        assert rule.status == RuleStatus.DRAFT


async def test_revert_refuses_a_rule_that_is_past_publication(db_session):
    """A compiled rule is reached through its snapshot, and the undo for that is
    a snapshot rollback rather than a status change."""
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("REVX")), actor=CHECKER)
    rules[0].status = RuleStatus.ACTIVE
    await db_session.flush()

    result = await svc.revert(
        db_session, rules, selector={}, actor_id=MAKER.id, actor_name=MAKER.name
    )
    assert result.counts.get(svc.Outcome.BLOCKED_TRANSITION) == 1
    assert "snapshot" in result.outcomes[0].reason


# --- Size guard --------------------------------------------------------------


async def test_an_oversized_selection_is_refused_rather_than_timing_out(db_session):
    await _ready(db_session)

    class _Fake:
        rule_id = rule_key = rule_name = "x"
        status = RuleStatus.DRAFT

    with pytest.raises(ValidationFailedError, match="synchronous limit"):
        await svc.approve(
            db_session, [_Fake()] * (svc.MAX_SYNCHRONOUS + 1),
            selector={}, actor_id=MAKER.id, actor_name=MAKER.name,
        )


# --- Bulk validate -----------------------------------------------------------


async def test_bulk_validate_is_partial_by_design(db_session):
    """Forty failures out of five hundred is information, and withholding the
    four hundred and sixty clean verdicts helps nobody."""
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("VAL1"), _row("VAL2")))
    cache = await resolver.build(db_session, TENANT, with_rule_index=False)
    result = await svc.validate(
        db_session, rules, cache, selector={}, tenant_id=TENANT
    )
    assert result.total == 2
    assert result.applied == 2


async def test_bulk_validate_refreshes_the_stored_verdict(db_session):
    await _ready(db_session)
    rules, _, _ = await _import(db_session, _csv(_row("VSTATE")))
    from app.modules.rules.canonical.rule import CanonicalRuleVersion

    version = await db_session.get(CanonicalRuleVersion, rules[0].current_version_id)
    version.validation_state = "UNKNOWN"
    await db_session.flush()

    cache = await resolver.build(db_session, TENANT, with_rule_index=False)
    await svc.validate(db_session, rules, cache, selector={}, tenant_id=TENANT)
    await db_session.refresh(version)
    assert version.validation_state == "PASS"


# --- Routing -----------------------------------------------------------------


def test_the_lifecycle_surface_is_registered():
    from app.main import create_app

    paths = {r.path for r in create_app().routes}
    for expected in (
        "/api/rating/rule-lifecycle/preview",
        "/api/rating/rule-lifecycle/validate",
        "/api/rating/rule-lifecycle/approve",
        "/api/rating/rule-lifecycle/revert",
    ):
        assert expected in paths, f"missing route {expected}"


def test_bulk_retire_is_not_offered():
    """Deliberately absent. Retiring five hundred rules is the one operation here
    with no undo, and the safe version of it is a withdrawal proposal per rule."""
    from app.main import create_app

    paths = {r.path for r in create_app().routes}
    assert "/api/rating/rule-lifecycle/retire" not in paths


async def test_a_rule_set_is_reported_on_the_batch(db_session):
    """So the UI can offer "approve this import" straight after the upload."""
    await _ready(db_session)
    _, rule_set, _ = await _import(db_session, _csv(_row("RPT")))
    stored = await db_session.get(CanonicalRuleSet, rule_set.rule_set_id)
    assert stored is not None
    assert stored.set_type == RuleSetType.VENDOR_IMPORT
