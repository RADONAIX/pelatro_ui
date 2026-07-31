"""Control-rule API — the catalog the Rule Explorer lists and edits.

`POST /api/rules/{id}/run` is the execution seam: the engine reports a run's
outcome and a failure automatically opens a case built from the rule's own
metadata.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import rule_service, schemas
from app.db import get_db
from app.rule_service import RuleFilters

router = APIRouter(prefix="/api/rules", tags=["rules"])


def filter_params(
    q: str | None = Query(None, description="Free text over id, name, intent, category"),
    assurance: list[str] | None = Query(None, description="Assurance code or name"),
    category: list[str] | None = Query(None, description="Primitive category"),
    entity_scope: list[str] | None = Query(None, alias="entityScope"),
    severity: list[str] | None = Query(None),
    frequency: list[str] | None = Query(None),
    lifecycle_state: list[str] | None = Query(None, alias="lifecycleState"),
    last_status: list[str] | None = Query(None, alias="lastStatus"),
) -> RuleFilters:
    return RuleFilters(
        q=q,
        assurance=assurance or [],
        category=category or [],
        entity_scope=entity_scope or [],
        severity=severity or [],
        frequency=frequency or [],
        lifecycle_state=lifecycle_state or [],
        last_status=last_status or [],
    )


@router.get("", response_model=schemas.RuleListResponse)
def list_rules(
    filters: RuleFilters = Depends(filter_params),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500, alias="pageSize"),
    sort_by: str = Query("id", alias="sortBy"),
    sort_dir: str = Query("asc", alias="sortDir", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> schemas.RuleListResponse:
    """The provisioned controls, filtered and paginated."""
    return rule_service.list_rules(db, filters, page, page_size, sort_by, sort_dir)


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict:
    """Rule counts by category, assurance and last run status."""
    return rule_service.rule_stats(db)


@router.post("", response_model=schemas.RuleOut, status_code=201)
def create_rule(payload: schemas.RuleCreate, db: Session = Depends(get_db)) -> schemas.RuleOut:
    """Author a control rule. The id is generated from the assurance code
    (UA001, UA002…) unless one is supplied."""
    return rule_service.create_rule(db, payload)


@router.get("/{rule_id}", response_model=schemas.RuleOut)
def get_rule(rule_id: str, db: Session = Depends(get_db)) -> schemas.RuleOut:
    """One rule, with a live count of the cases still open against it."""
    return rule_service.get_rule(db, rule_id)


@router.patch("/{rule_id}", response_model=schemas.RuleOut)
def update_rule(
    rule_id: str, payload: schemas.RuleUpdate, db: Session = Depends(get_db)
) -> schemas.RuleOut:
    """Edit a rule — including promoting it from Draft to Active."""
    return rule_service.update_rule(db, rule_id, payload)


@router.delete("/{rule_id}", response_model=schemas.OkResponse)
def delete_rule(rule_id: str, db: Session = Depends(get_db)) -> schemas.OkResponse:
    """Delete a rule. Cases it raised are kept and keep their rule id."""
    rule_service.delete_rule(db, rule_id)
    return schemas.OkResponse(message=f"Rule {rule_id} deleted")


@router.post("/{rule_id}/run", response_model=schemas.RuleRunResponse)
def record_run(
    rule_id: str, payload: schemas.RuleRunResult, db: Session = Depends(get_db)
) -> schemas.RuleRunResponse:
    """Report the outcome of a control execution.

    `PASS` just stamps the rule. `FAIL`/`WARNING` opens a case that inherits the
    rule's assurance, module, issue type, feeds and tolerance — the run only
    supplies what it measured and the mismatch rows behind it.
    """
    return rule_service.record_run(db, rule_id, payload)
