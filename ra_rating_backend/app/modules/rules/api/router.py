"""The canonical rule API: authoring (group 2) and validation (group 3).

Every write here goes through the ingestion kernel. The wizard is not a special
case — its payload becomes a ``CanonicalDraft`` and runs the same TYPE, RESOLVE,
VALIDATE, RECONCILE, COMMIT, PROJECT steps as a 40,000-row Ericsson dump. That
is the point of the kernel, and an API that bypassed it "just for manual
authoring" would recreate the divergence the whole phase exists to remove.

Two consequences worth knowing as a client:

**Saving is idempotent in the useful sense.** Posting a rule whose behaviour has
not changed returns ``decision: UNCHANGED`` and cuts no new version. The name and
description still follow, because those are not behaviour.

**Validation is never a separate opinion.** ``POST /validate`` runs exactly the
tiers a save runs, so a green validate followed by a failing save is not a state
this API can reach.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy import func, select

from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.errors import ConflictError, NotFoundError, RuleStateError
from app.core.rbac import RatingPermKey
from app.modules.rules.api import schemas as s
from app.modules.rules.api import service as svc
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.constants import ALLOWED_TRANSITIONS, EDITABLE_STATUSES, RuleStatus
from app.modules.rules.ingest import kernel, keys, resolver
from app.modules.rules.validation import registry as validation
from app.modules.rules.validation.issues import Report

router = APIRouter(prefix="/canonical-rules", tags=["canonical-rules"])

_view = require(RatingPermKey.RULES, "view")
_edit = require(RatingPermKey.RULES, "edit")
RuleEditor = principal_with(RatingPermKey.RULES, "edit")
RuleViewer = principal_with(RatingPermKey.RULES, "view")


def _issues(report: Report) -> list[s.IssueOut]:
    return [s.IssueOut(**issue.as_dict()) for issue in report.issues]


def _actor(principal) -> kernel.Actor:
    return kernel.Actor(id=principal.id, name=principal.full_name or principal.email)


# --- Group 2: rule management -----------------------------------------------


@router.get(
    "",
    response_model=s.ListEnvelope,
    summary="Search and filter the rule catalogue",
    dependencies=[Depends(_view)],
)
async def list_rules(
    db: DbSession,
    page: PageParams,
    search: str | None = Query(None, description="Matches name, key or description"),
    charging_mode: str | None = Query(None),
    service_type: str | None = Query(None),
    rule_type: str | None = Query(None),
    status: str | None = Query(None),
    product_id: str | None = Query(None),
    source_system_id: str | None = Query(None),
    snapshot_id: str | None = Query(None),
    validation_state: str | None = Query(None, description="PASS | WARNING | ERROR | UNKNOWN"),
    execution_mode: str | None = Query(None, description="ONLINE | OFFLINE | BOTH"),
    rule_set_id: str | None = Query(None),
    effective_on: date | None = Query(None, description="Rules live on this date"),
) -> s.ListEnvelope:
    """One row per logical rule, at its current version.

    Every filter is an indexed predicate on ``rule`` or ``rule_version`` — no
    JSONB scans, which is why Snapshot and Validation can be filters at all.
    """
    stmt = svc.build_list_query(
        search=search,
        charging_mode=charging_mode,
        service_type=service_type,
        rule_type=rule_type,
        status=status,
        product_id=product_id,
        source_system_id=source_system_id,
        snapshot_id=snapshot_id,
        validation_state=validation_state,
        execution_mode=execution_mode,
        rule_set_id=rule_set_id,
        effective_on=effective_on,
    )
    total = int(
        (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = (
        await db.execute(
            stmt.order_by(CanonicalRule.rule_key).limit(page.limit).offset(page.offset)
        )
    ).all()
    maps = await svc.lookup_maps(db)
    items = await svc.summarise(db, [(r, v) for r, v in rows], maps)
    return s.ListEnvelope(
        items=items, total=total, limit=page.limit, offset=page.offset
    )


@router.post(
    "",
    response_model=s.WriteResponse,
    status_code=201,
    summary="Create a draft rule",
)
async def create_rule(
    db: DbSession,
    principal: RuleEditor,
    payload: s.RuleWrite = Body(...),
) -> s.WriteResponse:
    """Author one rule. Runs the whole kernel, exactly as an import does.

    A rule that fails validation is **not** saved — the response carries the
    issues instead. A rule that only produces warnings *is* saved: an author must
    be able to store a rule that is merely broad, or the wizard cannot be used
    incrementally.
    """
    draft = svc.to_draft(payload)
    result = await kernel.ingest(
        db, [draft], actor=_actor(principal), channel="MANUAL", stop_on_error=True
    )
    return await _write_response(db, result, "created")


@router.post(
    "/{rule_id}/versions",
    response_model=s.WriteResponse,
    status_code=201,
    summary="Cut a new version of an existing rule",
)
async def new_version(
    db: DbSession,
    rule_id: str,
    principal: RuleEditor,
    payload: s.RuleWrite = Body(...),
) -> s.WriteResponse:
    """Change an approved rule by superseding it, never by editing it.

    That is what makes a rating result from February re-explicable in August: the
    version it references still says exactly what it said then.
    """
    rule = await svc.get_rule(db, rule_id)
    draft = svc.to_draft(payload)
    # The key is the rule's, not the payload's. A client that sent a different
    # one is describing a different rule, and silently retargeting the write
    # would move a version onto the wrong logical rule.
    draft = _rekey(draft, rule.rule_key)
    result = await kernel.ingest(
        db, [draft], actor=_actor(principal), channel="MANUAL", stop_on_error=True
    )
    return await _write_response(db, result, "version_created")


@router.patch(
    "/{rule_id}",
    response_model=s.WriteResponse,
    summary="Update a draft rule in place",
)
async def update_rule(
    db: DbSession,
    rule_id: str,
    principal: RuleEditor,
    payload: s.RuleWrite = Body(...),
) -> s.WriteResponse:
    """Editing is allowed only while the rule is a draft.

    Anything approved is immutable and takes a new version instead — the request
    is rejected rather than quietly converted, because "I edited it" and "I
    superseded it" are different events in an audit trail and the author needs to
    know which one happened.
    """
    rule = await svc.get_rule(db, rule_id)
    if rule.status not in EDITABLE_STATUSES:
        raise RuleStateError(
            f"A rule in '{rule.status}' cannot be edited.",
            details={
                "status": rule.status,
                "hint": "Cut a new version instead — approved rules are immutable.",
            },
        )
    draft = _rekey(svc.to_draft(payload), rule.rule_key)
    result = await kernel.ingest(
        db, [draft], actor=_actor(principal), channel="MANUAL", stop_on_error=True
    )
    return await _write_response(db, result, "updated")


@router.post(
    "/{rule_id}/clone",
    response_model=s.WriteResponse,
    status_code=201,
    summary="Copy a rule into a new logical rule at version 1",
)
async def clone_rule(
    db: DbSession,
    rule_id: str,
    principal: RuleEditor,
    payload: s.CloneRequest = Body(...),
) -> s.WriteResponse:
    from app.modules.rules.canonical import reader

    rule = await svc.get_rule(db, rule_id)
    if rule.current_version_id is None:
        raise NotFoundError("This rule has no version to copy.")

    draft = await reader.read_draft(db, rule.current_version_id)
    taken = await _taken_keys(db)
    key = payload.rule_key or keys.unique(keys.suggest(payload.rule_name), taken)
    if key in taken:
        raise ConflictError(
            f"Rule key '{key}' is already in use.", details={"rule_key": key}
        )

    from dataclasses import replace

    draft = replace(
        draft,
        rule_key=key,
        rule_name=payload.rule_name,
        change_reason=f"Cloned from {rule.rule_key}.",
    )
    result = await kernel.ingest(
        db, [draft], actor=_actor(principal), channel="MANUAL", stop_on_error=True
    )
    return await _write_response(db, result, "cloned")


@router.delete(
    "/{rule_id}",
    status_code=204,
    summary="Delete a first-version draft",
    dependencies=[Depends(_edit)],
)
async def delete_draft(db: DbSession, rule_id: str) -> None:
    """Hard-delete, and only for a draft that has never been anything else.

    Anything that has been validated, versioned or approved is retired instead,
    so a rule that might already appear in an audit trail or a rating result is
    never made to vanish.
    """
    rule = await svc.get_rule(db, rule_id)
    if rule.status != RuleStatus.DRAFT:
        raise RuleStateError(
            "Only a draft can be deleted; retire the rule instead.",
            details={"status": rule.status},
        )
    versions = int(
        (
            await db.execute(
                select(func.count())
                .select_from(CanonicalRuleVersion)
                .where(CanonicalRuleVersion.rule_id == rule_id)
            )
        ).scalar_one()
    )
    if versions > 1:
        raise RuleStateError(
            f"This rule has {versions} versions; retire it instead of deleting it.",
            details={"versions": versions},
        )
    await db.delete(rule)


# --- Static paths, declared before /{rule_id} ------------------------------
# Starlette matches in declaration order and has no regex path converter, so a
# literal segment declared after a path parameter is unreachable: /conflicts
# would arrive as rule_id='conflicts' and 404 with a message about a missing
# rule, which is a genuinely baffling thing to debug from the client side.


@router.get(
    "/key-suggestion",
    response_model=s.KeySuggestion,
    summary="Suggest a rule key from a name, uniqueness-checked",
    dependencies=[Depends(_view)],
)
async def suggest_key(db: DbSession, name: str = Query(min_length=1)) -> s.KeySuggestion:
    """The wizard's Step 1 helper.

    A *suggestion* only — nothing derives identity from a name behind the
    author's back. The legacy path raised a conflict and made the author invent a
    key; this returns the de-duplicated form so they can accept it.
    """
    taken = await _taken_keys(db)
    suggested = keys.suggest(name)
    return s.KeySuggestion(
        suggested=suggested,
        available=suggested not in taken,
        resolved=keys.unique(suggested, taken),
    )


@router.get(
    "/conflicts",
    response_model=s.ConflictReport,
    summary="Rules that can both win the same event",
    dependencies=[Depends(_view)],
)
async def detect_conflicts(
    db: DbSession,
    charging_mode: str | None = Query(None),
    service_type: str | None = Query(None),
) -> s.ConflictReport:
    """Cross-rule detection — the tier a single-rule validator cannot reach.

    Three kinds, and each is a different failure:

    *Overlapping windows in a conflict group* — two live prices for one event,
    the most expensive incident class there is.

    *Identical behaviour under different keys* — two rules that say the same
    thing, so a change to one silently leaves the other in place.

    *Same stage, same targeting, same priority* — a tie broken by nothing, which
    means the winner depends on row order and can change between compiles.
    """
    from app.modules.rules.api import conflicts

    return await conflicts.detect(
        db, charging_mode=charging_mode, service_type=service_type
    )


@router.get(
    "/{rule_id}",
    response_model=s.RuleDetail,
    summary="One rule, at its current version",
    dependencies=[Depends(_view)],
)
async def get_rule(db: DbSession, rule_id: str) -> s.RuleDetail:
    rule = await svc.get_rule(db, rule_id)
    return await svc.detail(db, rule, await svc.lookup_maps(db))


@router.get(
    "/{rule_id}/versions",
    response_model=list[s.VersionSummary],
    summary="Every version of a rule, newest first",
    dependencies=[Depends(_view)],
)
async def list_versions(db: DbSession, rule_id: str) -> list[s.VersionSummary]:
    await svc.get_rule(db, rule_id)
    rows = (
        await db.execute(
            select(CanonicalRuleVersion)
            .where(CanonicalRuleVersion.rule_id == rule_id)
            .order_by(CanonicalRuleVersion.version_number.desc())
        )
    ).scalars().all()
    return [s.VersionSummary.model_validate(r) for r in rows]


@router.get(
    "/{rule_id}/versions/{version_number}",
    response_model=s.RuleDetail,
    summary="One specific version, as it was written",
    dependencies=[Depends(_view)],
)
async def get_version(db: DbSession, rule_id: str, version_number: int) -> s.RuleDetail:
    rule = await svc.get_rule(db, rule_id)
    version = await svc.version_by_number(db, rule_id, version_number)
    return await svc.detail(db, rule, await svc.lookup_maps(db), version=version)


@router.post(
    "/{rule_id}/status",
    response_model=s.RuleDetail,
    summary="Submit, approve, reject or retire",
)
async def change_status(
    db: DbSession,
    rule_id: str,
    principal: RuleEditor,
    payload: s.StatusChange = Body(...),
) -> s.RuleDetail:
    """Move a rule through its lifecycle, with the same transitions the legacy
    model enforces — a rule cannot skip review, and cannot go back from retired."""
    rule = await svc.get_rule(db, rule_id)
    allowed = ALLOWED_TRANSITIONS.get(rule.status, ())
    if payload.status not in allowed:
        raise RuleStateError(
            f"Cannot move a rule from '{rule.status}' to '{payload.status}'.",
            details={"from": rule.status, "allowed": list(allowed)},
        )

    version = (
        await db.get(CanonicalRuleVersion, rule.current_version_id)
        if rule.current_version_id
        else None
    )
    now = datetime.now(UTC)
    rule.status = payload.status
    rule.updated_by = principal.id
    if version is not None:
        version.status = payload.status
        if payload.status == RuleStatus.REVIEWED:
            version.submitted_by, version.submitted_at = principal.id, now
        elif payload.status == RuleStatus.APPROVED:
            version.approved_by, version.approved_at = principal.id, now
        elif payload.status == RuleStatus.RETIRED:
            version.retired_at = now

    await _audit(db, rule, version, principal, payload)
    await db.flush()
    return await svc.detail(db, rule, await svc.lookup_maps(db), version=version)


@router.get(
    "/{rule_id}/audit",
    summary="Who changed what, when and why",
    dependencies=[Depends(_view)],
)
async def audit_trail(db: DbSession, rule_id: str) -> list[dict]:
    from app.modules.rules.canonical.lineage import CanonicalRuleAudit

    await svc.get_rule(db, rule_id)
    rows = (
        await db.execute(
            select(CanonicalRuleAudit)
            .where(CanonicalRuleAudit.rule_id == rule_id)
            .order_by(CanonicalRuleAudit.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "action": r.action,
            "version_number": r.version_number,
            "from_status": r.from_status,
            "to_status": r.to_status,
            "channel": r.channel,
            "batch_id": r.batch_id,
            "actor_id": r.actor_id,
            "actor_name": r.actor_name,
            "comment": r.comment,
            "diff": r.diff,
            "created_at": r.created_at,
        }
        for r in rows
    ]


# --- Group 3: validation ----------------------------------------------------


@router.post(
    "/validate",
    response_model=s.ValidationReport,
    summary="Validate a rule payload without saving it",
)
async def validate_payload(
    db: DbSession,
    principal: RuleViewer,
    payload: s.RuleWrite = Body(...),
) -> s.ValidationReport:
    """Exactly the tiers a save runs — structural, mode coherence, semantic.

    Not a second opinion: a green validate followed by a failing save is not a
    state this API can reach, because both call the same function.
    """
    del principal
    cache = await resolver.build(db, _tenant(), with_rule_index=False)
    report = await validation.check_draft(svc.to_draft(payload), cache)
    return _report(report)


@router.post(
    "/{rule_id}/validate",
    response_model=s.ValidationReport,
    summary="Re-validate a stored rule and refresh its issues",
)
async def validate_stored(
    db: DbSession, rule_id: str, principal: RuleEditor
) -> s.ValidationReport:
    """Re-checks the rule as stored and **replaces** its persisted issues.

    Replace rather than append: a stale issue against logic that has since been
    fixed is worse than no issue at all, because it makes the whole Validation
    column untrustworthy.
    """
    from app.modules.rules.canonical import reader

    del principal
    rule = await svc.get_rule(db, rule_id)
    if rule.current_version_id is None:
        raise NotFoundError("This rule has no version to validate.")

    draft = await reader.read_draft(db, rule.current_version_id)
    cache = await resolver.build(db, _tenant(), with_rule_index=False)
    report = await validation.check_draft(draft, cache)

    await validation.persist(db, rule.current_version_id, _tenant(), report)
    version = await db.get(CanonicalRuleVersion, rule.current_version_id)
    if version is not None:
        version.validation_state = report.state
    await db.flush()
    return _report(report)


@router.post(
    "/validate-batch",
    response_model=s.BatchValidationReport,
    summary="Validate many rules in one call",
)
async def validate_batch(
    db: DbSession,
    principal: RuleViewer,
    payload: list[s.RuleWrite] = Body(...),
) -> s.BatchValidationReport:
    """One resolution pass for the whole batch, not one per rule.

    A hundred rules validated individually is a hundred sets of catalogue
    queries; here the codes are resolved once, which is the same batching that
    turns a 40,000-row import from hours into minutes.
    """
    del principal
    cache = await resolver.build(db, _tenant(), with_rule_index=False)
    items: list[s.BatchValidationItem] = []
    grouped: dict[tuple[str, str], dict] = {}
    errors = warnings = valid = 0

    for index, entry in enumerate(payload):
        draft = svc.to_draft(entry)
        report = await validation.check_draft(draft, cache)
        errors += len(report.errors)
        warnings += len(report.warnings)
        valid += 1 if report.valid else 0
        items.append(
            s.BatchValidationItem(
                index=index,
                rule_key=entry.rule_key or keys.derive(draft),
                rule_name=entry.rule_name,
                valid=report.valid,
                validation_state=report.state,
                issues=_issues(report),
            )
        )
        for issue in report.errors:
            entry_ = grouped.setdefault(
                (issue.code, issue.message),
                {"code": issue.code, "message": issue.message, "hint": issue.hint,
                 "count": 0, "examples": []},
            )
            entry_["count"] += 1
            if len(entry_["examples"]) < 5:
                entry_["examples"].append(entry.rule_name)

    return s.BatchValidationReport(
        total=len(payload),
        valid_count=valid,
        error_count=errors,
        warning_count=warnings,
        items=items,
        reasons=sorted(grouped.values(), key=lambda e: -e["count"]),
    )


@router.get(
    "/{rule_id}/issues",
    response_model=list[s.IssueOut],
    summary="The persisted validation issues for a rule",
    dependencies=[Depends(_view)],
)
async def rule_issues(db: DbSession, rule_id: str) -> list[s.IssueOut]:
    """What the catalogue's Validation column drills into.

    The legacy model cached a summary — ``{"valid": false, "error_count": 3}`` —
    which can colour a dot and cannot say which three, so "every rule failing the
    unknown-zone check" was a script rather than a query.
    """
    rule = await svc.get_rule(db, rule_id)
    if rule.current_version_id is None:
        return []
    return await svc._issues(db, rule.current_version_id)


@router.get(
    "/issues/summary",
    summary="Issue counts by code across the estate",
    dependencies=[Depends(_view)],
)
async def issue_summary(db: DbSession) -> list[dict]:
    """One indexed query. This is the view that turns "400 rules are red" into
    "one destination zone is missing", which is a different afternoon."""
    from app.modules.rules.canonical.lineage import RuleValidationIssue

    rows = (
        await db.execute(
            select(
                RuleValidationIssue.code,
                RuleValidationIssue.severity,
                func.count().label("count"),
                func.min(RuleValidationIssue.message).label("message"),
                func.min(RuleValidationIssue.hint).label("hint"),
            )
            .group_by(RuleValidationIssue.code, RuleValidationIssue.severity)
            .order_by(func.count().desc())
        )
    ).all()
    return [
        {
            "code": code,
            "severity": severity,
            "count": int(count),
            "message": message,
            "hint": hint,
        }
        for code, severity, count, message, hint in rows
    ]


@router.post(
    "/validate-references",
    response_model=s.ReferenceReport,
    summary="Check every catalogue code a rule names",
)
async def validate_references(
    db: DbSession,
    principal: RuleViewer,
    payload: s.RuleWrite = Body(...),
) -> s.ReferenceReport:
    """Resolve the rule's references and report each one individually.

    Separate from ``/validate`` because the answers are different in kind: the
    validator says "this rule is not storable", this says "LOCAL_ONNET exists,
    MOBILE does not". The second is what an author fixes without leaving the
    condition row.
    """
    del principal
    draft = svc.to_draft(payload)
    cache = await resolver.build(db, _tenant(), with_rule_index=False)
    wanted = draft.reference_codes()
    missing = await cache.load_catalogues(wanted) if wanted else {}

    checks: list[s.ReferenceCheck] = []
    for slug, codes in sorted(wanted.items()):
        absent = missing.get(slug, set())
        for code in sorted(codes):
            resolved = cache.catalogue_id(slug, code)
            checks.append(
                s.ReferenceCheck(
                    catalogue=slug,
                    code=code,
                    exists=code not in absent and resolved is not None,
                    resolved_id=resolved,
                    message="" if resolved else cache.describe_missing(slug, {code}),
                )
            )
    return s.ReferenceReport(
        checked=len(checks),
        missing=sum(1 for c in checks if not c.exists),
        references=checks,
    )


# --- Helpers ----------------------------------------------------------------


def _tenant() -> str:
    from app.core.config import settings

    return settings.default_tenant_id


def _rekey(draft, rule_key: str):
    from dataclasses import replace

    return replace(draft, rule_key=rule_key)


async def _taken_keys(db: DbSession) -> set[str]:
    return set(
        (await db.execute(select(CanonicalRule.rule_key))).scalars().all()
    )


def _report(report: Report) -> s.ValidationReport:
    return s.ValidationReport(
        valid=report.valid,
        validation_state=report.state,
        error_count=len(report.errors),
        warning_count=len(report.warnings),
        issues=_issues(report),
        checked_at=datetime.now(UTC),
    )


async def _write_response(
    db: DbSession, result: kernel.BatchResult, action: str
) -> s.WriteResponse:
    """Turn a one-rule batch result into the shape a client expects.

    A rejected rule raises rather than returning 201 with a body full of errors:
    a 2xx that did not save anything is the kind of response a client checks once
    and then stops checking.
    """
    del action
    if not result.records:
        raise ConflictError("The rule produced no result.")

    record = result.records[0]
    issues = [s.IssueOut(**issue) for issue in record.issues]
    if record.decision == "REJECTED":
        from app.core.errors import ValidationFailedError

        raise ValidationFailedError(
            record.reason or "The rule has validation errors and was not saved.",
            details={"issues": [i.model_dump() for i in issues]},
        )

    rule_id = record.rule_id
    if rule_id is None:
        raise ConflictError("The rule was not persisted.")
    rule = await svc.get_rule(db, rule_id)
    version = (
        await db.get(CanonicalRuleVersion, record.rule_version_id)
        if record.rule_version_id
        else None
    )
    detail = await svc.detail(db, rule, await svc.lookup_maps(db), version=version)
    return s.WriteResponse(
        rule=detail,
        decision=record.decision,
        issues=issues,
        validation_state=detail.validation_state,
    )


async def _audit(db, rule, version, principal, payload: s.StatusChange) -> None:
    from app.modules.rules.canonical.lineage import CanonicalRuleAudit

    db.add(
        CanonicalRuleAudit(
            tenant_id=rule.tenant_id,
            rule_id=rule.rule_id,
            rule_version_id=version.rule_version_id if version else None,
            version_number=version.version_number if version else None,
            action=f"status_{payload.status.lower()}",
            to_status=payload.status,
            channel="MANUAL",
            actor_id=principal.id,
            actor_name=principal.full_name or principal.email,
            comment=payload.comment,
        )
    )
