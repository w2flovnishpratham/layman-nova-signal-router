from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.services.credential_vault import mask_client_id


RAW_RESPONSE_KEYS = {
    "raw_response",
    "rawresponse",
    "response_text",
    "responsetext",
    "response_json",
    "responsejson",
    "proxy_url",
    "proxyurl",
    "encoded_credentials",
    "encoded_proxy_credentials",
    "credential_payload",
    "raw_credential_payload",
}
CLIENT_ID_KEYS = {"client_id", "client-id", "dhan_client_id", "dhanclientid"}
SENSITIVE_KEYS = {
    "access_token",
    "access-token",
    "refresh_token",
    "authorization",
    "client_secret",
    "secret",
    "token",
    "proxy_url",
    "proxy_password",
    "password",
}
_CREDENTIAL_URL_RE = re.compile(r"://([^:/@\s]+):([^@\s]+)@")
_CLIENT_ID_JSON_RE = re.compile(r'(?i)("?(?:dhanClientId|dhan_client_id|client_id|client-id)"?\s*:\s*"?)([^",}\s]+)')
_CLIENT_ID_PHRASE_RE = re.compile(r"(?i)((?:dhan\s+client\s+id|client\s+id)\s+)([A-Za-z0-9_-]*\d[A-Za-z0-9_-]{3,})")
_SENSITIVE_JSON_RE = re.compile(
    r'(?i)("?(?:access_token|access-token|refresh_token|authorization|token|secret|client_secret|proxy_password|password)"?\s*:\s*"?)([^",}\s]+)'
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)((?:access_token|access-token|refresh_token|authorization|token|secret|client_secret|proxy_password|password)\s*[=:]\s*[\"']?)([^,\s}\]\"']+)"
)


def _normalized_key(key: Any) -> str:
    return str(key).strip().replace("-", "_").lower()


def _is_client_id_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    if "masked" in normalized:
        return False
    return normalized in CLIENT_ID_KEYS or normalized.endswith("_client_id")


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    if normalized.endswith("_present") or normalized.endswith("_configured") or "masked" in normalized:
        return False
    return (
        normalized in SENSITIVE_KEYS
        or normalized.endswith("_token")
        or "password" in normalized
        or "client_secret" in normalized
        or "proxy_password" in normalized
    )


def _sanitize_string(value: str) -> str:
    text = _CREDENTIAL_URL_RE.sub("://***REDACTED***:***REDACTED***@", value)
    text = _CLIENT_ID_JSON_RE.sub(lambda match: f"{match.group(1)}{mask_client_id(match.group(2))}", text)
    text = _CLIENT_ID_PHRASE_RE.sub(lambda match: f"{match.group(1)}{mask_client_id(match.group(2))}", text)
    text = _SENSITIVE_JSON_RE.sub(lambda match: f"{match.group(1)}***REDACTED***", text)
    return _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}***REDACTED***", text)


def sanitize_dhan_response_surface(value: Any) -> Any:
    """Return a copy safe for API/debug surfaces that may have broker data."""
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            normalized = _normalized_key(key)
            if normalized in RAW_RESPONSE_KEYS:
                continue
            if _is_client_id_key(key):
                sanitized[key] = mask_client_id(item)
            elif _is_sensitive_key(key):
                sanitized[key] = "***REDACTED***" if item not in (None, "") else None
            else:
                sanitized[key] = sanitize_dhan_response_surface(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_dhan_response_surface(item) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value)
    return deepcopy(value)


def sanitize_wallet_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    sanitized = sanitize_dhan_response_surface(snapshot or {})
    if isinstance(sanitized, dict) and "client_id" in sanitized:
        sanitized["client_id"] = mask_client_id(sanitized.get("client_id"))
    return sanitized if isinstance(sanitized, dict) else {}
