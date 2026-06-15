from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.audit_logger import log_audit_event
from app.services.credential_vault import get_webhook_secret
from app.services.state_store import (
    clear_seen_signals,
    fresh_runtime_start,
    reset_to_waiting_entry,
    update_app_state,
    update_runtime_settings,
    get_open_position,
)
from app.services.execution_router import route_signal
from app.schemas.signal import NormalizedSignal
import time


router = APIRouter()


class ToggleRequest(BaseModel):
    enabled: bool


class FreshStartRequest(BaseModel):
    confirmation: str


@router.post("/emergency-stop")
def emergency_stop() -> dict:
    settings_data = update_runtime_settings(emergency_stop=True)
    update_app_state(state="EMERGENCY_STOPPED", last_message="Emergency stop is active. New entries are blocked.")
    log_audit_event("EMERGENCY_STOP_ON", "Emergency stop enabled from control endpoint.", severity="WARNING")
    return {"ok": True, "settings": settings_data}


@router.post("/resume")
def resume() -> dict:
    settings_data = update_runtime_settings(emergency_stop=False)
    update_app_state(state="WAITING_ENTRY", last_message="Emergency stop cleared. Waiting for TradingView entry alert.")
    log_audit_event("EMERGENCY_STOP_OFF", "Emergency stop disabled from control endpoint.")
    return {"ok": True, "settings": settings_data}


@router.post("/global-kill-switch")
def global_kill_switch(body: ToggleRequest) -> dict:
    settings_data = update_runtime_settings(global_kill_switch=body.enabled)
    state = "GLOBAL_KILL_SWITCH_ACTIVE" if body.enabled else "WAITING_ENTRY"
    message = "Global kill switch is active." if body.enabled else "Global kill switch cleared."
    update_app_state(state=state, last_message=message)
    log_audit_event(
        "GLOBAL_KILL_SWITCH_CHANGED",
        f"Global kill switch set to {body.enabled}.",
        severity="WARNING" if body.enabled else "INFO",
    )
    return {"ok": True, "settings": settings_data}


@router.post("/reset-state")
def reset_state() -> dict:
    reset_to_waiting_entry("Runtime state reset. Waiting for TradingView entry alert.")
    log_audit_event("RUNTIME_STATE_RESET", "Open position cleared and app state reset.")
    return {"ok": True}


@router.post("/fresh-start")
def fresh_start(body: FreshStartRequest) -> dict:
    if body.confirmation.strip() != "FRESH START":
        raise HTTPException(status_code=400, detail="Type FRESH START to confirm this destructive action.")
    open_position = get_open_position()
    if open_position.get("has_open_position"):
        return {"ok": False, "message": "Fresh start skipped because an open position is tracked."}
    fresh_runtime_start(clear_logs=True)
    log_audit_event("FRESH_START", "Runtime state and displayed logs cleared.")
    return {"ok": True}


@router.post("/clear-seen-signals")
def clear_seen() -> dict:
    clear_seen_signals()
    log_audit_event("SEEN_SIGNALS_CLEARED", "Duplicate signal memory cleared.")
    return {"ok": True}


@router.post("/pause-entries")
def pause_entries() -> dict:
    settings_data = update_runtime_settings(allow_entry=False)
    log_audit_event("ALLOW_ENTRY_DISABLED", "Pause Entries enabled.")
    return {"ok": True, "settings": settings_data}


@router.post("/resume-entries")
def resume_entries() -> dict:
    settings_data = update_runtime_settings(allow_entry=True)
    log_audit_event("ALLOW_ENTRY_ENABLED", "Pause Entries disabled / entries resumed.")
    return {"ok": True, "settings": settings_data}


@router.post("/panic-exit")
def panic_exit() -> dict:
    open_position = get_open_position()
    if not open_position.get("has_open_position"):
        return {"ok": False, "message": "No open position exists."}

    # Create exit signal using open position data
    signal = NormalizedSignal(
        payload_format="NOVA",
        secret=get_webhook_secret() or "",
        signal_id=f"PANIC_EXIT_{int(time.time())}",
        strategy_code=open_position.get("strategy_code") or "MANUAL",
        action="EXIT",
        side="SELL",
        symbol=open_position.get("trading_symbol") or "MANUAL",
        security_id=open_position.get("security_id"),
        trading_symbol=open_position.get("trading_symbol"),
        qty=open_position.get("qty") or 1,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload={"manual_panic_exit": True}
    )

    log_audit_event("PANIC_EXIT_TRIGGERED", "Panic exit triggered from control endpoint.")
    execution_result = route_signal(signal)

    return {
        "ok": True,
        "message": execution_result.get("message") or "Panic exit flow completed.",
        "execution_result": execution_result
    }
