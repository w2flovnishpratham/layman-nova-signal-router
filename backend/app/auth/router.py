from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth.csrf import (
    csrf_token_from_request,
    delete_csrf_cookie,
    new_csrf_token,
    set_csrf_cookie,
)
from app.auth.db import session_scope
from app.auth.models import AuthSession, utc_now_dt
from app.auth.security import cookie_secure, current_user_from_request
from app.auth.service import admin_emails, create_auth_session, email_allowed, upsert_google_user
from app.config import settings
from app.store.session_token import SessionTokenError, issue_auth_token, verify_auth_token


router = APIRouter(prefix="/api/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


@router.get("/status")
def auth_status(request: Request) -> JSONResponse:
    user = current_user_from_request(request)
    csrf_token = csrf_token_from_request(request) if user else None
    if user and not csrf_token:
        csrf_token = new_csrf_token()
    response = JSONResponse(
        {
            "authRequired": settings.AUTH_REQUIRED,
            "googleConfigured": google_configured(),
            "authenticated": user is not None,
            "isAdmin": bool(user and user.email.strip().lower() in admin_emails()),
            "loginUrl": "/api/auth/google",
            "user": public_user(user) if user else None,
            "csrfToken": csrf_token,
        }
    )
    if csrf_token:
        set_csrf_cookie(response, csrf_token)
    elif not user:
        delete_csrf_cookie(response)
    return response


@router.get("/google")
@router.get("/google/login", include_in_schema=False)
def google_login() -> RedirectResponse:
    if not google_configured():
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")

    state = secrets.token_urlsafe(32)
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "include_granted_scopes": "true",
        "prompt": "select_account",
    }
    response = RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}", status_code=302)
    response.set_cookie(
        settings.OAUTH_STATE_COOKIE_NAME,
        state,
        max_age=settings.OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )
    return response


@router.get("/google/callback")
async def google_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None) -> RedirectResponse:
    if error:
        return _frontend_redirect("oauth_error", error)
    if not code or not state:
        return _frontend_redirect("oauth_error", "missing_code")

    expected_state = request.cookies.get(settings.OAUTH_STATE_COOKIE_NAME)
    if not expected_state or not secrets.compare_digest(expected_state, state):
        return _frontend_redirect("oauth_error", "invalid_state")

    token = await _exchange_code(code)
    profile = await _fetch_userinfo(str(token.get("access_token") or ""))
    email = str(profile.get("email") or "").strip().lower()
    google_sub = str(profile.get("sub") or "").strip()
    if not email or not google_sub:
        return _frontend_redirect("oauth_error", "missing_google_profile")
    if profile.get("email_verified") is False:
        return _frontend_redirect("oauth_error", "email_not_verified")
    if not email_allowed(email):
        return _frontend_redirect("oauth_error", "not_allowed")

    with session_scope() as session:
        try:
            user = upsert_google_user(
                session,
                email=email,
                google_sub=google_sub,
                name=_optional_str(profile.get("name")),
                avatar_url=_optional_str(profile.get("picture")),
            )
        except ValueError:
            return _frontend_redirect("oauth_error", "not_allowed")
        auth_session = create_auth_session(session, user)

    response = _frontend_redirect("login", "ok")
    response.delete_cookie(settings.OAUTH_STATE_COOKIE_NAME, path="/")
    response.set_cookie(
        settings.AUTH_COOKIE_NAME,
        issue_auth_token(user.id, auth_session_id=auth_session.id),
        max_age=settings.AUTH_COOKIE_TTL_SECONDS,
        httponly=True,
        secure=cookie_secure(),
        samesite="strict",
        path="/",
    )
    set_csrf_cookie(response, new_csrf_token())
    return response


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    token = request.cookies.get(settings.AUTH_COOKIE_NAME)
    if token:
        try:
            payload = verify_auth_token(token)
        except SessionTokenError:
            payload = {}
        auth_session_id = str(payload.get("asid") or "")
        user_id = str(payload.get("uid") or "")
        if auth_session_id:
            with session_scope() as session:
                auth_session = session.get(AuthSession, auth_session_id)
                if auth_session and auth_session.user_id == user_id and auth_session.revoked_at is None:
                    auth_session.revoked_at = utc_now_dt()
                    session.commit()
    response = JSONResponse({"success": True})
    response.delete_cookie(settings.AUTH_COOKIE_NAME, path="/")
    delete_csrf_cookie(response)
    return response


def google_configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def google_redirect_uri() -> str:
    configured = settings.GOOGLE_REDIRECT_URI.strip()
    if configured:
        return configured
    return f"{settings.BACKEND_PUBLIC_BASE_URL.rstrip('/')}/api/auth/google/callback"


async def _exchange_code(code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": google_redirect_uri(),
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Google token exchange failed.")
    return response.json()


async def _fetch_userinfo(access_token: str) -> dict[str, Any]:
    if not access_token:
        raise HTTPException(status_code=502, detail="Google did not return an access token.")
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Google userinfo lookup failed.")
    return response.json()


def public_user(user: Any) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "avatarUrl": user.avatar_url,
        "planTier": user.plan_tier,
    }


def _frontend_redirect(key: str, value: str) -> RedirectResponse:
    return RedirectResponse(f"{_frontend_url()}?{urlencode({key: value})}", status_code=302)


def _frontend_url() -> str:
    return settings.FRONTEND_ORIGIN.rstrip("/") or "/"


def _optional_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
