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
from zoneinfo import ZoneInfo

from app.config import DEFAULT_RUNTIME_SETTINGS, RUNTIME_LOG_DIR, RUNTIME_STATE_DIR, settings


_LOCK = threading.RLock()


APP_STATE_FILE = RUNTIME_STATE_DIR / "app_state.json"
OPEN_POSITION_FILE = RUNTIME_STATE_DIR / "open_position.json"
PAPER_POSITION_FILE = RUNTIME_STATE_DIR / "paper_position.json"
OPEN_POSITIONS_BY_INSTANCE_FILE = RUNTIME_STATE_DIR / "open_positions_by_instance.json"
PAPER_PORTFOLIO_FILE = RUNTIME_STATE_DIR / "paper_portfolio.json"
EXTERNAL_POSITIONS_FILE = RUNTIME_STATE_DIR / "external_positions.json"
SEEN_SIGNALS_FILE = RUNTIME_STATE_DIR / "seen_signals.json"
SETTINGS_FILE = RUNTIME_STATE_DIR / "settings.json"

LOG_FILES = {
    "webhook": RUNTIME_LOG_DIR / "webhook_events.jsonl",
    "order": RUNTIME_LOG_DIR / "order_events.jsonl",
    "audit": RUNTIME_LOG_DIR / "audit_events.jsonl",
    "error": RUNTIME_LOG_DIR / "errors.jsonl",
    "paper_orders": RUNTIME_LOG_DIR / "paper_orders.jsonl",
}


def scoped_runtime_path(path: Path) -> Path:
    """Map runtime state/log files into the active user's private directory."""
    from app.services.execution_context import current_execution_user
    from app.services.user_context import user_runtime_log_dir, user_runtime_state_dir

    user = current_execution_user()
    if user is None or user.is_dev:
        return path

    state_dir = user_runtime_state_dir(user)
    log_dir = user_runtime_log_dir(user)
    try:
        path.relative_to(state_dir)
    except ValueError:
        pass
    else:
        return path
    try:
        path.relative_to(log_dir)
    except ValueError:
        pass
    else:
        return path

    try:
        relative = path.relative_to(RUNTIME_STATE_DIR)
    except ValueError:
        pass
    else:
        return state_dir / relative

    try:
        relative = path.relative_to(RUNTIME_LOG_DIR)
    except ValueError:
        return path
    return log_dir / relative


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_app_state() -> dict[str, Any]:
    return {
        "state": "WAITING_ENTRY",
        "last_signal_id": None,
        "last_alert_at": None,
        "last_message": "Waiting for TradingView entry alert",
        "engine_started": False,
        "webhook_trading_enabled": False,
        "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
        "dhan_mode": settings.DHAN_MODE.upper(),
        "engine_mode": None,
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
        "live_pnl": None,
    }


# C7 — Seen-signals TTL. Without a TTL, signal_ids accumulates forever and
# every webhook serialises a list that grows linearly with usage. Schema
# stays backward-compatible: we still expose "signal_ids" (a list) for any
# legacy reader, but also persist "added_at_by_id" for prune logic.
SEEN_SIGNAL_TTL_SECONDS = 24 * 60 * 60  # 24 hours


def default_external_positions() -> dict[str, Any]:
    return {
        "status": "not_checked",
        "message": "Dhan external-position watcher has not checked broker state yet.",
        "last_checked_at": None,
        "stale": False,
        "external_count": 0,
        "positions": [],
        "open_orders": [],
        "broker_active_count": 0,
        "broker_open_order_count": 0,
        "local_position_present": False,
        "local_position_matched": False,
        "manual_exit_detected": False,
        "sl_tp_drift": {
            "status": "not_checked",
            "drift_detected": False,
            "message": "Dhan SL/TP drift has not been checked yet.",
            "checked_at": None,
            "expected": {},
            "actual": {},
            "items": [],
        },
        "failures": [],
    }


def default_seen_signals() -> dict[str, Any]:
    return {"signal_ids": [], "added_at_by_id": {}}


def default_daily_risk() -> dict[str, Any]:
    return {
        "date_ist": datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
        "entry_count": 0,
        "last_entry_signal_id": None,
        "updated_at": utc_now(),
    }


def _daily_risk_file() -> Path:
    return APP_STATE_FILE.parent / "daily_risk.json"


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
        # R1 — IST date stamp on the session_start_balance. When the wallet
        # refresher sees a new IST date, it resets session_start_balance to
        # the current available balance and zeroes session_pnl — so
        # session_pnl reflects today's intraday P&L (kept for display).
        "session_start_date_ist": None,
        "last_checked_at": None,
        "raw_response": None,
    }


def _state_defaults() -> dict[Path, Callable[[], dict[str, Any]]]:
    return {
        APP_STATE_FILE: default_app_state,
        OPEN_POSITION_FILE: default_open_position,
        PAPER_POSITION_FILE: default_open_position,
        EXTERNAL_POSITIONS_FILE: default_external_positions,
        SEEN_SIGNALS_FILE: default_seen_signals,
        SETTINGS_FILE: default_settings,
        _daily_risk_file(): default_daily_risk,
    }


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path = scoped_runtime_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default_factory: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    path = scoped_runtime_path(path)
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
    path = scoped_runtime_path(path)
    with _LOCK:
        _atomic_write_json(path, data)
        return deepcopy(data)


def _get_open_positions_by_instance_path() -> Path:
    return scoped_runtime_path(OPEN_POSITIONS_BY_INSTANCE_FILE)


def _read_open_positions_by_instance() -> dict[str, Any]:
    path = _get_open_positions_by_instance_path()
    with _LOCK:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            _write_open_positions_by_instance({})
            return {}
        if not isinstance(data, dict):
            _write_open_positions_by_instance({})
            return {}
        return deepcopy(data)


def _write_open_positions_by_instance(data: dict[str, Any]) -> dict[str, Any]:
    return _write_json(OPEN_POSITIONS_BY_INSTANCE_FILE, data)


def init_runtime_files() -> None:
    scoped_runtime_path(RUNTIME_STATE_DIR).mkdir(parents=True, exist_ok=True)
    scoped_runtime_path(RUNTIME_LOG_DIR).mkdir(parents=True, exist_ok=True)
    for path, default_factory in _state_defaults().items():
        if not scoped_runtime_path(path).exists():
            _atomic_write_json(path, default_factory())
    for path in LOG_FILES.values():
        path = scoped_runtime_path(path)
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


def get_open_position(instance_id: str | None = None) -> dict[str, Any] | None:
    """Return the tracked position for the currently selected engine mode."""
    if instance_id is not None:
        return deepcopy(_read_open_positions_by_instance().get(instance_id))
    if get_engine_mode() == "paper":
        return get_paper_position()
    return get_live_open_position()


def get_live_open_position() -> dict[str, Any]:
    return _read_json(OPEN_POSITION_FILE, default_open_position)


def set_open_position(position: dict[str, Any], instance_id: str | None = None) -> dict[str, Any]:
    """Persist the tracked position for the currently selected engine mode."""
    if instance_id is not None:
        with _LOCK:
            positions = _read_open_positions_by_instance()
            positions[instance_id] = deepcopy(position)
            _write_open_positions_by_instance(positions)
            return deepcopy(positions[instance_id])
    if get_engine_mode() == "paper":
        return set_paper_position(position)
    return set_live_open_position(position)


def set_live_open_position(data: dict[str, Any]) -> dict[str, Any]:
    return _write_json(OPEN_POSITION_FILE, data)


def clear_open_position(instance_id: str | None = None) -> dict[str, Any] | None:
    if instance_id is not None:
        with _LOCK:
            positions = _read_open_positions_by_instance()
            if instance_id not in positions:
                return None
            positions.pop(instance_id, None)
            _write_open_positions_by_instance(positions)
            return None
    if get_engine_mode() == "paper":
        return clear_paper_position()
    return clear_live_open_position()


def list_open_positions_by_instance() -> dict[str, Any]:
    return _read_open_positions_by_instance()


def clear_live_open_position() -> dict[str, Any]:
    return set_live_open_position(default_open_position())


def get_paper_position() -> dict[str, Any]:
    return _read_json(PAPER_POSITION_FILE, default_open_position)


def set_paper_position(data: dict[str, Any]) -> dict[str, Any]:
    return _write_json(PAPER_POSITION_FILE, data)


def clear_paper_position() -> dict[str, Any]:
    return set_paper_position(default_open_position())


def get_engine_mode(*, legacy_fallback: bool = True) -> str | None:
    mode = str(get_app_state().get("engine_mode") or "").strip().lower()
    if mode in {"paper", "live"}:
        return mode
    if legacy_fallback:
        return "live" if settings.DHAN_MODE.upper() == "REAL" else "paper"
    return None


def set_engine_mode(mode: str | None) -> dict[str, Any]:
    normalized = str(mode or "").strip().lower() or None
    if normalized not in {None, "paper", "live"}:
        raise ValueError("engine_mode must be paper, live, or null.")

    current_mode = get_engine_mode(legacy_fallback=False)
    if current_mode != normalized:
        if get_live_open_position().get("has_open_position") or get_paper_position().get("has_open_position"):
            raise ValueError("Cannot switch engine mode while a position is open.")
        if bool(get_app_state().get("webhook_trading_enabled")):
            raise ValueError("Stop the engine before switching mode.")
    return update_app_state(engine_mode=normalized)


def get_external_positions() -> dict[str, Any]:
    data = _read_json(EXTERNAL_POSITIONS_FILE, default_external_positions)
    defaults = default_external_positions()
    changed = False
    for key, value in defaults.items():
        if key not in data:
            data[key] = value
            changed = True
    if not isinstance(data.get("positions"), list):
        data["positions"] = []
        changed = True
    if not isinstance(data.get("open_orders"), list):
        data["open_orders"] = []
        changed = True
    if changed:
        set_external_positions(data)
    return data


def set_external_positions(data: dict[str, Any]) -> dict[str, Any]:
    return _write_json(EXTERNAL_POSITIONS_FILE, data)


def clear_external_positions() -> dict[str, Any]:
    return set_external_positions(default_external_positions())


def _prune_expired_seen_signals(data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Drop entries whose added_at is older than SEEN_SIGNAL_TTL_SECONDS.

    Returns (pruned_data, pruned_count). Tolerant of legacy data that has
    only signal_ids and no added_at_by_id — those legacy entries get a
    fresh timestamp on first prune so they survive one TTL window from the
    moment we first see them, then expire normally.
    """
    signal_ids = data.get("signal_ids") or []
    added_at_by_id = data.get("added_at_by_id") or {}
    now = datetime.now(timezone.utc)
    cutoff_epoch = now.timestamp() - SEEN_SIGNAL_TTL_SECONDS

    keep_ids: list[str] = []
    keep_map: dict[str, str] = {}
    pruned = 0
    for sid in signal_ids:
        added_at_str = added_at_by_id.get(sid)
        if added_at_str:
            try:
                added_at = datetime.fromisoformat(str(added_at_str).replace("Z", "+00:00"))
                if added_at.tzinfo is None:
                    added_at = added_at.replace(tzinfo=timezone.utc)
                if added_at.timestamp() < cutoff_epoch:
                    pruned += 1
                    continue
                keep_ids.append(sid)
                keep_map[sid] = added_at_str
            except (ValueError, TypeError):
                # Malformed timestamp — reset it so future prunes work.
                keep_ids.append(sid)
                keep_map[sid] = now.isoformat()
        else:
            # Legacy entry without timestamp: stamp it now, keep it.
            keep_ids.append(sid)
            keep_map[sid] = now.isoformat()

    return {"signal_ids": keep_ids, "added_at_by_id": keep_map}, pruned


def get_seen_signals() -> dict[str, Any]:
    data = _read_json(SEEN_SIGNALS_FILE, default_seen_signals)
    if "signal_ids" not in data or not isinstance(data["signal_ids"], list):
        data = default_seen_signals()
        set_seen_signals(data)
        return data
    # Prune-on-read keeps both in-memory and on-disk size bounded.
    pruned_data, pruned_count = _prune_expired_seen_signals(data)
    if pruned_count > 0 or pruned_data != data:
        set_seen_signals(pruned_data)
        return pruned_data
    return data


def set_seen_signals(data: dict[str, Any]) -> dict[str, Any]:
    return _write_json(SEEN_SIGNALS_FILE, data)


def has_seen_signal(signal_id: str) -> bool:
    return signal_id in set(get_seen_signals().get("signal_ids", []))


def add_seen_signal(signal_id: str) -> dict[str, Any]:
    data = get_seen_signals()
    signal_ids = data.setdefault("signal_ids", [])
    added_at_by_id = data.setdefault("added_at_by_id", {})
    if signal_id not in signal_ids:
        signal_ids.append(signal_id)
    added_at_by_id[signal_id] = datetime.now(timezone.utc).isoformat()
    return set_seen_signals(data)


def clear_seen_signals() -> dict[str, Any]:
    return set_seen_signals(default_seen_signals())


def get_daily_risk() -> dict[str, Any]:
    path = _daily_risk_file()
    data = _read_json(path, default_daily_risk)
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    if data.get("date_ist") != today:
        data = default_daily_risk()
        _write_json(path, data)
    return data


def record_entry_trade(signal_id: str) -> dict[str, Any]:
    data = get_daily_risk()
    data["entry_count"] = int(data.get("entry_count") or 0) + 1
    data["last_entry_signal_id"] = signal_id
    data["updated_at"] = utc_now()
    return _write_json(_daily_risk_file(), data)


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
    try:
        poll_seconds = float(data.get("option_ltp_poll_seconds") or 0)
    except (TypeError, ValueError):
        poll_seconds = 0
    if poll_seconds < 1:
        data["option_ltp_poll_seconds"] = defaults["option_ltp_poll_seconds"]
        changed = True
    if changed:
        set_runtime_settings(data)
    return data


class SettingsVersionMismatch(Exception):
    """H8 — Raised when an optimistic-locking save sees a stale version."""

    def __init__(self, expected: int, current: int, current_settings: dict[str, Any]):
        super().__init__(
            f"Settings version mismatch: caller expected {expected}, current is {current}."
        )
        self.expected = expected
        self.current = current
        self.current_settings = current_settings


def set_runtime_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Write settings; auto-increments _version so concurrent writers can
    detect lost updates. Always increments by +1 from the previous on-disk
    value (not from `data._version`) so the version is owned by the store,
    never by the caller."""
    with _LOCK:
        existing = _read_json(SETTINGS_FILE, default_settings)
        try:
            current_version = int(existing.get("_version", 0))
        except (TypeError, ValueError):
            current_version = 0
        data = dict(data)
        data["_version"] = current_version + 1
        return _write_json(SETTINGS_FILE, data)


def set_runtime_settings_if_version(
    data: dict[str, Any],
    expected_version: int,
) -> dict[str, Any]:
    """H8 — Optimistic-locking save. Writes only if the caller's
    `expected_version` matches the current on-disk version. Raises
    `SettingsVersionMismatch` otherwise."""
    with _LOCK:
        existing = _read_json(SETTINGS_FILE, default_settings)
        try:
            current_version = int(existing.get("_version", 0))
        except (TypeError, ValueError):
            current_version = 0
        if int(expected_version) != current_version:
            raise SettingsVersionMismatch(
                expected=int(expected_version),
                current=current_version,
                current_settings=existing,
            )
        data = dict(data)
        data["_version"] = current_version + 1
        return _write_json(SETTINGS_FILE, data)


def update_runtime_settings_if_version(
    expected_version: int,
    **changes: Any,
) -> dict[str, Any]:
    """H8 — Version-checked variant of update_runtime_settings."""
    data = get_runtime_settings()
    data.update({
        key: value for key, value in changes.items()
        if key in default_settings() and key != "_version"
    })
    saved = set_runtime_settings_if_version(data, expected_version=expected_version)
    update_app_state(
        emergency_stop=bool(saved.get("emergency_stop")),
        global_kill_switch=bool(saved.get("global_kill_switch")),
    )
    return saved


def update_runtime_settings(**changes: Any) -> dict[str, Any]:
    data = get_runtime_settings()
    # H8 — _version is owned by set_runtime_settings; callers can never set it.
    data.update({
        key: value for key, value in changes.items()
        if key in default_settings() and key != "_version"
    })
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
    if get_engine_mode(legacy_fallback=False) == "paper":
        from app.services.paper_portfolio import paper_wallet_snapshot

        return paper_wallet_snapshot()
    return deepcopy(get_app_state().get("wallet") or default_wallet_snapshot())


def set_wallet_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    wallet = default_wallet_snapshot()
    wallet.update(snapshot)
    update_app_state(wallet=wallet)
    return deepcopy(wallet)


def clear_runtime_logs() -> None:
    for path in LOG_FILES.values():
        path = scoped_runtime_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


def fresh_runtime_start(*, clear_logs: bool = True) -> None:
    from app.services.paper_portfolio import reset_paper_portfolio

    set_app_state(default_app_state())
    clear_live_open_position()
    clear_paper_position()
    reset_paper_portfolio()
    clear_external_positions()
    clear_seen_signals()
    _write_json(_daily_risk_file(), default_daily_risk())
    if clear_logs:
        clear_runtime_logs()
    sync_runtime_flags_from_env()
