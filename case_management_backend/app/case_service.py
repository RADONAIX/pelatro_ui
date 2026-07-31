"""Case management business logic.

The router stays thin: it parses query params into a `CaseFilters` and calls in
here. Every function returns Pydantic models (never live ORM objects), so the
session can close before the response is serialised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from fastapi import HTTPException
from sqlalchemy import Select, func, or_, select
from sqlalchemy import case as sa_case
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import attachments, catalog, schemas
from app.models import Case, CaseActivity, CaseComment, CaseMismatch, ControlRule, utcnow

REFERENCE_PREFIX = "CASE-"
# Demo references start at CASE-2031, so new ones continue the series.
REFERENCE_START = 2031

SORTABLE: dict[str, Any] = {
    "createdAt": Case.created_at,
    "detectedAt": Case.detected_at,
    "updatedAt": Case.updated_at,
    "reference": Case.reference,
    "title": Case.title,
    "assurance": Case.assurance_name,
    "module": Case.module,
    "ruleId": Case.rule_id,
    "category": Case.rule_category,
    "status": Case.status,
    "action": Case.action,
    "origin": Case.origin,
    "owner": Case.owner,
    "impact": Case.estimated_impact,
    "affected": Case.affected_count,
}

# Severity is a rank, not a word — ordering it alphabetically would put
# "critical" between "high" and "low". Sorted via a CASE expression instead.
SEVERITY_RANK = {s: i for i, s in enumerate(catalog.SEVERITIES)}


def _as_utc(dt: datetime | None) -> datetime | None:
    """Normalise an incoming timestamp to timezone-aware UTC.

    Columns are timestamptz, so a naive value from a client that omitted its
    offset is read as UTC rather than as the server's local zone.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------

@dataclass
class CaseFilters:
    q: str | None = None
    assurance: list[str] = field(default_factory=list)     # code or name
    group: list[str] = field(default_factory=list)
    module: list[str] = field(default_factory=list)
    category: list[str] = field(default_factory=list)      # issue type
    status: list[str] = field(default_factory=list)
    severity: list[str] = field(default_factory=list)
    origin: list[str] = field(default_factory=list)
    action: list[str] = field(default_factory=list)
    owner: list[str] = field(default_factory=list)
    rule_id: list[str] = field(default_factory=list)
    stream: list[str] = field(default_factory=list)
    # all | assigned | unassigned | mine (with `me` set)
    assignment: str = "all"
    me: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    date_field: str = "createdAt"   # createdAt | detectedAt | updatedAt
    open_only: bool = False


def _apply_filters(stmt: Select, f: CaseFilters) -> Select:
    if f.assurance:
        # Accept codes and names interchangeably — a UI filtering by "UA" and a
        # rule posting "Usage Assurance" must select the same cases.
        codes, names = set(), set()
        for raw in f.assurance:
            resolved = catalog.resolve_assurance(raw)
            if resolved:
                codes.add(resolved.code)
            else:
                names.add(raw)
        clauses = []
        if codes:
            clauses.append(Case.assurance_code.in_(codes))
        if names:
            clauses.append(Case.assurance_name.in_(names))
        stmt = stmt.where(or_(*clauses)) if clauses else stmt

    if f.group:
        stmt = stmt.where(Case.assurance_group.in_(f.group))
    if f.module:
        stmt = stmt.where(Case.module.in_(f.module))
    if f.category:
        stmt = stmt.where(Case.rule_category.in_(f.category))
    if f.status:
        stmt = stmt.where(Case.status.in_(f.status))
    if f.severity:
        stmt = stmt.where(Case.severity.in_(f.severity))
    if f.origin:
        stmt = stmt.where(Case.origin.in_(f.origin))
    if f.action:
        stmt = stmt.where(Case.action.in_(f.action))
    if f.owner:
        stmt = stmt.where(Case.owner.in_(f.owner))
    if f.rule_id:
        stmt = stmt.where(Case.rule_id.in_(f.rule_id))
    if f.stream:
        stmt = stmt.where(Case.stream.in_(f.stream))

    if f.assignment == "unassigned":
        stmt = stmt.where(Case.owner == "")
    elif f.assignment == "assigned":
        stmt = stmt.where(Case.owner != "")
    elif f.assignment == "mine":
        stmt = stmt.where(Case.owner == (f.me or ""))

    if f.open_only:
        stmt = stmt.where(Case.status.notin_(list(catalog.TERMINAL_STATUSES)))

    col = {
        "detectedAt": Case.detected_at,
        "updatedAt": Case.updated_at,
    }.get(f.date_field, Case.created_at)
    if f.date_from:
        stmt = stmt.where(col >= _as_utc(f.date_from))
    if f.date_to:
        stmt = stmt.where(col <= _as_utc(f.date_to))

    if f.q:
        needle = f"%{f.q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Case.reference).like(needle),
                func.lower(Case.title).like(needle),
                func.lower(Case.description).like(needle),
                func.lower(Case.owner).like(needle),
                func.lower(Case.linked_batch).like(needle),
                func.lower(Case.linked_txn_id).like(needle),
                func.lower(Case.node_id).like(needle),
                func.lower(func.coalesce(Case.rule_id, "")).like(needle),
                func.lower(func.coalesce(Case.rule_name, "")).like(needle),
                func.lower(Case.assurance_name).like(needle),
                func.lower(Case.module).like(needle),
                func.lower(Case.rule_category).like(needle),
            )
        )
    return stmt


def _order_by(stmt: Select, sort_by: str, sort_dir: str) -> Select:
    desc = sort_dir.lower() != "asc"
    if sort_by == "severity":
        col = sa_case(SEVERITY_RANK, value=Case.severity, else_=-1)
    else:
        col = SORTABLE.get(sort_by, Case.created_at)
    return stmt.order_by(col.desc() if desc else col.asc(), Case.reference.desc())


# --------------------------------------------------------------------------
# Serialisation helpers
# --------------------------------------------------------------------------

def _row(case: Case, mismatch_count: int) -> schemas.CaseRow:
    row = schemas.CaseRow.model_validate(case)
    row.mismatch_count = mismatch_count
    return row


def _detail(case: Case) -> schemas.CaseDetail:
    detail = schemas.CaseDetail.model_validate(case)
    detail.mismatch_count = len(case.mismatches)
    # The download URL is derived, not stored — build it here so every response
    # carries a link the client can use as-is.
    detail.attachments = [attachments.to_out(a) for a in case.attachments]
    return detail


def _counts_by(db: Session, f: CaseFilters, column) -> dict[str, int]:
    stmt = _apply_filters(select(column, func.count(Case.id)).group_by(column), f)
    return {str(k or "—"): int(v) for k, v in db.execute(stmt).all()}


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------

def list_cases(
    db: Session,
    f: CaseFilters,
    page: int = 1,
    page_size: int = 25,
    sort_by: str = "createdAt",
    sort_dir: str = "desc",
) -> schemas.CaseListResponse:
    page = max(1, page)
    page_size = max(1, min(500, page_size))

    total = int(db.execute(_apply_filters(select(func.count(Case.id)), f)).scalar_one())

    mismatch_count = (
        select(func.count(CaseMismatch.id))
        .where(CaseMismatch.case_id == Case.id)
        .correlate(Case)
        .scalar_subquery()
    )
    stmt = _apply_filters(select(Case, mismatch_count), f)
    stmt = _order_by(stmt, sort_by, sort_dir).offset((page - 1) * page_size).limit(page_size)

    items = [_row(case, int(count or 0)) for case, count in db.execute(stmt).all()]
    return schemas.CaseListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        page_count=max(1, (total + page_size - 1) // page_size),
    )


def summarise(db: Session, f: CaseFilters) -> schemas.SummaryResponse:
    total = int(db.execute(_apply_filters(select(func.count(Case.id)), f)).scalar_one())
    by_status = _counts_by(db, f, Case.status)
    unassigned = int(
        db.execute(
            _apply_filters(select(func.count(Case.id)), f).where(Case.owner == "")
        ).scalar_one()
    )
    open_impact = db.execute(
        _apply_filters(select(func.coalesce(func.sum(Case.estimated_impact), 0.0)), f)
        .where(Case.status.notin_(list(catalog.TERMINAL_STATUSES)))
    ).scalar_one()
    affected = db.execute(
        _apply_filters(select(func.coalesce(func.sum(Case.affected_count), 0)), f)
    ).scalar_one()

    return schemas.SummaryResponse(
        total=total,
        unassigned=unassigned,
        assigned=total - unassigned,
        # Every status is present as a key (0 when unused) so the tiles keep a
        # stable shape instead of appearing and disappearing with the data.
        by_status={s: by_status.get(s, 0) for s in catalog.STATUSES},
        by_severity=_counts_by(db, f, Case.severity),
        by_assurance=_counts_by(db, f, Case.assurance_name),
        by_category=_counts_by(db, f, Case.rule_category),
        open_estimated_impact=float(open_impact or 0.0),
        affected_records=int(affected or 0),
    )


def _facet(db: Session, column, labeller=None) -> list[schemas.FacetValue]:
    rows = db.execute(
        select(column, func.count(Case.id)).where(column.isnot(None)).group_by(column).order_by(column)
    ).all()
    out: list[schemas.FacetValue] = []
    for value, count in rows:
        if value in (None, ""):
            continue
        label = labeller(value) if labeller else str(value)
        out.append(schemas.FacetValue(value=str(value), label=label, count=int(count)))
    return out


def facets(db: Session) -> schemas.FacetsResponse:
    """Filter options with live counts.

    Deliberately unfiltered: the options must not disappear as the user narrows
    the list, or a filter can never be widened again from the UI.
    """
    return schemas.FacetsResponse(
        assurances=_facet(db, Case.assurance_name),
        groups=_facet(db, Case.assurance_group),
        modules=_facet(db, Case.module),
        categories=_facet(db, Case.rule_category),
        statuses=_facet(db, Case.status),
        severities=_facet(db, Case.severity),
        origins=_facet(db, Case.origin),
        actions=_facet(db, Case.action),
        owners=_facet(db, Case.owner, labeller=lambda v: v or "Unassigned"),
        rules=_facet(db, Case.rule_id),
    )


def get_case_or_404(db: Session, case_id: str) -> Case:
    stmt = select(Case).options(
        selectinload(Case.mismatches),
        selectinload(Case.comments),
        selectinload(Case.activities),
        selectinload(Case.attachments),
    )
    # Accept the reference (CASE-2031) as well as the uuid — the reference is
    # what a user copies out of the UI or an escalation email.
    stmt = stmt.where(or_(Case.id == case_id, Case.reference == case_id.upper()))
    case = db.execute(stmt).scalars().first()
    if case is None:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")
    return case


def get_detail(db: Session, case_id: str) -> schemas.CaseDetail:
    return _detail(get_case_or_404(db, case_id))


def list_mismatches(
    db: Session, case_id: str, page: int = 1, page_size: int = 100, status: str | None = None
) -> dict[str, Any]:
    case = get_case_or_404(db, case_id)
    stmt = select(CaseMismatch).where(CaseMismatch.case_id == case.id)
    if status:
        stmt = stmt.where(CaseMismatch.status == status)
    total = int(
        db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    )
    page = max(1, page)
    page_size = max(1, min(1000, page_size))
    rows = db.execute(
        stmt.order_by(CaseMismatch.position).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return {
        "items": [schemas.MismatchOut.model_validate(r) for r in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
        # The rule may have flagged far more records than it sampled into the
        # case — the UI shows "sample of N of M" using this.
        "affectedCount": case.affected_count,
    }


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------

def next_reference(db: Session) -> str:
    """CASE-#### continuing from the highest existing number."""
    refs = db.execute(select(Case.reference)).scalars().all()
    highest = REFERENCE_START
    for ref in refs:
        match = re.search(r"(\d+)$", ref or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{REFERENCE_PREFIX}{highest + 1}"


def _log(case: Case, actor: str, action: str, *, field_: str | None = None,
         from_value: Any = None, to_value: Any = None, note: str | None = None) -> None:
    case.activities.append(
        CaseActivity(
            actor=actor or "system",
            action=action,
            field=field_,
            from_value=None if from_value is None else str(from_value),
            to_value=None if to_value is None else str(to_value),
            note=note,
        )
    )


def _linked_rule(db: Session, rule_id: str | None) -> ControlRule | None:
    """Resolve the rule a case cites, rejecting an unknown id up front.

    The column is a foreign key, so an unknown id would otherwise surface as an
    opaque integrity error at commit time instead of a clear 400.
    """
    if not rule_id:
        return None
    rule = db.get(ControlRule, rule_id.strip().upper())
    if rule is None:
        raise HTTPException(
            400,
            f"Unknown rule '{rule_id}'. Register it via POST /api/rules before "
            "raising cases against it.",
        )
    return rule


def _validate_vocab(payload: schemas.CaseBase, origin: str) -> None:
    if payload.severity not in catalog.SEVERITIES:
        raise HTTPException(400, f"severity must be one of {catalog.SEVERITIES}")
    if payload.status not in catalog.STATUSES:
        raise HTTPException(400, f"status must be one of {catalog.STATUSES}")
    if origin not in catalog.ORIGINS:
        raise HTTPException(400, f"origin must be one of {catalog.ORIGINS}")


def _build_mismatches(rows: Iterable[schemas.MismatchIn], start: int = 0) -> list[CaseMismatch]:
    return [
        CaseMismatch(
            position=start + i,
            record_ref=m.record_ref,
            entity=m.entity,
            subscriber=m.subscriber,
            field=m.field,
            expected_value=m.expected_value,
            actual_value=m.actual_value,
            delta=m.delta,
            status=m.status,
            occurred_at=_as_utc(m.occurred_at),
            details=m.details or {},
        )
        for i, m in enumerate(rows)
    ]


def create_case(
    db: Session,
    payload: schemas.CaseBase,
    *,
    origin: str,
    reference: str | None = None,
    dedupe_key: str | None = None,
    actor: str = "system",
) -> Case:
    _validate_vocab(payload, origin)

    # A case raised by a rule inherits everything the rule already knows, so an
    # engine only has to post what it measured. Anything explicitly supplied in
    # the payload wins; inheritance fills the blanks.
    rule = _linked_rule(db, payload.rule_id)

    assurance = catalog.resolve_assurance(payload.assurance) or (
        catalog.resolve_assurance(rule.assurance_code) if rule else None
    )
    if assurance is None:
        known = ", ".join(a.code for a in catalog.ASSURANCES)
        raise HTTPException(
            400,
            f"Unknown assurance '{payload.assurance}'. Pass an assurance code "
            f"({known}) or a ruleId to inherit it from.",
        )

    detected = _as_utc(payload.detected_at) or utcnow()

    case = Case(
        reference=reference or next_reference(db),
        title=payload.title.strip(),
        description=payload.description.strip(),
        assurance_code=assurance.code,
        assurance_name=assurance.name,
        assurance_group=assurance.group,
        module=payload.module or (rule.entity_scope if rule else "")
        or (assurance.modules[0] if assurance.modules else ""),
        sub_module=payload.sub_module,
        rule_id=rule.id if rule else None,
        rule_name=payload.rule_name or (rule.name if rule else None),
        rule_category=payload.rule_category or (rule.primitive_category if rule else ""),
        rule_run_id=payload.rule_run_id,
        origin=origin,
        severity=payload.severity,
        status=payload.status,
        action=payload.action or "NA",
        owner=payload.owner.strip(),
        stream=payload.stream,
        node_id=payload.node_id,
        source_feed=payload.source_feed or (rule.source_feed if rule else ""),
        target_feed=payload.target_feed or (rule.target_feed if rule else ""),
        linked_batch=payload.linked_batch,
        linked_txn_id=payload.linked_txn_id,
        expected_value=payload.expected_value,
        actual_value=payload.actual_value,
        variance=payload.variance,
        variance_pct=payload.variance_pct,
        threshold=payload.threshold or (
            None if not rule or rule.tolerance_pct is None else f"{rule.tolerance_pct}%"
        ),
        estimated_impact=payload.estimated_impact,
        affected_count=payload.affected_count or len(payload.mismatches),
        evidence=payload.evidence,
        saved_insights=[],
        tags=payload.tags or [],
        details=payload.details or {},
        detected_at=detected,
        dedupe_key=dedupe_key,
    )
    case.mismatches = _build_mismatches(payload.mismatches)
    _log(
        case, actor, "created",
        note=(
            f"Raised by rule {payload.rule_id} ({payload.rule_name})"
            if origin == "auto_detected" and payload.rule_id
            else "Raised manually by an analyst"
        ),
    )
    if case.owner:
        _log(case, actor, "assigned", field_="owner", to_value=case.owner)

    db.add(case)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Two writers can pick the same reference; retry once with a fresh one.
        case.reference = next_reference(db)
        db.add(case)
        db.commit()
    db.refresh(case)
    return case


def ingest_rule_case(db: Session, payload: schemas.RuleCaseIngest) -> tuple[Case, bool]:
    """Rule-engine entry point. Returns (case, created)."""
    if payload.dedupe_key:
        existing = db.execute(
            select(Case).where(Case.dedupe_key == payload.dedupe_key)
        ).scalars().first()
        if existing is not None:
            if payload.append_mismatches_on_duplicate and payload.mismatches:
                start = len(existing.mismatches)
                existing.mismatches.extend(_build_mismatches(payload.mismatches, start))
                existing.affected_count = max(
                    existing.affected_count, start + len(payload.mismatches)
                )
                existing.updated_at = utcnow()
                _log(existing, "rule_engine", "evidence_added",
                     note=f"{len(payload.mismatches)} further mismatch rows appended")
                db.commit()
                db.refresh(existing)
            return existing, False

    case = create_case(
        db, payload, origin=payload.origin,
        dedupe_key=payload.dedupe_key, actor="rule_engine",
    )
    return case, True


def update_case(db: Session, case_id: str, payload: schemas.CaseUpdate) -> schemas.CaseDetail:
    case = get_case_or_404(db, case_id)
    actor = payload.actor or "system"

    if payload.status is not None and payload.status not in catalog.STATUSES:
        raise HTTPException(400, f"status must be one of {catalog.STATUSES}")
    if payload.severity is not None and payload.severity not in catalog.SEVERITIES:
        raise HTTPException(400, f"severity must be one of {catalog.SEVERITIES}")

    tracked = ("title", "description", "status", "severity", "action", "owner", "module", "sub_module")
    changed = False
    for name in tracked:
        new = getattr(payload, name)
        if new is None:
            continue
        old = getattr(case, name)
        if new == old:
            continue
        setattr(case, name, new)
        changed = True
        _log(case, actor, name if name in ("status", "owner", "severity") else "updated",
             field_=name, from_value=old, to_value=new)

    if payload.tags is not None:
        case.tags = payload.tags
        changed = True
    if payload.evidence is not None:
        case.evidence = payload.evidence
        changed = True

    if payload.status is not None:
        # closed_at tracks the last transition in or out of a terminal status,
        # so "time to close" stays correct if a case is reopened.
        case.closed_at = utcnow() if payload.status in catalog.TERMINAL_STATUSES else None

    if changed:
        case.updated_at = utcnow()
        db.commit()
        db.refresh(case)
    return _detail(case)


def assign_case(db: Session, case_id: str, payload: schemas.AssignIn) -> schemas.CaseDetail:
    return update_case(
        db, case_id,
        schemas.CaseUpdate(owner=payload.owner.strip(), actor=payload.actor),
    )


def set_status(db: Session, case_id: str, payload: schemas.StatusIn) -> schemas.CaseDetail:
    detail = update_case(
        db, case_id,
        schemas.CaseUpdate(status=payload.status, action=payload.action, actor=payload.actor),
    )
    if payload.note:
        add_comment(db, case_id, schemas.CommentIn(author=payload.actor, body=payload.note))
        detail = get_detail(db, case_id)
    return detail


def add_comment(db: Session, case_id: str, payload: schemas.CommentIn) -> schemas.CommentOut:
    case = get_case_or_404(db, case_id)
    comment = CaseComment(author=payload.author or "system", body=payload.body.strip())
    case.comments.append(comment)
    _log(case, payload.author or "system", "comment", note=comment.body[:160])
    case.updated_at = utcnow()
    db.commit()
    db.refresh(comment)
    return schemas.CommentOut.model_validate(comment)


def add_insight(db: Session, case_id: str, payload: schemas.InsightIn) -> schemas.CaseDetail:
    case = get_case_or_404(db, case_id)
    # JSON columns are replaced, never mutated in place — SQLAlchemy does not
    # track in-place edits of a JSON list and the write would be lost.
    case.saved_insights = [
        *case.saved_insights,
        {
            "id": f"si-{len(case.saved_insights) + 1}-{int(utcnow().timestamp())}",
            "body": payload.body,
            "at": schemas.to_iso(utcnow()),
        },
    ]
    case.updated_at = utcnow()
    db.commit()
    db.refresh(case)
    return _detail(case)


def add_mismatches(
    db: Session, case_id: str, rows: Sequence[schemas.MismatchIn]
) -> schemas.CaseDetail:
    case = get_case_or_404(db, case_id)
    start = len(case.mismatches)
    case.mismatches.extend(_build_mismatches(rows, start))
    case.affected_count = max(case.affected_count, start + len(rows))
    case.updated_at = utcnow()
    _log(case, "rule_engine", "evidence_added", note=f"{len(rows)} mismatch rows added")
    db.commit()
    db.refresh(case)
    return _detail(case)


def delete_case(db: Session, case_id: str) -> None:
    case = get_case_or_404(db, case_id)
    # Remove the files before the rows that point at them, or nothing is left
    # to tell us which files to clean up.
    attachments.purge_case_files(list(case.attachments))
    db.delete(case)
    db.commit()


# --------------------------------------------------------------------------
# Attachments
# --------------------------------------------------------------------------

def add_attachments(
    db: Session, case_id: str, uploads: Sequence[Any], uploaded_by: str = ""
) -> list[schemas.AttachmentOut]:
    """Attach one or more uploaded files to a case as evidence."""
    case = get_case_or_404(db, case_id)
    saved = [attachments.save_upload(db, case.id, upload, uploaded_by) for upload in uploads]
    case.updated_at = utcnow()
    _log(
        case, uploaded_by or "system", "attachment_added",
        note=", ".join(a.filename for a in saved),
    )
    db.commit()
    for attachment in saved:
        db.refresh(attachment)
    return [attachments.to_out(a) for a in saved]


def list_attachments(db: Session, case_id: str) -> list[schemas.AttachmentOut]:
    case = get_case_or_404(db, case_id)
    return [attachments.to_out(a) for a in case.attachments]


def remove_attachment(db: Session, case_id: str, attachment_id: str, actor: str = "system") -> None:
    case = get_case_or_404(db, case_id)
    attachment = attachments.get_or_404(db, case.id, attachment_id)
    filename = attachment.filename
    attachments.delete_file(attachment)
    db.delete(attachment)
    _log(case, actor, "attachment_removed", note=filename)
    case.updated_at = utcnow()
    db.commit()


def stale_open_cases(db: Session, older_than_hours: int = 48) -> list[schemas.CaseRow]:
    """Open cases untouched for a while — the nagging list for a daily digest."""
    cutoff = utcnow() - timedelta(hours=older_than_hours)
    stmt = (
        select(Case)
        .where(Case.status.notin_(list(catalog.TERMINAL_STATUSES)), Case.updated_at < cutoff)
        .order_by(Case.updated_at.asc())
    )
    return [_row(c, len(c.mismatches)) for c in db.execute(stmt).scalars().all()]
