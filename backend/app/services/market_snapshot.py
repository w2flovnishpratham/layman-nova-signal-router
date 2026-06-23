from __future__ import annotations

from typing import Any

from app.services.atm_ltp_service import get_atm_option_snapshot
from app.services.risk_manager import _market_is_open
from app.services.state_store import get_app_state, get_open_position, utc_now


def build_nifty_snapshot(*, allow_rest_fallback: bool = True) -> dict[str, Any]:
    app_state = get_app_state()
    position = get_open_position()
    latest_signal = app_state.get("last_signal") if isinstance(app_state.get("last_signal"), dict) else {}
    live_pnl = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
    active_ltp = _number(live_pnl.get("ltp") or position.get("entry_price"))
    nifty_spot = _number(latest_signal.get("niftyPrice") or latest_signal.get("niftySpot"))
    atm = get_atm_option_snapshot(option_side="BOTH", lots=1, allow_rest_fallback=allow_rest_fallback)
    if atm.get("niftySpot") is not None:
        nifty_spot = _number(atm.get("niftySpot"))
    return {
        "niftySpot": nifty_spot,
        "niftySpotSource": atm.get("niftySpotSource"),
        "dayChangePct": _number(latest_signal.get("dayChangePct")),
        "marketStatus": "open" if _market_is_open() else "closed",
        "lastUpdatedAt": utc_now(),
        "atm": atm,
        "latestSignal": {
            "signalId": latest_signal.get("signalId") or app_state.get("last_signal_id"),
            "action": latest_signal.get("action"),
            "side": latest_signal.get("side"),
            "optionSide": latest_signal.get("optionSide"),
            "receivedAt": latest_signal.get("receivedAt") or app_state.get("last_alert_at"),
        },
        "activeOptionLtp": active_ltp if position.get("has_open_position") else None,
        "activeOptionSide": position.get("option_side") if position.get("has_open_position") else None,
        "sparkline": _sparkline_from_position(active_ltp, live_pnl),
        "markers": _markers(latest_signal),
    }


def _sparkline_from_position(active_ltp: float | None, live_pnl: dict[str, Any]) -> list[float]:
    history = live_pnl.get("ltp_history")
    if isinstance(history, list):
        values = [_number(item) for item in history[-24:]]
        return [float(item) for item in values if item is not None]
    return [float(active_ltp)] if active_ltp is not None else []


def _markers(latest_signal: dict[str, Any]) -> list[dict[str, Any]]:
    action = latest_signal.get("action")
    if not action:
        return []
    return [
        {
            "side": action,
            "optionSide": latest_signal.get("optionSide"),
            "timestamp": latest_signal.get("receivedAt"),
        }
    ]


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
