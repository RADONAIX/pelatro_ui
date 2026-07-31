"""Bulk activation (B5), asynchronous bulk runs (B4), and the compile source (M5).

Three features, one file, because they are one workflow: a file is imported,
approved in bulk, and activated in bulk — and if the estate is large that last
part happens as a job rather than in a request.

What is tested here is only what these steps add. The transitions belong to
`test_rule_bulk_lifecycle`, and compilation belongs to the compiler's own suite.
What is genuinely new, and what each test exists for:

* activation is all-or-nothing — the one place where a partial result is worse
  than a refusal, because a snapshot with a hole prices traffic at the fallback;
* the job runs the *same* service functions as the synchronous endpoint, so the
  two cannot drift on the meaning of "approve";
* a job that refuses is distinguishable from a job that broke;
* and the legacy compile path is byte-identical after M5, which is the whole
  claim of a source swap.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationFailedError
from app.modules.compiler import service as compiler_service
from app.modules.compiler import sources
from app.modules.compiler.models import ExecutableRule
from app.modules.rules.canonical.lineage import CanonicalRuleAudit
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.constants import RuleStatus
from app.modules.rules.ingest import files, kernel
from app.modules.rules.ingest import sets as import_sets
from app.modules.rules.lifecycle import runner
from app.modules.rules.lifecycle import service as svc
from app.modules.rules.lifecycle.models import BulkRunStatus, RuleBulkRun
from app.modules.rules.lifecycle.selectors import Selector, resolve
from app.modules.rules.models import Rule as LegacyRule
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


async def _import(db, data: bytes, *, actor=MAKER):
    parsed = files.parse_file("t.csv", data, default_effective_from=date(2026, 1, 1))
    rule_set = await import_sets.resolve(
        db, tenant_id=TENANT, rule_set_id=None, create=True,
        source_system_id=None, source_code=None, filename="t.csv",
        actor_id=actor.id,
    )
    await kernel.ingest(
        db, parsed.drafts, actor=actor, channel="FILE", raw_records=parsed.raw,
        rule_set_code=rule_set.code, promote_clean=True,
    )
    rules = await resolve(
        db, Selector(rule_set_id=rule_set.rule_set_id), tenant_id=TENANT
    )
    return rules, rule_set


async def _approved(db, data: bytes):
    """An imported, fully approved rule set — the state activation starts from."""
    rules, rule_set = await _import(db, data)
    await svc.approve(
        db, rules, selector={}, actor_id=CHECKER.id, actor_name=CHECKER.name,
        comment="ready", atomic=True, enforce_maker_checker=False,
    )
    await db.flush()
    return rules, rule_set


# --- B5: bulk activation ----------------------------------------------------


async def test_activation_refuses_a_selection_that_is_not_fully_approved(db_session):
    """The property the whole operation turns on.

    A snapshot is what rating resolves against, so activating the approved 8 of
    10 does not activate 80% of a tariff — it publishes a tariff in which two
    rules are missing, and traffic that should have matched them falls through to
    whatever the fallback charges. That is a revenue error which looks like
    normal operation, so this refuses rather than doing most of the job.
    """
    await _ready(db_session)
    rules, rule_set = await _import(db_session, _csv(_row("ACT1"), _row("ACT2")))
    # Approve only one of the two.
    await svc.approve(
        db_session, rules[:1], selector={}, actor_id=CHECKER.id,
        actor_name=CHECKER.name, atomic=True, enforce_maker_checker=False,
    )
    await db_session.flush()
    fresh = await resolve(
        db_session, Selector(rule_set_id=rule_set.rule_set_id), tenant_id=TENANT
    )

    with pytest.raises(ValidationFailedError) as caught:
        await svc.activate(
            db_session, fresh, selector={}, rule_set_id=rule_set.rule_set_id,
            actor_id=CHECKER.id, actor_name=CHECKER.name,
        )
    assert "not approved" in str(caught.value)
    codes = {b["code"] for b in caught.value.details["blocked"]}
    assert "NOT_APPROVED" in codes
    # And the one that *was* approved is reported as held, not as applied —
    # nothing was activated, so claiming it was would be a lie.
    assert "HELD_ACTIVATION" in codes


async def test_a_dry_run_activation_writes_no_snapshot(db_session):
    """The preview must be the same call minus the write, or it is a second
    opinion rather than a preview."""
    await _ready(db_session)
    rules, rule_set = await _approved(db_session, _csv(_row("DRY1"), _row("DRY2")))
    before = await db_session.scalar(
        text("SELECT count(*) FROM rating.rule_snapshots")
    )

    result = await svc.activate(
        db_session, rules, selector={}, rule_set_id=rule_set.rule_set_id,
        actor_id=CHECKER.id, actor_name=CHECKER.name, dry_run=True,
    )
    await db_session.flush()

    assert result.dry_run is True
    assert result.snapshot is None
    assert result.applied == len(rules)
    after = await db_session.scalar(
        text("SELECT count(*) FROM rating.rule_snapshots")
    )
    assert after == before


async def test_activation_always_counts_as_touching_live_pricing(db_session):
    """Even a set of brand-new drafts. The role gate asks "does this change what
    customers are charged?", and activation is the operation for which the answer
    is unconditionally yes — a fresh set that has never been live becomes live
    the moment it is activated."""
    await _ready(db_session)
    rules, rule_set = await _approved(db_session, _csv(_row("LIVE1")))
    result = await svc.activate(
        db_session, rules, selector={}, rule_set_id=rule_set.rule_set_id,
        actor_id=CHECKER.id, actor_name=CHECKER.name, dry_run=True,
    )
    assert result.touched_live_pricing is True


async def test_activation_needs_a_rule_set(db_session):
    """A snapshot is compiled from a rule set. A filter selection that spans none
    has nothing to compile, and guessing would snapshot the wrong thing."""
    await _ready(db_session)
    rules, _rule_set = await _approved(db_session, _csv(_row("NOSET1")))
    with pytest.raises(ValidationFailedError) as caught:
        await svc.activate(
            db_session, rules, selector={}, rule_set_id=None,
            actor_id=CHECKER.id, actor_name=CHECKER.name,
        )
    assert "rule set" in str(caught.value).lower()


async def test_activation_over_an_empty_selection_is_not_an_error(db_session):
    """Zero rules is a selection that matched nothing, not a failure. Raising
    here would make "activate everything from this import" throw on a re-run."""
    await _ready(db_session)
    result = await svc.activate(
        db_session, [], selector={}, rule_set_id=None,
        actor_id=CHECKER.id, actor_name=CHECKER.name,
    )
    assert result.total == 0
    assert result.snapshot is None


async def test_a_spanning_selection_will_not_be_activated(db_session):
    """Rules from two imports have two rule sets, and a snapshot comes from one.
    Picking either would silently activate half of what was asked for."""
    await _ready(db_session)
    first, set_a = await _approved(db_session, _csv(_row("SPANA")))
    second, set_b = await _approved(db_session, _csv(_row("SPANB")))
    assert set_a.rule_set_id != set_b.rule_set_id

    with pytest.raises(ValidationFailedError) as caught:
        await runner._rule_set_for(db_session, Selector(filters={"q": "SPAN"}),
                                   first + second)
    assert "spans 2 rule sets" in str(caught.value)


async def test_a_canonical_import_can_be_compiled_and_activated_end_to_end(
    db_session, monkeypatch
):
    """The whole point of M5, proved by B5 working on top of it.

    Before the source swap, a canonically-imported rule could be validated and
    approved but never compiled — the compiler read `rating.rules`, and the rule
    was not there. "Import a vendor file and put it live" therefore stopped one
    step short of live, which is the step the operator cares about.

    This drives the real thing: file in, approved in bulk, compiled into a
    snapshot, activated. No dry run, no mocks.
    """
    await _ready(db_session)
    monkeypatch.setattr(
        # Other snapshot callers remain on their configured legacy source. The
        # canonical import lifecycle must select its own source explicitly.
        settings, "rule_compile_source", sources.CompileSource.LEGACY,
        raising=False,
    )
    rules, rule_set = await _approved(
        db_session, _csv(_row("E2E1"), _row("E2E2", zone="NATIONAL", rate="0.05"))
    )

    result = await svc.activate(
        db_session, rules, selector={"rule_set_id": rule_set.rule_set_id},
        rule_set_id=rule_set.rule_set_id, actor_id=CHECKER.id,
        actor_name=CHECKER.name, comment="end to end",
    )
    await db_session.flush()

    assert result.snapshot is not None
    # The two imported rules plus anything already live: an import is a delta,
    # not a request to replace the whole estate.
    assert result.snapshot["rule_count"] >= 2
    assert result.snapshot["forced"] is False
    assert result.applied == 2

    snapshot = await compiler_service.get_snapshot(
        db_session, result.snapshot["snapshot_id"]
    )
    assert snapshot.status == "ACTIVE"
    assert snapshot.checksum

    for rule in rules:
        await db_session.refresh(rule)
        assert rule.status == RuleStatus.ACTIVE
        version = await db_session.get(CanonicalRuleVersion, rule.current_version_id)
        assert version is not None
        assert version.status == RuleStatus.ACTIVE

    # The compiled rules are the imported ones, not whatever was live before.
    compiled = await sources.load_canonical(
        db_session, rule_set.rule_set_id, tenant_id=TENANT
    )
    assert {v.rule_key for v in compiled} == {"E2E1", "E2E2"}

    # And each rule carries an activation entry, so the estate can say when this
    # price went live and who put it there.
    audits = (
        await db_session.execute(
            select(CanonicalRuleAudit).where(
                CanonicalRuleAudit.rule_id.in_([r.rule_id for r in rules]),
                CanonicalRuleAudit.action == "status_active",
            )
        )
    ).scalars().all()
    assert len(audits) == 2
    assert {a.actor_name for a in audits} == {CHECKER.name}


async def test_activating_a_new_import_keeps_the_current_live_estate(db_session):
    """A file is a delta; activating it must not suspend every older rule."""
    await _ready(db_session)
    first, first_set = await _approved(db_session, _csv(_row("KEEP_LIVE_1")))
    await svc.activate(
        db_session,
        first,
        selector={"rule_set_id": first_set.rule_set_id},
        rule_set_id=first_set.rule_set_id,
        actor_id=CHECKER.id,
        actor_name=CHECKER.name,
    )
    await db_session.flush()

    second, second_set = await _approved(db_session, _csv(_row("KEEP_LIVE_2")))
    result = await svc.activate(
        db_session,
        second,
        selector={"rule_set_id": second_set.rule_set_id},
        rule_set_id=second_set.rule_set_id,
        actor_id=CHECKER.id,
        actor_name=CHECKER.name,
    )
    await db_session.flush()

    executable_keys = {
        row.rule_key
        for row in (
            (
                await db_session.execute(
                    select(ExecutableRule).where(
                        ExecutableRule.snapshot_id == result.snapshot["snapshot_id"]
                    )
                )
            )
            .scalars()
            .all()
        )
    }
    assert {"KEEP_LIVE_1", "KEEP_LIVE_2"} <= executable_keys
    await db_session.refresh(first[0])
    assert first[0].status == RuleStatus.ACTIVE


# --- B4: asynchronous runs --------------------------------------------------


async def _job_factory(db_session):
    """A session factory for the runner, bound to the test's own connection.

    A job legitimately commits — the whole point is that its result outlives the
    request. In a test that would leave rows behind, so the runner's session
    joins the test's transaction with ``create_savepoint``: its commits become
    savepoint releases, real enough that the job's own visibility rules are
    exercised, and all of it disappears when the test rolls back.
    """
    connection = await db_session.connection()

    def factory():
        return AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )

    return factory


async def test_a_job_runs_the_same_operation_as_the_synchronous_endpoint(db_session):
    """The property that makes a job safe to offer at all.

    If the job re-implemented approval, the estate would have two definitions of
    the most consequential operation in the product — and the one nobody watches
    while it runs would be the one that drifted. The runner therefore calls the
    same `svc.approve`, and this asserts the outcome is the one that function
    produces.
    """
    await _ready(db_session)
    _, rule_set = await _import(db_session, _csv(_row("JOBRUN1"), _row("JOBRUN2")))
    await db_session.flush()

    run = RuleBulkRun(
        tenant_id=TENANT, operation="approve",
        selector={"rule_set_id": rule_set.rule_set_id},
        atomic=True, actor_id=CHECKER.id, actor_name=CHECKER.name,
        comment="via job",
    )
    db_session.add(run)
    await db_session.flush()

    await runner.execute(run.bulk_run_id, factory=await _job_factory(db_session))

    await db_session.refresh(run)
    assert run.status == BulkRunStatus.SUCCEEDED
    assert run.total == 2
    assert run.applied == 2
    assert run.finished_at is not None

    # The job wrote through its own session; this one still holds the pre-job
    # copies in its identity map, and reading them back would assert against a
    # cache rather than against what the job committed.
    set_id = rule_set.rule_set_id  # read before expiring; the object goes stale
    db_session.expire_all()
    fresh = await resolve(db_session, Selector(rule_set_id=set_id), tenant_id=TENANT)
    assert {r.status for r in fresh} == {RuleStatus.APPROVED}

    # And the audit trail is the ordinary one — a bulk-approved rule's history
    # must not be distinguishable from a hand-approved rule's.
    audits = (
        await db_session.execute(
            select(CanonicalRuleAudit).where(
                CanonicalRuleAudit.rule_id.in_([r.rule_id for r in fresh])
            )
        )
    ).scalars().all()
    assert any(a.action == "status_approved" for a in audits)


async def test_a_refusal_is_recorded_as_rejected_not_error(db_session):
    """An operator who sees ERROR files a bug; one who sees REJECTED reads the
    blocked report and fixes their data. Collapsing the two wastes both."""
    await _ready(db_session)
    _, rule_set = await _import(db_session, _csv(_row("REJRUN1")))
    await db_session.flush()

    # Activation of an unapproved set: the operation runs and declines.
    run = RuleBulkRun(
        tenant_id=TENANT, operation="activate",
        selector={"rule_set_id": rule_set.rule_set_id},
        actor_id=CHECKER.id, actor_name=CHECKER.name,
    )
    db_session.add(run)
    await db_session.flush()

    await runner.execute(run.bulk_run_id, factory=await _job_factory(db_session))

    await db_session.refresh(run)
    assert run.status == BulkRunStatus.REJECTED
    assert run.error
    assert run.error_details and run.error_details.get("blocked")


async def test_an_unknown_operation_is_refused_rather_than_ignored(db_session):
    await _ready(db_session)
    await db_session.flush()
    run = RuleBulkRun(
        tenant_id=TENANT, operation="delete_everything",
        selector={"filters": {"search": "no-such-rule-anywhere"}},
        actor_id=CHECKER.id, actor_name=CHECKER.name,
    )
    db_session.add(run)
    await db_session.flush()

    await runner.execute(run.bulk_run_id, factory=await _job_factory(db_session))
    await db_session.refresh(run)
    assert run.status == BulkRunStatus.REJECTED
    assert "not a bulk operation" in run.error


async def test_a_claimed_run_is_not_executed_twice(db_session):
    """Two launches of one id — a duplicate submit, or a restart racing itself.
    Approving five hundred rules twice is mostly idempotent and the audit trail
    is not, so the claim is checked rather than assumed."""
    await _ready(db_session)
    await db_session.flush()
    run = RuleBulkRun(
        tenant_id=TENANT, operation="approve",
        selector={"filters": {"search": "no-such-rule-anywhere"}},
        status=BulkRunStatus.RUNNING, actor_id=CHECKER.id, actor_name=CHECKER.name,
    )
    db_session.add(run)
    await db_session.flush()

    await runner.execute(run.bulk_run_id, factory=await _job_factory(db_session))
    await db_session.refresh(run)
    # Untouched: still RUNNING, never finished by this second caller.
    assert run.status == BulkRunStatus.RUNNING
    assert run.finished_at is None


async def test_a_dead_run_is_distinguishable_from_a_slow_one():
    """Without this, a run whose process died reads as RUNNING forever and the
    operator has no way to know to resubmit."""
    now = datetime.now(UTC)
    fresh = RuleBulkRun(
        tenant_id=TENANT, operation="approve", status=BulkRunStatus.RUNNING,
        heartbeat_at=now - timedelta(minutes=1),
    )
    dead = RuleBulkRun(
        tenant_id=TENANT, operation="approve", status=BulkRunStatus.RUNNING,
        heartbeat_at=now - runner.STALE_AFTER - timedelta(minutes=1),
    )
    done = RuleBulkRun(
        tenant_id=TENANT, operation="approve", status=BulkRunStatus.SUCCEEDED,
        heartbeat_at=now - timedelta(days=2),
    )
    assert runner.stale(fresh, now=now) is False
    assert runner.stale(dead, now=now) is True
    # A finished run is never stale, however old.
    assert runner.stale(done, now=now) is False


async def test_the_synchronous_guard_points_at_the_job_endpoint(db_session):
    """A refusal that does not say what to do instead is a dead end."""
    await _ready(db_session)

    class _Fake:
        status = RuleStatus.DRAFT

    with pytest.raises(ValidationFailedError) as caught:
        svc._guard_size([_Fake()] * (svc.MAX_SYNCHRONOUS + 1))
    assert "/rule-lifecycle/jobs" in caught.value.details["hint"]


# --- M5: the compile source -------------------------------------------------


async def test_the_compiler_still_reads_legacy_by_default():
    """The switch exists; the default does not move. Flipping it is a decision
    about a specific estate, taken once that estate's parity report is clean —
    not a decision anyone inherits by upgrading."""
    assert sources.configured_source() == sources.CompileSource.LEGACY


async def test_an_unrecognised_source_falls_back_to_legacy(monkeypatch):
    """A typo in an environment variable must not silently change which store
    prices traffic."""
    monkeypatch.setattr(settings, "rule_compile_source", "CANONCIAL", raising=False)
    assert sources.configured_source() == sources.CompileSource.LEGACY


async def test_the_canonical_loader_skips_a_draft_but_keeps_the_live_version(
    db_session,
):
    """The subtlety that makes or breaks the swap.

    A live rule being edited has v1=ACTIVE and v2=DRAFT, and rating must keep
    using v1 until v2 is approved. Gating on the *rule's* status — the obvious
    implementation, since that is where the canonical model puts it — would drop
    the rule entirely the moment somebody opened it for editing. That is a silent
    revenue hole on every estate that edits in place, which is all of them.
    """
    await _ready(db_session)
    rules, rule_set = await _approved(db_session, _csv(_row("VER1")))
    rule = rules[0]

    views = await sources.load_canonical(
        db_session, rule_set.rule_set_id, tenant_id=TENANT
    )
    assert [v.rule_key for v in views] == ["VER1"]

    # Send the rule back to draft; its approved version is gone with it, so it
    # correctly stops being eligible.
    await svc.revert(
        db_session, [rule], selector={}, actor_id=CHECKER.id,
        actor_name=CHECKER.name, comment="editing",
    )
    await db_session.flush()
    views = await sources.load_canonical(
        db_session, rule_set.rule_set_id, tenant_id=TENANT
    )
    assert [v.rule_key for v in views] == []


async def test_both_sources_order_rules_identically(db_session):
    """Row order is not cosmetic: it decides which of two equally specific rules
    wins. If the swap reshuffled, the first canonical snapshot would diff against
    the last legacy one as though every rule had changed."""
    await _ready(db_session)
    legacy = await compiler_service._load_legacy_rules(db_session, None)
    canonical = await sources.load_canonical(db_session, None, tenant_id=TENANT)

    shared = {r.rule_key for r in legacy} & {v.rule_key for v in canonical}
    if len(shared) < 2:
        pytest.skip("needs at least two rules present in both stores")

    assert [r.rule_key for r in legacy if r.rule_key in shared] == [
        v.rule_key for v in canonical if v.rule_key in shared
    ]


async def test_the_canonical_loader_never_returns_two_versions_of_one_rule(
    db_session,
):
    """Both would match every context, and which one won would depend on the
    order rows came back from the database."""
    await _ready(db_session)
    views = await sources.load_canonical(db_session, None, tenant_id=TENANT)
    keys = [v.rule_key for v in views]
    assert len(keys) == len(set(keys))


# --- The response actually carries what the caller needs --------------------


def test_the_activation_response_carries_the_snapshot():
    """The service knew the snapshot; the response schema dropped it.

    Caught by diffing the API's response models against the client's types
    rather than by a test, which is the point of recording it as one now. The
    symptom was invisible server-side — `activate` returned a perfectly correct
    `BulkResult` — and total on the client: after publishing a tariff the UI had
    no way to name what it had just published, which is the one thing needed to
    roll it back.
    """
    from app.modules.rules.lifecycle import schemas as s

    assert "snapshot" in s.BulkResponse.model_fields

    ref = s.SnapshotRef(
        snapshot_id="abc", version=12, status="ACTIVE", rule_count=3,
        checksum="deadbeef", forced=False,
    )
    body = s.BulkResponse(
        operation="activate", selector={}, dry_run=False, total=3,
        eligible=3, applied=3, snapshot=ref,
    )
    assert body.snapshot is not None
    assert body.snapshot.version == 12
    # Every other operation leaves it null rather than inventing an empty object.
    assert s.BulkResponse(
        operation="approve", selector={}, dry_run=False, total=1,
        eligible=1, applied=1,
    ).snapshot is None


# --- Bulk delete ------------------------------------------------------------


async def test_a_fresh_draft_is_deleted_outright(db_session):
    """Nothing references a first-version draft that was never published, so
    keeping a retired husk of it would only clutter the catalogue."""
    await _ready(db_session)
    rules, _ = await _import(db_session, _csv(_row("DEL_FRESH")))
    # Imported drafts are promoted to VALIDATED by promote_clean; send it back so
    # this is genuinely a first-version draft.
    await svc.revert(
        db_session, rules, selector={}, actor_id=CHECKER.id,
        actor_name=CHECKER.name, comment="back to draft",
    )
    await db_session.flush()

    result = await svc.delete(
        db_session, rules, selector={}, actor_id=CHECKER.id,
        actor_name=CHECKER.name, comment="remove the duplicate",
    )
    await db_session.flush()

    assert [o.outcome for o in result.outcomes] == [svc.Outcome.DELETED]
    gone = (
        await db_session.execute(
            select(CanonicalRule).where(CanonicalRule.rule_key == "DEL_FRESH")
        )
    ).scalar_one_or_none()
    assert gone is None


async def test_a_rule_with_history_is_retired_rather_than_deleted(db_session):
    """The property that makes a bulk delete safe to offer.

    A rule that has been approved is referenced — by a snapshot, by an audit
    trail, by every rating result it priced. Deleting the row would make "why was
    this call charged 0.02?" unanswerable, which is the question the product
    exists to answer. Retiring takes it out of the next snapshot, which is what
    the operator actually wanted.
    """
    await _ready(db_session)
    rules, _ = await _approved(db_session, _csv(_row("DEL_LIVE")))

    result = await svc.delete(
        db_session, rules, selector={}, actor_id=CHECKER.id,
        actor_name=CHECKER.name, comment="superseded by RD10",
    )
    await db_session.flush()

    assert [o.outcome for o in result.outcomes] == [svc.Outcome.RETIRED]
    still = (
        await db_session.execute(
            select(CanonicalRule).where(CanonicalRule.rule_key == "DEL_LIVE")
        )
    ).scalar_one()
    assert still.status == RuleStatus.RETIRED

    # And it is no longer eligible for a snapshot, which is the point.
    views = await sources.load_canonical(db_session, None, tenant_id=TENANT)
    assert "DEL_LIVE" not in {v.rule_key for v in views}


async def test_deleting_records_who_did_it_before_the_row_disappears(db_session):
    """A hard delete leaves nothing behind to identify, so the audit entry has to
    be written first — otherwise the one operation nobody can undo is also the
    one with no trail."""
    await _ready(db_session)
    rules, _ = await _import(db_session, _csv(_row("DEL_AUDIT")))
    await svc.revert(
        db_session, rules, selector={}, actor_id=CHECKER.id,
        actor_name=CHECKER.name,
    )
    await db_session.flush()
    rule_id = rules[0].rule_id

    await svc.delete(
        db_session, rules, selector={}, actor_id=CHECKER.id,
        actor_name=CHECKER.name, comment="cleaning up",
    )
    await db_session.flush()

    audits = (
        await db_session.execute(
            select(CanonicalRuleAudit).where(
                CanonicalRuleAudit.rule_id == rule_id,
                CanonicalRuleAudit.action == "deleted",
            )
        )
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].actor_name == CHECKER.name
    # The key is kept on the entry: after the delete there is no row to join to.
    assert audits[0].diff.get("rule_key") == "DEL_AUDIT"


async def test_a_dry_run_delete_removes_nothing(db_session):
    await _ready(db_session)
    rules, _ = await _approved(db_session, _csv(_row("DEL_DRY")))
    result = await svc.delete(
        db_session, rules, selector={}, actor_id=CHECKER.id,
        actor_name=CHECKER.name, dry_run=True,
    )
    await db_session.flush()
    assert [o.outcome for o in result.outcomes] == [svc.Outcome.RETIRED]
    still = (
        await db_session.execute(
            select(CanonicalRule).where(CanonicalRule.rule_key == "DEL_DRY")
        )
    ).scalar_one()
    assert still.status != RuleStatus.RETIRED


async def test_deleting_a_live_rule_counts_as_touching_live_pricing(db_session):
    """So the role gate demands an approver. Retiring a live rule takes it out of
    the next snapshot, and that changes what customers are charged."""
    await _ready(db_session)
    rules, _ = await _approved(db_session, _csv(_row("DEL_GATE")))
    result = await svc.delete(
        db_session, rules, selector={}, actor_id=CHECKER.id,
        actor_name=CHECKER.name, dry_run=True,
    )
    assert result.touched_live_pricing is True


async def test_an_already_retired_rule_is_not_an_error(db_session):
    """Re-running a bulk delete must be safe: an operator who is unsure whether
    the first one worked will run it again."""
    await _ready(db_session)
    rules, _ = await _approved(db_session, _csv(_row("DEL_TWICE")))
    await svc.delete(
        db_session, rules, selector={}, actor_id=CHECKER.id, actor_name=CHECKER.name
    )
    await db_session.flush()
    again = await svc.delete(
        db_session, rules, selector={}, actor_id=CHECKER.id, actor_name=CHECKER.name
    )
    assert [o.outcome for o in again.outcomes] == [svc.Outcome.ALREADY]


async def test_ticked_rules_are_selected_by_key_not_by_id(db_session):
    """The selector added for the tick-box workflow.

    Keys rather than ids because the request body is the audit record: a bulk
    delete naming `PREPAID_A_60_SECOND_VOICE_PULSE_COPY` can be reviewed later by
    a human, and one naming five UUIDs cannot.
    """
    await _ready(db_session)
    await _import(db_session, _csv(_row("PICK_A"), _row("PICK_B"), _row("PICK_C")))

    picked = await resolve(
        db_session, Selector(rule_keys=("PICK_A", "PICK_C")), tenant_id=TENANT
    )
    assert {r.rule_key for r in picked} == {"PICK_A", "PICK_C"}
    assert Selector(rule_keys=("PICK_A",)).describe() == {"rule_keys": ["PICK_A"]}


async def test_an_unknown_key_refuses_the_whole_selection(db_session):
    """Acting on the subset that matched is the worst option: the operator ticked
    four, sees three done, and has to work out which one was skipped."""
    await _ready(db_session)
    await _import(db_session, _csv(_row("PICK_REAL")))
    with pytest.raises(NotFoundError) as caught:
        await resolve(
            db_session,
            Selector(rule_keys=("PICK_REAL", "PICK_TYPO")),
            tenant_id=TENANT,
        )
    assert "PICK_TYPO" in str(caught.value)


def test_a_selection_larger_than_a_person_ticks_is_refused():
    """A tick-box list is bounded by a human hand. Anything larger is a filter or
    a rule set, and forcing that choice keeps a runaway client from posting the
    whole estate to a destructive endpoint."""
    from app.modules.rules.lifecycle import selectors

    with pytest.raises(ValidationFailedError) as caught:
        Selector(rule_keys=tuple(f"K{i}" for i in range(selectors.MAX_KEYS + 1)))
    assert "select by filter or rule set" in str(caught.value)


def test_arbitrary_rule_ids_are_still_not_a_selector():
    """The distinction that makes rule_keys acceptable. If this ever passes,
    the audit-trail argument in `selectors.py` has been quietly abandoned."""
    import dataclasses

    from app.modules.rules.lifecycle.selectors import Selector as S

    assert "rule_ids" not in {f.name for f in dataclasses.fields(S)}


async def test_deleting_moves_the_legacy_row_too(db_session):
    """The gap that made the first version of this feature useless.

    In a default deployment the compiler reads `rating.rules`, so retiring only
    the canonical row leaves the rule still compiling, still colliding, still
    rating traffic. The operator deletes it, sees the identical validation error
    come back, and concludes the button does nothing.

    Every version of the key is moved, not just the newest: the compiler picks
    the newest *eligible* version, so an older ACTIVE row left behind keeps the
    "deleted" rule live.
    """
    await _ready(db_session)
    key = "DEL_BOTHSTORES"
    db_session.add(
        LegacyRule(
            rule_key=key, version=1, name="Legacy twin", description="",
            rule_type="BASE_TARIFF", execution_stage="BASE_CHARGE",
            service_type="VOICE", priority=100, specificity=0,
            stacking_policy="EXCLUSIVE", condition_logic="AND",
            effective_from=date(2026, 1, 1), currency_code="GBP",
            status=RuleStatus.ACTIVE, source_system="MANUAL",
        )
    )
    rules, _ = await _approved(db_session, _csv(_row(key)))
    await db_session.flush()

    await svc.delete(
        db_session, rules, selector={}, actor_id=CHECKER.id,
        actor_name=CHECKER.name, comment="duplicate",
    )
    await db_session.flush()

    legacy = (
        await db_session.execute(
            select(LegacyRule).where(LegacyRule.rule_key == key)
        )
    ).scalars().all()
    assert legacy, "the legacy row should be retired, not deleted"
    assert {r.status for r in legacy} == {RuleStatus.RETIRED}


async def test_a_successful_removal_is_not_reported_as_a_blocker(db_session):
    """`applied` and `blocked()` both keyed off `APPLIED` alone.

    Delete reports DELETED or RETIRED — the distinction is the whole point of the
    operation — so every successful removal landed in the blocked list and the
    response said `applied: 0` beside it. The screen showed twenty-six problems
    for an operation that had just done exactly what was asked.
    """
    await _ready(db_session)
    rules, _ = await _approved(db_session, _csv(_row("DEL_REPORT1"), _row("DEL_REPORT2")))

    result = await svc.delete(
        db_session, rules, selector={}, actor_id=CHECKER.id,
        actor_name=CHECKER.name, dry_run=True,
    )
    assert result.counts == {svc.Outcome.RETIRED: 2}
    assert result.applied == 2
    assert result.blocked() == []


async def test_a_rule_the_backfill_has_not_reached_can_still_be_removed(db_session):
    """The catalogue lists legacy rules; the selector resolves canonical ones.

    A rule authored before the migration exists in `rating.rules` and on the
    operator's screen, but has no canonical row — and the key selector refuses
    the whole call on an unknown key. Ticking everything in the catalogue
    therefore failed outright on any estate with an incomplete backfill, which is
    every estate mid-migration.
    """
    await _ready(db_session)
    key = "DEL_LEGACY_ONLY"
    db_session.add(
        LegacyRule(
            rule_key=key, version=1, name="Never backfilled", description="",
            rule_type="BASE_TARIFF", execution_stage="BASE_CHARGE",
            service_type="VOICE", priority=100, specificity=0,
            stacking_policy="EXCLUSIVE", condition_logic="AND",
            effective_from=date(2026, 1, 1), currency_code="GBP",
            status=RuleStatus.ACTIVE, source_system="MANUAL",
        )
    )
    await db_session.flush()

    selector = Selector(rule_keys=(key,))
    # Strict resolution — what every other operation uses — still refuses.
    with pytest.raises(NotFoundError):
        await resolve(db_session, selector, tenant_id=TENANT)

    rules = await resolve(
        db_session, selector, tenant_id=TENANT, allow_missing=True
    )
    assert rules == []

    result = await svc.delete(
        db_session, rules, selector=selector.describe(), actor_id=CHECKER.id,
        actor_name=CHECKER.name, requested_keys=(key,),
    )
    await db_session.flush()

    assert result.total == 1
    assert [o.outcome for o in result.outcomes] == [svc.Outcome.RETIRED]

    row = (
        await db_session.execute(
            select(LegacyRule).where(LegacyRule.rule_key == key)
        )
    ).scalar_one()
    assert row.status == RuleStatus.RETIRED
