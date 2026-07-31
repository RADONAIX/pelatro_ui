"""Health, readiness and dashboard headline KPIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.core.config import settings
from app.core.database import engine
from app.core.deps import Principal, require
from app.core.rbac import PermKey
from app.integrations import airflow, broker, clickhouse, ra_postgres
from app.modules.assurance import service as assurance_service
from app.modules.meta import metadata_catalog

router = APIRouter(tags=["meta"])


class Health(BaseModel):
    status: str
    version: str
    environment: str


class Readiness(BaseModel):
    ready: bool
    checks: dict[str, bool]


class DashboardKpis(BaseModel):
    assuredRevenue: float
    matchRate: float
    openLeakageRisk: float
    criticalAlerts: int


@router.get(
    "/metadata/tables",
    response_model=list[metadata_catalog.TableMetadata],
)
async def metadata_tables(
    assurance: str,
) -> list[metadata_catalog.TableMetadata]:
    return await metadata_catalog.list_tables(assurance)


@router.get("/metadata/file-logs", response_model=list[metadata_catalog.TableMetadata])
async def metadata_file_logs() -> list[metadata_catalog.TableMetadata]:
    """The AIR/SDP raw and processed file logs — the tables single-table rules
    (Sequence, Duplicate) are authored against. Not assurance-scoped: the same
    four logs answer the question for every assurance."""
    return await metadata_catalog.list_file_logs()


@router.get(
    "/metadata/file-logs/{schema_name}/{table_name}/columns",
    response_model=list[metadata_catalog.ColumnMetadata],
)
async def metadata_file_log_columns(
    schema_name: str,
    table_name: str,
) -> list[metadata_catalog.ColumnMetadata]:
    return await metadata_catalog.list_file_log_columns(schema_name, table_name)


@router.get(
    "/metadata/tables/{schema_name}/{table_name}/columns",
    response_model=list[metadata_catalog.ColumnMetadata],
)
async def metadata_columns(
    schema_name: str,
    table_name: str,
    assurance: str,
    database: str | None = None,
) -> list[metadata_catalog.ColumnMetadata]:
    return await metadata_catalog.list_columns(
        assurance, schema_name, table_name, database_name=database
    )


@router.get("/health", response_model=Health)
async def health() -> Health:
    return Health(status="ok", version=__version__, environment=settings.environment)


@router.get("/health/ready", response_model=Readiness)
async def readiness() -> Readiness:
    checks: dict[str, bool] = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["app_db"] = True
    except Exception:  # noqa: BLE001
        checks["app_db"] = False
    checks["clickhouse"] = await clickhouse.ping()
    checks["ra_postgres"] = await ra_postgres.ping()
    checks["airflow"] = await airflow.ping()
    # Exports need Redis + a running Celery worker. Neither is required to serve
    # traffic, but with `celery_worker` false every export sits at Queued forever
    # — surface it here instead of letting jobs pile up silently.
    if settings.exports_enabled:
        checks["redis"] = await broker.ping_broker()
        checks["celery_worker"] = await broker.ping_workers()
    # Readiness only requires the owned app DB; integrations are optional.
    return Readiness(ready=checks["app_db"], checks=checks)


@router.get("/dashboard/kpis", response_model=DashboardKpis)
async def dashboard_kpis(
    _: Principal = Depends(require(PermKey.DASHBOARD, "view")),
) -> DashboardKpis:
    summary = await assurance_service.recon_summary(hours=24)
    matched_amount = summary.total - summary.amountMismatch - summary.rawOnly - summary.procOnly
    return DashboardKpis(
        assuredRevenue=round(max(matched_amount, 0), 2),
        matchRate=summary.matchRate,
        openLeakageRisk=summary.estimatedLeakage,
        criticalAlerts=summary.amountMismatch + summary.rawOnly,
    )
