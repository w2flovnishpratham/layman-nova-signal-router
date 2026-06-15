"""Signed HTTP-only cookie session management.

The cookie carries only an opaque, signed session id. The actual session record
(user_id, expiry, revocation) lives in the `user_sessions` table in Neon. No user
data or secrets are ever placed in the cookie.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

_SESSION_SALT = "nova.session.v1"
_STATE_SALT = "nova.oauth-state.v1"


def _serializer(salt: str) -> URLSafeTimedSerializer:
    secret = settings.app_secret
    if not secret:
        # In production main.validate_production_configuration() fails loudly
        # before we ever get here; this guard protects dev misconfiguration.
        raise RuntimeError("APP_SECRET_KEY / SESSION_TOKEN_SECRET is not configured.")
    return URLSafeTimedSerializer(secret, salt=salt)


# ---------------------------------------------------------------------------
# Session cookie
# ---------------------------------------------------------------------------
def sign_session_id(session_id: uuid.UUID | str) -> str:
    return _serializer(_SESSION_SALT).dumps(str(session_id))


def read_session_id(cookie_value: str | None) -> Optional[uuid.UUID]:
    if not cookie_value:
        return None
    try:
        raw = _serializer(_SESSION_SALT).loads(cookie_value, max_age=settings.SESSION_TOKEN_TTL_SECONDS)
        return uuid.UUID(str(raw))
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None


def set_session_cookie(response: Response, session_id: uuid.UUID | str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=sign_session_id(session_id),
        max_age=settings.SESSION_TOKEN_TTL_SECONDS,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        domain=settings.SESSION_COOKIE_DOMAIN or None,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        domain=settings.SESSION_COOKIE_DOMAIN or None,
        path="/",
    )


# ---------------------------------------------------------------------------
# OAuth state (CSRF protection for the redirect round-trip)
# ---------------------------------------------------------------------------
def sign_oauth_state(payload: dict) -> str:
    return _serializer(_STATE_SALT).dumps(payload)


def read_oauth_state(value: str | None, max_age: int = 600) -> Optional[dict]:
    if not value:
        return None
    try:
        data = _serializer(_STATE_SALT).loads(value, max_age=max_age)
        return data if isinstance(data, dict) else None
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None
