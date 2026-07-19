"""Server-authoritative runtime lifecycle and reliability controls.

The existing per-user JSON runtime remains the execution authority. This
module serializes lifecycle mutations for each owner, exposes one hydrated
status contract, and records append-only P&L checkpoints without introducing
a second engine or position authority.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DEFAULT_NIFTY_LOT_SIZE, settings
from app.schemas.signal import NormalizedSignal
from app.services import user_credential_vault
from app.services.audit_logger import log_audit_event
from app.services.credential_vault import mask_client_id
from app.services.execution_router import route_signal
from app.services.paper_portfolio import get_paper_portfolio, reset_paper_portfolio
from app.services.state_store import (
    LOG_FILES,
    clear_paper_position,
    get_app_state,
    get_engine_mode,
    get_live_open_position,
    get_mode_runtime_settings,
    get_open_position,
    get_paper_position,
    scoped_runtime_path,
    set_engine_mode,
    set_runtime_settings,
    update_app_state,
    utc_now,
)
from app.services.user_context import CurrentUser


_LOCKS_GUARD = threading.Lock()
_OWNER_LOCKS: dict[str, threading.RLock] = {}
LTP_STALE_AFTER_SECONDS = 15.0


def _owner_lock(user: CurrentUser) -> threading.RLock:
    key = user.id_str
    with _LOCKS_GUARD:
        return _OWNER_LOCKS.setdefault(key, threading.RLock())


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _token_status(user: CurrentUser) -> dict[str, Any]:
    status = user_credential_vault.user_credential_status(user.id)
    saved_at = _parse_time(status.get("dhan_token_saved_at"))
    age_minutes: float | None = None
    expired: bool | None = None
    estimated_expiry_at: str | None = None
    if saved_at is not None:
        now = datetime.now(timezone.utc)
        age_minutes = round((now - saved_at.astimezone(timezone.utc)).total_seconds() / 60, 1)
        max_age_minutes = float(settings.TOKEN_MAX_AGE_HOURS) * 60
        expired = age_minutes >= max_age_minutes
        estimated_expiry_at = (
            saved_at.timestamp() + max_age_minutes * 60
        )
        estimated_expiry_at = datetime.fromtimestamp(estimated_expiry_at, timezone.utc).isoformat()
    return {
        **status,
        "token_age_minutes": age_minutes,
        "token_expired": expired,
        "token_estimated_expiry_at": estimated_expiry_at,
    }


def _position_status(position: dict[str, Any]) -> dict[str, Any]:
    live_pnl = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
    checked_at = _parse_time(live_pnl.get("last_checked_at"))
    age_seconds: float | None = None
    if checked_at is not None:
        age_seconds = max(
            0.0,
            round(
                (datetime.now(timezone.utc) - checked_at.astimezone(timezone.utc)).total_seconds(),
                2,
            ),
        )
    ltp = live_pnl.get("ltp")
    quote_status = str(live_pnl.get("status") or ("ready" if ltp is not None else "unavailable"))
    stale = bool(
        ltp is not None
        and (age_seconds is None or age_seconds > LTP_STALE_AFTER_SECONDS)
    )
    if stale:
        quote_status = "stale"
    return {
        "has_open_position": bool(position.get("has_open_position")),
        "security_id": position.get("security_id"),
        "trading_symbol": position.get("trading_symbol"),
        "option_side": position.get("option_side"),
        "qty": position.get("qty") or 0,
        "lots": (
            round(float(position.get("qty") or 0) / DEFAULT_NIFTY_LOT_SIZE, 4)
            if position.get("qty")
            else 0
        ),
        "entry_price": position.get("entry_price"),
        "opened_at": position.get("opened_at"),
        "unrealized_pnl": live_pnl.get("unrealized_pnl"),
        "ltp": {
            "value": ltp,
            "source": live_pnl.get("source"),
            "status": quote_status,
            "received_at": live_pnl.get("last_checked_at"),
            "age_seconds": age_seconds,
            "stale": stale,
            "message": live_pnl.get("message"),
        },
    }


def _pnl_status(mode: str | None, position: dict[str, Any]) -> dict[str, Any]:
    live_pnl = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
    unrealized = live_pnl.get("unrealized_pnl")
    if mode == "paper":
        portfolio = get_paper_portfolio()
        return {
            "realized": portfolio.realized_pnl,
            "unrealized": unrealized,
            "session": round(float(portfolio.realized_pnl) + float(unrealized or 0), 2),
            "available_balance": portfolio.available_balance,
        }
    wallet = get_app_state().get("wallet") or {}
    return {
        "realized": wallet.get("session_pnl"),
        "unrealized": unrealized,
        "session": (
            round(float(wallet.get("session_pnl") or 0) + float(unrealized or 0), 2)
            if wallet.get("session_pnl") is not None or unrealized is not None
            else None
        ),
        "available_balance": wallet.get("available_balance"),
    }


def runtime_status(user: CurrentUser) -> dict[str, Any]:
    app_state = get_app_state()
    mode = get_engine_mode(legacy_fallback=False)
    position = get_open_position() if mode else (
        get_paper_position()
        if get_paper_position().get("has_open_position")
        else get_live_open_position()
    )
    lifecycle = str(
        app_state.get("engine_lifecycle")
        or ("RUNNING" if app_state.get("webhook_trading_enabled") else "STOPPED")
    ).upper()
    accepting = bool(app_state.get("webhook_trading_enabled")) and lifecycle == "RUNNING"
    position_view = _position_status(position)
    if position_view["has_open_position"] and lifecycle == "STOPPED":
        display = "POSITION OPEN — ENGINE STOPPED"
    elif position_view["has_open_position"] and lifecycle == "STOPPING":
        display = "EXIT PENDING — ENGINE STOPPING"
    else:
        display = lifecycle
    return {
        "ok": True,
        "owner_user_id": user.id_str,
        "engine": {
            "state": lifecycle,
            "running": lifecycle == "RUNNING",
            "accepting_signals": accepting,
            "mode": mode,
            "display": display,
            "last_transition_at": app_state.get("last_lifecycle_transition_at"),
        },
        "exit": {
            "state": str(app_state.get("exit_state") or "NONE").upper(),
            "operation_id": app_state.get("exit_operation_id"),
            "requested_at": app_state.get("exit_requested_at"),
        },
        "position": position_view,
        "pnl": _pnl_status(mode, position),
        "config": {
            "active": get_mode_runtime_settings(mode or "paper"),
            "paper": get_mode_runtime_settings("paper"),
            "live": get_mode_runtime_settings("live"),
        },
        "account": _token_status(user),
        "safety": {
            "dhan_mode": settings.DHAN_MODE.upper(),
            "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
        },
    }


def mark_starting() -> None:
    update_app_state(
        engine_lifecycle="STARTING",
        accepting_signals=False,
        last_lifecycle_transition_at=utc_now(),
    )


def mark_running() -> None:
    update_app_state(
        engine_lifecycle="RUNNING",
        engine_started=True,
        webhook_trading_enabled=True,
        accepting_signals=True,
        exit_state="NONE",
        exit_operation_id=None,
        exit_requested_at=None,
        last_lifecycle_transition_at=utc_now(),
    )


def mark_start_failed() -> None:
    update_app_state(
        engine_lifecycle="STOPPED",
        engine_started=False,
        webhook_trading_enabled=False,
        accepting_signals=False,
        last_lifecycle_transition_at=utc_now(),
    )


def _append_snapshot(user: CurrentUser, *, reason: str, final: bool) -> dict[str, Any]:
    status = runtime_status(user)
    snapshot = {
        "id": str(uuid.uuid4()),
        "owner_user_id": user.id_str,
        "reason": reason,
        "final": final,
        "created_at": utc_now(),
        "mode": status["engine"]["mode"],
        "engine_state": status["engine"]["state"],
        "position_open": status["position"]["has_open_position"],
        "pnl": status["pnl"],
        "ltp": status["position"]["ltp"],
    }
    configured = LOG_FILES.get("runtime_snapshots")
    if configured is None:
        configured = LOG_FILES["audit"].parent / "runtime_snapshots.jsonl"
    path: Path = scoped_runtime_path(configured)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _owner_lock(user):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, sort_keys=True) + "\n")
    return snapshot


def stop_engine(user: CurrentUser, *, reason: str = "manual_stop") -> dict[str, Any]:
    with _owner_lock(user):
        update_app_state(
            state="ENGINE_STOPPING",
            engine_lifecycle="STOPPING",
            engine_started=False,
            webhook_trading_enabled=False,
            accepting_signals=False,
            last_message="Engine stopping. New entries are blocked.",
            last_lifecycle_transition_at=utc_now(),
        )
        update_app_state(
            state="ENGINE_STOPPED",
            engine_lifecycle="STOPPED",
            engine_started=False,
            webhook_trading_enabled=False,
            accepting_signals=False,
            last_message="Engine stopped. Tracked positions remain monitored.",
            last_lifecycle_transition_at=utc_now(),
        )
        _append_snapshot(user, reason=reason, final=True)
        log_audit_event(
            "ENGINE_STOPPED",
            "Webhook trading disabled; tracked position was preserved.",
            severity="WARNING",
            metadata={"reason": reason, "position_open": get_open_position().get("has_open_position")},
        )
        return runtime_status(user)


def checkpoint(user: CurrentUser, *, reason: str) -> dict[str, Any]:
    return _append_snapshot(user, reason=reason, final=False)


def configure_mode(
    user: CurrentUser,
    *,
    mode: str,
    lots: int,
    stop_loss_percent: float,
    target_profit_percent: float,
) -> dict[str, Any]:
    normalized = str(mode).strip().lower()
    if normalized not in {"paper", "live"}:
        raise ValueError("mode must be paper or live.")
    if not 1 <= int(lots) <= 20:
        raise ValueError("lots must be between 1 and 20.")
    if not 0 <= float(stop_loss_percent) <= 100:
        raise ValueError("stop_loss_percent must be between 0 and 100.")
    if not 0 <= float(target_profit_percent) <= 1000:
        raise ValueError("target_profit_percent must be between 0 and 1000.")
    with _owner_lock(user):
        if get_app_state().get("webhook_trading_enabled"):
            raise ValueError("Stop the engine before changing configuration.")
        if get_live_open_position().get("has_open_position") or get_paper_position().get("has_open_position"):
            raise ValueError("Configuration cannot change while a tracked position is open.")
        current_mode = get_engine_mode(legacy_fallback=False)
        if current_mode != normalized:
            set_engine_mode(normalized)
        data = get_mode_runtime_settings(normalized)
        data.update(
            configured_lots=int(lots),
            option_sl_percent=float(stop_loss_percent),
            option_tp_percent=float(target_profit_percent),
            option_disable_sl=float(stop_loss_percent) <= 0,
        )
        set_runtime_settings(data)
        log_audit_event(
            "ENGINE_CONFIG_UPDATED",
            f"{normalized.title()} configuration updated while stopped.",
            metadata={
                "mode": normalized,
                "lots": int(lots),
                "stop_loss_percent": float(stop_loss_percent),
                "target_profit_percent": float(target_profit_percent),
            },
        )
        return runtime_status(user)


def switch_mode(user: CurrentUser, mode: str) -> dict[str, Any]:
    with _owner_lock(user):
        if get_app_state().get("webhook_trading_enabled"):
            raise ValueError("Stop the engine before switching mode.")
        set_engine_mode(mode)
        return runtime_status(user)


def reset_paper_session(user: CurrentUser, starting_balance: float | None = None) -> dict[str, Any]:
    with _owner_lock(user):
        if get_app_state().get("webhook_trading_enabled"):
            raise ValueError("Stop the engine before resetting Paper.")
        if get_paper_position().get("has_open_position"):
            raise ValueError("Square off the Paper position before resetting Paper.")
        if str(get_app_state().get("exit_state") or "NONE").upper() == "EXIT_PENDING":
            raise ValueError("Paper reset is blocked while an exit is pending.")
        _append_snapshot(user, reason="paper_reset", final=True)
        clear_paper_position()
        reset_paper_portfolio(starting_balance)
        log_audit_event("PAPER_SESSION_RESET", "Paper session reset after a final snapshot.")
        return runtime_status(user)


def _tracked_exit_signal(operation_id: str, position: dict[str, Any]) -> NormalizedSignal:
    return NormalizedSignal(
        payload_format="NOVA",
        secret="",
        signal_id=operation_id,
        strategy_code=position.get("strategy_code") or "MANUAL",
        action="EXIT",
        side="SELL",
        symbol=position.get("trading_symbol") or position.get("symbol") or "MANUAL",
        security_id=position.get("security_id"),
        trading_symbol=position.get("trading_symbol"),
        option_side=position.get("option_side"),
        strike=position.get("strike"),
        expiry=position.get("expiry"),
        qty=max(int(position.get("qty") or 1), 1),
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload={
            "runtime_square_off": True,
            "entry_price": position.get("entry_price"),
            "live_pnl": position.get("live_pnl"),
        },
    )


def square_off(user: CurrentUser) -> dict[str, Any]:
    with _owner_lock(user):
        # Block new signals immediately, but do not publish STOPPED while
        # exposure still exists. STOPPED is a confirmed-flat terminal state for
        # this combined action.
        update_app_state(
            state="SQUARE_OFF_PENDING",
            engine_lifecycle="STOPPING",
            engine_started=False,
            webhook_trading_enabled=False,
            accepting_signals=False,
            last_message="Square-off requested. New signals blocked; awaiting confirmed flat.",
            last_lifecycle_transition_at=utc_now(),
        )
        _append_snapshot(user, reason="square_off_requested", final=False)
        position = get_open_position()
        if not position.get("has_open_position"):
            update_app_state(
                state="ENGINE_STOPPED",
                engine_lifecycle="STOPPED",
                engine_started=False,
                webhook_trading_enabled=False,
                accepting_signals=False,
                exit_state="CONFIRMED_FLAT",
                exit_operation_id=None,
                exit_requested_at=utc_now(),
                last_message="Square-off confirmed. Engine stopped and flat.",
                last_lifecycle_transition_at=utc_now(),
            )
            _append_snapshot(user, reason="square_off_already_flat", final=True)
            return runtime_status(user)

        app_state = get_app_state()
        operation_id = app_state.get("exit_operation_id")
        if (
            str(app_state.get("exit_state") or "").upper()
            in {"EXIT_PENDING", "RECONCILIATION_REQUIRED"}
            and operation_id
        ):
            # A retry observes the existing operation. It must never submit a
            # second broker order while the first result is unresolved or its
            # broker outcome requires reconciliation.
            return runtime_status(user)

        operation_id = f"RUNTIME_EXIT_{uuid.uuid4().hex}"
        update_app_state(
            exit_state="EXIT_PENDING",
            exit_operation_id=operation_id,
            exit_requested_at=utc_now(),
            state="EXIT_ORDER_SENDING",
        )
        result = route_signal(_tracked_exit_signal(operation_id, position))
        flat = not get_open_position().get("has_open_position")
        if flat:
            update_app_state(
                state="ENGINE_STOPPED",
                engine_lifecycle="STOPPED",
                engine_started=False,
                webhook_trading_enabled=False,
                accepting_signals=False,
                exit_state="CONFIRMED_FLAT",
                last_message="Square-off confirmed. Engine stopped and flat.",
                last_lifecycle_transition_at=utc_now(),
            )
            _append_snapshot(user, reason="square_off_confirmed", final=True)
        elif result.get("success") and result.get("status") in {
            "REVERSAL_EXIT_PENDING",
            "PARTIAL_EXIT_FILLED",
        }:
            update_app_state(exit_state="EXIT_PENDING")
        else:
            update_app_state(
                engine_lifecycle="STOPPING",
                exit_state="RECONCILIATION_REQUIRED",
                last_message="Exit outcome requires reconciliation; engine is not confirmed stopped.",
            )
        return {**runtime_status(user), "execution_result": result}


def refresh_account(user: CurrentUser) -> dict[str, Any]:
    if settings.DHAN_MODE.upper() != "REAL":
        return {
            "ok": False,
            "status": "UNAVAILABLE_IN_MOCK",
            "message": "Broker account refresh is unavailable while DHAN_MODE=MOCK.",
            "engine": runtime_status(user)["engine"],
            "account": _token_status(user),
        }
    creds = user_credential_vault.get_user_dhan_credentials(user.id)
    if creds is None:
        return {
            "ok": False,
            "status": "CREDENTIALS_REQUIRED",
            "message": "Connect Dhan credentials before refreshing account data.",
            "engine": runtime_status(user)["engine"],
            "account": _token_status(user),
        }
    from app.services.dhan_client import RealDhanClient

    result = RealDhanClient().get_fund_limit(
        client_id=creds.client_id,
        access_token=creds.access_token,
    )
    return {
        "ok": result.success,
        "status": "READY" if result.success else "AUTH_OR_BROKER_ERROR",
        "message": result.message,
        "account": {
            **_token_status(user),
            "dhan_client_id_masked": mask_client_id(creds.client_id),
            "available_balance": result.available_balance,
            "withdrawable_balance": result.withdrawable_balance,
            "utilized_amount": result.utilized_amount,
        },
        "engine": runtime_status(user)["engine"],
    }
