"""Reconciliation REST surface: /reconciliation.

  GET    /reconciliation/reports?assurance=      the dynamic Reports menu
  GET    /reconciliation/reports/{key}           one page of the latest run
  GET    /reconciliation/reports/{key}/summary   headline + generated SQL
  GET    /reconciliation/rules/{rule_id}         the compiled definition
  POST   /reconciliation/rules/{rule_id}/run     run now
  GET    /reconciliation/rules/{rule_id}/executions   audit history
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.deps import Principal, require
from app.core.rbac import PermKey
from app.modules.reconciliation import repository, service
from app.modules.reconciliation.plan import ALL_STATUSES

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.get("/reports")
async def list_reports(
    assurance: str | None = Query(default=None, description="Assurance app id, e.g. billing"),
    _: Principal = Depends(require(PermKey.REPORTS, "view")),
) -> list[dict[str, Any]]:
    """Every generated reconciliation report, optionally for one assurance.

    The Reports menu calls this per scope, which is what keeps it dynamic: a
    rule created a minute ago is in the list without a deploy.
    """
    return await service.list_reports(assurance_id=assurance)


@router.get("/reports/{report_key}")
async def read_report(
    report_key: str,
    status: str | None = Query(default=None, description=f"One of {', '.join(ALL_STATUSES)}"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: Principal = Depends(require(PermKey.REPORTS, "view")),
) -> dict[str, Any]:
    """One page of the latest successful execution — keys, metrics, status."""
    return await service.read_report(report_key, status=status, limit=limit, offset=offset)


@router.get("/reports/{report_key}/summary")
async def report_summary(
    report_key: str,
    _: Principal = Depends(require(PermKey.REPORTS, "view")),
) -> dict[str, Any]:
    return await service.report_summary(report_key)


@router.get("/rules/{rule_id}")
async def get_definition(
    rule_id: str,
    _: Principal = Depends(require(PermKey.WORKBENCH, "view")),
) -> dict[str, Any]:
    definition = await repository.get_definition(rule_id)
    # jsonb columns come back parsed; timestamps are serialised by FastAPI.
    return definition


@router.post("/rules/{rule_id}/run")
async def run_now(
    rule_id: str,
    principal: Principal = Depends(require(PermKey.WORKBENCH, "edit")),
) -> dict[str, Any]:
    """Execute immediately, outside the schedule. Same path the scheduler uses,
    so a manual run and a scheduled run cannot diverge."""
    return await service.run_stored(
        rule_id, trigger="manual", triggered_by=principal.email
    )


@router.get("/rules/{rule_id}/executions")
async def list_executions(
    rule_id: str,
    limit: int = Query(default=20, ge=1, le=200),
    _: Principal = Depends(require(PermKey.WORKBENCH, "view")),
) -> list[dict[str, Any]]:
    return await repository.list_executions(rule_id, limit=limit)
