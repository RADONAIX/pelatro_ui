"""Bulk validate, approve and revert.

Plain async functions taking a session and a list of rules, so the same code
serves the synchronous endpoint and (later) the job runner. A bulk operation
that behaves differently at 201 rules than at 199 is one nobody can reason
about, so there is exactly one implementation and the wrapper adds progress
reporting and nothing else.

The transitions themselves are the boring part — ``ALLOWED_TRANSITIONS`` already
governs them and already refuses illegal hops. What this module is actually for
is the four properties that only matter at scale:

**Maker is not checker, per rule rather than per batch.** A bulk approval skips
the rules the caller wrote and approves the rest. Refusing the whole batch
because the caller authored eight of five hundred would teach operators to
import under a shared account, which costs more accountability than it buys.

**Validation errors block, with no override.** ``force`` exists on snapshot
compilation because a rule *set* can carry cross-rule warnings a human may
knowingly accept. A structurally invalid rule is not in that category, and an
override here would be used routinely within a month.

**Approval is atomic by default.** Approving 460 of 500 leaves a tariff where
the peak rate is live and the off-peak one is not. That does not fail loudly —
it prices traffic wrong in a way that reads as a rating bug for as long as it
takes somebody to notice.

**Every hop is audited separately.** Walking DRAFT → VALIDATED → REVIEWED →
APPROVED writes three audit entries, exactly as three clicks would. A rule's
history should not reveal how it was moved, only that it was and by whom.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailedError
from app.modules.mirror import hooks as mirror_hooks
from app.modules.rules.canonical import reader
from app.modules.rules.canonical.lineage import CanonicalRuleAudit
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.constants import ALLOWED_TRANSITIONS, RuleStatus
from app.modules.rules.ingest import kernel
from app.modules.rules.ingest.resolver import ResolutionCache
from app.modules.rules.models import Rule as LegacyRule
from app.modules.rules.validation import registry as validation
from app.modules.rules.vocabulary.modes import ValidationState

#: The ladder a bulk approval walks. Each hop is a real transition with its own
#: audit entry, so a bulk-approved rule's history is indistinguishable from a
#: hand-approved one.
APPROVAL_LADDER: tuple[str, ...] = (
    RuleStatus.VALIDATED,
    RuleStatus.REVIEWED,
    RuleStatus.APPROVED,
)

#: Above this, a synchronous request is the wrong shape. `/rule-lifecycle/jobs`
#: runs the identical operation with no ceiling — see the plan's B4.
#: A setting rather than a constant because the right number depends on the
#: estate and on how fast its database is.
MAX_SYNCHRONOUS = 2_000


class Outcome:
    """Why a rule was or was not moved. One vocabulary for every operation."""

    APPLIED = "APPLIED"
    #: Already where the operation would have put it. Not an error.
    ALREADY = "ALREADY"
    #: Has validation errors. The one blocker with no override.
    BLOCKED_VALIDATION = "BLOCKED_VALIDATION"
    #: The caller authored or imported it, and maker-checker is on.
    SKIPPED_OWN_WORK = "SKIPPED_OWN_WORK"
    #: The lifecycle does not allow this move from where the rule is.
    BLOCKED_TRANSITION = "BLOCKED_TRANSITION"
    #: No current version to act on.
    BLOCKED_NO_VERSION = "BLOCKED_NO_VERSION"
    #: Would have been applied, but the run is atomic and something else in the
    #: selection was blocked. Distinct from BLOCKED_* because the fix is not on
    #: this rule — it is on whatever stopped the batch.
    HELD_BY_ATOMIC = "HELD_BY_ATOMIC"
    #: Hard-deleted. Only ever a first-version draft that was never published.
    DELETED = "DELETED"
    #: Retired instead of deleted, because the rule has history — it has been
    #: validated, versioned or published, so something references it.
    RETIRED = "RETIRED"
    #: Activation only: the rule is not APPROVED, so the snapshot cannot carry
    #: it. Never overridable — compiling around it would activate a tariff with
    #: a hole in it, which prices traffic wrong rather than failing.
    BLOCKED_NOT_APPROVED = "BLOCKED_NOT_APPROVED"


#: Outcomes that mean the operation did what it set out to do. `delete` reports
#: DELETED or RETIRED rather than APPLIED — the distinction is the point of the
#: operation — and a success set that only knew about APPLIED counted every
#: successful removal as a blocker and reported `applied: 0` beside it.
_SUCCEEDED: frozenset[str] = frozenset(
    {Outcome.APPLIED, Outcome.DELETED, Outcome.RETIRED}
)


@dataclass(slots=True)
class RuleOutcome:
    rule_id: str
    rule_key: str
    rule_name: str
    outcome: str
    from_status: str = ""
    to_status: str = ""
    reason: str = ""
    #: Grouping key, so five hundred failures collapse to the handful of causes
    #: behind them.
    code: str = ""

    @property
    def applied(self) -> bool:
        return self.outcome in _SUCCEEDED


@dataclass(slots=True)
class BulkResult:
    operation: str
    selector: dict[str, Any]
    dry_run: bool
    total: int = 0
    outcomes: list[RuleOutcome] = field(default_factory=list)
    #: True when the set contains anything already live, so the caller needs the
    #: approver role rather than merely edit rights.
    touched_live_pricing: bool = False
    #: Activation only — the snapshot this run compiled and made live.
    snapshot: dict[str, Any] | None = None

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for entry in self.outcomes:
            out[entry.outcome] += 1
        return dict(out)

    @property
    def applied(self) -> int:
        return sum(1 for e in self.outcomes if e.applied)

    @property
    def eligible(self) -> int:
        return sum(
            1 for e in self.outcomes if e.outcome in (Outcome.APPLIED, Outcome.ALREADY)
        )

    def blocked(self) -> list[dict[str, Any]]:
        """Blockers grouped by cause, never one line per rule.

        Thirty-two rules failing one check is one fix. Shown as thirty-two lines
        it reads as a broken batch, and an operator who reaches that conclusion
        stops reading the report at all.
        """
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in self.outcomes:
            if entry.applied or entry.outcome == Outcome.ALREADY:
                continue
            key = (entry.outcome, entry.code or entry.reason)
            row = grouped.setdefault(
                key,
                {
                    "outcome": entry.outcome,
                    "code": entry.code,
                    "reason": entry.reason,
                    "count": 0,
                    "examples": [],
                },
            )
            row["count"] += 1
            if len(row["examples"]) < 5:
                row["examples"].append(entry.rule_key)
        return sorted(grouped.values(), key=lambda r: -r["count"])


#: Statuses that mean the rule is, or is about to be, pricing real traffic.
_LIVE: frozenset[str] = frozenset(
    {RuleStatus.APPROVED, RuleStatus.COMPILED, RuleStatus.PUBLISHED, RuleStatus.ACTIVE}
)


def _touches_live(rules: list[CanonicalRule]) -> bool:
    return any(r.status in _LIVE for r in rules)


def _guard_size(rules: list[CanonicalRule]) -> None:
    if len(rules) > MAX_SYNCHRONOUS:
        raise ValidationFailedError(
            f"This selection covers {len(rules):,} rules; the synchronous limit is "
            f"{MAX_SYNCHRONOUS:,}. Narrow the selection, or run it as a job.",
            details={
                "count": len(rules),
                "limit": MAX_SYNCHRONOUS,
                "hint": "POST the same body to /rule-lifecycle/jobs and poll "
                        "/rule-lifecycle/jobs/{id} for progress.",
            },
        )


# --- Validate ---------------------------------------------------------------


async def validate(
    db: AsyncSession,
    rules: list[CanonicalRule],
    cache: ResolutionCache,
    *,
    selector: dict[str, Any],
    tenant_id: str,
    dry_run: bool = False,
) -> BulkResult:
    """Re-run every tier over each rule and refresh its persisted issues.

    Partial by design, unlike approval: validating five hundred rules and having
    forty fail is *information*, and refusing to record the four hundred and
    sixty clean verdicts because forty were not would make the operation useless
    for the case it exists to serve.
    """
    _guard_size(rules)
    result = BulkResult(
        operation="validate",
        selector=selector,
        dry_run=dry_run,
        total=len(rules),
        touched_live_pricing=_touches_live(rules),
    )

    for rule in rules:
        if rule.current_version_id is None:
            result.outcomes.append(
                _outcome(rule, Outcome.BLOCKED_NO_VERSION,
                         reason="The rule has no version to validate.")
            )
            continue

        draft = await reader.read_draft(db, rule.current_version_id)
        report = await validation.check_draft(draft, cache)
        version = await db.get(CanonicalRuleVersion, rule.current_version_id)

        if not dry_run:
            await validation.persist(
                db, rule.current_version_id, tenant_id, report
            )
            if version is not None:
                version.validation_state = report.state

        if report.valid:
            result.outcomes.append(
                _outcome(rule, Outcome.APPLIED, to_status=report.state)
            )
        else:
            first = report.errors[0]
            result.outcomes.append(
                _outcome(
                    rule, Outcome.BLOCKED_VALIDATION,
                    reason=first.message, code=first.code,
                )
            )

    if not dry_run:
        await db.flush()
    return result


# --- Approve ----------------------------------------------------------------


async def approve(
    db: AsyncSession,
    rules: list[CanonicalRule],
    *,
    selector: dict[str, Any],
    actor_id: str | None,
    actor_name: str,
    comment: str = "",
    atomic: bool = True,
    enforce_maker_checker: bool = True,
    dry_run: bool = False,
) -> BulkResult:
    """Walk each rule up the ladder to APPROVED, or refuse the whole set.

    ``atomic`` defaults to true, and the caller must opt out explicitly. A
    half-approved tariff does not announce itself: the rules that made it are
    live, the ones that did not are not, and the resulting charges are wrong in a
    way that looks like an engine fault rather than an incomplete action.
    """
    _guard_size(rules)
    result = BulkResult(
        operation="approve",
        selector=selector,
        dry_run=dry_run,
        total=len(rules),
        touched_live_pricing=_touches_live(rules),
    )

    for rule in rules:
        result.outcomes.append(
            await _plan_approval(db, rule, actor_id, enforce_maker_checker)
        )

    blocked = [
        e for e in result.outcomes
        if e.outcome in (Outcome.BLOCKED_VALIDATION, Outcome.BLOCKED_TRANSITION,
                         Outcome.BLOCKED_NO_VERSION)
    ]
    if atomic and blocked:
        # Nothing is written — so nothing may still be reported as applied. A
        # response that says "460 approved" after an atomic run wrote zero rows
        # is worse than a failure: a UI shows a success, an operator moves on,
        # and the tariff is not live.
        held = (
            f"Held: {len(blocked)} of {len(rules)} rules in this selection are "
            "blocked, and the run is atomic."
        )
        for entry in result.outcomes:
            if entry.applied:
                entry.outcome = Outcome.HELD_BY_ATOMIC
                entry.to_status = ""
                entry.reason = held
                entry.code = "held_by_atomic"
        return result

    if dry_run:
        return result

    for entry in result.outcomes:
        if not entry.applied:
            continue
        rule = next(r for r in rules if r.rule_id == entry.rule_id)
        if rule.status == RuleStatus.RETIRED:
            await _reintroduce_retired(
                db, rule, actor_id=actor_id, actor_name=actor_name, comment=comment
            )
        await _walk(db, rule, actor_id, actor_name, comment)

    await db.flush()
    return result


async def _plan_approval(
    db: AsyncSession,
    rule: CanonicalRule,
    actor_id: str | None,
    enforce_maker_checker: bool,
) -> RuleOutcome:
    """Decide one rule's fate without changing anything.

    Separated from the write so that ``atomic`` is a real property rather than a
    best-effort one: every rule is judged before the first is moved.
    """
    if rule.status == RuleStatus.APPROVED:
        return _outcome(rule, Outcome.ALREADY, from_status=rule.status,
                        to_status=RuleStatus.APPROVED)

    if rule.current_version_id is None:
        return _outcome(rule, Outcome.BLOCKED_NO_VERSION,
                        reason="The rule has no version to approve.")

    if enforce_maker_checker and actor_id and rule.created_by == actor_id:
        return _outcome(
            rule, Outcome.SKIPPED_OWN_WORK, from_status=rule.status,
            reason="You created this rule, so somebody else must approve it.",
            code="maker_is_checker",
        )

    version = await db.get(CanonicalRuleVersion, rule.current_version_id)
    if version is not None and version.validation_state == ValidationState.ERROR:
        return _outcome(
            rule, Outcome.BLOCKED_VALIDATION, from_status=rule.status,
            reason="The rule has validation errors. Fix them and re-validate.",
            code="validation_error",
        )

    if rule.status == RuleStatus.RETIRED:
        return _outcome(
            rule,
            Outcome.APPLIED,
            from_status=RuleStatus.RETIRED,
            to_status=RuleStatus.APPROVED,
            reason="Approval will reintroduce this retired rule as a new version.",
            code="reintroduce_retired",
        )

    path = _ladder_from(rule.status)
    if path is None:
        return _outcome(
            rule, Outcome.BLOCKED_TRANSITION, from_status=rule.status,
            reason=f"A rule in '{rule.status}' cannot be approved.",
            code="illegal_transition",
        )
    return _outcome(rule, Outcome.APPLIED, from_status=rule.status,
                    to_status=RuleStatus.APPROVED)


async def _reintroduce_retired(
    db: AsyncSession,
    rule: CanonicalRule,
    *,
    actor_id: str | None,
    actor_name: str,
    comment: str,
) -> None:
    """Copy a retired version before approving it; never mutate history.

    This path is reachable only from an explicit bulk Approve operation. The
    retired version remains retired and continues to explain old results; the
    copy becomes the current DRAFT and follows the normal approval ladder.
    """
    if rule.current_version_id is None:
        raise ValidationFailedError(
            f"Retired rule '{rule.rule_key}' has no version to reintroduce."
        )
    draft = await reader.read_draft(db, rule.current_version_id)
    draft = replace(
        draft,
        change_reason=comment or "Reintroduced by explicit bulk approval.",
    )
    batch = await kernel.ingest(
        db,
        [draft],
        actor=kernel.Actor(actor_id, actor_name),
        channel="BULK",
        tenant_id=rule.tenant_id,
        status=RuleStatus.DRAFT,
        stop_on_error=True,
        force_version=True,
    )
    if not batch.records or not batch.records[0].committed:
        reason = batch.records[0].reason if batch.records else "No version was written."
        raise ValidationFailedError(
            f"Retired rule '{rule.rule_key}' could not be reintroduced.",
            details={"reason": reason},
        )
    await db.refresh(rule)


def _ladder_from(status: str) -> tuple[str, ...] | None:
    """The hops needed to get from here to APPROVED, or None if there is no path.

    Walked rather than jumped so each transition is checked by the same
    ``ALLOWED_TRANSITIONS`` table a single-rule move uses — a bulk path that
    skipped straight to APPROVED would be a second lifecycle, and the two would
    disagree the first time somebody added a status.
    """
    try:
        start = APPROVAL_LADDER.index(status)
    except ValueError:
        return APPROVAL_LADDER if status == RuleStatus.DRAFT else None
    return APPROVAL_LADDER[start + 1 :]


async def _walk(
    db: AsyncSession,
    rule: CanonicalRule,
    actor_id: str | None,
    actor_name: str,
    comment: str,
) -> None:
    """Move one rule up the ladder, auditing every hop."""
    version = await db.get(CanonicalRuleVersion, rule.current_version_id)
    now = datetime.now(UTC)

    for target in _ladder_from(rule.status) or ():
        allowed = ALLOWED_TRANSITIONS.get(rule.status, ())
        if target not in allowed:
            return
        previous = rule.status
        rule.status = target
        rule.updated_by = actor_id
        if version is not None:
            version.status = target
            if target == RuleStatus.REVIEWED:
                version.submitted_by, version.submitted_at = actor_id, now
            elif target == RuleStatus.APPROVED:
                version.approved_by, version.approved_at = actor_id, now

        db.add(
            CanonicalRuleAudit(
                tenant_id=rule.tenant_id,
                rule_id=rule.rule_id,
                rule_version_id=rule.current_version_id,
                version_number=getattr(version, "version_number", None),
                action=f"status_{target.lower()}",
                from_status=previous,
                to_status=target,
                channel="BULK",
                actor_id=actor_id,
                actor_name=actor_name,
                comment=comment,
            )
        )
        # The mirrored rule's status must follow the ladder, or the target keeps
        # reporting DRAFT for a rule that went live weeks ago.
        if rule.current_version_id:
            mirror_hooks.record_rule_version(db, rule.current_version_id)


# --- Revert -----------------------------------------------------------------


async def revert(
    db: AsyncSession,
    rules: list[CanonicalRule],
    *,
    selector: dict[str, Any],
    actor_id: str | None,
    actor_name: str,
    comment: str = "",
    dry_run: bool = False,
) -> BulkResult:
    """Send a set back to DRAFT — the undo for a bulk approval.

    Only from the pre-publication statuses. A rule that has been compiled into a
    snapshot is not revertible by moving its status: the snapshot is what rating
    resolves against, and the undo for that is a snapshot rollback.
    """
    _guard_size(rules)
    result = BulkResult(
        operation="revert",
        selector=selector,
        dry_run=dry_run,
        total=len(rules),
        touched_live_pricing=_touches_live(rules),
    )

    for rule in rules:
        if rule.status == RuleStatus.DRAFT:
            result.outcomes.append(
                _outcome(rule, Outcome.ALREADY, from_status=rule.status,
                         to_status=RuleStatus.DRAFT)
            )
            continue
        if RuleStatus.DRAFT not in ALLOWED_TRANSITIONS.get(rule.status, ()):
            result.outcomes.append(
                _outcome(
                    rule, Outcome.BLOCKED_TRANSITION, from_status=rule.status,
                    reason=(
                        f"A rule in '{rule.status}' cannot go back to draft. "
                        "Roll back the snapshot instead."
                    ),
                    code="illegal_transition",
                )
            )
            continue
        result.outcomes.append(
            _outcome(rule, Outcome.APPLIED, from_status=rule.status,
                     to_status=RuleStatus.DRAFT)
        )

    if dry_run:
        return result

    for entry in result.outcomes:
        if not entry.applied:
            continue
        rule = next(r for r in rules if r.rule_id == entry.rule_id)
        previous = rule.status
        rule.status = RuleStatus.DRAFT
        rule.updated_by = actor_id
        version = (
            await db.get(CanonicalRuleVersion, rule.current_version_id)
            if rule.current_version_id
            else None
        )
        if version is not None:
            version.status = RuleStatus.DRAFT
        db.add(
            CanonicalRuleAudit(
                tenant_id=rule.tenant_id,
                rule_id=rule.rule_id,
                rule_version_id=rule.current_version_id,
                version_number=getattr(version, "version_number", None),
                action="status_draft",
                from_status=previous,
                to_status=RuleStatus.DRAFT,
                channel="BULK",
                actor_id=actor_id,
                actor_name=actor_name,
                comment=comment,
            )
        )
        if rule.current_version_id:
            mirror_hooks.record_rule_version(db, rule.current_version_id)

    await db.flush()
    return result


def _outcome(
    rule: CanonicalRule,
    outcome: str,
    *,
    from_status: str = "",
    to_status: str = "",
    reason: str = "",
    code: str = "",
) -> RuleOutcome:
    return RuleOutcome(
        rule_id=rule.rule_id,
        rule_key=rule.rule_key,
        rule_name=rule.rule_name,
        outcome=outcome,
        from_status=from_status or rule.status,
        to_status=to_status,
        reason=reason,
        code=code,
    )


# --- Activate ---------------------------------------------------------------


async def activate(
    db: AsyncSession,
    rules: list[CanonicalRule],
    *,
    selector: dict[str, Any],
    rule_set_id: str | None,
    actor_id: str,
    actor_name: str,
    comment: str = "",
    force: bool = False,
    dry_run: bool = False,
) -> BulkResult:
    """Compile a selection into a snapshot and make it live. Plan step **B5**.

    The shape of this operation is different from its siblings and the difference
    is not cosmetic. ``validate`` and ``approve`` act on rules one at a time and
    report per-rule outcomes; a partial result is useful. Activation cannot be
    partial. A snapshot is the unit rating resolves against, so activating "most
    of" a tariff publishes a tariff with a hole in it — and a hole prices traffic
    at whatever the fallback is rather than failing visibly. That is worse than
    not activating at all, so this refuses unless every selected rule is eligible.

    It still returns a ``BulkResult``, because the caller's question is the same
    one — "what happened to my import?" — and answering it in a different shape
    for this one verb makes the UI carry two renderers for one workflow.
    """
    result = BulkResult(
        operation="activate", selector=selector, dry_run=dry_run, total=len(rules)
    )
    if not rules:
        return result
    _guard_size(rules)
    result.touched_live_pricing = True  # activation is, definitionally, live

    # Every rule must already be APPROVED. ACTIVE counts — re-activating a set
    # that contains what is already live is how a corrected rule reaches traffic.
    ready: list[CanonicalRule] = []
    for rule in rules:
        if rule.status in (RuleStatus.APPROVED, RuleStatus.COMPILED,
                           RuleStatus.PUBLISHED, RuleStatus.ACTIVE):
            ready.append(rule)
            result.outcomes.append(_outcome(rule, Outcome.APPLIED,
                                            to_status=RuleStatus.ACTIVE))
        else:
            result.outcomes.append(_outcome(
                rule, Outcome.BLOCKED_NOT_APPROVED,
                reason=f"Status is {rule.status}; activation needs APPROVED.",
                code="NOT_APPROVED",
            ))

    blocked = [o for o in result.outcomes if o.outcome == Outcome.BLOCKED_NOT_APPROVED]
    if blocked:
        # Downgrade the would-have-worked rules: nothing is being applied, and
        # reporting them as APPLIED when no snapshot exists would be a lie.
        for entry in result.outcomes:
            if entry.outcome == Outcome.APPLIED:
                entry.outcome = Outcome.HELD_BY_ATOMIC
                entry.to_status = ""
                entry.reason = "Held: other rules in the selection are not approved."
                entry.code = "HELD_ACTIVATION"
        retired = [rule for rule in rules if rule.status == RuleStatus.RETIRED]
        hint = (
            "These rules were retired. Re-import them to create a fresh version, "
            "then approve and activate that version."
            if len(retired) == len(blocked)
            else "Approve the whole selection first — a snapshot missing rules "
            "prices traffic at the fallback rather than failing."
        )
        raise ValidationFailedError(
            f"{len(blocked)} of {len(rules)} rules are not approved, so this "
            f"selection cannot be activated.",
            details={
                "blocked": result.blocked(),
                "hint": hint,
            },
        )

    if dry_run:
        return result

    if not rule_set_id:
        raise ValidationFailedError(
            "Activation needs a rule set: a snapshot is compiled from one.",
            details={"hint": "Select by rule_set_id, or by a batch that created one."},
        )

    # Imported here rather than at module scope: the compiler imports the rule
    # modules, and a top-level import in both directions is a cycle.
    from app.modules.compiler import service as compiler_service
    from app.modules.compiler.sources import CompileSource

    snapshot = await compiler_service.compile_snapshot(
        db,
        name=comment or f"Bulk activation by {actor_name}",
        description=f"Activated from selection {selector}",
        rule_set_id=rule_set_id,
        force=force,
        actor_id=actor_id,
        actor_name=actor_name,
        # This lifecycle selects CanonicalRule rows and a ra_rule.rule_set id.
        # The global compiler source may deliberately remain LEGACY for other
        # callers during migration; using it here would hand a canonical set id
        # to rating.rules and produce an empty snapshot.
        source=CompileSource.CANONICAL,
        carry_forward_active=True,
    )
    snapshot = await compiler_service.activate(db, snapshot.id, actor_id=actor_id)

    for rule in ready:
        previous = rule.status
        rule.status = RuleStatus.ACTIVE
        version = (
            await db.get(CanonicalRuleVersion, rule.current_version_id)
            if rule.current_version_id
            else None
        )
        if version is not None:
            version.status = RuleStatus.ACTIVE
        db.add(CanonicalRuleAudit(
            tenant_id=rule.tenant_id,
            rule_id=rule.rule_id,
            rule_version_id=rule.current_version_id,
            action="status_active",
            from_status=previous,
            to_status=RuleStatus.ACTIVE,
            channel="BULK",
            actor_id=actor_id,
            actor_name=actor_name,
            comment=comment or f"Snapshot v{snapshot.version}",
        ))
        if rule.current_version_id:
            mirror_hooks.record_rule_version(db, rule.current_version_id)

    result.snapshot = {
        "snapshot_id": snapshot.id,
        "version": snapshot.version,
        "status": snapshot.status,
        "rule_count": snapshot.rule_count,
        "checksum": snapshot.checksum,
        "forced": force,
    }
    return result


# --- Delete -----------------------------------------------------------------


async def delete(
    db: AsyncSession,
    rules: list[CanonicalRule],
    *,
    selector: dict[str, Any],
    actor_id: str,
    actor_name: str,
    comment: str = "",
    atomic: bool = True,
    dry_run: bool = False,
    requested_keys: tuple[str, ...] | None = None,
) -> BulkResult:
    """Remove a selection of rules — deleting what can be deleted, retiring the rest.

    The policy is not invented here. ``rules/service.py:delete_draft`` already
    settled it for one rule: a first-version draft is hard-deleted, and anything
    that has ever been validated, versioned or published is *retired* instead, so
    a rule that might already appear in an audit trail is never made to vanish.
    This applies exactly that rule five hundred times. A bulk path with a more
    permissive policy would be a way to launder a deletion the single-rule path
    refuses — and the audit trail is the product.

    So "delete these forty rules" does the safest thing that achieves the intent,
    and reports which rules got which outcome rather than picking one word for
    both. A retired rule stops reaching new snapshots, which is what the operator
    actually wanted; the row survives, which is what the six-month-old rating
    result referencing it needs.

    **Both stores, always.** Every other operation in this module is canonical-
    only, which is correct while a rule's *status* is a canonical concept. Delete
    is not: in a default deployment the compiler reads `rating.rules`
    (`RULE_COMPILE_SOURCE=LEGACY`), so retiring only the canonical row leaves the
    duplicate still colliding at compile time — the operator deletes the rule,
    watches the same validation error come back, and reasonably concludes the
    feature is broken. The legacy row is moved by the same policy, so "removed"
    means removed from whichever store actually prices traffic.

    ``requested_keys`` carries the keys the caller actually ticked, which may
    include rules the backfill has not reached — they exist in `rating.rules`
    and in the catalogue the operator is looking at, but have no canonical row.
    Those are removed through the legacy path alone. Dropping them silently
    would mean the operator ticks twenty-five rules, sees twenty-two removed,
    and has to work out which three the migration happens not to have covered.
    """
    known = {r.rule_key for r in rules}
    legacy_only = tuple(k for k in (requested_keys or ()) if k not in known)

    result = BulkResult(
        operation="delete", selector=selector, dry_run=dry_run,
        total=len(rules) + len(legacy_only),
    )
    if not rules and not legacy_only:
        return result
    _guard_size(rules)
    result.touched_live_pricing = _touches_live(rules)

    plans: list[tuple[CanonicalRule, str]] = []
    for rule in rules:
        outcome = await _plan_delete(db, rule)
        result.outcomes.append(outcome)
        if outcome.outcome in (Outcome.DELETED, Outcome.RETIRED):
            plans.append((rule, outcome.outcome))

    legacy_plans = await _plan_legacy_only(db, legacy_only)
    result.outcomes.extend(outcome for _, outcome in legacy_plans)
    if any(o.outcome in (Outcome.DELETED, Outcome.RETIRED) for _, o in legacy_plans):
        result.touched_live_pricing = result.touched_live_pricing or any(
            o.from_status in _LIVE for _, o in legacy_plans
        )

    blocked = [
        entry for entry in result.outcomes
        if entry.outcome not in (Outcome.DELETED, Outcome.RETIRED, Outcome.ALREADY)
    ]
    if atomic and blocked:
        for entry in result.outcomes:
            if entry.outcome in (Outcome.DELETED, Outcome.RETIRED):
                entry.outcome = Outcome.HELD_BY_ATOMIC
                entry.to_status = ""
                entry.reason = "Held: something else in the selection was blocked."
                entry.code = "held_by_atomic"
        return result

    if dry_run:
        return result

    for rule, action in plans:
        # Written *before* the delete. A hard delete leaves no row to identify
        # afterwards, and "who removed this rule and when" is precisely the
        # question an assurance platform must be able to answer.
        db.add(
            CanonicalRuleAudit(
                tenant_id=rule.tenant_id,
                rule_id=rule.rule_id,
                rule_version_id=rule.current_version_id,
                action="deleted" if action == Outcome.DELETED else "status_retired",
                from_status=rule.status,
                to_status="" if action == Outcome.DELETED else RuleStatus.RETIRED,
                channel="BULK",
                actor_id=actor_id,
                actor_name=actor_name,
                comment=comment,
                diff={"rule_key": rule.rule_key, "rule_name": rule.rule_name},
            )
        )
        if action == Outcome.DELETED:
            await db.delete(rule)
        else:
            rule.status = RuleStatus.RETIRED
            rule.updated_by = actor_id
            version = await db.get(CanonicalRuleVersion, rule.current_version_id)
            if version is not None:
                version.status = RuleStatus.RETIRED

    await _apply_legacy(
        db,
        [r.rule_key for r, _ in plans]
        + [key for key, o in legacy_plans if o.outcome != Outcome.ALREADY],
        actor_id,
    )
    await db.flush()
    return result


async def _plan_legacy_only(
    db: AsyncSession, rule_keys: tuple[str, ...]
) -> list[tuple[str, RuleOutcome]]:
    """Outcomes for keys that exist only in `rating.rules`.

    Same policy as everything else — a lone first-version draft goes, anything
    with history is retired — decided here so the response reports one verdict
    per ticked rule regardless of which store the rule happens to live in.
    """
    if not rule_keys:
        return []
    rows = (
        (
            await db.execute(
                select(LegacyRule).where(LegacyRule.rule_key.in_(rule_keys))
            )
        )
        .scalars()
        .all()
    )
    by_key: dict[str, list[LegacyRule]] = {}
    for row in rows:
        by_key.setdefault(row.rule_key, []).append(row)

    out: list[tuple[str, RuleOutcome]] = []
    for key in rule_keys:
        versions = by_key.get(key, [])
        if not versions:
            continue
        newest = max(versions, key=lambda r: r.version)
        if all(v.status == RuleStatus.RETIRED for v in versions):
            decision, to_status = Outcome.ALREADY, RuleStatus.RETIRED
        elif len(versions) == 1 and newest.status == RuleStatus.DRAFT:
            decision, to_status = Outcome.DELETED, ""
        else:
            decision, to_status = Outcome.RETIRED, RuleStatus.RETIRED
        out.append((
            key,
            RuleOutcome(
                rule_id=newest.id,
                rule_key=key,
                rule_name=newest.name,
                outcome=decision,
                from_status=newest.status,
                to_status=to_status,
                reason="Not yet in the canonical store; removed from the legacy "
                       "catalogue." if decision != Outcome.ALREADY else "",
            ),
        ))
    return out


async def _apply_legacy(
    db: AsyncSession, rule_keys: list[str], actor_id: str
) -> None:
    """Move the legacy rows for these keys by the same policy.

    Deliberately mirrors `rules/service.py:delete_draft` rather than reaching for
    a blanket delete: a first-version draft goes, everything else is retired.
    Every version of the key is retired, not just the newest — leaving an older
    ACTIVE version behind is exactly how a "deleted" rule keeps rating traffic,
    because the compiler picks the newest *eligible* version rather than the
    newest one.
    """
    if not rule_keys:
        return
    rows = list(
        (
            await db.execute(
                select(LegacyRule).where(LegacyRule.rule_key.in_(rule_keys))
            )
        )
        .scalars()
        .all()
    )
    by_key: dict[str, list[LegacyRule]] = {}
    for row in rows:
        by_key.setdefault(row.rule_key, []).append(row)

    for versions in by_key.values():
        virgin = len(versions) == 1 and versions[0].status == RuleStatus.DRAFT
        for row in versions:
            if virgin:
                # Captured before the delete: afterwards there is no row to name.
                mirror_hooks.record_legacy_rule_delete(db, row.id)
                await db.delete(row)
            elif row.status != RuleStatus.RETIRED:
                row.status = RuleStatus.RETIRED
                mirror_hooks.record_legacy_rule(db, row.id)


async def _plan_delete(db: AsyncSession, rule: CanonicalRule) -> RuleOutcome:
    """Decide one rule's fate without changing anything."""
    if rule.status == RuleStatus.RETIRED:
        return _outcome(rule, Outcome.ALREADY, to_status=RuleStatus.RETIRED)

    versions = list(
        (
            await db.execute(
                select(CanonicalRuleVersion).where(
                    CanonicalRuleVersion.rule_id == rule.rule_id
                )
            )
        )
        .scalars()
        .all()
    )
    published = any(v.published_snapshot_id for v in versions)
    virgin = (
        rule.status == RuleStatus.DRAFT
        and len(versions) <= 1
        and not published
        and all(v.version_number <= 1 for v in versions)
    )
    if virgin:
        return _outcome(rule, Outcome.DELETED, to_status="")

    if RuleStatus.RETIRED not in ALLOWED_TRANSITIONS.get(rule.status, ()):
        return _outcome(
            rule, Outcome.BLOCKED_TRANSITION,
            reason=f"A rule in '{rule.status}' cannot be retired.",
            code="illegal_transition",
        )
    return _outcome(rule, Outcome.RETIRED, to_status=RuleStatus.RETIRED)
