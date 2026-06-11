from __future__ import annotations

from datetime import timezone

from fastapi import HTTPException, Request

from app.auth.db import session_scope
from app.auth.models import AuthSession, User, utc_now_dt
from app.auth.service import find_user_by_id
from app.config import settings
from app.store.session_token import SessionTokenError, verify_auth_token


def auth_enabled() -> bool:
    if not settings.AUTH_REQUIRED and settings.APP_ENV.lower() == "production":
        raise RuntimeError("AUTH_REQUIRED cannot be false in production.")
    return bool(settings.AUTH_REQUIRED)


def cookie_secure() -> bool:
    return settings.BACKEND_PUBLIC_BASE_URL.startswith("https://")


def current_user_from_request(request: Request) -> User | None:
    token = request.cookies.get(settings.AUTH_COOKIE_NAME)
    if not token:
        return None
    try:
        payload = verify_auth_token(token)
    except SessionTokenError:
        return None
    user_id = str(payload.get("uid") or "")
    auth_session_id = str(payload.get("asid") or "")
    if not user_id:
        return None
    with session_scope() as session:
        if auth_session_id:
            auth_session = session.get(AuthSession, auth_session_id)
            if auth_session is None or auth_session.user_id != user_id or auth_session.revoked_at is not None or _is_expired(auth_session):
                return None
            auth_session.last_used_at = utc_now_dt()
            session.add(auth_session)
            session.commit()
        user = find_user_by_id(session, user_id)
        if user is None or user.status != "active":
            return None
        return user


def require_user_if_auth_enabled(request: Request) -> User | None:
    if not auth_enabled():
        return None
    user = current_user_from_request(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    return user


def _is_expired(auth_session: AuthSession) -> bool:
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < utc_now_dt()
