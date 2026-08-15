"""Application wiring and the error boundary.

Every exception the API can raise maps to a structured JSON body with a stable
error code. The catch-all at the bottom means even an unforeseen bug returns a
documented shape rather than a traceback, but it is a last resort — bad input is
classified long before it gets there.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.routes import router
from .config import API_PREFIX, allowed_origins
from .core.errors import BillingLogError, DayNotFoundError, NarrativeNotCachedError
from .core.reconciliation import ReconciliationInvariantError
from .storage.db import get_connection

log = logging.getLogger("swasthiq")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create the schema and load the sample days before the first request."""
    get_connection()
    from .seed import seed_if_empty

    seed_if_empty()
    yield


app = FastAPI(
    title="SwasthiQ EOD Billing & Analytics Agent",
    version="1.0.0",
    description=(
        "Ingests a clinic's daily billing log and serves a deterministic "
        "end-of-day reconciliation, analytics, and an LLM narrative whose every "
        "figure is traceable to the report."
    ),
    docs_url="/docs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(router)


# --------------------------------------------------------------------------
# Error boundary
# --------------------------------------------------------------------------


@app.exception_handler(BillingLogError)
def handle_billing_log_error(_: Request, exc: BillingLogError) -> JSONResponse:
    """A malformed log is the caller's to fix, and the response says how."""
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content=exc.to_dict())


@app.exception_handler(DayNotFoundError)
def handle_day_not_found(_: Request, exc: DayNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={
            "error": "day_not_found",
            "message": str(exc),
            "clinic_id": exc.clinic_id,
            "business_date": exc.business_date,
            "hint": f"Ingest the day first: POST {API_PREFIX}/billing-logs",
        },
    )


@app.exception_handler(NarrativeNotCachedError)
def handle_narrative_not_cached(_: Request, exc: NarrativeNotCachedError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "narrative_not_current", "message": str(exc)},
    )


@app.exception_handler(RequestValidationError)
def handle_request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's own validation, reshaped to match our error envelope."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "error": "invalid_request",
            "message": "The request could not be understood",
            "errors": [
                {
                    "field": ".".join(str(p) for p in err["loc"]),
                    "message": err["msg"],
                    "code": err["type"],
                }
                for err in exc.errors()
            ],
        },
    )


@app.exception_handler(StarletteHTTPException)
def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Preserve structured details raised by routes; wrap plain string details."""
    detail = exc.detail
    if isinstance(detail, dict):
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "http_error", "message": str(detail)},
    )


@app.exception_handler(ReconciliationInvariantError)
def handle_invariant_error(_: Request, exc: ReconciliationInvariantError) -> JSONResponse:
    """Our arithmetic disagreed with itself. Say so; do not serve the report."""
    log.exception("reconciliation invariant violated")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "reconciliation_invariant_violated",
            "message": (
                "The payment-mode breakdown did not sum to the headline totals, so "
                "the report was withheld rather than served inconsistent."
            ),
            "detail": str(exc),
        },
    )


@app.exception_handler(Exception)
def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    """Last resort. Logged with a traceback, returned without one."""
    log.exception("unhandled error")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_error",
            "message": "Something went wrong on our side. The failure has been logged.",
        },
    )


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "SwasthiQ EOD Billing & Analytics Agent",
        "docs": "/docs",
        "health": f"{API_PREFIX}/health",
    }
