"""Validate a rule set, compile it into a snapshot, activate and roll back."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.modules.compiler import compiler, conflicts, sources
from app.modules.compiler.constants import SnapshotStatus
from app.modules.compiler.models import ExecutableRule, RuleSnapshot
from app.modules.compiler.schemas import (
    RuleSetValidationReport,
    SnapshotDiff,
    SnapshotDiffEntry,
)
from app.modules.rules import service as rule_svc
from app.modules.rules import validation as rule_validation
from app.modules.rules.constants import RuleStatus, ValidationSeverity
from app.modules.rules.models import Rule
from app.modules.rules.schemas import ValidationIssue

log = get_logger("compiler")


async def _load_rules(
    db: AsyncSession,
    rule_set_id: str | None,
    *,
    source: str | None = None,
) -> list[Rule] | list[Any]:
    """The latest version of every rule eligible for a snapshot.

    Reads whichever store `RULE_COMPILE_SOURCE` names. The canonical branch
    returns `compilerview.RuleView` objects, which present exactly the attributes
    `compile_rule` reads — so this is a change of *source*, not of compiler, and
    the parity gate comparing the two remains a statement about the data.
    """
    selected = (source or sources.configured_source()).upper()
    if selected == sources.CompileSource.CANONICAL:
        return await sources.load_canonical(db, rule_set_id)
    return await _load_legacy_rules(db, rule_set_id)


async def _load_legacy_rules(
    db: AsyncSession, rule_set_id: str | None
) -> list[Rule]:
    """Unchanged since before M5. Left exactly as it was so the legacy path
    cannot drift while both stores are live."""
    stmt = (
        select(Rule)
        .options(selectinload(Rule.conditions), selectinload(Rule.actions))
        .where(Rule.status.in_(
            [
                RuleStatus.APPROVED.value,
                RuleStatus.COMPILED.value,
                RuleStatus.PUBLISHED.value,
                RuleStatus.ACTIVE.value,
            ]
        ))
    )
    if rule_set_id:
        stmt = stmt.where(Rule.rule_set_id == rule_set_id)

    rows = list((await db.execute(stmt)).scalars().all())
    # One version per logical rule: the newest eligible one. Two versions of the
    # same rule in a snapshot would both match every context.
    newest: dict[str, Rule] = {}
    for rule in rows:
        current = newest.get(rule.rule_key)
        if current is None or rule.version > current.version:
            newest[rule.rule_key] = rule
    return sorted(newest.values(), key=lambda r: (r.execution_stage, -r.specificity, r.rule_key))


async def validate_rule_set(
    db: AsyncSession,
    *,
    rule_set_id: str | None,
    include_coverage: bool = True,
    source: str | None = None,
) -> RuleSetValidationReport:
    rules = await _load_rules(db, rule_set_id, source=source)

    structural: list[ValidationIssue] = []
    for rule in rules:
        report = await rule_validation.validate_rule(db, rule)
        for issue in report.issues:
            if issue.severity == ValidationSeverity.ERROR:
                structural.append(
                    ValidationIssue(
                        severity=issue.severity,
                        code=issue.code,
                        message=f"{rule.rule_key} v{rule.version}: {issue.message}",
                        path=f"{rule.rule_key}:v{rule.version}",
                        hint=issue.hint,
                    )
                )

    conflict_issues = conflicts.detect_conflicts(rules)
    coverage_issues = (
        await conflicts.detect_coverage_gaps(db, rules) if include_coverage else []
    )

    everything = structural + conflict_issues + coverage_issues
    errors = sum(1 for i in everything if i.severity == ValidationSeverity.ERROR)
    warnings = sum(1 for i in everything if i.severity == ValidationSeverity.WARNING)

    return RuleSetValidationReport(
        checked_at=datetime.now(UTC),
        rule_count=len(rules),
        error_count=errors,
        warning_count=warnings,
        can_compile=errors == 0 and len(rules) > 0,
        structural=structural,
        conflicts=conflict_issues,
        coverage=coverage_issues,
    )


async def compile_snapshot(
    db: AsyncSession,
    *,
    name: str,
    description: str,
    rule_set_id: str | None,
    force: bool,
    actor_id: str,
    actor_name: str,
    source: str | None = None,
    carry_forward_active: bool = False,
) -> RuleSnapshot:
    started = time.perf_counter()
    selected = (source or sources.configured_source()).upper()
    canonical = selected == sources.CompileSource.CANONICAL
    rules = await _load_rules(db, rule_set_id, source=selected)
    if not rules:
        raise ValidationFailedError(
            "There are no approved rules to compile.",
            details={"hint": "Approve at least one rule first — drafts are not compiled."},
        )

    report = await validate_rule_set(
        db, rule_set_id=rule_set_id, source=selected
    )
    if report.error_count and not force:
        raise ValidationFailedError(
            f"The rule set has {report.error_count} blocking issue(s).",
            details={
                "error_count": report.error_count,
                "issues": [
                    # mode="json" so the enum renders as "ERROR" rather than
                    # <ValidationSeverity.ERROR: 'ERROR'> in the structured log.
                    # The HTTP body was always fine — StrEnum encodes as its
                    # value — but a log line that looks like a serialisation bug
                    # costs somebody twenty minutes.
                    i.model_dump(mode="json")
                    for i in (report.structural + report.conflicts)
                    if i.severity == ValidationSeverity.ERROR
                ][:25],
                "hint": "Fix them, or compile with force=true to record an override.",
            },
        )

    next_version = int(
        (await db.execute(select(func.coalesce(func.max(RuleSnapshot.version), 0)))).scalar_one()
    ) + 1
    effective_from, effective_to = compiler.snapshot_window(rules)

    snapshot = RuleSnapshot(
        version=next_version,
        name=name,
        description=description,
        # A canonical set lives in `ra_rule.rule_set`; the legacy column's FK
        # points at `rating.rule_sets`, so the id goes in whichever column can
        # actually hold it. Both are nullable, and exactly one is ever set.
        rule_set_id=None if canonical else rule_set_id,
        canonical_rule_set_id=rule_set_id if canonical else None,
        status=SnapshotStatus.PUBLISHED.value,
        effective_from=effective_from,
        effective_to=effective_to,
        checksum="",
        compiled_by=actor_id,
        compiled_by_name=actor_name,
    )
    db.add(snapshot)
    await db.flush()

    references = await compiler._reference_index(db)
    payloads = await compiler._catalog_payloads(db)
    codes = await compiler._code_index(db)

    executables: list[ExecutableRule] = []
    for rule in rules:
        try:
            executables.append(
                compiler.compile_rule(rule, snapshot.id, references, payloads, codes)
            )
        except compiler.CompileError as exc:
            raise ValidationFailedError(str(exc)) from exc

    if carry_forward_active:
        # Activating an imported set is additive. A snapshot is the whole live
        # estate, not merely the delta in the uploaded file. Copy the immutable
        # executable rows from the current snapshot and let newly compiled rule
        # keys replace matching old ones.
        selected_keys = {rule.rule_key for rule in executables}
        current = await active_snapshot(db)
        if current is not None:
            carried = (
                (
                    await db.execute(
                        select(ExecutableRule).where(
                            ExecutableRule.snapshot_id == current.id,
                            ExecutableRule.rule_key.notin_(selected_keys),
                        )
                    )
                )
                .scalars()
                .all()
            )
            executables.extend(
                _copy_executable(rule, snapshot.id) for rule in carried
            )

    db.add_all(executables)

    # The initial window was calculated from the imported delta. Once the live
    # estate is carried forward, the snapshot window must describe the complete
    # executable set.
    snapshot.effective_from = min(rule.effective_from for rule in executables)
    ends = [rule.effective_to for rule in executables]
    snapshot.effective_to = (
        None if any(end is None for end in ends) else max(end for end in ends if end)
    )

    snapshot.rule_count = len(executables)
    snapshot.checksum = compiler.checksum(executables)
    stats = compiler.build_stats(executables, rules)
    stats["compile_ms"] = round((time.perf_counter() - started) * 1000, 1)
    stats["forced"] = force
    stats["blocking_issues_overridden"] = report.error_count if force else 0
    snapshot.stats = stats
    snapshot.product_count = stats["distinct_products"]
    snapshot.issues = [
        i.model_dump()
        for i in (report.structural + report.conflicts + report.coverage)
    ][:200]

    # Mark the source rules COMPILED so the catalogue shows what is in a
    # snapshot without joining through executable_rules.
    #
    # A `RuleView` is a read projection over the canonical tables, not a mapped
    # row, so it is promoted through its own model rather than by assignment —
    # writing to the view would update nothing and report success.
    if canonical:
        await sources.mark_compiled(db, [r.rule_key for r in rules], tenant_id=None)
    else:
        for rule in rules:
            if rule.status == RuleStatus.APPROVED.value:
                rule.status = RuleStatus.COMPILED.value

    await db.flush()
    await db.refresh(snapshot)
    log.info(
        "snapshot_compiled",
        version=snapshot.version,
        rules=snapshot.rule_count,
        checksum=snapshot.checksum[:12],
        ms=stats["compile_ms"],
    )
    return snapshot


def _copy_executable(rule: ExecutableRule, snapshot_id: str) -> ExecutableRule:
    """Copy one immutable executable row into a replacement snapshot."""
    values = {
        column.name: getattr(rule, column.name)
        for column in ExecutableRule.__table__.columns
        if column.name not in {"id", "snapshot_id", "created_at"}
    }
    return ExecutableRule(snapshot_id=snapshot_id, **values)


async def get_snapshot(db: AsyncSession, snapshot_id: str) -> RuleSnapshot:
    snapshot = await db.get(RuleSnapshot, snapshot_id)
    if snapshot is None:
        raise NotFoundError(f"Snapshot '{snapshot_id}' was not found.")
    return snapshot


async def active_snapshot(db: AsyncSession) -> RuleSnapshot | None:
    return (
        await db.execute(
            select(RuleSnapshot)
            .where(RuleSnapshot.status == SnapshotStatus.ACTIVE.value)
            .order_by(RuleSnapshot.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def activate(
    db: AsyncSession, snapshot_id: str, *, actor_id: str
) -> RuleSnapshot:
    """Make a snapshot the one rating runs resolve against.

    Exactly one snapshot is ACTIVE at a time. The previous one is SUPERSEDED,
    never deleted — results reference it.
    """
    snapshot = await get_snapshot(db, snapshot_id)
    if snapshot.status == SnapshotStatus.ACTIVE.value:
        raise ConflictError(f"Snapshot {snapshot.version} is already active.")
    if snapshot.status == SnapshotStatus.FAILED.value:
        raise ConflictError("A failed snapshot cannot be activated.")

    now = datetime.now(UTC)
    current = await active_snapshot(db)
    if current is not None:
        current.status = SnapshotStatus.SUPERSEDED.value
        current.superseded_at = now

    snapshot.status = SnapshotStatus.ACTIVE.value
    snapshot.activated_by = actor_id
    snapshot.activated_at = now
    snapshot.superseded_at = None

    # Promote the rules this snapshot contains to ACTIVE, and retire the
    # versions the previous snapshot had that this one no longer carries.
    rule_ids = [
        rid
        for (rid,) in (
            await db.execute(
                select(ExecutableRule.rule_id).where(
                    ExecutableRule.snapshot_id == snapshot.id
                )
            )
        ).all()
    ]
    if rule_ids:
        for rule in (
            await db.execute(select(Rule).where(Rule.id.in_(rule_ids)))
        ).scalars().all():
            rule.status = RuleStatus.ACTIVE.value

    if current is not None:
        previous_ids = {
            rid
            for (rid,) in (
                await db.execute(
                    select(ExecutableRule.rule_id).where(
                        ExecutableRule.snapshot_id == current.id
                    )
                )
            ).all()
        }
        dropped = previous_ids - set(rule_ids)
        if dropped:
            for rule in (
                await db.execute(select(Rule).where(Rule.id.in_(dropped)))
            ).scalars().all():
                if rule.status == RuleStatus.ACTIVE.value:
                    rule.status = RuleStatus.SUPERSEDED.value

    await db.flush()
    await db.refresh(snapshot)
    log.info("snapshot_activated", version=snapshot.version, rules=len(rule_ids))
    return snapshot


async def rollback(db: AsyncSession, *, actor_id: str) -> RuleSnapshot:
    """Re-activate the most recently superseded snapshot."""
    previous = (
        await db.execute(
            select(RuleSnapshot)
            .where(RuleSnapshot.status == SnapshotStatus.SUPERSEDED.value)
            .order_by(RuleSnapshot.superseded_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if previous is None:
        raise ConflictError(
            "There is no superseded snapshot to roll back to.",
            details={"hint": "Rollback needs a previously active snapshot."},
        )
    return await activate(db, previous.id, actor_id=actor_id)


async def diff(db: AsyncSession, from_id: str, to_id: str) -> SnapshotDiff:
    """Compare two snapshots rule by rule.

    Keyed on ``rule_key`` rather than rule id, so a rule that gained a version
    reads as CHANGED rather than as one removal plus one addition.
    """
    a = await get_snapshot(db, from_id)
    b = await get_snapshot(db, to_id)

    async def _rules(snapshot_id: str) -> dict[str, ExecutableRule]:
        rows = (
            await db.execute(
                select(ExecutableRule).where(ExecutableRule.snapshot_id == snapshot_id)
            )
        ).scalars().all()
        return {r.rule_key: r for r in rows}

    left, right = await _rules(a.id), await _rules(b.id)
    entries: list[SnapshotDiffEntry] = []
    added = removed = changed = unchanged = 0

    for key in sorted(set(left) | set(right)):
        lhs, rhs = left.get(key), right.get(key)
        if lhs is None and rhs is not None:
            added += 1
            entries.append(
                SnapshotDiffEntry(rule_key=key, change="ADDED", to_version=rhs.rule_version)
            )
        elif rhs is None and lhs is not None:
            removed += 1
            entries.append(
                SnapshotDiffEntry(rule_key=key, change="REMOVED", from_version=lhs.rule_version)
            )
        elif lhs is not None and rhs is not None:
            details = _describe_change(lhs, rhs)
            if details:
                changed += 1
                entries.append(
                    SnapshotDiffEntry(
                        rule_key=key,
                        change="CHANGED",
                        from_version=lhs.rule_version,
                        to_version=rhs.rule_version,
                        details=details,
                    )
                )
            else:
                unchanged += 1

    return SnapshotDiff(
        from_snapshot=a.version,
        to_snapshot=b.version,
        added=added,
        removed=removed,
        changed=changed,
        unchanged=unchanged,
        identical=a.checksum == b.checksum,
        entries=entries,
    )


def _describe_change(a: ExecutableRule, b: ExecutableRule) -> list[str]:
    out: list[str] = []
    if a.rule_version != b.rule_version:
        out.append(f"version {a.rule_version} → {b.rule_version}")
    if a.signature != b.signature:
        out.append("match conditions changed")
    if a.actions != b.actions:
        out.append("actions changed")
    if a.priority != b.priority:
        out.append(f"priority {a.priority} → {b.priority}")
    if a.specificity != b.specificity:
        out.append(f"specificity {a.specificity} → {b.specificity}")
    if a.effective_from != b.effective_from or a.effective_to != b.effective_to:
        out.append("validity window changed")
    return out


async def snapshot_stats(db: AsyncSession) -> dict[str, Any]:
    total = int(
        (await db.execute(select(func.count()).select_from(RuleSnapshot))).scalar_one()
    )
    current = await active_snapshot(db)
    return {
        "total_snapshots": total,
        "active_version": current.version if current else None,
        "active_rule_count": current.rule_count if current else 0,
        "active_checksum": current.checksum if current else None,
    }


# Re-exported so callers do not reach into two modules for one workflow.
selectable = conflicts.selectable
latest_version = rule_svc.latest_version
