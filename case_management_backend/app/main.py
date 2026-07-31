"""RA Backend — FastAPI service for the RA UI application."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import settings
from app.db import SessionLocal, init_db
from app.routers import cases as cases_router
from app.routers import catalog as catalog_router
from app.routers import rules as rules_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ra.backend")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create the assurance schema and, on a fresh database, load the demo set."""
    init_db()
    logger.info("schema '%s' ready on %s", settings.db_schema, settings.db_name)
    if settings.seed_demo_data:
        from app.seed import seed_all

        db = SessionLocal()
        try:
            rules, cases = seed_all(db)
            if rules or cases:
                logger.info("seeded %d control rules and %d cases", rules, cases)
        except Exception:
            # A seeding failure must not take the API down — the endpoints work
            # fine against an empty schema.
            logger.exception("demo seeding failed")
            db.rollback()
        finally:
            db.close()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cases_router.router)
app.include_router(rules_router.router)
app.include_router(catalog_router.router)


class Message(BaseModel):
    message: str


@app.get("/health", response_model=Message, tags=["system"])
def health() -> Message:
    """Liveness probe."""
    return Message(message="ok")


@app.get("/say_hi", response_model=Message, tags=["demo"])
def say_hi() -> Message:
    """Sample endpoint returning a greeting."""
    return Message(message="hi")
