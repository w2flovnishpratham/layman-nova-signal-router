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
from app.services.audit_logger import read_jsonl
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


def _entry_trades_today() -> int:
    today = datetime.now(timezone.utc).date()
    count = 0
    for event in read_jsonl("order", limit=5000):
        if event.get("action") != "ENTRY" or event.get("phase") != "after_response":
            continue
        if event.get("blocked") is True or event.get("success") is False:
            continue
        timestamp = event.get("timestamp")
        if not timestamp:
            continue
        try:
            if datetime.fromisoformat(timestamp).date() == today:
                count += 1
        except ValueError:
            continue
    return count


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


def evaluate_entry(payload: NormalizedSignal) -> RiskDecision:
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

    max_trades = _setting_int(runtime, "max_trades_per_day", 1)
    trades_today = _entry_trades_today()
    if trades_today >= max_trades:
        return RiskDecision(False, f"Trade blocked: MAX_TRADES_PER_DAY reached ({trades_today}/{max_trades}).")

    loss_limit = float(runtime.get("daily_loss_limit", 500))
    realized_pnl_today = 0.0
    if realized_pnl_today <= -abs(loss_limit):
        return RiskDecision(False, f"Trade blocked: DAILY_LOSS_LIMIT reached ({realized_pnl_today}).")

    return RiskDecision(True, "All entry risk checks passed.", final_qty=payload.qty)


def evaluate_exit(payload: NormalizedSignal) -> RiskDecision:
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
