"""
File-backed runtime state for the deployable MVP.

Writes are atomic: data is written to a temporary file in the same directory
and then replaces the target JSON file.
"""
from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.config import DEFAULT_RUNTIME_SETTINGS, RUNTIME_LOG_DIR, RUNTIME_STATE_DIR, settings


_LOCK = threading.RLock()


APP_STATE_FILE = RUNTIME_STATE_DIR / "app_state.json"
OPEN_POSITION_FILE = RUNTIME_STATE_DIR / "open_position.json"
SEEN_SIGNALS_FILE = RUNTIME_STATE_DIR / "seen_signals.json"
SETTINGS_FILE = RUNTIME_STATE_DIR / "settings.json"

LOG_FILES = {
    "webhook": RUNTIME_LOG_DIR / "webhook_events.jsonl",
    "order": RUNTIME_LOG_DIR / "order_events.jsonl",
    "audit": RUNTIME_LOG_DIR / "audit_events.jsonl",
    "error": RUNTIME_LOG_DIR / "errors.jsonl",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_app_state() -> dict[str, Any]:
    return {
        "state": "WAITING_ENTRY",
        "last_signal_id": None,
        "last_alert_at": None,
        "last_message": "Waiting for TradingView entry alert",
        "engine_started": settings.WEBHOOK_TRADING_ENABLED,
        "webhook_trading_enabled": settings.WEBHOOK_TRADING_ENABLED,
        "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
        "dhan_mode": settings.DHAN_MODE.upper(),
        "emergency_stop": DEFAULT_RUNTIME_SETTINGS["emergency_stop"],
        "global_kill_switch": DEFAULT_RUNTIME_SETTINGS["global_kill_switch"],
        "wallet": default_wallet_snapshot(),
    }


def default_open_position() -> dict[str, Any]:
    return {
        "has_open_position": False,
        "strategy_code": None,
        "security_id": None,
        "trading_symbol": None,
        "qty": 0,
        "entry_order_id": None,
        "entry_price": None,
        "opened_at": None,
    }


def default_seen_signals() -> dict[str, Any]:
    return {"signal_ids": []}


def default_settings() -> dict[str, Any]:
    return dict(DEFAULT_RUNTIME_SETTINGS)


def default_wallet_snapshot() -> dict[str, Any]:
    return {
        "success": False,
        "message": "Dhan wallet not checked yet.",
        "client_id": None,
        "available_balance": None,
        "withdrawable_balance": None,
        "utilized_amount": None,
        "sod_limit": None,
        "collateral_amount": None,
        "blocked_payout_amount": None,
        "session_start_balance": None,
        "session_pnl": None,
        "last_checked_at": None,
        "raw_response": None,
    }


def _state_defaults() -> dict[Path, Callable[[], dict[str, Any]]]:
    return {
        APP_STATE_FILE: default_app_state,
        OPEN_POSITION_FILE: default_open_position,
        SEEN_SIGNALS_FILE: default_seen_signals,
        SETTINGS_FILE: default_settings,
    }


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default_factory: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    with _LOCK:
        if not path.exists():
            data = default_factory()
            _atomic_write_json(path, data)
            return deepcopy(data)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = default_factory()
            data["recovered_at"] = utc_now()
            _atomic_write_json(path, data)
            return deepcopy(data)


def _write_json(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        _atomic_write_json(path, data)
        return deepcopy(data)


def init_runtime_files() -> None:
    RUNTIME_STATE_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_LOG_DIR.mkdir(parents=True, exist_ok=True)
    for path, default_factory in _state_defaults().items():
        if not path.exists():
            _atomic_write_json(path, default_factory())
    for path in LOG_FILES.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
    sync_runtime_flags_from_env()


def sync_runtime_flags_from_env() -> None:
    runtime = get_runtime_settings()
    defaults = default_settings()
    changed = False
    for key, value in defaults.items():
        if key not in runtime:
            runtime[key] = value
            changed = True
    if changed:
        set_runtime_settings(runtime)
    app_state = get_app_state()
    engine_started = app_state.get("webhook_trading_enabled")
    if engine_started is None:
        engine_started = settings.WEBHOOK_TRADING_ENABLED
    app_state.update(
        {
            "engine_started": bool(engine_started),
            "webhook_trading_enabled": bool(engine_started),
            "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
            "dhan_mode": settings.DHAN_MODE.upper(),
            "emergency_stop": bool(runtime.get("emergency_stop")),
            "global_kill_switch": bool(runtime.get("global_kill_switch")),
        }
    )
    set_app_state(app_state)


def get_app_state() -> dict[str, Any]:
    data = _read_json(APP_STATE_FILE, default_app_state)
    defaults = default_app_state()
    changed = False
    for key, value in defaults.items():
        if key not in data:
            data[key] = value
            changed = True
    if "wallet" not in data or not isinstance(data["wallet"], dict):
        data["wallet"] = default_wallet_snapshot()
        changed = True
    else:
        wallet_defaults = default_wallet_snapshot()
        for key, value in wallet_defaults.items():
            if key not in data["wallet"]:
                data["wallet"][key] = value
                changed = True
    if changed:
        set_app_state(data)
    return data


def set_app_state(data: dict[str, Any]) -> dict[str, Any]:
    return _write_json(APP_STATE_FILE, data)


def update_app_state(**changes: Any) -> dict[str, Any]:
    state = get_app_state()
    state.update(changes)
    return set_app_state(state)


def get_open_position() -> dict[str, Any]:
    return _read_json(OPEN_POSITION_FILE, default_open_position)


def set_open_position(data: dict[str, Any]) -> dict[str, Any]:
    return _write_json(OPEN_POSITION_FILE, data)


def clear_open_position() -> dict[str, Any]:
    return set_open_position(default_open_position())


def get_seen_signals() -> dict[str, Any]:
    data = _read_json(SEEN_SIGNALS_FILE, default_seen_signals)
    if "signal_ids" not in data or not isinstance(data["signal_ids"], list):
        data = default_seen_signals()
        set_seen_signals(data)
    return data


def set_seen_signals(data: dict[str, Any]) -> dict[str, Any]:
    return _write_json(SEEN_SIGNALS_FILE, data)


def has_seen_signal(signal_id: str) -> bool:
    return signal_id in set(get_seen_signals().get("signal_ids", []))


def add_seen_signal(signal_id: str) -> dict[str, Any]:
    data = get_seen_signals()
    signal_ids = data.setdefault("signal_ids", [])
    if signal_id not in signal_ids:
        signal_ids.append(signal_id)
    return set_seen_signals(data)


def clear_seen_signals() -> dict[str, Any]:
    return set_seen_signals(default_seen_signals())


def get_runtime_settings() -> dict[str, Any]:
    data = _read_json(SETTINGS_FILE, default_settings)
    defaults = default_settings()
    changed = False
    allowed_keys = set(defaults)
    for key in list(data):
        if key not in allowed_keys:
            data.pop(key, None)
            changed = True
    for key, value in defaults.items():
        if key not in data:
            data[key] = value
            changed = True
    if changed:
        set_runtime_settings(data)
    return data


def set_runtime_settings(data: dict[str, Any]) -> dict[str, Any]:
    return _write_json(SETTINGS_FILE, data)


def update_runtime_settings(**changes: Any) -> dict[str, Any]:
    data = get_runtime_settings()
    data.update({key: value for key, value in changes.items() if key in default_settings()})
    saved = set_runtime_settings(data)
    update_app_state(
        emergency_stop=bool(saved.get("emergency_stop")),
        global_kill_switch=bool(saved.get("global_kill_switch")),
    )
    return saved


def reset_to_waiting_entry(message: str = "Waiting for TradingView entry alert") -> None:
    clear_open_position()
    update_app_state(
        state="WAITING_ENTRY",
        last_message=message,
        last_signal_id=None,
        last_alert_at=None,
        emergency_stop=bool(get_runtime_settings().get("emergency_stop")),
        global_kill_switch=bool(get_runtime_settings().get("global_kill_switch")),
    )


def get_wallet_snapshot() -> dict[str, Any]:
    return deepcopy(get_app_state().get("wallet") or default_wallet_snapshot())


def set_wallet_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    wallet = default_wallet_snapshot()
    wallet.update(snapshot)
    update_app_state(wallet=wallet)
    return deepcopy(wallet)


def clear_runtime_logs() -> None:
    for path in LOG_FILES.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


def fresh_runtime_start(*, clear_logs: bool = True) -> None:
    set_app_state(default_app_state())
    clear_open_position()
    clear_seen_signals()
    if clear_logs:
        clear_runtime_logs()
    sync_runtime_flags_from_env()
