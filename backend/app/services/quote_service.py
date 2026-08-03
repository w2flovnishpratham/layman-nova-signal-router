"""Authoritative current-price service for NOVA runtime consumers.

Every LTP consumer enters here. Fresh Dhan WebSocket ticks are preferred, a
short shared cache absorbs UI/runtime fan-out, and REST is a rate-limited,
single-flight fallback only.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from app.services.dhan_client import DhanLtpResult, RealDhanClient, get_broker_client
from app.services.dhan_marketfeed_ws import (
    clear_marketfeed_subscription,
    ensure_marketfeed_subscription,
    get_marketfeed_ltp,
)
from app.services.dhan_rate_limiter import dhan_quote_rate_limiter
from app.services.risk_manager import _market_is_open
from app.services.shared_market_data import (
    market_data_credentials,
    refresh_shared_token_after_auth_failure,
    shared_market_data_configured,
)
from app.services.state_store import get_engine_mode, get_runtime_settings, utc_now


logger = logging.getLogger("quote_service")
DEFAULT_CACHE_SECONDS = 1.0
_QUOTE_LOCK = threading.RLock()
_QUOTE_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_REST_FLIGHTS: dict[tuple[str, str], threading.Event] = {}
_REST_RESULTS: dict[tuple[str, str], dict[str, Any]] = {}
_METRICS: dict[str, int] = {
    "websocket_hits": 0,
    "cache_hits": 0,
    "rest_requests": 0,
    "rest_instruments": 0,
    "singleflight_followers": 0,
    "rate_limited": 0,
    "unavailable": 0,
}


@dataclass(frozen=True)
class QuoteRequest:
    exchange_segment: str
    security_id: str
    symbol: str | None = None


def get_quote_snapshot(
    *,
    exchange_segment: str,
    security_id: str,
    symbol: str | None = None,
    max_age_seconds: float = 2.0,
    allow_rest_fallback: bool = True,
) -> dict[str, Any]:
    request = QuoteRequest(exchange_segment, security_id, symbol)
    return get_quote_snapshots(
        [request],
        max_age_seconds=max_age_seconds,
        allow_rest_fallback=allow_rest_fallback,
    )[_key(exchange_segment, security_id)]


def get_quote_snapshots(
    requests: list[QuoteRequest],
    *,
    max_age_seconds: float = 2.0,
    allow_rest_fallback: bool = True,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Resolve many instruments while issuing at most one Dhan REST batch."""
    normalized = {
        _key(item.exchange_segment, item.security_id): QuoteRequest(
            str(item.exchange_segment or "").upper(),
            str(item.security_id or "").strip(),
            item.symbol,
        )
        for item in requests
        if str(item.exchange_segment or "").strip() and str(item.security_id or "").strip()
    }
    if not normalized:
        return {}
    if not _market_is_open():
        clear_marketfeed_subscription()
        return {key: _unavailable(item, "MARKET_CLOSED", "Market is closed.", "market_closed") for key, item in normalized.items()}

    runtime = get_runtime_settings()
    ws_enabled = bool(runtime.get("marketfeed_ws_enabled", True))
    ltp_source = str(runtime.get("option_ltp_source") or "AUTO").upper()
    cache_seconds = _positive_float(runtime.get("option_ltp_cache_seconds"), DEFAULT_CACHE_SECONDS)
    results: dict[tuple[str, str], dict[str, Any]] = {}
    unresolved: dict[tuple[str, str], QuoteRequest] = {}

    for key, item in normalized.items():
        if ws_enabled and ltp_source in {"AUTO", "WEBSOCKET"}:
            ensure_marketfeed_subscription(exchange_segment=item.exchange_segment, security_id=item.security_id)
            quote = get_marketfeed_ltp(
                exchange_segment=item.exchange_segment,
                security_id=item.security_id,
                max_age_seconds=max_age_seconds,
            )
            if quote.success and quote.ltp is not None:
                result = _snapshot(
                    item,
                    ltp=quote.ltp,
                    source="DHAN_WEBSOCKET",
                    received_at=quote.received_at,
                    age_seconds=quote.age_seconds,
                    status="FRESH",
                    stale=False,
                    message=quote.message,
                )
                _remember(key, result)
                _increment("websocket_hits")
                results[key] = result
                continue
            cached = _cached(key, cache_seconds)
            if cached is not None:
                _increment("cache_hits")
                results[key] = cached
                continue
            if ltp_source == "WEBSOCKET" or not allow_rest_fallback:
                results[key] = _unavailable(item, "WAITING_FOR_WEBSOCKET", quote.message, quote.error)
                continue
        else:
            cached = _cached(key, cache_seconds)
            if cached is not None:
                _increment("cache_hits")
                results[key] = cached
                continue
        unresolved[key] = item

    if not unresolved:
        return results
    if not allow_rest_fallback:
        for key, item in unresolved.items():
            results[key] = _unavailable(item, "REST_DISABLED", "REST fallback is disabled.", "rest_disabled")
        return results

    cooldown = dhan_quote_rate_limiter.cooldown_remaining()
    if cooldown > 0:
        _increment("rate_limited", len(unresolved))
        for key, item in unresolved.items():
            results[key] = _unavailable(
                item,
                "RATE_LIMITED",
                f"Quote service temporarily rate-limited; retry after {cooldown:.1f}s.",
                "rate_limited",
                retry_after_seconds=cooldown,
            )
        return results

    results.update(_rest_singleflight(unresolved, cache_seconds=cache_seconds))
    return results


def quote_metrics() -> dict[str, Any]:
    with _QUOTE_LOCK:
        metrics = dict(_METRICS)
        metrics["cache_entries"] = len(_QUOTE_CACHE)
        metrics["rest_in_flight"] = len(_REST_FLIGHTS)
    metrics["limiter"] = dhan_quote_rate_limiter.snapshot()
    return metrics


def reset_quote_state_for_tests() -> None:
    with _QUOTE_LOCK:
        _QUOTE_CACHE.clear()
        _REST_RESULTS.clear()
        _REST_FLIGHTS.clear()
        for key in _METRICS:
            _METRICS[key] = 0


def _rest_singleflight(
    unresolved: dict[tuple[str, str], QuoteRequest],
    *,
    cache_seconds: float,
) -> dict[tuple[str, str], dict[str, Any]]:
    owned: dict[tuple[str, str], QuoteRequest] = {}
    owned_events: dict[tuple[str, str], threading.Event] = {}
    followers: dict[tuple[str, str], threading.Event] = {}
    with _QUOTE_LOCK:
        for key, item in unresolved.items():
            event = _REST_FLIGHTS.get(key)
            if event is None:
                event = threading.Event()
                _REST_FLIGHTS[key] = event
                owned[key] = item
                owned_events[key] = event
            else:
                followers[key] = event
                _METRICS["singleflight_followers"] += 1

    owner_results: dict[tuple[str, str], dict[str, Any]] = {}
    if owned:
        try:
            owner_results = _fetch_rest_batch(owned)
        except Exception:
            logger.exception("quote_rest_fallback_failed endpoint=marketfeed_ltp instruments=%s", len(owned))
            owner_results = {
                key: _unavailable(item, "UNAVAILABLE", "Quote fallback failed.", "rest_error")
                for key, item in owned.items()
            }
        finally:
            with _QUOTE_LOCK:
                for key, item in owned.items():
                    result = owner_results.get(key) or _unavailable(item, "UNAVAILABLE", "Quote unavailable.", "rest_error")
                    _REST_RESULTS[key] = result
                    _REST_FLIGHTS.pop(key, None)
                    owned_events[key].set()

    follower_results: dict[tuple[str, str], dict[str, Any]] = {}
    for key, event in followers.items():
        event.wait(timeout=6.0)
        with _QUOTE_LOCK:
            result = dict(_REST_RESULTS.get(key) or {})
        if not result:
            cached = _cached(key, cache_seconds)
            result = cached or _unavailable(unresolved[key], "UNAVAILABLE", "Quote fallback timed out.", "singleflight_timeout")
        follower_results[key] = result
    return {**owner_results, **follower_results}


def _fetch_rest_batch(items: dict[tuple[str, str], QuoteRequest]) -> dict[tuple[str, str], dict[str, Any]]:
    creds = market_data_credentials()
    if not creds:
        status = "SHARED_MARKET_DATA_UNAVAILABLE" if shared_market_data_configured() else "CREDENTIALS_MISSING"
        return {key: _unavailable(item, status, "Dhan market-data credentials are unavailable.", status.lower()) for key, item in items.items()}

    client = RealDhanClient(proxy_url="") if creds.source == "shared_market_data" else get_broker_client(get_engine_mode())
    requests = [(item.exchange_segment, item.security_id) for item in items.values()]
    _increment("rest_requests")
    _increment("rest_instruments", len(requests))
    logger.info("quote_rest_fallback endpoint=marketfeed_ltp instruments=%s attempt=1", len(requests))
    raw = client.get_ltp_batch(
        client_id=creds.client_id,
        access_token=creds.access_token,
        instruments=requests,
    )
    if creds.source == "shared_market_data" and _batch_auth_failed(raw) and refresh_shared_token_after_auth_failure(
        status_code=next((result.status_code for result in raw.values() if result.status_code), None),
        message=next((result.message for result in raw.values() if result.message), None),
        raw_response=None,
    ):
        refreshed = market_data_credentials()
        if refreshed is not None:
            raw = RealDhanClient(proxy_url="").get_ltp_batch(
                client_id=refreshed.client_id,
                access_token=refreshed.access_token,
                instruments=requests,
            )

    results: dict[tuple[str, str], dict[str, Any]] = {}
    for key, item in items.items():
        quote = raw.get(key) or DhanLtpResult(False, "Quote missing from Dhan batch.", None, error="missing_batch_quote")
        if quote.success and quote.ltp is not None:
            result = _snapshot(item, ltp=quote.ltp, source="DHAN_REST", received_at=utc_now(), age_seconds=0.0, status="FRESH", stale=False, message=quote.message)
            _remember(key, result)
        else:
            status = "RATE_LIMITED" if quote.status_code == 429 else "UNAVAILABLE"
            if quote.status_code == 429:
                _increment("rate_limited")
            else:
                _increment("unavailable")
            result = _unavailable(item, status, quote.message, quote.error)
        results[key] = result
    return results


def _batch_auth_failed(results: dict[tuple[str, str], DhanLtpResult]) -> bool:
    return bool(results) and all(result.status_code in {401, 403} for result in results.values())


def _snapshot(
    item: QuoteRequest,
    *,
    ltp: float,
    source: str,
    received_at: str | None,
    age_seconds: float | None,
    status: str,
    stale: bool,
    message: str | None,
) -> dict[str, Any]:
    return {
        "security_id": item.security_id,
        "exchange_segment": item.exchange_segment,
        "symbol": item.symbol,
        "ltp": float(ltp),
        "source": source,
        "received_at": received_at,
        "age_seconds": age_seconds,
        "status": status,
        "stale": stale,
        "message": message,
        "error": None,
    }


def _unavailable(
    item: QuoteRequest,
    status: str,
    message: str | None,
    error: str | None,
    *,
    retry_after_seconds: float | None = None,
) -> dict[str, Any]:
    _increment("unavailable")
    return {
        "security_id": item.security_id,
        "exchange_segment": item.exchange_segment,
        "symbol": item.symbol,
        "ltp": None,
        "source": "UNAVAILABLE",
        "received_at": None,
        "age_seconds": None,
        "status": status,
        "stale": True,
        "message": message or "Quote unavailable.",
        "error": error or status.lower(),
        "retry_after_seconds": round(retry_after_seconds, 3) if retry_after_seconds is not None else None,
    }


def _remember(key: tuple[str, str], quote: dict[str, Any]) -> None:
    cached = dict(quote)
    cached["_cached_monotonic"] = time.monotonic()
    with _QUOTE_LOCK:
        _QUOTE_CACHE[key] = cached


def _cached(key: tuple[str, str], max_age_seconds: float) -> dict[str, Any] | None:
    with _QUOTE_LOCK:
        cached = dict(_QUOTE_CACHE.get(key) or {})
    cached_at = cached.pop("_cached_monotonic", None)
    if cached_at is None:
        return None
    age = time.monotonic() - float(cached_at)
    if age < 0 or age > max_age_seconds:
        return None
    base_age = float(cached.get("age_seconds") or 0.0)
    cached["age_seconds"] = round(base_age + age, 3)
    cached["status"] = "CACHED"
    cached["stale"] = False
    cached["message"] = "Using recent shared quote cache."
    return cached


def _increment(key: str, amount: int = 1) -> None:
    with _QUOTE_LOCK:
        _METRICS[key] = _METRICS.get(key, 0) + amount


def _key(exchange_segment: str, security_id: str) -> tuple[str, str]:
    return (str(exchange_segment or "").upper(), str(security_id or "").strip())


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
