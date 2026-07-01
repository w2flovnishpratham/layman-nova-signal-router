from __future__ import annotations

from typing import Any


GENERIC_SETUP_ERROR = "Nova could not complete that step. Please check the setup values and try again."


def dhan_connection_failure_message(message: str, details: dict[str, Any] | None = None) -> str:
    details = details if isinstance(details, dict) else {}
    interpreted = details.get("interpreted_error")
    interpreted = interpreted if isinstance(interpreted, dict) else {}
    category = str(interpreted.get("category") or details.get("error_kind") or "").lower()
    text = " ".join(
        [
            str(message or ""),
            category,
            str(interpreted.get("message") or ""),
            str(interpreted.get("userMessage") or ""),
            str(interpreted.get("technicalMessage") or ""),
        ]
    ).lower()

    if "client identity mismatch" in text or "client id mismatch" in text:
        return (
            "Dhan client identity mismatch. The Client ID does not match this access token. "
            "Generate a token from the same Dhan account and try again."
        )
    if any(term in text for term in ("static ip", "whitelist", "white list", "unauthorized ip", "unauthorised ip")):
        return (
            "Dhan rejected this connection from the current IP. Confirm the assigned Nova Static IP "
            "is whitelisted in Dhan, then generate a fresh access token and try again."
        )
    if any(term in text for term in ("auth", "token", "unauthorized", "unauthorised", "expired")):
        return (
            "Dhan authentication failed. The access token is invalid or expired. "
            "Generate a fresh Dhan access token for this Client ID and try again."
        )
    return "Dhan connection failed. Check the Client ID and access token, then try again."


def safe_client_error_message(message: Any) -> str:
    text = str(message or "").strip()
    if not text:
        return GENERIC_SETUP_ERROR

    lowered = text.lower()
    if "dhan connection failed" in lowered or "dhan authentication" in lowered or "token invalid" in lowered:
        return dhan_connection_failure_message(text, {})

    if _looks_like_diagnostic_payload(text):
        return GENERIC_SETUP_ERROR

    if len(text) > 500:
        return text[:497].rstrip() + "..."
    return text


def safe_dhan_failure_detail(message: str, details: dict[str, Any] | None = None) -> dict[str, str]:
    details = details if isinstance(details, dict) else {}
    interpreted = details.get("interpreted_error")
    interpreted = interpreted if isinstance(interpreted, dict) else {}
    category = str(interpreted.get("category") or details.get("error_kind") or "DHAN_CONNECTION_FAILED").upper()
    return {
        "message": dhan_connection_failure_message(message, details),
        "error_code": _safe_error_code(category),
    }


def _safe_error_code(value: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in value.upper()).strip("_")
    return normalized or "DHAN_CONNECTION_FAILED"


def _looks_like_diagnostic_payload(text: str) -> bool:
    lowered = text.lower()
    if "{" in text and "}" in text:
        return True
    diagnostic_terms = (
        "interpreted_error",
        "debugpack",
        "rawstatuscode",
        "raw_status_code",
        "access_token",
        "refresh_token",
        "proxy_url",
        "proxy password",
        "client_secret",
        "webhook_secret",
        "authorization",
        "status_code",
        "error_kind",
    )
    return any(term in lowered for term in diagnostic_terms)
