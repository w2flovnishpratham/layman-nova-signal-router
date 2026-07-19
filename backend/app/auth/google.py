"""Google OAuth 2.0 login routes.

Flow:
  GET  /api/auth/google/start     -> redirect to Google consent
  GET  /api/auth/google/callback  -> exchange code, upsert user, set cookie
  POST /api/auth/logout           -> revoke session + clear cookie
  GET  /api/me                    -> current logged-in user

Any verified Google account may log in. ADMIN_EMAILS only sets the is_admin flag.
"""
from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth.dependencies import get_optional_user
from app.auth.session import (
    clear_session_cookie,
    read_oauth_state,
    read_session_id,
    set_session_cookie,
    sign_oauth_state,
)
from app.config import settings
from app.db import crud
from app.db.engine import database_configured, session_scope
from app.services.user_context import is_admin_user

router = APIRouter(prefix="/api", tags=["Auth"])

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

_OAUTH_STATE_COOKIE = "nova_oauth_state"


def _require_oauth_configured() -> None:
    missing = [
        name
        for name, value in (
            ("GOOGLE_CLIENT_ID", settings.GOOGLE_CLIENT_ID),
            ("GOOGLE_CLIENT_SECRET", settings.GOOGLE_CLIENT_SECRET),
            ("GOOGLE_REDIRECT_URI", settings.GOOGLE_REDIRECT_URI),
        )
        if not (value or "").strip()
    ]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"Google OAuth is not configured (missing: {', '.join(missing)}).",
        )
    if not database_configured():
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured; cannot persist users.")


def _frontend_url() -> str:
    return (settings.FRONTEND_URL or settings.FRONTEND_ORIGIN or "/").rstrip("/") or "/"


@router.get("/auth/google/start")
def google_start(request: Request) -> RedirectResponse:
    _require_oauth_configured()
    nonce = secrets.token_urlsafe(16)
    state_token = sign_oauth_state({"nonce": nonce})
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state_token,
        "access_type": "online",
        "include_granted_scopes": "true",
        "prompt": "select_account",
    }
    response = RedirectResponse(f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}", status_code=302)
    # Short-lived signed state cookie ties the callback to this browser.
    response.set_cookie(
        key=_OAUTH_STATE_COOKIE,
        value=state_token,
        max_age=600,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        path="/",
    )
    return response


@router.get("/auth/google/callback")
def google_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    _require_oauth_configured()
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing authorization code or state.")

    cookie_state = request.cookies.get(_OAUTH_STATE_COOKIE)
    if not cookie_state or not secrets.compare_digest(cookie_state, state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state (possible CSRF).")
    if read_oauth_state(state) is None:
        raise HTTPException(status_code=400, detail="OAuth state expired or tampered.")

    # Exchange the authorization code for tokens.
    try:
        with httpx.Client(timeout=15.0) as client:
            token_resp = client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )
            token_resp.raise_for_status()
            tokens = token_resp.json()
            access_token = tokens.get("access_token")
            if not access_token:
                raise HTTPException(status_code=400, detail="Google did not return an access token.")
            userinfo_resp = client.get(
                GOOGLE_USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_resp.raise_for_status()
            profile = userinfo_resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Google token exchange failed: {exc}") from exc

    google_sub = profile.get("sub")
    email = (profile.get("email") or "").strip().lower()
    email_verified = profile.get("email_verified")
    if isinstance(email_verified, str):
        email_verified = email_verified.lower() == "true"
    if not google_sub or not email:
        raise HTTPException(status_code=400, detail="Google profile missing sub/email.")
    if not email_verified:
        raise HTTPException(status_code=403, detail="Google email is not verified.")

    is_admin = is_admin_user(email)  # derived AFTER login; does not gate login
    with session_scope() as db:
        user = crud.upsert_google_user(
            db,
            google_sub=google_sub,
            email=email,
            name=profile.get("name"),
            picture_url=profile.get("picture"),
            is_admin=is_admin,
        )
        sess = crud.create_session(
            db,
            user_id=user.id,
            ttl_seconds=settings.SESSION_TOKEN_TTL_SECONDS,
            metadata={"ip": request.client.host if request.client else None},
        )
        crud.add_audit_log(db, user_id=user.id, action="LOGIN_SUCCESS", metadata={"email": email})
        session_id = sess.id

    response = RedirectResponse(_frontend_url(), status_code=302)
    set_session_cookie(response, session_id)
    response.delete_cookie(_OAUTH_STATE_COOKIE, path="/")
    return response


class LogoutRequest(BaseModel):
    engine_action: str = "keep_running"


@router.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    body: LogoutRequest | None = None,
) -> JSONResponse:
    action = (body or LogoutRequest()).engine_action.strip().lower()
    if action not in {"keep_running", "stop_engine"}:
        raise HTTPException(status_code=400, detail="engine_action must be keep_running or stop_engine.")
    user = get_optional_user(request)
    runtime = None
    if user is not None:
        from app.services.execution_context import bind_user_execution_context
        from app.services import runtime_reliability

        with bind_user_execution_context(user):
            runtime = (
                runtime_reliability.stop_engine(user, reason="logout")
                if action == "stop_engine"
                else runtime_reliability.checkpoint(user, reason="logout_keep_running")
            )
    cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    session_id = read_session_id(cookie)
    if session_id is not None and database_configured():
        try:
            with session_scope() as db:
                row = crud.get_active_session(db, session_id)
                if row is not None:
                    crud.add_audit_log(
                        db,
                        user_id=row.user_id,
                        action="LOGOUT",
                        metadata={"engine_action": action},
                    )
                crud.revoke_session(db, session_id)
        except Exception:
            pass
    out = JSONResponse({"ok": True, "engine_action": action, "runtime": runtime})
    clear_session_cookie(out)
    return out


@router.get("/me")
def me(request: Request):
    user = get_optional_user(request)
    if user is None:
        return JSONResponse({"authenticated": False, "user": None}, status_code=401)
    return {
        "authenticated": True,
        "user": {
            "id": user.id_str,
            "email": user.email,
            "name": user.name,
            "picture_url": user.picture_url,
            "is_admin": user.is_admin,
            "is_dev": user.is_dev,
        },
        "auth_required": settings.AUTH_REQUIRED,
    }
