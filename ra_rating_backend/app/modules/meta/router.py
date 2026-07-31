"""Health, dictionaries and the caller's effective permissions.

The dictionary endpoints are what make the visual rule builder possible: the UI
renders whatever attributes, operators and actions the ENGINE supports, so the
two cannot drift. Adding an attribute in ``rules/constants.py`` makes it appear
in the builder with no UI change.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.core import identity
from app.core.config import settings
from app.core.database import engine as db_engine
from app.core.deps import CurrentUser, DbSession
from app.core.rbac import PERMISSION_CATALOG
from app.modules.catalog.constants import (
    AccountType,
    CatalogStatus,
    DiscountType,
    ResetPeriod,
    RoundingMode,
    ServiceType,
    TaxType,
    UsageUnit,
    ZoneType,
)
from app.modules.rules.constants import (
    ACTION_SPECS,
    ALLOWED_TRANSITIONS,
    OPERATOR_ARITY,
    OPERATOR_LABELS,
    OPERATORS_BY_TYPE,
    RULE_ATTRIBUTES,
    RULE_TYPE_STAGE,
    STAGE_ORDER,
    ConditionLogic,
    RuleStatus,
    RuleType,
    StackingPolicy,
)
from app.modules.tenancy import isolation as tenancy_isolation

router = APIRouter(tags=["meta"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    rating_db: bool
    identity_db: bool
    clickhouse_enabled: bool
    airflow_enabled: bool
    #: Whether row-level tenant isolation actually binds this connection. False
    #: with a populated `tenant_isolation_detail` means the policies exist and do
    #: nothing — the service is connecting as a role that bypasses them.
    tenant_isolation: bool = False
    tenant_isolation_detail: str = ""


@router.get("/health", response_model=HealthResponse, summary="Liveness / readiness")
async def health(db: DbSession) -> HealthResponse:
    try:
        await db.execute(text("SELECT 1"))
        rating_ok = True
    except Exception:
        rating_ok = False
    identity_ok = await identity.ping()
    isolation = await tenancy_isolation.inspect(db_engine, settings.rule_db_schema)
    return HealthResponse(
        status="ok" if rating_ok else "degraded",
        service=settings.project_name,
        version=__version__,
        environment=settings.environment,
        rating_db=rating_ok,
        identity_db=identity_ok,
        clickhouse_enabled=settings.clickhouse_enabled,
        airflow_enabled=settings.airflow_enabled,
        tenant_isolation=isolation.enforced,
        tenant_isolation_detail=isolation.problem,
    )


class MeResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    permissions: dict[str, dict[str, bool]]


@router.get("/me", response_model=MeResponse, summary="Caller identity + rating permissions")
async def me(principal: CurrentUser) -> MeResponse:
    return MeResponse(
        id=principal.id,
        email=principal.email,
        full_name=principal.full_name,
        role=principal.role,
        permissions=principal.permissions,
    )


@router.get("/permissions", summary="Rating permission catalogue")
async def permission_catalog() -> list[dict[str, str]]:
    return [{"key": k.value, "label": label, "path": path} for k, label, path in PERMISSION_CATALOG]


# --- Rule vocabulary --------------------------------------------------------


@router.get("/meta/attributes", summary="Selectable condition attributes")
async def rule_attributes(_: CurrentUser) -> list[dict[str, Any]]:
    return [
        {
            "key": a.key,
            "label": a.label,
            "data_type": a.data_type,
            "group": a.group,
            "reference": a.reference,
            "values": list(a.values),
            "specificity": a.specificity,
            "description": a.description,
            "operators": list(OPERATORS_BY_TYPE.get(a.data_type, ())),
        }
        for a in RULE_ATTRIBUTES
    ]


@router.get("/meta/operators", summary="Operators, their labels and value arity")
async def operators(_: CurrentUser) -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": label,
            "min_values": OPERATOR_ARITY[key][0],
            "max_values": OPERATOR_ARITY[key][1],
        }
        for key, label in OPERATOR_LABELS.items()
    ]


@router.get("/meta/actions", summary="Action types and their parameter schema")
async def action_types(_: CurrentUser) -> list[dict[str, Any]]:
    # These tuning fields remain valid in stored/imported rules for backwards
    # compatibility, but are intentionally not authorable in the rule builder.
    # The target mirror derives their canonical equivalents from Unit, Initial
    # pulse and Mode, so exposing both would let users state the same fact twice.
    hidden_builder_params = {
        "per_units",
        "subsequent_seconds",
        "decimals",
        "consume_order",
    }
    return [
        {
            "type": spec.type,
            "label": spec.label,
            "stage": spec.stage,
            "description": spec.description,
            "params": [
                {
                    "key": p.key,
                    "label": p.label,
                    "data_type": p.data_type,
                    "required": p.required,
                    "reference": p.reference,
                    "values": list(p.values),
                    "description": p.description,
                }
                for p in spec.params
                if p.key not in hidden_builder_params
            ],
        }
        for spec in ACTION_SPECS
    ]


@router.get("/meta/enums", summary="Every enumeration the UI needs")
async def enums(_: CurrentUser) -> dict[str, Any]:
    return {
        "service_type": [e.value for e in ServiceType],
        "usage_unit": [e.value for e in UsageUnit],
        "account_type": [e.value for e in AccountType],
        "zone_type": [e.value for e in ZoneType],
        "tax_type": [e.value for e in TaxType],
        "rounding_mode": [e.value for e in RoundingMode],
        "discount_type": [e.value for e in DiscountType],
        "reset_period": [e.value for e in ResetPeriod],
        "catalog_status": [e.value for e in CatalogStatus],
        "rule_status": [e.value for e in RuleStatus],
        "rule_type": [e.value for e in RuleType],
        "stacking_policy": [e.value for e in StackingPolicy],
        "condition_logic": [e.value for e in ConditionLogic],
        "execution_stage": list(STAGE_ORDER),
        "rule_type_stage": dict(RULE_TYPE_STAGE),
        "allowed_transitions": {k: list(v) for k, v in ALLOWED_TRANSITIONS.items()},
    }
