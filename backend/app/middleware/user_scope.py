from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.csrf import csrf_error, csrf_exempt_path
from app.auth.security import auth_enabled, current_user_from_request
from app.services.user_context import reset_current_user_id, set_current_user_id


PUBLIC_PATHS = {
    "/health",
    "/api/health",
    "/api/readiness",
    "/api/auth/status",
    "/api/auth/google",
    "/api/auth/google/login",
    "/api/auth/google/callback",
    "/api/docs",
    "/api/redoc",
    "/openapi.json",
}


def public_request_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith("/relay/") or csrf_exempt_path(path)


class UserRuntimeScopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        user_id = None
        request.state.auth_user = None
        if auth_enabled():
            is_webhook = csrf_exempt_path(request.url.path)
            user = None if is_webhook else current_user_from_request(request)
            request.state.auth_user = user
            user_id = user.id if user else None
            if request.method.upper() != "OPTIONS" and not public_request_path(request.url.path) and user is None:
                return JSONResponse(status_code=401, content={"detail": "Login required."})
            if user is not None:
                error = csrf_error(request)
                if error:
                    return JSONResponse(status_code=403, content={"detail": error})

        token = set_current_user_id(user_id)
        try:
            return await call_next(request)
        finally:
            reset_current_user_id(token)
