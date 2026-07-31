"""Bulk lifecycle operations over an import, a rule set, or a filter.

One response shape for every operation, so a UI renders "what would happen" and
"what happened" with the same component — and so a dry run is genuinely the same
call as the real thing minus the write.

The role gate is the interesting part. A batch of pure new drafts needs only
``ratingRules:edit``: a draft changes no price until it is activated, and
requiring a manager to approve five hundred harmless drafts is how approval
becomes a rubber stamp. A batch touching anything already live needs
``ratingApprovals:edit``, which the RBAC already withholds from ANALYST.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy import func, select

from app.core.config import settings
from app.core.deps import DbSession, principal_with, require
from app.core.errors import NotFoundError, PermissionDeniedError
from app.core.rbac import RatingPermKey
from app.modules.rules.ingest import resolver
from app.modules.rules.lifecycle import runner
from app.modules.rules.lifecycle import schemas as s
from app.modules.rules.lifecycle import service as svc
from app.modules.rules.lifecycle.models import BulkRunStatus, RuleBulkRun
from app.modules.rules.lifecycle.selectors import Selector, resolve

router = APIRouter(prefix="/rule-lifecycle", tags=["rule-lifecycle"])

_view = require(RatingPermKey.RULES, "view")
Editor = principal_with(RatingPermKey.RULES, "edit")


def _tenant(principal) -> str:
    return principal.tenant_id or settings.default_tenant_id


def _selector(request: s.BulkRequest) -> Selector:
    return Selector(
        batch_id=request.batch_id,
        rule_set_id=request.rule_set_id,
        filters=request.filters,
        rule_keys=tuple(request.rule_keys) if request.rule_keys else None,
    )


def _gate(principal, result: svc.BulkResult) -> None:
    """A set containing anything already live needs the approver role.

    Checked after resolution rather than before, because whether the approver
    role is needed depends on what the selector actually matched — and the
    honest answer to "do I need a manager for this?" is not knowable from the
    request body alone.
    """
    if not result.touched_live_pricing:
        return
    if not principal.can(RatingPermKey.APPROVALS, "edit"):
        raise PermissionDeniedError(
            "This selection includes rules that are already live, so it needs "
            "rule-approval rights.",
            details={
                "required": "ratingApprovals:edit",
                "hint": "A batch of new drafts does not — this one changes "
                        "something that is already pricing traffic.",
            },
        )


@router.post(
    "/preview",
    response_model=s.BulkResponse,
    summary="What a bulk operation would do, without doing it",
    dependencies=[Depends(_view)],
)
async def preview(
    db: DbSession,
    principal: Editor,
    request: s.BulkPreview = Body(...),
) -> s.BulkResponse:
    """A dry run of any operation, in the shape its real response takes.

    Exists so a UI can show the outcome before enabling the button, which is the
    only thing that makes a five-hundred-rule action safe to offer at all.
    """
    tenant = _tenant(principal)
    rules = await resolve(db, _selector(request), tenant_id=tenant)

    if request.operation == "approve":
        result = await svc.approve(
            db, rules, selector=_selector(request).describe(),
            actor_id=principal.id, actor_name=principal.full_name,
            atomic=request.atomic,
            enforce_maker_checker=await _maker_checker(db, tenant),
            dry_run=True,
        )
    elif request.operation == "revert":
        result = await svc.revert(
            db, rules, selector=_selector(request).describe(),
            actor_id=principal.id, actor_name=principal.full_name, dry_run=True,
        )
    else:
        cache = await resolver.build(db, tenant, with_rule_index=False)
        result = await svc.validate(
            db, rules, cache, selector=_selector(request).describe(),
            tenant_id=tenant, dry_run=True,
        )
    return _response(result, principal)


@router.post(
    "/validate",
    response_model=s.BulkResponse,
    summary="Re-validate every rule in a selection",
)
async def validate_bulk(
    db: DbSession, principal: Editor, request: s.BulkRequest = Body(...)
) -> s.BulkResponse:
    """Partial by design: forty failures out of five hundred is information, and
    withholding the four hundred and sixty clean verdicts helps nobody."""
    tenant = _tenant(principal)
    rules = await resolve(db, _selector(request), tenant_id=tenant)
    cache = await resolver.build(db, tenant, with_rule_index=False)
    result = await svc.validate(
        db, rules, cache, selector=_selector(request).describe(),
        tenant_id=tenant, dry_run=request.dry_run,
    )
    return _response(result, principal)


@router.post(
    "/approve",
    response_model=s.BulkResponse,
    summary="Approve every eligible rule in a selection",
)
async def approve_bulk(
    db: DbSession, principal: Editor, request: s.ApproveRequest = Body(...)
) -> s.BulkResponse:
    """Walks DRAFT → VALIDATED → REVIEWED → APPROVED, auditing each hop.

    Atomic unless the caller says otherwise: a half-approved tariff prices
    traffic wrong in a way that reads as an engine fault rather than an
    incomplete action.
    """
    tenant = _tenant(principal)
    rules = await resolve(db, _selector(request), tenant_id=tenant)
    result = await svc.approve(
        db, rules, selector=_selector(request).describe(),
        actor_id=principal.id, actor_name=principal.full_name,
        comment=request.comment, atomic=request.atomic,
        enforce_maker_checker=await _maker_checker(db, tenant),
        dry_run=request.dry_run,
    )
    _gate(principal, result)
    return _response(result, principal)


@router.post(
    "/revert",
    response_model=s.BulkResponse,
    summary="Send a selection back to draft",
)
async def revert_bulk(
    db: DbSession, principal: Editor, request: s.ApproveRequest = Body(...)
) -> s.BulkResponse:
    """The undo for a bulk approval. Not an undo for activation — a compiled
    rule is reached through its snapshot, and the undo for that is a rollback."""
    tenant = _tenant(principal)
    rules = await resolve(db, _selector(request), tenant_id=tenant)
    result = await svc.revert(
        db, rules, selector=_selector(request).describe(),
        actor_id=principal.id, actor_name=principal.full_name,
        comment=request.comment, dry_run=request.dry_run,
    )
    _gate(principal, result)
    return _response(result, principal)


@router.post(
    "/activate",
    response_model=s.BulkResponse,
    summary="Compile a selection into a snapshot and make it live",
)
async def activate_bulk(
    db: DbSession, principal: Editor, request: s.ActivateRequest = Body(...)
) -> s.BulkResponse:
    """Plan step **B5** — the last hop of an import: file in, traffic priced.

    Unlike its siblings this is all-or-nothing with no opt-out. A snapshot is
    what rating resolves against, so activating part of a tariff publishes a
    tariff with a hole in it, and traffic that falls through a hole is priced at
    the fallback rather than failing visibly.
    """
    tenant = _tenant(principal)
    selector = _selector(request)
    rules = await resolve(db, selector, tenant_id=tenant)
    result = await svc.activate(
        db, rules, selector=selector.describe(),
        rule_set_id=await runner._rule_set_for(db, selector, rules),
        actor_id=principal.id, actor_name=principal.full_name,
        comment=request.comment, force=request.force, dry_run=request.dry_run,
    )
    _gate(principal, result)
    return _response(result, principal)


@router.post(
    "/delete",
    response_model=s.BulkResponse,
    summary="Delete or retire every rule in a selection",
)
async def delete_bulk(
    db: DbSession, principal: Editor, request: s.ApproveRequest = Body(...)
) -> s.BulkResponse:
    """Deletes what can be deleted and retires the rest, per rule.

    Same policy as deleting one rule by hand: a first-version draft is removed,
    anything with history is retired. A bulk endpoint that deleted more freely
    would be a way to launder a removal the single-rule path refuses.

    The role gate applies as everywhere else — a selection containing anything
    live needs rule-approval rights, because retiring a live rule takes it out of
    the next snapshot and that changes what customers are charged.
    """
    tenant = _tenant(principal)
    selector = _selector(request)
    # `allow_missing` for this endpoint only: a rule the backfill has not reached
    # still exists in the catalogue the operator is looking at, and refusing to
    # remove it because of an incomplete migration would break the button on
    # exactly the estates that most need it. Those keys go down the legacy path.
    rules = await resolve(db, selector, tenant_id=tenant, allow_missing=True)
    result = await svc.delete(
        db, rules, selector=selector.describe(),
        actor_id=principal.id, actor_name=principal.full_name,
        comment=request.comment, atomic=request.atomic, dry_run=request.dry_run,
        requested_keys=selector.rule_keys,
    )
    _gate(principal, result)
    return _response(result, principal)


# --- Jobs (plan step B4) ----------------------------------------------------


@router.post(
    "/jobs",
    response_model=s.JobOut,
    status_code=202,
    summary="Queue a bulk operation to run in the background",
)
async def submit_job(
    db: DbSession, principal: Editor, request: s.JobRequest = Body(...)
) -> s.JobOut:
    """For selections too large to answer inside a request.

    Returns 202 with a run id immediately. The selector is stored rather than
    resolved here: resolution is itself slow on a large estate, and doing it in
    the request would reintroduce exactly the timeout this endpoint exists to
    avoid.

    The approver gate cannot be applied at submission — whether a selection
    touches live pricing is only knowable once it resolves. So it is applied at
    execution instead, from the role recorded here, and a job that turns out to
    need rights the submitter lacks lands as REJECTED with that reason.
    """
    tenant = _tenant(principal)
    if request.operation == "activate" and not principal.can(
        RatingPermKey.APPROVALS, "edit"
    ):
        # Activation is live by definition, so this one *is* knowable up front —
        # and refusing now beats accepting a job that will only fail later.
        raise PermissionDeniedError(
            "Activating a rule set needs rule-approval rights.",
            details={"required": "ratingApprovals:edit"},
        )

    run = RuleBulkRun(
        tenant_id=tenant,
        operation=request.operation,
        selector=_selector(request).describe(),
        dry_run=request.dry_run,
        atomic=request.atomic,
        force=request.force,
        comment=request.comment,
        actor_id=principal.id,
        actor_name=principal.full_name,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    # Launched after the commit, so the worker's own session can see the row.
    runner.launch(run.bulk_run_id)
    return _job(run)


@router.get(
    "/jobs",
    response_model=s.JobList,
    summary="Recent bulk runs",
)
async def list_jobs(
    db: DbSession,
    principal=Depends(_view),
    status: str | None = None,
    limit: int = 50,
) -> s.JobList:
    tenant = _tenant(principal)
    stmt = select(RuleBulkRun).where(RuleBulkRun.tenant_id == tenant)
    if status:
        stmt = stmt.where(RuleBulkRun.status == status.upper())
    total = await db.scalar(
        select(func.count()).select_from(stmt.subquery())
    )
    rows = (
        await db.execute(
            stmt.order_by(RuleBulkRun.created_at.desc()).limit(min(limit, 200))
        )
    ).scalars().all()
    return s.JobList(items=[_job(r) for r in rows], total=int(total or 0))


@router.get(
    "/jobs/{run_id}",
    response_model=s.JobOut,
    summary="Progress and result of one bulk run",
)
async def get_job(
    db: DbSession, run_id: str, principal=Depends(_view)
) -> s.JobOut:
    run = await db.get(RuleBulkRun, run_id)
    if run is None or run.tenant_id != _tenant(principal):
        raise NotFoundError(f"Bulk run '{run_id}' was not found.")
    return _job(run)


def _job(run: RuleBulkRun) -> s.JobOut:
    percent: int | None = None
    if run.status in (BulkRunStatus.SUCCEEDED, BulkRunStatus.REJECTED):
        percent = 100
    elif run.total:
        percent = min(100, round(run.processed * 100 / run.total))
    return s.JobOut(
        bulk_run_id=run.bulk_run_id,
        operation=run.operation,
        status=run.status,
        selector=run.selector or {},
        dry_run=run.dry_run,
        atomic=run.atomic,
        force=run.force,
        comment=run.comment,
        total=run.total,
        processed=run.processed,
        applied=run.applied,
        counts=run.counts or {},
        blocked=run.blocked or [],
        snapshot=run.snapshot,
        touched_live_pricing=run.touched_live_pricing,
        error=run.error,
        error_details=run.error_details,
        actor_name=run.actor_name,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        stale=runner.stale(run),
        percent=percent,
    )


# --- Helpers ----------------------------------------------------------------


async def _maker_checker(db: DbSession, tenant_id: str) -> bool:
    """Whether the importer may approve their own import.

    On by default and disableable per tenant. Hard-coding it on makes a
    single-analyst deployment unable to activate anything it imported; hard-coding
    it off throws away the control entirely. The tenant's own settings are the
    right place for a policy that genuinely differs between a one-person shop and
    a group operator.
    """
    from app.modules.tenancy.models import Tenant

    tenant = await db.get(Tenant, tenant_id)
    settings_blob = getattr(tenant, "settings", None) or {}
    return bool(settings_blob.get("enforce_maker_checker", True))


def _response(result: svc.BulkResult, principal) -> s.BulkResponse:
    return s.BulkResponse(
        operation=result.operation,
        selector=result.selector,
        dry_run=result.dry_run,
        total=result.total,
        eligible=result.eligible,
        applied=result.applied,
        counts=result.counts,
        blocked=result.blocked(),
        touched_live_pricing=result.touched_live_pricing,
        requires_approver_role=result.touched_live_pricing,
        caller_can_approve=principal.can(RatingPermKey.APPROVALS, "edit"),
        snapshot=(
            s.SnapshotRef(**result.snapshot) if result.snapshot else None
        ),
        rules=[
            s.RuleOutcomeOut(
                rule_id=o.rule_id,
                rule_key=o.rule_key,
                rule_name=o.rule_name,
                outcome=o.outcome,
                from_status=o.from_status,
                to_status=o.to_status,
                reason=o.reason,
                code=o.code,
            )
            for o in result.outcomes[:200]
        ],
    )
