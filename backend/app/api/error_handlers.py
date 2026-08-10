"""Safe, consistent error responses.

Two rules, deliberately asymmetric:

  * 4xx keeps its detail. Those strings are written for the user ("Duplicate
    webhook signal.", "Invalid signal.") and the frontend renders them
    directly (api.ts reads body.detail). Blanket-sanitising them would make
    the product worse, not safer.
  * 5xx never leaks. The client gets a fixed message plus an errorId; the
    real exception text goes to the server log under that same id, so a
    support request stays traceable without exposing internals.

Before this, an unhandled exception fell through to Starlette's default and
rendered a bare "Internal Server Error" page, and 5xx HTTPExceptions shipped
their raw detail -- e.g. the OAuth failure that echoed the upstream Google
token endpoint URL back to the browser.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("nova_signal_router.errors")

# Deliberately says nothing about the cause. The errorId is the only handle,
# and it only resolves against the server log.
GENERIC_SERVER_ERROR = "Something went wrong on our side. Please try again."


def _new_error_id() -> str:
    """Unique per occurrence so it pins one log line, unlike the content
    hash normalized_errors uses to group repeats of the same failure."""
    return f"ERR-{uuid.uuid4().hex[:10].upper()}"


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    error_id = _new_error_id()
    logger.exception(
        "Unhandled error %s on %s %s", error_id, request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={"detail": GENERIC_SERVER_ERROR, "errorId": error_id},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    headers = getattr(exc, "headers", None)
    if exc.status_code >= 500:
        error_id = _new_error_id()
        # The raw detail is logged, never returned -- 5xx details in this
        # codebase embed upstream URLs and driver exception text.
        logger.error(
            "Server error %s on %s %s: %s",
            error_id,
            request.method,
            request.url.path,
            exc.detail,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": GENERIC_SERVER_ERROR, "errorId": error_id},
            headers=headers,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=headers,
    )
