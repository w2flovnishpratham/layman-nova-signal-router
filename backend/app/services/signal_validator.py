from __future__ import annotations

from app.config import ALLOW_ONLY_INTRADAY, ALLOW_ONLY_NIFTY, DEFAULT_EXCHANGE_SEGMENT
from app.schemas.signal import NormalizedSignal


def validate_signal(payload: NormalizedSignal) -> tuple[bool, str | None]:
    # Accept any strategy the registry currently lists as READY (the legacy
    # default plus anything published via admin_pine_conversion_service
    # .publish_as_shared -- see built_in_strategy_registry.list_built_ins),
    # not just one hardcoded literal. DEFAULT_STRATEGY_CODE
    # ("TRADINGVIEW_NIFTY_V1") still canonicalizes to "supertrend" via
    # strategy_fanout.canonical_strategy_name, so existing senders keep working.
    from app.services import built_in_strategy_registry
    from app.services.strategy_fanout import canonical_strategy_name

    canonical = canonical_strategy_name(payload.strategy_code)
    ready_codes = {
        canonical_strategy_name(item["catalog_code"])
        for item in built_in_strategy_registry.list_built_ins()
        if item["availability"] == "READY"
    }
    if canonical not in ready_codes:
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
