"""FastAPI auth dependencies.

get_current_user resolves the logged-in user from the signed session cookie.
When AUTH_REQUIRED=false (local dev) and there is no valid session, it falls back
to the deterministic dev user so the existing paper flow keeps working.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, WebSocket, status

from app.auth.session import read_session_id
from app.config import settings
from app.db import crud
from app.db.engine import database_configured, session_scope
from app.services.user_context import (
    CurrentUser,
    current_user_from_model,
    dev_user,
)
from app.services.execution_context import bind_user_execution_context


def resolve_user_from_cookie(cookie: str | None) -> CurrentUser | None:
    """Return the CurrentUser for a valid signed session cookie, else None."""
    session_id = read_session_id(cookie)
    if session_id is None:
        return None
    if not database_configured():
        return None
    try:
        with session_scope() as db:
            row = crud.get_active_session(db, session_id)
            if row is None:
                return None
            user = crud.get_user_by_id(db, row.user_id)
            if user is None:
                return None
            return current_user_from_model(user)
    except Exception:
        return None


def _resolve_user(request: Request) -> CurrentUser | None:
    return resolve_user_from_cookie(request.cookies.get(settings.SESSION_COOKIE_NAME))


def _authenticated_or_dev(user: CurrentUser | None) -> CurrentUser | None:
    if user is not None:
        return user
    if not settings.AUTH_REQUIRED and not settings.is_production:
        return dev_user()
    return None


def get_current_user(request: Request) -> CurrentUser:
    user = _authenticated_or_dev(_resolve_user(request))
    if user is not None:
        return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


async def get_execution_scoped_user(request: Request):
    """Keep legacy authenticated endpoints inside the user's isolated runtime."""
    user = get_current_user(request)
    with bind_user_execution_context(user):
        yield user


def get_optional_user(request: Request) -> CurrentUser | None:
    return _authenticated_or_dev(_resolve_user(request))


def get_current_websocket_user(websocket: WebSocket) -> CurrentUser:
    user = _authenticated_or_dev(
        resolve_user_from_cookie(websocket.cookies.get(settings.SESSION_COOKIE_NAME))
    )
    if user is not None:
        return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
