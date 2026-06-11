from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.config import settings


class SessionTokenError(ValueError):
    pass


def issue_session_token(session_id: str, *, user_id: str | None = None) -> str:
    payload = {
        "typ": "session",
        "sid": session_id,
        "exp": int(time.time()) + settings.SESSION_TOKEN_TTL_SECONDS,
    }
    if user_id:
        payload["uid"] = user_id
    return _encode_signed_payload(payload)


def issue_auth_token(user_id: str, *, auth_session_id: str) -> str:
    payload = {
        "typ": "auth",
        "uid": user_id,
        "asid": auth_session_id,
        "exp": int(time.time()) + settings.AUTH_COOKIE_TTL_SECONDS,
    }
    return _encode_signed_payload(payload)


def verify_auth_token(token: str) -> dict[str, Any]:
    payload = _decode_signed_payload(token)
    if payload.get("typ") != "auth":
        raise SessionTokenError("Invalid auth token")
    if not payload.get("uid"):
        raise SessionTokenError("Auth token is missing a user")
    return payload


def _encode_signed_payload(payload: dict[str, Any]) -> str:
    encoded_payload = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign(encoded_payload)
    return f"{encoded_payload}.{signature}"


def verify_session_token(token: str, session_id: str) -> dict[str, Any]:
    payload = _decode_signed_payload(token)
    if payload.get("typ") not in {None, "session"}:
        raise SessionTokenError("Invalid session token")
    if payload.get("sid") != session_id:
        raise SessionTokenError("Session token does not match this session")
    return payload


def _decode_signed_payload(token: str) -> dict[str, Any]:
    try:
        encoded_payload, signature = token.split(".", 1)
    except ValueError as exc:
        raise SessionTokenError("Malformed session token") from exc

    expected = _sign(encoded_payload)
    if not hmac.compare_digest(signature, expected):
        raise SessionTokenError("Invalid session token")

    payload = json.loads(_b64decode(encoded_payload))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise SessionTokenError("Session token expired")
    return payload


def _sign(encoded_payload: str) -> str:
    digest = hmac.new(
        settings.SESSION_TOKEN_SECRET.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _b64encode(digest)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(raw: str) -> bytes:
    padded = raw + "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))
