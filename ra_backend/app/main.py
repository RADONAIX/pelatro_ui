"""FastAPI application factory.

Modular monolith: every bounded-context module is mounted under the API
prefix via ``app.api.api_router``. Designed to split into independent services
later without changing module internals.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

# Import the models aggregate so every table is registered on Base.metadata.
import app.models  # noqa: F401
from app import __version__
from app.api import api_router
from app.core.config import settings
from app.core.database import SessionFactory, engine
from app.core.errors import register_exception_handlers
from app.core.integrations_shutdown import close_all_integrations
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RateLimitMiddleware, RequestContextMiddleware

configure_logging(level=settings.log_level, json_logs=settings.log_json)
log = get_logger("app")

_EXPORTS_PURGE_INTERVAL_SECONDS = 6 * 60 * 60  # every 6h


async def _exports_cleanup_loop() -> None:
    """Periodically delete expired export jobs + their files. Best-effort: any
    error is logged and the loop continues (never crashes the app)."""
    from app.modules.exports import service as exports_service

    while True:
        try:
            async with SessionFactory() as db:
                await exports_service.purge_expired(db)
        except Exception as exc:  # noqa: BLE001
            log.warning("exports_cleanup_failed", error=str(exc))
        await asyncio.sleep(_EXPORTS_PURGE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info(
        "startup",
        environment=settings.environment,
        clickhouse=settings.clickhouse_enabled,
        ra_postgres=settings.ra_pg_enabled,
        airflow=settings.airflow_enabled,
        exports=settings.exports_enabled,
    )
    cleanup_task = (
        asyncio.create_task(_exports_cleanup_loop()) if settings.exports_enabled else None
    )
    yield
    if cleanup_task is not None:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
    await close_all_integrations()
    await engine.dispose()
    log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.project_name,
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Innermost: per-IP rate limit on login (returns 429 before the handler).
    # Added first so CORS/RequestContext wrap it and the 429 still gets their
    # headers (X-Request-ID + CORS for the browser to read the message).
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=settings.login_rate_limit_max,
        window_seconds=settings.login_rate_limit_window_seconds,
        paths={
            ("POST", f"{settings.api_prefix}/auth/login"),
            ("POST", f"{settings.api_prefix}/auth/forgot-password"),
        },
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Checksum-SHA256"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": settings.project_name, "docs": "/docs", "api": settings.api_prefix}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
