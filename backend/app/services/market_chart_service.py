"""Global NIFTY intraday candle service for the dashboard chart.

Candles are GLOBAL: one Dhan fetch (via the shared auto-TOTP market-data
identity) serves every connected user. Per-user data (trade markers) is
resolved separately in the markers endpoint from user-scoped order logs.

CRITICAL CHART RULE: only the CURRENT IST trading day is ever returned.
Candles are filtered backend-side to today's 09:15-15:30 IST session window,
sorted ascending, deduplicated by timestamp. The cache key includes the IST
trading date so a date rollover can never serve yesterday's series.

Never returns credentials; unavailable states are sanitized.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
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
IST_TIMEZONE_NAME = "Asia/Kolkata"

NIFTY_INDEX_SECURITY_ID = "13"
NIFTY_INDEX_EXCHANGE_SEGMENT = "IDX_I"
NIFTY_INDEX_INSTRUMENT = "INDEX"
SUPPORTED_INTERVALS = {"5m": "5"}

SESSION_OPEN_HOUR, SESSION_OPEN_MINUTE = 9, 15
SESSION_CLOSE_HOUR, SESSION_CLOSE_MINUTE = 15, 30

# Refresh the global cache at most this often while the market is open.
CACHE_TTL_OPEN_SECONDS = 20.0
# When the market is closed the series is final for the day.
CACHE_TTL_CLOSED_SECONDS = 300.0
TOKEN_EXPIRING_SOON_SECONDS = 30 * 60

_CACHE_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()  # single-flight: one Dhan call even under concurrency
# Keyed by cache_key (nifty:5m:YYYY-MM-DD:Asia/Kolkata); only today's key is kept.
_CACHE: dict[str, Any] = {"key": None, "data": None, "built_monotonic": 0.0}


def trading_date_ist() -> date:
    return datetime.now(_IST).date()


def session_bounds_ist(trading_date: date) -> tuple[datetime, datetime]:
    start = datetime(trading_date.year, trading_date.month, trading_date.day, SESSION_OPEN_HOUR, SESSION_OPEN_MINUTE, tzinfo=_IST)
    end = datetime(trading_date.year, trading_date.month, trading_date.day, SESSION_CLOSE_HOUR, SESSION_CLOSE_MINUTE, tzinfo=_IST)
    return start, end


def cache_key(interval: str, trading_date: date) -> str:
    return f"nifty:{interval}:{trading_date.isoformat()}:{IST_TIMEZONE_NAME}"


def filter_today_session(candles: list[dict[str, Any]], trading_date: date) -> list[dict[str, Any]]:
    """Keep only candles inside today's IST session window; sort asc; dedupe."""
    start, end = session_bounds_ist(trading_date)
    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())
    seen: set[int] = set()
    filtered: list[dict[str, Any]] = []
    for candle in sorted(candles, key=lambda c: int(c.get("time") or 0)):
        t = int(candle.get("time") or 0)
        if t < start_epoch or t > end_epoch or t in seen:
            continue
        seen.add(t)
        filtered.append(candle)
    return filtered


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
    creds = market_data_credentials()
    return "valid" if creds else "unavailable"


def market_state() -> str:
    return "open" if _market_is_open() else "closed"


def get_nifty_candles(interval: str = "5m") -> dict[str, Any]:
    """Return today's (IST) cached global candle series, refreshing when stale."""
    dhan_interval = SUPPORTED_INTERVALS.get(interval)
    if dhan_interval is None:
        return _unavailable("unsupported_interval", f"Interval {interval!r} is not supported.")

    today = trading_date_ist()
    key = cache_key(interval, today)
    ttl = CACHE_TTL_OPEN_SECONDS if _market_is_open() else CACHE_TTL_CLOSED_SECONDS

    with _CACHE_LOCK:
        fresh = (
            _CACHE.get("key") == key
            and _CACHE.get("data") is not None
            and (time.monotonic() - float(_CACHE.get("built_monotonic") or 0.0)) <= ttl
        )
        cached = _CACHE.get("data") if _CACHE.get("key") == key else None
    if fresh:
        return cached

    with _FETCH_LOCK:
        with _CACHE_LOCK:
            fresh = (
                _CACHE.get("key") == key
                and _CACHE.get("data") is not None
                and (time.monotonic() - float(_CACHE.get("built_monotonic") or 0.0)) <= ttl
            )
            cached = _CACHE.get("data") if _CACHE.get("key") == key else None
        if fresh:
            return cached
        data = _fetch_candles(dhan_interval, interval, today)
        # Keep serving today's last good series if a refresh fails.
        if data.get("status") != "ready" and isinstance(cached, dict) and cached.get("status") == "ready":
            stale = dict(cached)
            stale["stale"] = True
            stale["refresh_error"] = data.get("reason")
            data = stale
        with _CACHE_LOCK:
            _CACHE["key"] = key
            _CACHE["data"] = data
            _CACHE["built_monotonic"] = time.monotonic()
        return data


def _fetch_candles(dhan_interval: str, interval_label: str, today: date) -> dict[str, Any]:
    session_start, session_end = session_bounds_ist(today)
    now_ist = datetime.now(_IST)

    if now_ist < session_start:
        # Before today's open: today has no candles yet. Never fall back to
        # yesterday's series.
        return _ready_payload(interval_label, today, [], note="preopen")

    creds = market_data_credentials()
    if not creds:
        reason = "market_data_token_expired" if shared_market_data_configured() else "market_data_unavailable"
        return _unavailable(reason, "NIFTY chart is temporarily unavailable. Nova is reconnecting market data.")

    from_date = session_start.strftime("%Y-%m-%d %H:%M:%S")
    to_date = min(now_ist, session_end).strftime("%Y-%m-%d %H:%M:%S")

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

    # Defense-in-depth: even if Dhan returns mixed dates, only today's IST
    # session survives (sorted ascending, deduplicated).
    candles = filter_today_session(result.get("candles") or [], today)
    return _ready_payload(interval_label, today, candles)


def _base_payload(interval_label: str, today: date) -> dict[str, Any]:
    session_start, session_end = session_bounds_ist(today)
    return {
        "symbol": "NIFTY",
        "interval": interval_label,
        "source": "dhan",
        "timezone": IST_TIMEZONE_NAME,
        "trading_date": today.isoformat(),
        "session_start": session_start.isoformat(),
        "session_end": session_end.isoformat(),
        "market_state": market_state(),
        "token_status": token_status(),
        "updated_at": utc_now(),
    }


def _ready_payload(interval_label: str, today: date, candles: list[dict[str, Any]], *, note: str | None = None) -> dict[str, Any]:
    payload = _base_payload(interval_label, today)
    payload.update(
        {
            "status": "ready",
            "candle_count": len(candles),
            "candles": candles,
        }
    )
    if note:
        payload["note"] = note
    return payload


def _unavailable(reason: str, message: str) -> dict[str, Any]:
    payload = _base_payload("5m", trading_date_ist())
    payload.update(
        {
            "status": "unavailable",
            "candle_count": 0,
            "candles": [],
            "reason": reason,
            "message": message,
        }
    )
    return payload


def chart_status() -> dict[str, Any]:
    with _CACHE_LOCK:
        cached = _CACHE.get("data") if isinstance(_CACHE.get("data"), dict) else None
    return {
        "source": "dhan",
        "interval": "5m",
        "timezone": IST_TIMEZONE_NAME,
        "trading_date": trading_date_ist().isoformat(),
        "token_status": token_status(),
        "market_state": market_state(),
        "last_updated_at": cached.get("updated_at") if cached else None,
        "candle_count": cached.get("candle_count") if cached else 0,
        "status": cached.get("status") if cached else "not_loaded",
        "reason": cached.get("reason") if cached else None,
    }
