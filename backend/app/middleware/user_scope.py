from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.security import auth_enabled, current_user_from_request
from app.services.user_context import reset_current_user_id, set_current_user_id


class UserRuntimeScopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        user_id = None
        if auth_enabled():
            user = current_user_from_request(request)
            user_id = user.id if user else None

        token = set_current_user_id(user_id)
        try:
            return await call_next(request)
        finally:
            reset_current_user_id(token)
