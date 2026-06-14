from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from fastapi import Request, Response

from app.auth.security import cookie_secure
from app.config import settings


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
WEBHOOK_PREFIXES = ("/webhook/", "/api/webhook/")


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_token_from_request(request: Request) -> str | None:
    token = str(request.cookies.get(settings.CSRF_COOKIE_NAME) or "").strip()
    return token or None


def set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.CSRF_COOKIE_NAME,
        token,
        max_age=settings.AUTH_COOKIE_TTL_SECONDS,
        httponly=False,
        secure=cookie_secure(),
        samesite="strict",
        path="/",
    )


def delete_csrf_cookie(response: Response) -> None:
    response.delete_cookie(settings.CSRF_COOKIE_NAME, path="/")


def csrf_exempt_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in WEBHOOK_PREFIXES)


def csrf_error(request: Request) -> str | None:
    if request.method.upper() in SAFE_METHODS or csrf_exempt_path(request.url.path):
        return None

    origin_error = _origin_error(request)
    if origin_error:
        return origin_error

    cookie_token = csrf_token_from_request(request)
    header_token = str(request.headers.get(settings.CSRF_HEADER_NAME) or "").strip()
    if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
        return "CSRF validation failed."
    return None


def _origin_error(request: Request) -> str | None:
    supplied_origin = str(request.headers.get("origin") or "").strip()
    if not supplied_origin:
        referer = str(request.headers.get("referer") or "").strip()
        if referer:
            parsed = urlsplit(referer)
            supplied_origin = f"{parsed.scheme}://{parsed.netloc}"
    if not supplied_origin:
        return None

    allowed = {
        _normalized_origin(settings.FRONTEND_ORIGIN),
        _normalized_origin(settings.BACKEND_PUBLIC_BASE_URL),
    }
    if _normalized_origin(supplied_origin) not in allowed:
        return "Request origin is not allowed."
    return None


def _normalized_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
