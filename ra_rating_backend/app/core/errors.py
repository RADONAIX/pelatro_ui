"""Domain exceptions and global handlers.

The error envelope is byte-compatible with the existing RADONaix API, so the UI
can surface failures from either service through the same code path:

    {"error": {"code": "not_found", "message": "...", "details": {...}}}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

log = get_logger("errors")


class AppError(Exception):
    """Base application error mapped to an HTTP response."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "app_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class ValidationFailedError(AppError):
    status_code = 422
    code = "validation_failed"


class RuleStateError(AppError):
    """An operation is not legal for the rule's current lifecycle state.

    Distinct from a plain 409 so the UI can explain *which* transition was
    refused (e.g. editing conditions on a PUBLISHED rule).
    """

    status_code = status.HTTP_409_CONFLICT
    code = "invalid_rule_state"


class UpstreamUnavailableError(AppError):
    """A dependency (ClickHouse / identity Postgres / Airflow) is unreachable."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "upstream_unavailable"


# --- Rating-assurance domain errors (§32) -----------------------------------
# These are raised deep inside the batch pipeline, where the correct response is
# almost always "record an exception row and carry on with the next CDR" rather
# than "fail the request". They subclass AppError so that the handful of paths
# that *do* surface over HTTP — re-rating one usage record, retrying one
# exception — still return the standard envelope instead of a bare 500.


class RatingAssuranceError(AppError):
    """Base for every failure the rating pipeline attributes to one CDR."""

    status_code = 422
    code = "rating_failed"
    #: Written to rating_exception.exception_code, so the operational taxonomy
    #: and the API error code cannot drift apart.
    exception_code: str = "RATING_FAILED"


class EnrichmentError(RatingAssuranceError):
    code = "enrichment_failed"
    exception_code = "ENRICHMENT_FAILED"


class NoRuleFoundError(RatingAssuranceError):
    code = "no_rule_found"
    exception_code = "NO_RULE_FOUND"


class AmbiguousRuleError(RatingAssuranceError):
    """Two rules were equally entitled to price this usage.

    Deliberately fatal for the record rather than resolved by a tie-break: an
    arbitrary winner produces a charge nobody can defend, and the silence is
    worse than the exception.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "ambiguous_rule_match"
    exception_code = "AMBIGUOUS_RULE_MATCH"


class CalculationError(RatingAssuranceError):
    code = "calculation_failed"
    exception_code = "CALCULATION_FAILED"


class ActualChargeNotFoundError(RatingAssuranceError):
    code = "no_actual_charge"
    exception_code = "NO_ACTUAL_CHARGE"


def _envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _field_summary(error: dict[str, Any]) -> str:
    """``body.actions.0.action_type: not a valid enumeration member``.

    Enough to fix the call from the log alone. Without it a 422 in the access log
    is just a number, and diagnosing it means asking the reporter to reproduce it
    with the response body captured — which is a round trip for information the
    server already had.
    """
    location = ".".join(str(part) for part in error.get("loc", ()))
    return f"{location}: {error.get('msg', 'invalid')}"


def _log_rejection(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None,
) -> None:
    """Record why a request was refused, next to the access-log line that says
    it was.

    Only 4xx: a 5xx already logs a traceback, and logging it twice makes the
    traceback harder to find. Server errors are somebody's bug; client errors are
    a conversation between two systems, and that conversation is what needs a
    transcript.
    """
    if status_code >= 500:
        return
    log.warning(
        "request_rejected",
        method=request.method,
        path=request.url.path,
        status=status_code,
        code=code,
        reason=message,
        details=details or {},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        _log_rejection(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = jsonable_encoder(exc.errors())
        _log_rejection(
            request,
            status_code=422,
            code="validation_failed",
            message="Request validation failed.",
            # The field paths, not the whole error objects: `ctx` on an enum
            # failure carries every permitted value, and a log line listing 39
            # rule types buries the one field that was wrong.
            details={"fields": [_field_summary(e) for e in errors]},
        )
        return JSONResponse(
            status_code=422,
            content=_envelope(
                "validation_failed",
                "Request validation failed.",
                {"errors": errors},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope("http_error", str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError) -> JSONResponse:
        # The asyncpg DBAPI wraps every constraint failure in a class literally
        # named "IntegrityError", so the class name alone cannot distinguish a
        # duplicate from a dead reference. SQLSTATE can (23505 unique, 23503
        # foreign key); the name check stays as a fallback for other drivers.
        orig = getattr(exc, "orig", exc)
        origin = type(orig).__name__.lower()
        sqlstate = getattr(orig, "sqlstate", None) or getattr(
            getattr(orig, "__cause__", None), "sqlstate", None
        )
        if sqlstate == "23505" or "unique" in origin:
            code, http = "conflict", status.HTTP_409_CONFLICT
            message = "A record with these values already exists."
        elif sqlstate == "23503" or "foreignkey" in origin:
            code, http = "conflict", status.HTTP_409_CONFLICT
            message = "Referenced record does not exist."
        else:
            code, http = "validation_failed", 422
            message = "A field value violates a database constraint."
        log.warning("integrity_error", origin=origin, sqlstate=sqlstate)
        return JSONResponse(status_code=http, content=_envelope(code, message))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_exception", path=request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope("internal_error", "An unexpected error occurred."),
        )
