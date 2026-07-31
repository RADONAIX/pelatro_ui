"""FastAPI application factory for the Rating Assurance service.

Runs as its own process on its own port. It shares nothing with the existing
RADONaix API except the JWT signing secret (to verify tokens) and a read-only
view of the ``administration`` schema (to resolve who the caller is).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

# Registers every table on Base.metadata.
import app.models
from app import __version__
from app.api import api_router
from app.core.config import settings
from app.core.database import dispose_engines, engine
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.modules.tenancy.isolation import report_at_startup as report_tenant_isolation

configure_logging(level=settings.log_level, json_logs=settings.log_json)
log = get_logger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.environment == "production" and settings.jwt_secret_is_insecure:
        # Loud, not fatal: a wrong secret here means every request 401s, which is
        # far more confusing to diagnose than a startup warning.
        log.error("insecure_jwt_secret", hint="JWT_SECRET must match the RADONaix API's.")
    # Whether tenant isolation actually binds this connection, or is merely
    # configured. Asked at boot because the difference is invisible everywhere
    # else: a superuser sails through a perfect set of policies without touching
    # them, and nothing errors.
    await report_tenant_isolation(engine, settings.rule_db_schema)
    # The mirror's three lookup tables have no write event to observe — the
    # platform holds that vocabulary in Python, not in rows — and the rule
    # tables have foreign keys onto them, so they are reconciled before any
    # rule can be mirrored. Never raises; a no-op when the mirror is disabled.
    if settings.mirror_enabled:
        from app.modules.mirror.hooks import reconcile_vocabulary

        await reconcile_vocabulary()
    from app.modules.mirror_assurance.service import start_scheduler

    start_scheduler()
    log.info(
        "startup",
        environment=settings.environment,
        api_prefix=settings.api_prefix,
        rating_schema=settings.rating_db_schema,
        clickhouse=settings.clickhouse_enabled,
        airflow=settings.airflow_enabled,
    )
    yield
    from app.modules.mirror_assurance.service import stop_scheduler

    await stop_scheduler()
    await dispose_engines()
    log.info("shutdown")


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.project_name,
        version=__version__,
        description=(
            "Rating Assurance control plane: canonical tariff metadata, rule authoring, "
            "versioning and validation. Authenticates with tokens issued by the RADONaix API."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    application.add_middleware(RequestContextMiddleware)

    register_exception_handlers(application)
    application.include_router(api_router, prefix=settings.api_prefix)

    @application.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": settings.project_name,
            "docs": "/docs",
            "api": settings.api_prefix,
        }

    @application.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return application


app = create_app()
