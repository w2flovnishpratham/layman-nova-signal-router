"""Global NIFTY intraday candle service for the dashboard chart.

Candles are GLOBAL: one Dhan fetch (via the shared auto-TOTP market-data
identity) serves every connected user. Per-user data (trade markers) is
resolved separately in the markers endpoint from user-scoped order logs.

Never returns credentials; unavailable states are sanitized.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.dhan_client import RealDhanClient
from app.services.risk_manager import _market_is_open
from app.services.shared_market_data import (
    market_data_credentials,
    shared_market_data_configured,
    shared_market_data_status,
)
from app.services.state_store import utc_now

_IST = ZoneInfo("Asia/Kolkata")

NIFTY_INDEX_SECURITY_ID = "13"
NIFTY_INDEX_EXCHANGE_SEGMENT = "IDX_I"
NIFTY_INDEX_INSTRUMENT = "INDEX"
SUPPORTED_INTERVALS = {"5m": "5"}

# Refresh the global cache at most this often while the market is open.
CACHE_TTL_OPEN_SECONDS = 20.0
# When the market is closed the series is final for the day.
CACHE_TTL_CLOSED_SECONDS = 300.0
TOKEN_EXPIRING_SOON_SECONDS = 30 * 60

_CACHE_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()  # single-flight: one Dhan call even under concurrency
_CACHE: dict[str, Any] = {"data": None, "built_monotonic": 0.0}


def token_status() -> str:
    """valid / expiring_soon / expired / unavailable - no secrets."""
    if shared_market_data_configured():
        status = shared_market_data_status()
        if not status.get("has_token"):
            return "unavailable"
        if not status.get("token_valid"):
            return "expired"
        seconds = float(status.get("seconds_to_expiry") or 0)
        return "expiring_soon" if seconds < TOKEN_EXPIRING_SOON_SECONDS else "valid"
    # Fallback: per-user credentials path (no shared account configured).
    creds = market_data_credentials()
    return "valid" if creds else "unavailable"


def market_state() -> str:
    return "open" if _market_is_open() else "closed"


def get_nifty_candles(interval: str = "5m") -> dict[str, Any]:
    """Return the cached global candle series, refreshing when stale."""
    dhan_interval = SUPPORTED_INTERVALS.get(interval)
    if dhan_interval is None:
        return _unavailable("unsupported_interval", f"Interval {interval!r} is not supported.")

    ttl = CACHE_TTL_OPEN_SECONDS if _market_is_open() else CACHE_TTL_CLOSED_SECONDS
    with _CACHE_LOCK:
        cached = _CACHE.get("data")
        fresh = cached is not None and (time.monotonic() - float(_CACHE.get("built_monotonic") or 0.0)) <= ttl
    if fresh:
        return cached

    # Single-flight: first caller fetches, concurrent callers reuse the result.
    with _FETCH_LOCK:
        with _CACHE_LOCK:
            cached = _CACHE.get("data")
            fresh = cached is not None and (time.monotonic() - float(_CACHE.get("built_monotonic") or 0.0)) <= ttl
        if fresh:
            return cached
        data = _fetch_candles(dhan_interval, interval)
        # Keep serving the last good series if a refresh fails.
        if data.get("status") != "ready" and isinstance(cached, dict) and cached.get("status") == "ready":
            stale = dict(cached)
            stale["stale"] = True
            stale["refresh_error"] = data.get("reason")
            with _CACHE_LOCK:
                _CACHE["data"] = stale
                _CACHE["built_monotonic"] = time.monotonic()
            return stale
        with _CACHE_LOCK:
            _CACHE["data"] = data
            _CACHE["built_monotonic"] = time.monotonic()
        return data


def _fetch_candles(dhan_interval: str, interval_label: str) -> dict[str, Any]:
    creds = market_data_credentials()
    if not creds:
        reason = "market_data_token_expired" if shared_market_data_configured() else "market_data_unavailable"
        return _unavailable(reason, "NIFTY chart is temporarily unavailable. Nova is reconnecting market data.")

    now_ist = datetime.now(_IST)
    # Include the previous session for context; Dhan accepts a date-time range.
    from_date = (now_ist - timedelta(days=4)).strftime("%Y-%m-%d 09:15:00")
    to_date = now_ist.strftime("%Y-%m-%d %H:%M:%S")

    # Shared data identity reads directly (no per-user egress proxy).
    client = RealDhanClient(proxy_url="")
    result = client.get_intraday_candles(
        client_id=creds.client_id,
        access_token=creds.access_token,
        security_id=NIFTY_INDEX_SECURITY_ID,
        exchange_segment=NIFTY_INDEX_EXCHANGE_SEGMENT,
        instrument=NIFTY_INDEX_INSTRUMENT,
        interval=dhan_interval,
        from_date=from_date,
        to_date=to_date,
    )
    if not result.get("success"):
        error_text = str(result.get("error") or "")
        lowered = error_text.lower()
        if "token" in lowered or "auth" in lowered or result.get("status_code") in (401, 403):
            reason = "market_data_token_expired"
            # Lazy refresh: trigger the shared auto-TOTP flow so the next poll
            # succeeds without waiting for the 5-minute background worker.
            if shared_market_data_configured():
                try:
                    from app.services.shared_market_data import refresh_shared_token_after_auth_failure

                    refresh_shared_token_after_auth_failure(status_code=result.get("status_code"), message=error_text)
                except Exception:
                    pass
        else:
            reason = "market_data_fetch_failed"
        return _unavailable(reason, "NIFTY chart is temporarily unavailable. Nova is reconnecting market data.")

    candles = result.get("candles") or []
    # Trim to the most recent sessions (keep payload small; ~2 sessions of 5m).
    candles = candles[-150:]
    return {
        "symbol": "NIFTY",
        "interval": interval_label,
        "source": "dhan",
        "status": "ready",
        "market_state": market_state(),
        "token_status": token_status(),
        "updated_at": utc_now(),
        "candle_count": len(candles),
        "candles": candles,
    }


def _unavailable(reason: str, message: str) -> dict[str, Any]:
    return {
        "symbol": "NIFTY",
        "interval": "5m",
        "source": "dhan",
        "status": "unavailable",
        "market_state": market_state(),
        "token_status": token_status(),
        "updated_at": utc_now(),
        "candle_count": 0,
        "candles": [],
        "reason": reason,
        "message": message,
    }


def chart_status() -> dict[str, Any]:
    with _CACHE_LOCK:
        cached = _CACHE.get("data") if isinstance(_CACHE.get("data"), dict) else None
    return {
        "source": "dhan",
        "interval": "5m",
        "token_status": token_status(),
        "market_state": market_state(),
        "last_updated_at": cached.get("updated_at") if cached else None,
        "candle_count": cached.get("candle_count") if cached else 0,
        "status": cached.get("status") if cached else "not_loaded",
        "reason": cached.get("reason") if cached else None,
    }
