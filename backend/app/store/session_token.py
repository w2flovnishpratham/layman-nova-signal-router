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


def issue_session_token(session_id: str) -> str:
    payload = {
        "sid": session_id,
        "exp": int(time.time()) + settings.SESSION_TOKEN_TTL_SECONDS,
    }
    encoded_payload = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign(encoded_payload)
    return f"{encoded_payload}.{signature}"


def verify_session_token(token: str, session_id: str) -> dict[str, Any]:
    try:
        encoded_payload, signature = token.split(".", 1)
    except ValueError as exc:
        raise SessionTokenError("Malformed session token") from exc

    expected = _sign(encoded_payload)
    if not hmac.compare_digest(signature, expected):
        raise SessionTokenError("Invalid session token")

    payload = json.loads(_b64decode(encoded_payload))
    if payload.get("sid") != session_id:
        raise SessionTokenError("Session token does not match this session")
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
