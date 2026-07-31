"""Authored assurance rules: /assurance-rules.

The Rule Explorer's own CRUD. Separate from /rules (the versioned, approvable
rule engine in the app database) — these are the rules a user writes on the
Controls screen, stored in application_schema.assurance_rule and scoped to one
assurance.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.deps import Principal, require
from app.core.rbac import PermKey
from app.modules.assurance_rules import schemas, service

router = APIRouter(prefix="/assurance-rules", tags=["assurance-rules"])

# `assurance` is required on every list and create. It is what keeps Billing
# Assurance showing billing rules and nothing else, so there is deliberately no
# "all assurances" mode to fall into by omission.
AssuranceId = Query(min_length=1, max_length=64, description="Assurance app id, e.g. billing")


@router.get("", response_model=list[schemas.RuleRow])
async def list_rules(
    assurance: str = AssuranceId,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: Principal = Depends(require(PermKey.WORKBENCH, "view")),
) -> list[schemas.RuleRow]:
    rows = await service.list_rules(assurance_id=assurance, limit=limit, offset=offset)
    return [schemas.RuleRow(**r) for r in rows]


@router.get("/{rule_id}", response_model=schemas.RuleRow)
async def get_rule(
    rule_id: str,
    _: Principal = Depends(require(PermKey.WORKBENCH, "view")),
) -> schemas.RuleRow:
    return schemas.RuleRow(**await service.get_rule(rule_id))


@router.post("", response_model=schemas.RuleRow, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: schemas.RuleCreate,
    assurance: str = AssuranceId,
    prefix: str = Query(min_length=2, max_length=4, description="Control-id prefix, e.g. BA"),
    principal: Principal = Depends(require(PermKey.WORKBENCH, "edit")),
) -> schemas.RuleRow:
    row = await service.create_rule(
        assurance_id=assurance,
        prefix=prefix,
        payload=payload,
        created_by=principal.email,
    )
    return schemas.RuleRow(**row)


@router.put("/{rule_id}", response_model=schemas.RuleRow)
async def update_rule(
    rule_id: str,
    payload: schemas.RuleUpdate,
    _: Principal = Depends(require(PermKey.WORKBENCH, "edit")),
) -> schemas.RuleRow:
    return schemas.RuleRow(**await service.update_rule(rule_id=rule_id, payload=payload))


@router.post("/{rule_id}/state", response_model=schemas.RuleRow)
async def set_state(
    rule_id: str,
    state: str = Query(description="Draft or Active"),
    _: Principal = Depends(require(PermKey.WORKBENCH, "edit")),
) -> schemas.RuleRow:
    return schemas.RuleRow(**await service.set_state(rule_id=rule_id, state=state))


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: str,
    _: Principal = Depends(require(PermKey.WORKBENCH, "edit")),
) -> Response:
    await service.delete_rule(rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
