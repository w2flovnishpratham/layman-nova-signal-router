from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.config import (
    DEFAULT_EXCHANGE_SEGMENT,
    DEFAULT_STRATEGY_CODE,
    PINE_EXCHANGE_NSE_OPT_MAPS_TO,
    PINE_ORDER_MKT_MAPS_TO,
    PINE_PRODUCT_I_MAPS_TO,
)
from app.schemas.signal import NormalizedSignal
from app.services.instrument_resolver import resolve_option_security_id


class PayloadParseError(ValueError):
    status = "INVALID_PAYLOAD"


class UnsupportedPayloadFormatError(PayloadParseError):
    status = "UNSUPPORTED_PAYLOAD_FORMAT"


ORDER_TYPE_MAP = {
    "MKT": PINE_ORDER_MKT_MAPS_TO,
    "MARKET": "MARKET",
    "LMT": "LIMIT",
    "LIMIT": "LIMIT",
}

PRODUCT_TYPE_MAP = {
    "I": PINE_PRODUCT_I_MAPS_TO,
    "INTRADAY": "INTRADAY",
    "CNC": "CNC",
    "MARGIN": "MARGIN",
}

TRANSACTION_MAP = {
    "B": ("ENTRY", "BUY"),
    "BUY": ("ENTRY", "BUY"),
    "S": ("EXIT", "SELL"),
    "SELL": ("EXIT", "SELL"),
}


def _bucket_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M")


def _stable_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _required(data: dict[str, Any], fields: list[str], prefix: str = "") -> None:
    missing = [field for field in fields if data.get(field) in (None, "")]
    if missing:
        raise PayloadParseError(f"Missing required fields: {', '.join(prefix + item for item in missing)}")


def _as_positive_int(value: Any, field_name: str) -> int:
    try:
        qty = int(float(value))
    except (TypeError, ValueError):
        raise PayloadParseError(f"{field_name} must be a positive integer.") from None
    if qty <= 0:
        raise PayloadParseError(f"{field_name} must be a positive integer.")
    return qty


def _as_float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise PayloadParseError("strike_price must be numeric.") from None


def _normalize_order_type(value: Any) -> str:
    mapped = ORDER_TYPE_MAP.get(str(value).upper())
    if not mapped:
        raise PayloadParseError(f"Unsupported order_type/orderType: {value}")
    return mapped


def _normalize_product_type(value: Any) -> str:
    mapped = PRODUCT_TYPE_MAP.get(str(value).upper())
    if not mapped:
        raise PayloadParseError(f"Unsupported product_type/productType: {value}")
    return mapped


def _timestamp_bucket(raw_payload: dict[str, Any]) -> str:
    for key in ("signal_time", "timestamp", "time", "bar_time", "timenow"):
        value = raw_payload.get(key)
        if value not in (None, ""):
            return str(value)
    return _bucket_now()


def _format_strike(strike: float | None) -> str:
    if strike is None:
        return ""
    return str(int(strike)) if strike.is_integer() else str(strike)


def _build_readable_symbol(symbol: str, expiry: str | None, strike: float | None, option_side: str | None) -> str:
    parts = [symbol]
    if expiry:
        parts.append(expiry)
    if strike is not None:
        parts.append(_format_strike(strike))
    if option_side:
        parts.append(option_side)
    return " ".join(part for part in parts if part)


def _nova_signal_id(data: dict[str, Any]) -> str:
    provided = data.get("signal_id")
    if provided not in (None, ""):
        return str(provided)
    strategy_code = str(data.get("strategy_code") or DEFAULT_STRATEGY_CODE)
    action = str(data.get("action") or "")
    side = str(data.get("side") or "")
    symbol = str(data.get("symbol") or "")
    instrument_key = data.get("security_id") or data.get("trading_symbol") or ""
    return f"NOVA-{strategy_code}-{action}-{side}-{symbol}-{instrument_key}-{_timestamp_bucket(data)}"


def _pine_signal_id(raw_payload: dict[str, Any], leg: dict[str, Any]) -> str:
    provided = raw_payload.get("signal_id") or leg.get("signal_id")
    if provided not in (None, ""):
        return str(provided)
    parts = [
        "PINE_MULTI_LEG",
        str(leg.get("transactionType") or ""),
        str(leg.get("symbol") or ""),
        str(leg.get("option_type") or ""),
        str(leg.get("strike_price") or ""),
        str(leg.get("expiry_date") or ""),
        _stable_hash(raw_payload),
        _timestamp_bucket(raw_payload),
    ]
    return "-".join(part for part in parts if part)


def _parse_nova_payload(raw_payload: dict[str, Any]) -> NormalizedSignal:
    _required(raw_payload, ["secret", "strategy_code", "action", "side", "symbol", "qty", "order_type", "product_type"])
    normalized = {
        "payload_format": "NOVA",
        "secret": str(raw_payload["secret"]),
        "signal_id": _nova_signal_id(raw_payload),
        "strategy_code": raw_payload["strategy_code"],
        "action": str(raw_payload["action"]).upper(),
        "side": str(raw_payload["side"]).upper(),
        "symbol": str(raw_payload["symbol"]).upper(),
        "instrument_type": raw_payload.get("instrument_type"),
        "exchange_segment": raw_payload.get("exchange_segment") or DEFAULT_EXCHANGE_SEGMENT,
        "security_id": raw_payload.get("security_id"),
        "trading_symbol": raw_payload.get("trading_symbol"),
        "option_side": raw_payload.get("option_side"),
        "strike": _as_float_or_none(raw_payload.get("strike")),
        "expiry": raw_payload.get("expiry"),
        "qty": _as_positive_int(raw_payload.get("qty"), "qty"),
        "order_type": _normalize_order_type(raw_payload.get("order_type")),
        "product_type": _normalize_product_type(raw_payload.get("product_type")),
        "source": "tradingview",
        "raw_payload": raw_payload,
    }
    if not normalized["trading_symbol"]:
        normalized["trading_symbol"] = _build_readable_symbol(
            normalized["symbol"],
            normalized["expiry"],
            normalized["strike"],
            normalized["option_side"],
        )
    try:
        return NormalizedSignal.model_validate(normalized)
    except ValidationError as exc:
        raise PayloadParseError(f"Signal validation failed: {exc.errors()[0]['msg']}") from None


def _parse_pine_multi_leg_payload(raw_payload: dict[str, Any]) -> NormalizedSignal:
    _required(raw_payload, ["secret", "alertType", "order_legs"])
    legs = raw_payload.get("order_legs")
    if not isinstance(legs, list) or not legs:
        raise PayloadParseError("order_legs must be a non-empty array.")
    leg = legs[0]
    if not isinstance(leg, dict):
        raise PayloadParseError("order_legs[0] must be an object.")

    _required(
        leg,
        ["transactionType", "quantity", "symbol", "exchange", "productType", "orderType"],
        prefix="order_legs[0].",
    )
    transaction = str(leg["transactionType"]).upper()
    if transaction not in TRANSACTION_MAP:
        raise PayloadParseError(f"Unsupported transactionType: {leg['transactionType']}")
    action, side = TRANSACTION_MAP[transaction]

    symbol = str(leg["symbol"]).upper()
    instrument = str(leg.get("instrument") or "").upper()
    option_side = str(leg.get("option_type")).upper() if leg.get("option_type") not in (None, "") else None
    strike = _as_float_or_none(leg.get("strike_price"))
    expiry = leg.get("expiry_date")
    exchange = str(leg["exchange"]).upper()
    exchange_segment = PINE_EXCHANGE_NSE_OPT_MAPS_TO if exchange == "NSE" and instrument == "OPT" else exchange

    normalized = {
        "payload_format": "PINE_MULTI_LEG",
        "secret": str(raw_payload["secret"]),
        "signal_id": _pine_signal_id(raw_payload, leg),
        "strategy_code": raw_payload.get("strategy_code") or DEFAULT_STRATEGY_CODE,
        "action": action,
        "side": side,
        "symbol": symbol,
        "instrument_type": instrument or None,
        "exchange_segment": leg.get("exchange_segment") or exchange_segment,
        "security_id": leg.get("security_id") or raw_payload.get("security_id"),
        "trading_symbol": leg.get("trading_symbol")
        or raw_payload.get("trading_symbol")
        or _build_readable_symbol(symbol, expiry, strike, option_side),
        "option_side": option_side,
        "strike": strike,
        "expiry": expiry,
        "qty": _as_positive_int(leg.get("quantity"), "order_legs[0].quantity"),
        "order_type": _normalize_order_type(leg.get("orderType")),
        "product_type": _normalize_product_type(leg.get("productType")),
        "source": "tradingview",
        "raw_payload": raw_payload,
    }
    try:
        signal = NormalizedSignal.model_validate(normalized)
    except ValidationError as exc:
        raise PayloadParseError(f"Signal validation failed: {exc.errors()[0]['msg']}") from None
    match = resolve_option_security_id(signal)
    if match:
        updates: dict[str, Any] = {"security_id": match.security_id}
        if match.trading_symbol:
            updates["trading_symbol"] = match.trading_symbol
        signal = signal.model_copy(update=updates)
    return signal


def parse_webhook_payload(raw_payload: dict[str, Any]) -> NormalizedSignal:
    if raw_payload.get("strategy_code") and raw_payload.get("action"):
        return _parse_nova_payload(raw_payload)
    if raw_payload.get("alertType") == "multi_leg_order" and "order_legs" in raw_payload:
        return _parse_pine_multi_leg_payload(raw_payload)
    raise UnsupportedPayloadFormatError("Unsupported payload format.")
