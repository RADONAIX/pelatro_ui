"""Control-rule catalog logic, and the bridge from a failed run to a case.

`record_run` is the seam the rules module plugs into: it posts the outcome of
an execution and, when that outcome is a failure, this module composes the case
from the rule's own metadata plus the measured values. The caller never has to
repeat the assurance, module, category, feeds or tolerance — those come from
the rule definition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app import catalog, schemas
from app.models import Case, ControlRule, utcnow


@dataclass
class RuleFilters:
    q: str | None = None
    assurance: list[str] = field(default_factory=list)
    category: list[str] = field(default_factory=list)
    entity_scope: list[str] = field(default_factory=list)
    severity: list[str] = field(default_factory=list)
    frequency: list[str] = field(default_factory=list)
    lifecycle_state: list[str] = field(default_factory=list)
    last_status: list[str] = field(default_factory=list)


SORTABLE: dict[str, Any] = {
    "id": ControlRule.id,
    "name": ControlRule.name,
    "assurance": ControlRule.assurance_name,
    "category": ControlRule.primitive_category,
    "entity": ControlRule.entity_scope,
    "frequency": ControlRule.frequency,
    "status": ControlRule.last_status,
    "lifecycle": ControlRule.lifecycle_state,
    "lastRun": ControlRule.last_run_at,
    "casesRaised": ControlRule.cases_raised,
}


def _apply(stmt: Select, f: RuleFilters) -> Select:
    if f.assurance:
        codes, names = set(), set()
        for raw in f.assurance:
            resolved = catalog.resolve_assurance(raw)
            (codes.add(resolved.code) if resolved else names.add(raw))
        clauses = []
        if codes:
            clauses.append(ControlRule.assurance_code.in_(codes))
        if names:
            clauses.append(ControlRule.assurance_name.in_(names))
        if clauses:
            stmt = stmt.where(or_(*clauses))
    if f.category:
        stmt = stmt.where(ControlRule.primitive_category.in_(f.category))
    if f.entity_scope:
        stmt = stmt.where(ControlRule.entity_scope.in_(f.entity_scope))
    if f.severity:
        stmt = stmt.where(ControlRule.severity.in_(f.severity))
    if f.frequency:
        stmt = stmt.where(ControlRule.frequency.in_(f.frequency))
    if f.lifecycle_state:
        stmt = stmt.where(ControlRule.lifecycle_state.in_(f.lifecycle_state))
    if f.last_status:
        stmt = stmt.where(ControlRule.last_status.in_(f.last_status))
    if f.q:
        needle = f"%{f.q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(ControlRule.id).like(needle),
                func.lower(ControlRule.name).like(needle),
                func.lower(ControlRule.intent).like(needle),
                func.lower(ControlRule.primitive_category).like(needle),
                func.lower(ControlRule.entity_scope).like(needle),
            )
        )
    return stmt


def _out(rule: ControlRule, open_cases: int = 0) -> schemas.RuleOut:
    row = schemas.RuleOut.model_validate(rule)
    row.open_cases = open_cases
    return row


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------

def list_rules(
    db: Session, f: RuleFilters, page: int = 1, page_size: int = 100,
    sort_by: str = "id", sort_dir: str = "asc",
) -> schemas.RuleListResponse:
    page = max(1, page)
    page_size = max(1, min(500, page_size))
    total = int(db.execute(_apply(select(func.count(ControlRule.id)), f)).scalar_one())

    open_cases = (
        select(func.count(Case.id))
        .where(
            Case.rule_id == ControlRule.id,
            Case.status.notin_(list(catalog.TERMINAL_STATUSES)),
        )
        .correlate(ControlRule)
        .scalar_subquery()
    )
    col = SORTABLE.get(sort_by, ControlRule.id)
    stmt = _apply(select(ControlRule, open_cases), f)
    stmt = stmt.order_by(col.desc() if sort_dir.lower() == "desc" else col.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    return schemas.RuleListResponse(
        items=[_out(rule, int(n or 0)) for rule, n in db.execute(stmt).all()],
        total=total,
        page=page,
        page_size=page_size,
        page_count=max(1, (total + page_size - 1) // page_size),
    )


def get_rule_or_404(db: Session, rule_id: str) -> ControlRule:
    rule = db.get(ControlRule, rule_id.upper())
    if rule is None:
        raise HTTPException(404, f"Rule '{rule_id}' not found")
    return rule


def get_rule(db: Session, rule_id: str) -> schemas.RuleOut:
    rule = get_rule_or_404(db, rule_id)
    open_cases = int(db.execute(
        select(func.count(Case.id)).where(
            Case.rule_id == rule.id,
            Case.status.notin_(list(catalog.TERMINAL_STATUSES)),
        )
    ).scalar_one())
    return _out(rule, open_cases)


def rule_stats(db: Session) -> dict[str, Any]:
    """Counts for the Rule Explorer header and its category rail."""
    by_category = {
        str(k): int(v) for k, v in db.execute(
            select(ControlRule.primitive_category, func.count(ControlRule.id))
            .group_by(ControlRule.primitive_category)
        ).all()
    }
    by_assurance = {
        str(k): int(v) for k, v in db.execute(
            select(ControlRule.assurance_name, func.count(ControlRule.id))
            .group_by(ControlRule.assurance_name)
        ).all()
    }
    by_status = {
        str(k): int(v) for k, v in db.execute(
            select(ControlRule.last_status, func.count(ControlRule.id))
            .group_by(ControlRule.last_status)
        ).all()
    }
    total = int(db.execute(select(func.count(ControlRule.id))).scalar_one())
    active = int(db.execute(
        select(func.count(ControlRule.id)).where(ControlRule.lifecycle_state == "Active")
    ).scalar_one())
    return {
        "total": total,
        "active": active,
        "byCategory": by_category,
        "byAssurance": by_assurance,
        "byStatus": by_status,
    }


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------

def next_rule_id(db: Session, assurance_code: str) -> str:
    """UA001, UA002… continuing from the highest id in that assurance."""
    ids = db.execute(
        select(ControlRule.id).where(ControlRule.assurance_code == assurance_code)
    ).scalars().all()
    highest = 0
    for value in ids:
        match = re.search(r"(\d+)$", value or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{assurance_code}{highest + 1:03d}"


def _validate(payload: schemas.RuleBase) -> None:
    if payload.severity not in catalog.SEVERITIES:
        raise HTTPException(400, f"severity must be one of {catalog.SEVERITIES}")
    if payload.frequency not in catalog.FREQUENCIES:
        raise HTTPException(400, f"frequency must be one of {catalog.FREQUENCIES}")
    if payload.lifecycle_state not in catalog.LIFECYCLE_STATES:
        raise HTTPException(400, f"lifecycleState must be one of {catalog.LIFECYCLE_STATES}")
    if payload.primitive_category and payload.primitive_category not in catalog.RULE_CATEGORIES:
        raise HTTPException(400, f"primitiveCategory must be one of {catalog.RULE_CATEGORIES}")


def create_rule(db: Session, payload: schemas.RuleCreate) -> schemas.RuleOut:
    _validate(payload)
    assurance = catalog.resolve_assurance(payload.assurance)
    if assurance is None:
        known = ", ".join(a.code for a in catalog.ASSURANCES)
        raise HTTPException(400, f"Unknown assurance '{payload.assurance}'. Known codes: {known}")
    if payload.entity_scope and payload.entity_scope not in assurance.modules:
        raise HTTPException(
            400,
            f"entityScope '{payload.entity_scope}' is not in scope for {assurance.name}. "
            f"Valid scopes: {assurance.modules}",
        )

    rule_id = (payload.id or next_rule_id(db, assurance.code)).upper()
    if db.get(ControlRule, rule_id) is not None:
        raise HTTPException(409, f"Rule '{rule_id}' already exists")

    rule = ControlRule(
        id=rule_id,
        name=payload.name.strip(),
        intent=payload.intent.strip(),
        assurance_code=assurance.code,
        assurance_name=assurance.name,
        assurance_group=assurance.group,
        entity_scope=payload.entity_scope or (assurance.modules[0] if assurance.modules else ""),
        primitive_category=payload.primitive_category,
        severity=payload.severity,
        frequency=payload.frequency,
        source_feed=payload.source_feed,
        target_feed=payload.target_feed,
        tolerance_pct=payload.tolerance_pct,
        params=payload.params or {},
        lifecycle_state=payload.lifecycle_state,
        last_status="NOT_RUN",
        created_by=payload.created_by,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _out(rule)


def update_rule(db: Session, rule_id: str, payload: schemas.RuleUpdate) -> schemas.RuleOut:
    rule = get_rule_or_404(db, rule_id)
    data = payload.model_dump(exclude_none=True)

    if "severity" in data and data["severity"] not in catalog.SEVERITIES:
        raise HTTPException(400, f"severity must be one of {catalog.SEVERITIES}")
    if "frequency" in data and data["frequency"] not in catalog.FREQUENCIES:
        raise HTTPException(400, f"frequency must be one of {catalog.FREQUENCIES}")
    if "lifecycle_state" in data and data["lifecycle_state"] not in catalog.LIFECYCLE_STATES:
        raise HTTPException(400, f"lifecycleState must be one of {catalog.LIFECYCLE_STATES}")
    if "last_status" in data and data["last_status"] not in catalog.RULE_STATUSES:
        raise HTTPException(400, f"lastStatus must be one of {catalog.RULE_STATUSES}")

    for key, value in data.items():
        setattr(rule, key, value)
    rule.updated_at = utcnow()
    db.commit()
    db.refresh(rule)
    return _out(rule)


def delete_rule(db: Session, rule_id: str) -> None:
    rule = get_rule_or_404(db, rule_id)
    db.delete(rule)
    db.commit()


# --------------------------------------------------------------------------
# Execution → case
# --------------------------------------------------------------------------

def record_run(db: Session, rule_id: str, result: schemas.RuleRunResult) -> schemas.RuleRunResponse:
    """Record a control execution and open a case when it failed.

    The case inherits the rule's assurance, entity scope, category, feeds,
    severity and tolerance, so a run only has to report what it measured.
    """
    from app import case_service  # imported here to avoid a circular import

    rule = get_rule_or_404(db, rule_id)
    if result.status not in catalog.RULE_STATUSES:
        raise HTTPException(400, f"status must be one of {catalog.RULE_STATUSES}")

    rule.last_status = result.status
    rule.last_run_at = utcnow()

    if result.status == "PASS":
        db.commit()
        return schemas.RuleRunResponse(rule_id=rule.id, status=result.status, case_created=False)

    if rule.lifecycle_state != "Active":
        # A draft or paused rule may be executed for a dry run, but it must not
        # put work in front of an analyst.
        db.commit()
        raise HTTPException(
            409,
            f"Rule '{rule.id}' is {rule.lifecycle_state} — activate it before it can raise cases.",
        )

    payload = schemas.RuleCaseIngest(
        title=result.title or f"{rule.name} — {rule.assurance_name}",
        description=result.description,
        assurance=rule.assurance_code,
        module=rule.entity_scope,
        sub_module=result.sub_module,
        rule_id=rule.id,
        rule_name=rule.name,
        rule_category=rule.primitive_category,
        rule_run_id=result.run_id,
        severity=result.severity or rule.severity,
        owner=result.owner,
        stream=result.stream,
        node_id=result.node_id,
        source_feed=rule.source_feed,
        target_feed=rule.target_feed,
        linked_batch=result.linked_batch,
        linked_txn_id=result.linked_txn_id,
        expected_value=result.expected_value,
        actual_value=result.actual_value,
        variance=result.variance,
        variance_pct=result.variance_pct,
        threshold=None if rule.tolerance_pct is None else f"{rule.tolerance_pct}%",
        estimated_impact=result.estimated_impact,
        affected_count=result.affected_count,
        details={**result.details, "ruleIntent": rule.intent, "ruleFrequency": rule.frequency},
        detected_at=result.detected_at,
        mismatches=result.mismatches,
        dedupe_key=result.dedupe_key,
    )
    case, created = case_service.ingest_rule_case(db, payload)
    if created:
        rule.cases_raised += 1
    db.commit()

    return schemas.RuleRunResponse(
        rule_id=rule.id,
        status=result.status,
        case_created=created,
        case=case_service.get_detail(db, case.id),
    )
