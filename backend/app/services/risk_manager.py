from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import (
    ALLOW_ONLY_INTRADAY,
    ALLOW_ONLY_NIFTY,
    GLOBAL_KILL_SWITCH_BLOCKS_EXITS,
    QTY_MODE,
    settings,
)
from app.schemas.signal import NormalizedSignal
from app.services.state_store import get_app_state, get_open_position, get_runtime_settings


IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    final_qty: int = 0


def _setting_bool(runtime: dict, key: str, env_value: bool) -> bool:
    return bool(runtime.get(key, env_value))


def _setting_int(runtime: dict, key: str, env_value: int) -> int:
    try:
        return int(runtime.get(key, env_value))
    except (TypeError, ValueError):
        return env_value


def _market_is_open() -> bool:
    try:
        market_tz = ZoneInfo("Asia/Kolkata")
    except ZoneInfoNotFoundError:
        market_tz = IST
    now = datetime.now(market_tz)
    if now.weekday() >= 5:
        return False
    return time(9, 15) <= now.time() <= time(15, 30)


def _common_signal_checks(payload: NormalizedSignal) -> RiskDecision | None:
    if QTY_MODE.upper() != "ABSOLUTE":
        return RiskDecision(
            False,
            "QTY_MODE=LOTS is not fully implemented. Set QTY_MODE=ABSOLUTE in config.py. "
            "In ABSOLUTE mode, signal qty is sent directly as Dhan quantity (not lots).",
        )

    if ALLOW_ONLY_INTRADAY and payload.product_type != "INTRADAY":
        return RiskDecision(False, "Trade blocked: only INTRADAY product_type is allowed.")

    if ALLOW_ONLY_NIFTY:
        trading_symbol = payload.trading_symbol or ""
        symbol_ok = payload.symbol.upper() == "NIFTY" or "NIFTY" in trading_symbol.upper()
        if not symbol_ok:
            return RiskDecision(False, "Trade blocked: only NIFTY symbols are allowed.")

    if settings.REQUIRE_MARKET_HOURS and not _market_is_open():
        return RiskDecision(False, "Trade blocked: market-hours check failed.")

    return None


def evaluate_entry(payload: NormalizedSignal, runtime: dict | None = None) -> RiskDecision:
    # C11 — Use the caller's snapshot if provided; otherwise read live.
    if runtime is None:
        runtime = get_runtime_settings()
    common = _common_signal_checks(payload)
    if common:
        return common

    if not bool(get_app_state().get("webhook_trading_enabled")):
        return RiskDecision(False, "Trade blocked: WEBHOOK_TRADING_ENABLED=false.")

    if _setting_bool(runtime, "emergency_stop", False):
        return RiskDecision(False, "Trade blocked: EMERGENCY_STOP=true.")

    if _setting_bool(runtime, "global_kill_switch", False):
        return RiskDecision(False, "Trade blocked: GLOBAL_KILL_SWITCH=true.")

    if not _setting_bool(runtime, "allow_entry", True):
        return RiskDecision(False, "Trade blocked: ALLOW_ENTRY=false.")

    open_position = get_open_position()
    if open_position.get("has_open_position"):
        return RiskDecision(
            False,
            f"Trade blocked: open position already exists for {open_position.get('trading_symbol')}.",
        )

    max_qty = _setting_int(runtime, "max_qty_per_order", 1)
    if payload.qty > max_qty:
        return RiskDecision(False, "Quantity exceeds MAX_QTY_PER_ORDER.")

    # Strategy uses opposite-side Supertrend flips for exits; trade-count
    # and daily-loss ceilings were removed per the live-strategy direction.
    return RiskDecision(True, "All entry risk checks passed.", final_qty=payload.qty)


def _is_opposite_option_side(left: str | None, right: str | None) -> bool:
    left_side = str(left or "").upper()
    right_side = str(right or "").upper()
    return {left_side, right_side} == {"CE", "PE"}


def evaluate_reversal_entry(payload: NormalizedSignal, runtime: dict | None = None) -> RiskDecision:
    # C11 — Use the caller's snapshot if provided; otherwise read live.
    if runtime is None:
        runtime = get_runtime_settings()
    common = _common_signal_checks(payload)
    if common:
        return common

    if not bool(get_app_state().get("webhook_trading_enabled")):
        return RiskDecision(False, "Trade blocked: WEBHOOK_TRADING_ENABLED=false.")

    if _setting_bool(runtime, "emergency_stop", False):
        return RiskDecision(False, "Trade blocked: EMERGENCY_STOP=true.")

    if _setting_bool(runtime, "global_kill_switch", False):
        return RiskDecision(False, "Trade blocked: GLOBAL_KILL_SWITCH=true.")

    if not _setting_bool(runtime, "allow_entry", True):
        return RiskDecision(False, "Trade blocked: ALLOW_ENTRY=false.")

    if not _setting_bool(runtime, "allow_exit", True):
        return RiskDecision(False, "Reversal blocked: ALLOW_EXIT=false.")

    open_position = get_open_position()
    if not open_position.get("has_open_position"):
        return RiskDecision(False, "Reversal blocked: no open position exists.")

    if not _is_opposite_option_side(open_position.get("option_side"), payload.option_side):
        return RiskDecision(
            False,
            f"Trade blocked: open position already exists for {open_position.get('trading_symbol')}.",
        )

    max_qty = _setting_int(runtime, "max_qty_per_order", 1)
    if payload.qty > max_qty:
        return RiskDecision(False, "Quantity exceeds MAX_QTY_PER_ORDER.")

    # Trade-count + daily-loss ceilings removed (strategy uses opposite-side
    # Supertrend flips as the natural exit signal).
    return RiskDecision(True, "Opposite option-side reversal checks passed.", final_qty=payload.qty)


def evaluate_exit(payload: NormalizedSignal, runtime: dict | None = None) -> RiskDecision:
    # C11 — Use the caller's snapshot if provided; otherwise read live.
    if runtime is None:
        runtime = get_runtime_settings()
    common = _common_signal_checks(payload)
    if common:
        return common

    if not _setting_bool(runtime, "allow_exit", True):
        return RiskDecision(False, "Exit blocked: ALLOW_EXIT=false.")

    if _setting_bool(runtime, "global_kill_switch", False):
        if GLOBAL_KILL_SWITCH_BLOCKS_EXITS:
            return RiskDecision(False, "Exit blocked: GLOBAL_KILL_SWITCH=true and exits are blocked.")

    open_position = get_open_position()
    if not open_position.get("has_open_position"):
        return RiskDecision(False, "Exit blocked: no open position exists.")

    qty = int(open_position.get("qty") or payload.qty)
    if qty <= 0:
        return RiskDecision(False, "Exit blocked: open position qty is not positive.")

    return RiskDecision(True, "Exit risk checks passed.", final_qty=qty)
