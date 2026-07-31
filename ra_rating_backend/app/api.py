"""Top-level API router aggregating every module under the API prefix."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.balances.router import router as balances_router
from app.modules.catalog.router import router as catalog_router
from app.modules.cdr.router import router as cdr_router
from app.modules.compiler.router import router as compiler_router
from app.modules.connectors.router import router as connectors_router
from app.modules.dashboards.router import router as dashboards_router
from app.modules.imports.router import router as imports_router
from app.modules.meta.canonical import router as canonical_meta_router
from app.modules.meta.router import router as meta_router
from app.modules.mirror_assurance.router import router as mirror_assurance_router
from app.modules.pipeline.router import router as pipeline_router
from app.modules.rating.assurance_router import router as rating_assurance_router
from app.modules.rating.router import router as rating_router
from app.modules.replay.router import router as replay_router
from app.modules.reports.router import router as reports_router
from app.modules.rules.api.ingest_router import router as rule_ingest_router
from app.modules.rules.api.router import router as canonical_rules_router
from app.modules.rules.lifecycle.router import router as rule_lifecycle_router
from app.modules.rules.router import router as rules_router

api_router = APIRouter()
api_router.include_router(meta_router)
api_router.include_router(canonical_meta_router)
api_router.include_router(catalog_router)
api_router.include_router(rules_router)
api_router.include_router(canonical_rules_router)
api_router.include_router(rule_ingest_router)
api_router.include_router(rule_lifecycle_router)
api_router.include_router(imports_router)
api_router.include_router(compiler_router)
api_router.include_router(cdr_router)
api_router.include_router(rating_router)
api_router.include_router(rating_assurance_router)
api_router.include_router(pipeline_router)
api_router.include_router(mirror_assurance_router)
api_router.include_router(connectors_router)
api_router.include_router(dashboards_router)
api_router.include_router(balances_router)
api_router.include_router(reports_router)
api_router.include_router(replay_router)
