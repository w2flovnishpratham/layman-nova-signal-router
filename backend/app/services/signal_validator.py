from __future__ import annotations

from app.config import ALLOW_ONLY_INTRADAY, ALLOW_ONLY_NIFTY, DEFAULT_EXCHANGE_SEGMENT, DEFAULT_STRATEGY_CODE
from app.schemas.signal import NormalizedSignal


def validate_signal(payload: NormalizedSignal) -> tuple[bool, str | None]:
    if payload.strategy_code != DEFAULT_STRATEGY_CODE:
        return False, f"Unknown strategy_code: {payload.strategy_code}"

    if ALLOW_ONLY_NIFTY:
        trading_symbol = payload.trading_symbol or ""
        symbol_ok = payload.symbol.upper() == "NIFTY" or "NIFTY" in trading_symbol.upper()
        if not symbol_ok:
            return False, "Only NIFTY alerts are allowed in this test build."

    if ALLOW_ONLY_INTRADAY and payload.product_type != "INTRADAY":
        return False, "Only INTRADAY product_type is allowed in this test build."

    if not payload.exchange_segment:
        payload.exchange_segment = DEFAULT_EXCHANGE_SEGMENT

    if payload.qty <= 0:
        return False, f"qty must be > 0, got {payload.qty}."

    return True, None
