"""
app/errors.py
─────────────
Uniform error envelope for every router (SEC-03).

Every error response — raised via ApiError or caught by the handlers registered
in main.py — is serialised as:

    {"error": {"code": "<snake_case_code>", "message": "...", "request_id": "..."}}

No stack traces, SQL, or raw exception text ever reach the client.
"""

import logging
import uuid
from typing import Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def new_request_id() -> str:
    """Generate a correlation id for one request."""
    return uuid.uuid4().hex


def error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: Optional[list] = None,
) -> JSONResponse:
    """Build the uniform error envelope. Internal detail goes to logs only."""
    body = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": details,
        }
    }
    return JSONResponse(status_code=status_code, content=body)


class ApiError(Exception):
    """Raise inside handlers/routers to emit the uniform error envelope."""

    def __init__(self, status_code: int, code: str, message: str, details: Optional[list] = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    rid = getattr(request.state, "request_id", new_request_id())
    return error_response(exc.status_code, exc.code, exc.message, rid, exc.details)


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Convert plain HTTPException(detail=…) into the envelope; never leak internals."""
    rid = getattr(request.state, "request_id", new_request_id())
    if isinstance(exc.detail, dict):
        # Already envelope-shaped — pass through.
        return JSONResponse(status_code=exc.status_code, content={"error": {**exc.detail, "request_id": rid}})
    return error_response(
        status_code=exc.status_code,
        code=_code_for_status(exc.status_code),
        message=str(exc.detail) if exc.detail else "Request failed.",
        request_id=rid,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    rid = getattr(request.state, "request_id", new_request_id())
    details = [
        {"field": ".".join(str(p) for p in err.get("loc", []) if p != "body"),
         "issue": err.get("msg", "invalid")}
        for err in exc.errors()
    ]
    return error_response(422, "validation_error", "Request validation failed.", rid, details)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all 500 — log the real exception server-side, return a generic body."""
    rid = getattr(request.state, "request_id", new_request_id())
    logger.error("[Errors] Unhandled exception on %s %s (request_id=%s): %s",
                 request.method, request.url.path, rid, exc, exc_info=True)
    return error_response(500, "internal_error", "Internal server error.", rid)


def _code_for_status(status_code: int) -> str:
    mapping = {
        400: "bad_request",
        401: "unauthenticated",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        413: "payload_too_large",
        422: "validation_error",
        429: "rate_limited",
        502: "upstream_error",
        503: "service_unavailable",
        504: "gateway_timeout",
    }
    return mapping.get(status_code, "internal_error" if status_code >= 500 else "request_failed")
