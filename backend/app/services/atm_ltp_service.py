from __future__ import annotations

import time
import threading
from typing import Any

from app.config import DEFAULT_EXCHANGE_SEGMENT
from app.services.credential_vault import get_dhan_credentials
from app.services.dhan_client import get_broker_client
from app.services.dhan_marketfeed_ws import (
    clear_marketfeed_subscription,
    ensure_marketfeed_subscription,
    get_marketfeed_ltp,
    marketfeed_ws_status,
)
from app.services.risk_manager import _market_is_open
from app.services.security_id_resolver import OptionContractSuggestion, suggest_option_contract
from app.services.state_store import get_app_state, get_engine_mode, get_runtime_settings, utc_now


NIFTY_INDEX_EXCHANGE_SEGMENT = "IDX_I"
NIFTY_INDEX_SECURITY_ID = "13"
NIFTY_ATM_STEP = 50
DEFAULT_LTP_CACHE_SECONDS = 15.0
_LTP_CACHE_LOCK = threading.RLock()
_LTP_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def get_atm_option_snapshot(
    *,
    option_side: str = "BOTH",
    lots: int = 1,
    allow_rest_fallback: bool | None = None,
) -> dict[str, Any]:
    runtime = get_runtime_settings()
    side = str(option_side or "BOTH").upper()
    sides = ["CE", "PE"] if side == "BOTH" else [side]
    sides = [item for item in sides if item in {"CE", "PE"}] or ["CE", "PE"]
    if not _market_is_open():
        return _market_closed_snapshot(sides=sides, lots=lots)

    max_age_seconds = _positive_float(runtime.get("option_ws_stale_seconds"), 5.0)
    rest_fallback = bool(runtime.get("option_rest_fallback_enabled", True)) if allow_rest_fallback is None else bool(allow_rest_fallback)

    spot_quote = _ltp_with_prefer_ws(
        exchange_segment=NIFTY_INDEX_EXCHANGE_SEGMENT,
        security_id=NIFTY_INDEX_SECURITY_ID,
        max_age_seconds=max_age_seconds,
        allow_rest_fallback=rest_fallback,
    )
    nifty_spot = _number(spot_quote.get("ltp")) or _latest_signal_nifty_price()
    spot_source = spot_quote.get("source") if spot_quote.get("ltp") is not None else "latest_signal" if nifty_spot is not None else None
    atm_strike = _round_atm_strike(nifty_spot) if nifty_spot is not None else None

    options: dict[str, Any] = {}
    for item in sides:
        suggestion = _suggest_contract(item, nifty_spot, atm_strike)
        options[item] = _option_snapshot(
            option_side=item,
            suggestion=suggestion,
            lots=lots,
            max_age_seconds=max_age_seconds,
            allow_rest_fallback=rest_fallback,
        )

    ok = nifty_spot is not None and any(option.get("ltp") is not None for option in options.values())
    return {
        "ok": ok,
        "mode": get_engine_mode(legacy_fallback=False) or get_engine_mode(),
        "symbol": "NIFTY",
        "marketOpen": True,
        "niftySpot": nifty_spot,
        "niftySpotSource": spot_source,
        "niftySpotStatus": spot_quote.get("status"),
        "atmStrike": atm_strike,
        "strikeStep": NIFTY_ATM_STEP,
        "lots": lots,
        "qtyPerLot": None,
        "computedAt": utc_now(),
        "options": options,
        "marketfeed": marketfeed_ws_status(),
        "message": "ATM LTP ready." if ok else "Waiting for Dhan market-feed ticks.",
    }


def _market_closed_snapshot(*, sides: list[str], lots: int) -> dict[str, Any]:
    clear_marketfeed_subscription()
    return {
        "ok": False,
        "mode": get_engine_mode(legacy_fallback=False) or get_engine_mode(),
        "symbol": "NIFTY",
        "marketOpen": False,
        "niftySpot": None,
        "niftySpotSource": "market_closed",
        "niftySpotStatus": "market_closed",
        "atmStrike": None,
        "strikeStep": NIFTY_ATM_STEP,
        "lots": lots,
        "qtyPerLot": None,
        "computedAt": utc_now(),
        "options": {side: _market_closed_option(side) for side in sides},
        "marketfeed": marketfeed_ws_status(),
        "message": "Market is closed; Dhan WebSocket and REST LTP fetching are disabled.",
    }


def _market_closed_option(option_side: str) -> dict[str, Any]:
    return {
        "ok": False,
        "side": option_side,
        "ltp": None,
        "ltpSource": "market_closed",
        "ltpStatus": "market_closed",
        "status": "market_closed",
        "message": "Market is closed; LTP fetching is disabled.",
    }


def _option_snapshot(
    *,
    option_side: str,
    suggestion: OptionContractSuggestion | None,
    lots: int,
    max_age_seconds: float,
    allow_rest_fallback: bool,
) -> dict[str, Any]:
    if suggestion is None:
        return {
            "ok": False,
            "side": option_side,
            "ltp": None,
            "status": "contract_missing",
            "message": "Could not resolve an ATM option contract from the local Dhan scrip master.",
        }

    quote = _ltp_with_prefer_ws(
        exchange_segment=DEFAULT_EXCHANGE_SEGMENT,
        security_id=suggestion.security_id,
        max_age_seconds=max_age_seconds,
        allow_rest_fallback=allow_rest_fallback,
    )
    lot_size = suggestion.lot_size or 0
    qty = max(int(lots or 1), 1) * lot_size if lot_size else None
    ltp = _number(quote.get("ltp"))
    cost = round(ltp * qty, 2) if ltp is not None and qty else None
    return {
        "ok": ltp is not None,
        "side": option_side,
        "expiry": suggestion.expiry,
        "strike": suggestion.strike,
        "securityId": suggestion.security_id,
        "tradingSymbol": suggestion.trading_symbol,
        "lotSize": suggestion.lot_size,
        "qty": qty,
        "ltp": ltp,
        "ltpSource": quote.get("source"),
        "ltpStatus": quote.get("status"),
        "ltpReceivedAt": quote.get("receivedAt"),
        "ltpAgeSeconds": quote.get("ageSeconds"),
        "estimatedCost": cost,
        "autoContract": suggestion.model_dump(),
        "message": quote.get("message"),
        "error": quote.get("error"),
    }


def _ltp_with_prefer_ws(
    *,
    exchange_segment: str,
    security_id: str,
    max_age_seconds: float,
    allow_rest_fallback: bool,
) -> dict[str, Any]:
    runtime = get_runtime_settings()
    if not _market_is_open():
        clear_marketfeed_subscription()
        return {
            "status": "market_closed",
            "source": "market_closed",
            "message": "Market is closed; Dhan WebSocket and REST LTP fetching are disabled.",
            "ltp": None,
            "receivedAt": None,
            "ageSeconds": None,
            "error": "market_closed",
        }
    ltp_source = str(runtime.get("option_ltp_source") or "AUTO").upper()
    ws_enabled = bool(runtime.get("marketfeed_ws_enabled", True))
    cache_seconds = _positive_float(runtime.get("option_ltp_cache_seconds"), DEFAULT_LTP_CACHE_SECONDS)
    if ws_enabled and ltp_source in {"AUTO", "WEBSOCKET"}:
        ensure_marketfeed_subscription(exchange_segment=exchange_segment, security_id=security_id)
        quote = get_marketfeed_ltp(
            exchange_segment=exchange_segment,
            security_id=security_id,
            max_age_seconds=max_age_seconds,
        )
        if quote.success and quote.ltp is not None:
            result = {
                "status": "websocket",
                "source": quote.source,
                "message": quote.message,
                "ltp": quote.ltp,
                "receivedAt": quote.received_at,
                "ageSeconds": quote.age_seconds,
                "error": None,
            }
            _remember_ltp(exchange_segment, security_id, result)
            return result
        cached = _cached_ltp(exchange_segment, security_id, max_age_seconds=cache_seconds)
        if cached is not None:
            return cached
        if ltp_source == "WEBSOCKET" or not allow_rest_fallback:
            return {
                "status": quote.error or "ws_waiting",
                "source": quote.source,
                "message": quote.message,
                "ltp": quote.ltp,
                "receivedAt": quote.received_at,
                "ageSeconds": quote.age_seconds,
                "error": quote.error,
            }

    if not allow_rest_fallback:
        cached = _cached_ltp(exchange_segment, security_id, max_age_seconds=cache_seconds)
        if cached is not None:
            return cached
        return {
            "status": "rest_disabled",
            "source": "none",
            "message": "REST fallback is disabled.",
            "ltp": None,
            "error": "rest_disabled",
        }

    creds = get_dhan_credentials()
    if not creds:
        return {
            "status": "credentials_missing",
            "source": "none",
            "message": "Dhan credentials are missing.",
            "ltp": None,
            "error": "credentials_missing",
        }

    try:
        client = get_broker_client(get_engine_mode())
        quote = client.get_ltp(
            client_id=creds.client_id,
            access_token=creds.access_token,
            exchange_segment=exchange_segment,
            security_id=security_id,
        )
    except Exception as exc:
        cached = _cached_ltp(exchange_segment, security_id, max_age_seconds=cache_seconds)
        if cached is not None:
            return cached
        return {
            "status": "rest_error",
            "source": "dhan_rest_ltp",
            "message": str(exc),
            "ltp": None,
            "error": str(exc),
        }

    result = {
        "status": "rest" if quote.success and quote.ltp is not None else quote.error or "rest_error",
        "source": "dhan_rest_ltp",
        "message": quote.message,
        "ltp": quote.ltp,
        "receivedAt": utc_now() if quote.success and quote.ltp is not None else None,
        "ageSeconds": 0.0 if quote.success and quote.ltp is not None else None,
        "error": quote.error,
    }
    if quote.success and quote.ltp is not None:
        _remember_ltp(exchange_segment, security_id, result)
    else:
        cached = _cached_ltp(exchange_segment, security_id, max_age_seconds=cache_seconds)
        if cached is not None:
            return cached
    return result


def _cache_key(exchange_segment: str, security_id: str) -> tuple[str, str]:
    return (str(exchange_segment or "").upper(), str(security_id or "").strip())


def _remember_ltp(exchange_segment: str, security_id: str, quote: dict[str, Any]) -> None:
    if _number(quote.get("ltp")) is None:
        return
    key = _cache_key(exchange_segment, security_id)
    if not key[0] or not key[1]:
        return
    cached = dict(quote)
    cached["cachedAtMonotonic"] = time.monotonic()
    with _LTP_CACHE_LOCK:
        _LTP_CACHE[key] = cached


def _cached_ltp(exchange_segment: str, security_id: str, *, max_age_seconds: float) -> dict[str, Any] | None:
    key = _cache_key(exchange_segment, security_id)
    with _LTP_CACHE_LOCK:
        cached = dict(_LTP_CACHE.get(key) or {})
    if not cached:
        return None
    cached_at = _number(cached.get("cachedAtMonotonic"))
    if cached_at is None:
        return None
    cache_age = time.monotonic() - cached_at
    if cache_age < 0 or cache_age > max_age_seconds:
        return None
    base_age = _number(cached.get("ageSeconds")) or 0.0
    cached["status"] = f"cache:{cached.get('status') or 'ltp'}"
    cached["ageSeconds"] = round(base_age + cache_age, 3)
    cached["message"] = "Using recent cached LTP while waiting for a fresh Dhan tick."
    cached["error"] = None
    return cached


def _suggest_contract(option_side: str, nifty_spot: float | None, atm_strike: int | None) -> OptionContractSuggestion | None:
    if atm_strike is None:
        return None
    return suggest_option_contract(
        symbol="NIFTY",
        option_side=option_side,
        exchange_segment=DEFAULT_EXCHANGE_SEGMENT,
        reference_price=nifty_spot,
        strike=float(atm_strike),
    )


def _latest_signal_nifty_price() -> float | None:
    latest = get_app_state().get("last_signal")
    if not isinstance(latest, dict):
        return None
    for key in ("niftyPrice", "niftySpot"):
        value = _number(latest.get(key))
        if value is not None:
            return value
    return None


def _round_atm_strike(price: float) -> int:
    return int(round(float(price) / NIFTY_ATM_STEP) * NIFTY_ATM_STEP)


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
