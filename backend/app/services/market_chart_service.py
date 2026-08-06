"""Authoritative NIFTY intraday candles for the Trading chart.

The provider is queried only for native 1-minute candles. The 1m response is
validated, clipped to one IST session, and then used as the sole source for
5m/15m presentation bars. No code path derives 1m data from a coarser series.
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
AUTHORITATIVE_DHAN_INTERVAL = "1"
SUPPORTED_INTERVALS = {"1m": 1, "5m": 5, "15m": 15}

SESSION_OPEN_HOUR, SESSION_OPEN_MINUTE = 9, 15
# 15:40, not 15:30: NSE's Closing Auction Session change (2026-08-03) extended
# the F&O close by 10 minutes. This bounds both the Dhan fetch window and the
# in-session candle filter below, so leaving it at 15:30 silently dropped the
# last 10 minutes of every trading day from the chart -- and disagreed with
# risk_manager._market_is_open() and the frontend's own session filter, which
# both already moved to 15:40.
SESSION_CLOSE_HOUR, SESSION_CLOSE_MINUTE = 15, 40
CACHE_TTL_OPEN_SECONDS = 20.0
CACHE_TTL_CLOSED_SECONDS = 300.0
CACHE_TTL_FAILURE_SECONDS = 20.0
MAX_OPEN_SOURCE_AGE_SECONDS = 180
# How many trading days back get_nifty_candles() may walk looking for a session
# that actually has candles, when the market is closed. Bounded because each
# step is a Dhan round trip; 5 covers a long weekend plus adjacent holidays,
# which is the realistic worst case.
MAX_TRADING_DAY_LOOKBACK = 5
TOKEN_EXPIRING_SOON_SECONDS = 30 * 60

_CACHE_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()
_CACHE: dict[str, dict[str, Any]] = {}


def trading_date_ist() -> date:
    return datetime.now(_IST).date()


def chart_trading_date_ist(now_ist: datetime | None = None) -> date:
    now_ist = now_ist or datetime.now(_IST)
    today = now_ist.date()
    session_start, _ = session_bounds_ist(today)
    selected = today if now_ist >= session_start else today - timedelta(days=1)
    while selected.weekday() >= 5:
        selected -= timedelta(days=1)
    return selected


def session_bounds_ist(trading_date: date) -> tuple[datetime, datetime]:
    start = datetime(
        trading_date.year,
        trading_date.month,
        trading_date.day,
        SESSION_OPEN_HOUR,
        SESSION_OPEN_MINUTE,
        tzinfo=_IST,
    )
    end = datetime(
        trading_date.year,
        trading_date.month,
        trading_date.day,
        SESSION_CLOSE_HOUR,
        SESSION_CLOSE_MINUTE,
        tzinfo=_IST,
    )
    return start, end


def cache_key(interval: str, trading_date: date) -> str:
    return f"nifty:{interval}:{trading_date.isoformat()}:{IST_TIMEZONE_NAME}"


def _valid_one_minute_candle(raw: dict[str, Any]) -> dict[str, Any] | None:
    try:
        candle = {
            "time": int(raw["time"]),
            "open": float(raw["open"]),
            "high": float(raw["high"]),
            "low": float(raw["low"]),
            "close": float(raw["close"]),
            "volume": float(raw.get("volume") or 0),
        }
    except (KeyError, TypeError, ValueError):
        return None
    prices = [candle["open"], candle["high"], candle["low"], candle["close"]]
    if (
        candle["time"] % 60 != 0
        or any(not value or value <= 0 for value in prices)
        or candle["high"] < max(candle["open"], candle["close"], candle["low"])
        or candle["low"] > min(candle["open"], candle["close"], candle["high"])
        or candle["volume"] < 0
    ):
        return None
    return candle


def filter_today_session(
    candles: list[dict[str, Any]],
    trading_date: date,
) -> list[dict[str, Any]]:
    """Validate native 1m candles and keep exactly one IST session."""
    def sort_time(item: Any) -> int:
        try:
            return int(item.get("time") or 0) if isinstance(item, dict) else 0
        except (TypeError, ValueError):
            return 0

    start, end = session_bounds_ist(trading_date)
    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())
    seen: set[int] = set()
    filtered: list[dict[str, Any]] = []
    for raw in sorted(candles, key=sort_time):
        candle = _valid_one_minute_candle(raw)
        if candle is None:
            continue
        stamp = int(candle["time"])
        if stamp < start_epoch or stamp >= end_epoch or stamp in seen:
            continue
        seen.add(stamp)
        filtered.append(candle)
    return filtered


def aggregate_one_minute_candles(
    candles: list[dict[str, Any]],
    *,
    interval_minutes: int,
    trading_date: date,
) -> list[dict[str, Any]]:
    """Build IST-aligned bars solely from validated native 1m candles."""
    if interval_minutes not in {1, 5, 15}:
        raise ValueError("Chart interval must be 1, 5, or 15 minutes.")
    source = filter_today_session(candles, trading_date)
    if interval_minutes == 1:
        return source
    start, _ = session_bounds_ist(trading_date)
    start_epoch = int(start.timestamp())
    seconds = interval_minutes * 60
    buckets: dict[int, list[dict[str, Any]]] = {}
    for candle in source:
        bucket = start_epoch + ((int(candle["time"]) - start_epoch) // seconds) * seconds
        buckets.setdefault(bucket, []).append(candle)
    aggregated: list[dict[str, Any]] = []
    for bucket, members in sorted(buckets.items()):
        aggregated.append(
            {
                "time": bucket,
                "open": members[0]["open"],
                "high": max(member["high"] for member in members),
                "low": min(member["low"] for member in members),
                "close": members[-1]["close"],
                "volume": sum(member["volume"] for member in members),
            }
        )
    return aggregated


def token_status() -> str:
    if shared_market_data_configured():
        status = shared_market_data_status()
        if not status.get("has_token"):
            return "unavailable"
        if not status.get("token_valid"):
            return "expired"
        seconds = float(status.get("seconds_to_expiry") or 0)
        return "expiring_soon" if seconds < TOKEN_EXPIRING_SOON_SECONDS else "valid"
    return "valid" if market_data_credentials() else "unavailable"


def market_state() -> str:
    return "open" if _market_is_open() else "closed"


def _cached(key: str, market_open: bool) -> dict[str, Any] | None:
    # A failed/unavailable fetch (e.g. one transient Dhan hiccup) must not be
    # treated as valid for the full closed-market TTL, or it locks every
    # viewer out of the chart for up to 5 minutes before retrying.
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        data = entry["data"]
        ttl = (
            (CACHE_TTL_OPEN_SECONDS if market_open else CACHE_TTL_CLOSED_SECONDS)
            if data.get("status") == "ready"
            else CACHE_TTL_FAILURE_SECONDS
        )
        if time.monotonic() - float(entry.get("built_monotonic") or 0) <= ttl:
            return data
    return None


def _store_cache(key: str, data: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = {"data": data, "built_monotonic": time.monotonic()}


def get_nifty_candles(interval: str = "5m") -> dict[str, Any]:
    if interval not in SUPPORTED_INTERVALS:
        return _unavailable(
            "unsupported_interval",
            f"Interval {interval!r} is not supported. Choose 1m, 5m, or 15m.",
            interval_label=interval,
        )
    trading_date = chart_trading_date_ist()
    market_open = _market_is_open()
    key = cache_key(interval, trading_date)
    cached = _cached(key, market_open)
    if cached is not None:
        return cached

    with _FETCH_LOCK:
        cached = _cached(key, market_open)
        if cached is not None:
            return cached
        one_minute_key = cache_key("1m", trading_date)
        one_minute = _cached(one_minute_key, market_open)
        if one_minute is None:
            one_minute = _fetch_authoritative_one_minute(trading_date)
            _store_cache(one_minute_key, one_minute)
        if one_minute.get("status") != "ready" and not market_open:
            # Walk back to the most recent session that actually has candles.
            # chart_trading_date_ist() already steps over weekends, but it can
            # only reason about the calendar -- it cannot know a date returned
            # nothing. Two real cases it misses: a trading holiday (a weekday
            # with no session), and Dhan not yet serving the just-closed
            # session (measured: at 02:25 IST, Aug 6 returned 0 candles while
            # Aug 5/4/3 each returned 383), which leaves the chart showing an
            # error all night and every pre-market morning.
            #
            # Deliberately only while the market is CLOSED: during live hours an
            # empty result means the session has just opened and candles are
            # still coming, and silently substituting a previous day's prices
            # into a live trading chart would be dangerous.
            for _ in range(MAX_TRADING_DAY_LOOKBACK):
                trading_date -= timedelta(days=1)
                while trading_date.weekday() >= 5:
                    trading_date -= timedelta(days=1)
                one_minute_key = cache_key("1m", trading_date)
                fallback = _cached(one_minute_key, market_open)
                if fallback is None:
                    fallback = _fetch_authoritative_one_minute(trading_date)
                    _store_cache(one_minute_key, fallback)
                if fallback.get("status") == "ready":
                    one_minute = fallback
                    key = cache_key(interval, trading_date)
                    break
        if one_minute.get("status") != "ready":
            unavailable = {
                **one_minute,
                "interval": interval,
            }
            _store_cache(key, unavailable)
            return unavailable
        candles = aggregate_one_minute_candles(
            one_minute["candles"],
            interval_minutes=SUPPORTED_INTERVALS[interval],
            trading_date=trading_date,
        )
        payload = _ready_payload(interval, trading_date, candles)
        _store_cache(key, payload)
        return payload


def _fetch_authoritative_one_minute(trading_date: date) -> dict[str, Any]:
    session_start, session_end = session_bounds_ist(trading_date)
    now_ist = datetime.now(_IST)
    creds = market_data_credentials()
    if not creds:
        reason = (
            "market_data_token_expired"
            if shared_market_data_configured()
            else "market_data_unavailable"
        )
        return _unavailable(
            reason,
            "Authoritative 1m NIFTY candles are unavailable. Nova is reconnecting market data.",
            trading_date,
            "1m",
        )
    result = RealDhanClient(proxy_url="").get_intraday_candles(
        client_id=creds.client_id,
        access_token=creds.access_token,
        security_id=NIFTY_INDEX_SECURITY_ID,
        exchange_segment=NIFTY_INDEX_EXCHANGE_SEGMENT,
        instrument=NIFTY_INDEX_INSTRUMENT,
        interval=AUTHORITATIVE_DHAN_INTERVAL,
        from_date=session_start.strftime("%Y-%m-%d %H:%M:%S"),
        to_date=min(now_ist, session_end).strftime("%Y-%m-%d %H:%M:%S"),
    )
    if not result.get("success"):
        error_text = str(result.get("error") or "").lower()
        reason = (
            "market_data_chart_auth_failed"
            if "token" in error_text
            or "auth" in error_text
            or result.get("status_code") in (401, 403)
            else "market_data_fetch_failed"
        )
        return _unavailable(
            reason,
            "Authoritative 1m NIFTY candles are unavailable. Nova is reconnecting market data.",
            trading_date,
            "1m",
        )
    raw = result.get("candles")
    if not isinstance(raw, list) or not raw:
        return _unavailable(
            "authoritative_1m_empty",
            "Authoritative 1m NIFTY candles are unavailable for this session.",
            trading_date,
            "1m",
        )
    start_epoch = int(session_start.timestamp())
    end_epoch = int(session_end.timestamp())
    in_session_count = 0
    malformed_in_session = False
    for candidate in raw:
        if not isinstance(candidate, dict):
            malformed_in_session = True
            continue
        try:
            stamp = int(candidate.get("time") or 0)
        except (TypeError, ValueError):
            malformed_in_session = True
            continue
        if start_epoch <= stamp < end_epoch:
            in_session_count += 1
            if _valid_one_minute_candle(candidate) is None:
                malformed_in_session = True
    candles = filter_today_session(raw, trading_date)
    if (
        not candles
        or malformed_in_session
        or len(candles) != in_session_count
    ):
        return _unavailable(
            "authoritative_1m_malformed",
            "Authoritative 1m NIFTY candles were malformed and were not displayed.",
            trading_date,
            "1m",
        )
    if (
        _market_is_open()
        and trading_date == now_ist.date()
        and int(now_ist.timestamp()) - int(candles[-1]["time"]) > MAX_OPEN_SOURCE_AGE_SECONDS
    ):
        return _unavailable(
            "authoritative_1m_stale",
            "Authoritative 1m NIFTY candles are stale and were not displayed.",
            trading_date,
            "1m",
        )
    return _ready_payload("1m", trading_date, candles)


def _fetch_candles(
    _dhan_interval: str,
    interval_label: str,
    today: date,
) -> dict[str, Any]:
    """Compatibility wrapper; provider interval is intentionally always 1m."""
    one_minute = _fetch_authoritative_one_minute(today)
    if one_minute.get("status") != "ready":
        return {**one_minute, "interval": interval_label}
    minutes = SUPPORTED_INTERVALS.get(interval_label)
    if minutes is None:
        return _unavailable(
            "unsupported_interval",
            f"Interval {interval_label!r} is not supported.",
            today,
            interval_label,
        )
    return _ready_payload(
        interval_label,
        today,
        aggregate_one_minute_candles(
            one_minute["candles"],
            interval_minutes=minutes,
            trading_date=today,
        ),
    )


def _base_payload(interval_label: str, trading_date: date) -> dict[str, Any]:
    session_start, session_end = session_bounds_ist(trading_date)
    return {
        "symbol": "NIFTY",
        "interval": interval_label,
        "source": "dhan_authoritative_1m",
        "timezone": IST_TIMEZONE_NAME,
        "trading_date": trading_date.isoformat(),
        "session_start": session_start.isoformat(),
        "session_end": session_end.isoformat(),
        "market_state": market_state(),
        "token_status": token_status(),
        "updated_at": utc_now(),
    }


def _ready_payload(
    interval_label: str,
    trading_date: date,
    candles: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        **_base_payload(interval_label, trading_date),
        "status": "ready",
        "candle_count": len(candles),
        "candles": candles,
    }


def _unavailable(
    reason: str,
    message: str,
    trading_date: date | None = None,
    interval_label: str = "5m",
) -> dict[str, Any]:
    return {
        **_base_payload(interval_label, trading_date or chart_trading_date_ist()),
        "status": "unavailable",
        "candle_count": 0,
        "candles": [],
        "reason": reason,
        "message": message,
    }


def chart_status(interval: str = "5m") -> dict[str, Any]:
    trading_date = chart_trading_date_ist()
    with _CACHE_LOCK:
        entry = _CACHE.get(cache_key(interval, trading_date))
        cached = entry.get("data") if entry else None
    return {
        "source": "dhan_authoritative_1m",
        "interval": interval,
        "timezone": IST_TIMEZONE_NAME,
        "trading_date": trading_date.isoformat(),
        "token_status": token_status(),
        "market_state": market_state(),
        "last_updated_at": cached.get("updated_at") if cached else None,
        "candle_count": cached.get("candle_count") if cached else 0,
        "status": cached.get("status") if cached else "not_loaded",
        "reason": cached.get("reason") if cached else None,
    }
