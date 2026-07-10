"""Global shared Dhan market-data credentials via TOTP auto-refresh.

A single dedicated, data-only Dhan account powers live market data (LTP, option
chain, WebSocket feed) for every paper-mode user, so users never need to supply
their own Dhan token to use paper mode. The access token is regenerated
automatically from a stored TOTP secret (RFC 6238) before it expires, using
Dhan's ``POST /app/generateAccessToken`` endpoint.

Security:
  * The dedicated account should hold no funds and no trading use — if the
    server is compromised, no real money is reachable.
  * Secrets (client id, PIN, TOTP secret) come from the environment ONLY and are
    never logged or returned to the frontend. Status output is always masked.
  * Only market-data reads use these credentials. Live order placement still
    routes through each user's own credentials and egress.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import struct
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import settings
from app.services.credential_vault import DhanCredentials

logger = logging.getLogger("nova_signal_router.shared_market_data")

_GENERATE_TOKEN_URL = "https://auth.dhan.co/app/generateAccessToken"
# Refresh a little before the 24h expiry; also refresh on demand after a 401.
_REFRESH_MARGIN_SECONDS = 30 * 60  # 30 minutes
_HTTP_TIMEOUT_SECONDS = 12.0

_LOCK = threading.RLock()
_STATE: dict[str, object] = {
    "access_token": None,
    "client_id": None,
    "expiry_epoch": 0.0,
    "refreshed_at": None,
    "last_error": None,
}

_STOP_EVENT = threading.Event()
_WORKER: threading.Thread | None = None
_WORKER_LOCK = threading.RLock()


def _set_last_error(code: str) -> None:
    # Keep browser-facing status and logs free of PIN, TOTP, token, URL, and
    # provider-specific error details.
    _STATE["last_error"] = code


@contextmanager
def _suppress_httpx_info_logs():
    httpx_logger = logging.getLogger("httpx")
    previous_level = httpx_logger.level
    httpx_logger.setLevel(max(previous_level, logging.WARNING))
    try:
        yield
    finally:
        httpx_logger.setLevel(previous_level)


def shared_market_data_configured() -> bool:
    """True when the dedicated data account is fully configured via env."""
    return bool(
        settings.DHAN_SHARED_DATA_ENABLED
        and (settings.DHAN_SHARED_CLIENT_ID or "").strip()
        and (settings.DHAN_SHARED_PIN or "").strip()
        and (settings.DHAN_SHARED_TOTP_SECRET or "").strip()
    )


# ---------------------------------------------------------------------------
# TOTP (RFC 6238) — implemented inline to avoid an extra dependency.
# ---------------------------------------------------------------------------
def generate_totp(secret: str, *, at: float | None = None, digits: int = 6, period: int = 30) -> str:
    """Return the current TOTP code for a base32 secret (SHA1, 6 digits/30s)."""
    cleaned = (secret or "").strip().replace(" ", "").replace("-", "").upper()
    if not cleaned:
        raise ValueError("TOTP secret is empty.")
    # Base32 requires padding to a multiple of 8 characters.
    padding = "=" * (-len(cleaned) % 8)
    key = base64.b32decode(cleaned + padding, casefold=True)
    counter = int((time.time() if at is None else at) // period)
    message = struct.pack(">Q", counter)
    digest = hmac.new(key, message, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10 ** digits)).zfill(digits)


def _parse_expiry(expiry_time: str | None) -> float:
    """Parse Dhan's ISO expiry (IST, no tz) into an epoch; default to +24h."""
    if expiry_time:
        try:
            text = str(expiry_time).replace("Z", "")
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                # Dhan returns IST timestamps without a tz suffix.
                from zoneinfo import ZoneInfo

                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
            return parsed.astimezone(timezone.utc).timestamp()
        except Exception:
            pass
    return time.time() + 24 * 3600


def _find_response_value(payload: object, names: set[str]) -> object | None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key) in names and value not in (None, ""):
                return value
        for value in payload.values():
            found = _find_response_value(value, names)
            if found not in (None, ""):
                return found
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for item in payload:
            found = _find_response_value(item, names)
            if found not in (None, ""):
                return found
    return None


def _generate_token_locked() -> bool:
    """Call Dhan to mint a fresh access token. Caller holds _LOCK. Never raises."""
    import httpx

    client_id = (settings.DHAN_SHARED_CLIENT_ID or "").strip()
    pin = (settings.DHAN_SHARED_PIN or "").strip()
    secret = (settings.DHAN_SHARED_TOTP_SECRET or "").strip()
    try:
        totp = generate_totp(secret)
    except Exception as exc:
        _set_last_error("totp_generation_failed")
        logger.error("Shared market-data TOTP generation failed (%s).", type(exc).__name__)
        return False

    params = {"dhanClientId": client_id, "pin": pin, "totp": totp}
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            with _suppress_httpx_info_logs():
                response = client.post(_GENERATE_TOKEN_URL, params=params)
    except Exception as exc:
        _set_last_error("token_request_failed")
        logger.warning("Shared market-data token request failed (%s).", type(exc).__name__)
        return False

    if response.status_code != 200:
        _set_last_error(f"token_http_{response.status_code}")
        logger.warning("Shared market-data token HTTP %s", response.status_code)
        return False

    try:
        body = response.json()
    except Exception as exc:
        _set_last_error("token_bad_response")
        logger.warning("Shared market-data token response JSON parse failed (%s).", type(exc).__name__)
        return False

    token = str(
        _find_response_value(body, {"accessToken", "access_token", "token", "jwtToken"})
        or ""
    ).strip()
    if not token:
        _set_last_error("token_missing_in_response")
        keys = sorted(str(key) for key in body.keys()) if isinstance(body, dict) else [type(body).__name__]
        logger.warning("Shared market-data token response had no access token field (keys=%s).", keys)
        return False

    _STATE["access_token"] = token
    _STATE["client_id"] = str(_find_response_value(body, {"dhanClientId", "clientId", "client_id"}) or client_id)
    _STATE["expiry_epoch"] = _parse_expiry(_find_response_value(body, {"expiryTime", "expiresAt", "expires_at"}))
    _STATE["refreshed_at"] = datetime.now(timezone.utc).isoformat()
    _STATE["last_error"] = None
    logger.info("Shared market-data access token refreshed (expires in ~%.1fh).",
                max(0.0, (float(_STATE["expiry_epoch"]) - time.time()) / 3600.0))
    return True


def _token_is_fresh() -> bool:
    token = _STATE.get("access_token")
    expiry = float(_STATE.get("expiry_epoch") or 0.0)
    return bool(token) and (expiry - time.time()) > _REFRESH_MARGIN_SECONDS


def refresh_shared_token(force: bool = False) -> bool:
    """Refresh the shared token if missing/near-expiry (or forced). Thread-safe."""
    if not shared_market_data_configured():
        return False
    with _LOCK:
        if not force and _token_is_fresh():
            return True
        return _generate_token_locked()


def _looks_like_auth_failure(
    *,
    status_code: int | None = None,
    message: object | None = None,
    raw_response: object | None = None,
) -> bool:
    if status_code in {401, 403}:
        return True
    text = " ".join(
        str(item or "")
        for item in (message, raw_response)
        if item not in (None, "")
    ).lower()
    return any(
        term in text
        for term in (
            "401",
            "403",
            "unauthorized",
            "unauthorised",
            "invalid token",
            "token expired",
            "access token",
            "jwt",
            "authentication",
            "auth failed",
        )
    )


def refresh_shared_token_after_auth_failure(
    *,
    status_code: int | None = None,
    message: object | None = None,
    raw_response: object | None = None,
) -> bool:
    """Refresh after an auth failure without discarding a usable token on throttle."""
    if not shared_market_data_configured():
        return False
    if not _looks_like_auth_failure(
        status_code=status_code,
        message=message,
        raw_response=raw_response,
    ):
        return False
    logger.warning("Shared market-data token auth failure detected; refreshing token.")
    with _LOCK:
        old_token = _STATE.get("access_token")
        old_client_id = _STATE.get("client_id")
        old_expiry = _STATE.get("expiry_epoch")
        ok = _generate_token_locked()
        if ok:
            return True
        if old_token and old_client_id and old_expiry:
            _STATE["access_token"] = old_token
            _STATE["client_id"] = old_client_id
            _STATE["expiry_epoch"] = old_expiry
        return False


def get_shared_market_credentials() -> DhanCredentials | None:
    """Return valid shared data credentials, refreshing on demand. None if off."""
    if not shared_market_data_configured():
        return None
    with _LOCK:
        if not _token_is_fresh():
            _generate_token_locked()
        token = _STATE.get("access_token")
        client_id = _STATE.get("client_id")
        if token and client_id:
            return DhanCredentials(client_id=str(client_id), access_token=str(token), source="shared_market_data")
    return None


def invalidate_shared_token() -> None:
    """Drop the cached token so the next read forces a refresh (use after a 401)."""
    with _LOCK:
        _STATE["access_token"] = None
        _STATE["expiry_epoch"] = 0.0


def market_data_credentials() -> DhanCredentials | None:
    """Credentials for MARKET-DATA reads (LTP, feed, option chain).

    Prefers the shared dedicated account so data is global and users don't need
    their own Dhan token. Falls back to the request's per-user credentials only
    when the shared feed is not configured.
    """
    if shared_market_data_configured():
        return get_shared_market_credentials()
    from app.services.credential_vault import get_dhan_credentials

    return get_dhan_credentials()


def market_data_is_shared() -> bool:
    """True when market data is being served from the shared dedicated account."""
    return get_shared_market_credentials() is not None


def shared_market_data_status() -> dict[str, object]:
    """Masked health snapshot — safe to expose to admins / the frontend."""
    from app.services.credential_vault import mask_client_id

    with _LOCK:
        expiry = float(_STATE.get("expiry_epoch") or 0.0)
        has_token = bool(_STATE.get("access_token"))
        seconds_to_expiry = max(0.0, expiry - time.time()) if expiry else 0.0
        return {
            "configured": shared_market_data_configured(),
            "enabled": bool(settings.DHAN_SHARED_DATA_ENABLED),
            "has_token": has_token,
            "client_id_masked": mask_client_id(_STATE.get("client_id")),
            "token_valid": _token_is_fresh(),
            "seconds_to_expiry": round(seconds_to_expiry),
            "refreshed_at": _STATE.get("refreshed_at"),
            "last_error": _STATE.get("last_error"),
        }


# ---------------------------------------------------------------------------
# Background refresh worker
# ---------------------------------------------------------------------------
def _worker_loop() -> None:
    logger.info("Shared market-data token worker started.")
    # Prime immediately so the feed is ready before the first user request.
    try:
        refresh_shared_token(force=True)
    except Exception:
        logger.exception("Initial shared market-data token refresh failed.")
    while not _STOP_EVENT.is_set():
        # Wake every ~5 minutes; refresh_shared_token only acts near expiry.
        _STOP_EVENT.wait(300)
        if _STOP_EVENT.is_set():
            break
        try:
            refresh_shared_token(force=False)
        except Exception:
            logger.exception("Scheduled shared market-data token refresh failed.")
    logger.info("Shared market-data token worker stopped.")


def start_shared_token_worker() -> None:
    global _WORKER
    if not shared_market_data_configured():
        return
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _STOP_EVENT.clear()
        _WORKER = threading.Thread(target=_worker_loop, name="nova-shared-market-token", daemon=True)
        _WORKER.start()


def stop_shared_token_worker() -> None:
    global _WORKER
    with _WORKER_LOCK:
        _STOP_EVENT.set()
        if _WORKER is not None and _WORKER.is_alive():
            _WORKER.join(timeout=3.0)
        _WORKER = None
