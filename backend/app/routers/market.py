from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.services.atm_ltp_service import get_atm_option_snapshot
from app.services.credential_vault import dhan_metadata
from app.services.execution_context import current_execution_user
from app.services.risk_manager import _market_is_open
from app.services.state_store import get_app_state, get_engine_mode, get_open_position, get_runtime_settings, get_wallet_snapshot, utc_now
from app.workers.strategy_job_worker import strategy_job_worker_status


router = APIRouter()


@router.get("/market/nifty-snapshot")
def nifty_snapshot() -> dict[str, Any]:
    app_state = get_app_state()
    position = get_open_position()
    latest_signal = app_state.get("last_signal") if isinstance(app_state.get("last_signal"), dict) else {}
    live_pnl = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
    active_ltp = _number(live_pnl.get("ltp") or position.get("entry_price"))
    nifty_spot = _number(latest_signal.get("niftyPrice") or latest_signal.get("niftySpot"))
    atm = get_atm_option_snapshot(option_side="BOTH", lots=1, allow_rest_fallback=True)
    if atm.get("niftySpot") is not None:
        nifty_spot = _number(atm.get("niftySpot"))
    sparkline = _sparkline_from_position(active_ltp, live_pnl)
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
        "sparkline": sparkline,
        "markers": _markers(latest_signal),
    }


@router.get("/market/atm-ltp")
def atm_ltp(
    side: str = Query(default="BOTH", pattern="^(CE|PE|BOTH)$"),
    lots: int = Query(default=1, ge=1, le=20),
) -> dict[str, Any]:
    return get_atm_option_snapshot(option_side=side, lots=lots, allow_rest_fallback=True)


@router.get("/system/health-strip")
def system_health_strip() -> dict[str, Any]:
    app_state = get_app_state()
    runtime = get_runtime_settings()
    dhan = dhan_metadata()
    worker = strategy_job_worker_status()
    return {
        "dhan": _dhan_status(dhan),
        "staticIp": _static_ip_status(),
        "market": "open" if _market_is_open() else "closed",
        "engine": "paused" if not bool(runtime.get("allow_entry", True)) else "listening" if app_state.get("webhook_trading_enabled") else "idle",
        "pubsub": "healthy" if worker.get("running") else "delayed" if worker.get("enabled") else "unknown",
        "lastSignalAt": (app_state.get("last_signal") or {}).get("receivedAt") if isinstance(app_state.get("last_signal"), dict) else app_state.get("last_alert_at"),
        "walletOk": bool(get_wallet_snapshot().get("success")),
    }


def _dhan_status(meta: dict[str, Any]) -> str:
    if not meta.get("connected"):
        return "unknown"
    if meta.get("token_expired") is True:
        return "auth_issue"
    return "connected"


def _static_ip_status() -> str:
    user = current_execution_user()
    if user is None or user.is_dev:
        return "unknown"
    try:
        from app.services.strategy_fanout import get_user_egress

        egress = get_user_egress(user.id)
    except Exception:
        return "failed"
    if not egress:
        return "unknown"
    return "verified" if egress.get("verified") else "failed"


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
