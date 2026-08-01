"""RA Backend — minimal FastAPI service for the UI application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import settings

app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
