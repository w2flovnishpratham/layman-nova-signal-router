from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.audit_logger import log_audit_event, log_error_event, log_order_event
from app.services.credential_vault import get_dhan_credentials
from app.services.dhan_client import DHAN_OPEN_ORDER_STATUSES, DHAN_TERMINAL_STATUSES, RealDhanClient
from app.services.state_store import (
    clear_open_position,
    default_open_position,
    get_engine_mode,
    get_open_position,
    set_open_position,
    update_app_state,
    utc_now,
)


def _normalized_instance_id(instance_id: str | None) -> str | None:
    if instance_id is None:
        return None
    value = str(instance_id).strip()
    return value or None


def _get_open_position_for_instance(instance_id: str | None) -> dict[str, Any] | None:
    scoped_instance_id = _normalized_instance_id(instance_id)
    if scoped_instance_id is None:
        return get_open_position()
    return get_open_position(instance_id=scoped_instance_id)


def _set_open_position_for_instance(position: dict[str, Any], instance_id: str | None) -> dict[str, Any]:
    scoped_instance_id = _normalized_instance_id(instance_id)
    if scoped_instance_id is None:
        return set_open_position(position)
    return set_open_position(position, instance_id=scoped_instance_id)


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _text(value: Any) -> str:
    return str(value or "").strip().upper()


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_active_dhan_position(row: dict[str, Any]) -> bool:
    net_qty = _number(_pick(row, "netQty", "netQuantity", "net_qty"))
    if net_qty is not None and abs(net_qty) > 0:
        return True

    buy_qty = _number(_pick(row, "buyQty", "buyQuantity", "buy_qty"))
    sell_qty = _number(_pick(row, "sellQty", "sellQuantity", "sell_qty"))
    if buy_qty is not None and sell_qty is not None and abs(buy_qty - sell_qty) > 0:
        return True

    position_type = _text(_pick(row, "positionType", "position_type"))
    return position_type in {"LONG", "SHORT", "OPEN"}


def _is_open_dhan_order(row: dict[str, Any]) -> bool:
    status = _text(_pick(row, "orderStatus", "order_status", "status"))
    if status in DHAN_OPEN_ORDER_STATUSES:
        return True
    if status in DHAN_TERMINAL_STATUSES:
        return False

    remaining_qty = _number(_pick(row, "remainingQuantity", "remainingQty", "pendingQuantity", "pendingQty"))
    if remaining_qty is not None:
        return remaining_qty > 0

    return bool(status)


def _row_matches_local(row: dict[str, Any], local_position: dict[str, Any]) -> bool:
    row_security_id = _text(_pick(row, "securityId", "security_id"))
    local_security_id = _text(local_position.get("security_id"))
    if row_security_id and local_security_id and row_security_id == local_security_id:
        return True

    row_symbol = _text(_pick(row, "tradingSymbol", "trading_symbol"))
    local_symbol = _text(local_position.get("trading_symbol"))
    return bool(row_symbol and local_symbol and row_symbol == local_symbol)


def _row_matches_local_super_order(row: dict[str, Any], local_position: dict[str, Any]) -> bool:
    entry_order_id = _text(local_position.get("entry_order_id"))
    if entry_order_id:
        for key in ("orderId", "order_id", "parentOrderId", "parent_order_id", "superOrderId", "super_order_id"):
            if _text(_pick(row, key)) == entry_order_id:
                return True
    return _row_matches_local(row, local_position)


def _active_waiting_message(position: dict[str, Any], sync: dict[str, Any]) -> str:
    option_side = position.get("option_side") or "position"
    if sync.get("exit_management") == "DHAN_SUPER":
        if sync.get("super_order_status") == "verified":
            return f"Startup verified {option_side}; Dhan Super Order SL/TP legs are still visible."
        return f"Startup verified {option_side}; Dhan Super Order legs need manual confirmation."
    return f"Startup verified {option_side}; server-side exit monitor will resume tracking."


def reconcile_open_position_on_startup(instance_id: str | None = None) -> dict[str, Any]:
    scoped_instance_id = _normalized_instance_id(instance_id)
    checked_at = utc_now()
    if get_engine_mode() == "paper":
        return {"status": "skipped", "reason": "paper_mode", "checked_at": checked_at}
    position = _get_open_position_for_instance(scoped_instance_id) or {}
    if not position.get("has_open_position"):
        return {"status": "skipped", "reason": "no_local_open_position", "checked_at": checked_at}

    if settings.DHAN_MODE.upper() != "REAL":
        sync = {
            "source": "startup_reconciler",
            "status": "skipped",
            "reason": "not_real_mode",
            "checked_at": checked_at,
        }
        _set_open_position_for_instance({**position, "broker_restart_sync": sync}, scoped_instance_id)
        return sync

    creds = get_dhan_credentials()
    if not creds:
        sync = {
            "source": "startup_reconciler",
            "status": "failed",
            "reason": "missing_dhan_credentials",
            "checked_at": checked_at,
        }
        _set_open_position_for_instance({**position, "broker_restart_sync": sync}, scoped_instance_id)
        if scoped_instance_id is None:
            update_app_state(
                state="RESTART_RECONCILE_FAILED",
                last_message="Startup could not verify the tracked open position because Dhan credentials are missing.",
            )
        log_audit_event(
            "STARTUP_OPEN_POSITION_RECONCILE_FAILED",
            "Startup could not verify the tracked open position because Dhan credentials are missing.",
            severity="WARNING",
            metadata={"instance_id": scoped_instance_id, "open_position": position, "broker_restart_sync": sync},
        )
        return sync

    client = RealDhanClient()
    try:
        positions_result = client.get_positions_snapshot(client_id=creds.client_id, access_token=creds.access_token)
        orders_result = client.get_order_book(client_id=creds.client_id, access_token=creds.access_token)
    except Exception as exc:
        sync = {
            "source": "startup_reconciler",
            "status": "failed",
            "reason": "dhan_exception",
            "error": str(exc),
            "checked_at": checked_at,
        }
        _set_open_position_for_instance({**position, "broker_restart_sync": sync}, scoped_instance_id)
        if scoped_instance_id is None:
            update_app_state(
                state="RESTART_RECONCILE_FAILED",
                last_message="Startup could not verify Dhan open position. Keeping local tracker for safety.",
            )
        log_error_event(
            "STARTUP_OPEN_POSITION_RECONCILE_EXCEPTION",
            str(exc),
            metadata={"instance_id": scoped_instance_id, "open_position": position},
        )
        return sync

    failures: list[str] = []
    if not positions_result.success:
        failures.append(positions_result.message)
    if not orders_result.success:
        failures.append(orders_result.message)
    if failures:
        sync = {
            "source": "startup_reconciler",
            "status": "failed",
            "reason": "dhan_verification_failed",
            "failures": failures,
            "checked_at": checked_at,
        }
        _set_open_position_for_instance({**position, "broker_restart_sync": sync}, scoped_instance_id)
        if scoped_instance_id is None:
            update_app_state(
                state="RESTART_RECONCILE_FAILED",
                last_message="Startup could not verify Dhan open position. Keeping local tracker for safety.",
            )
        log_audit_event(
            "STARTUP_OPEN_POSITION_RECONCILE_FAILED",
            "Startup could not verify Dhan open position.",
            severity="WARNING",
            metadata={"instance_id": scoped_instance_id, "open_position": position, "broker_restart_sync": sync},
        )
        return sync

    active_positions = [row for row in positions_result.items if _is_active_dhan_position(row)]
    open_orders = [row for row in orders_result.items if _is_open_dhan_order(row)]
    matching_positions = [row for row in active_positions if _row_matches_local(row, position)]
    matching_orders = [row for row in open_orders if _row_matches_local(row, position)]
    matched = bool(matching_positions or matching_orders)

    if not matched:
        sync = {
            "source": "startup_reconciler",
            "status": "broker_missing_local",
            "message": "Dhan does not show NOVA's tracked open position after restart; local tracker cleared.",
            "checked_at": checked_at,
            "cleared": True,
            "active_positions_count": len(active_positions),
            "open_orders_count": len(open_orders),
        }
        if scoped_instance_id is None:
            set_open_position({**default_open_position(), "broker_restart_sync": sync})
            update_app_state(
                state="WAITING_ENTRY",
                last_message="Startup reconciliation cleared stale local position; Dhan appears flat for NOVA's tracked contract.",
            )
        else:
            clear_open_position(instance_id=scoped_instance_id)
        log_audit_event(
            "STARTUP_STALE_OPEN_POSITION_CLEARED",
            "Startup reconciliation cleared a stale local open position after Dhan verification.",
            severity="WARNING",
            metadata={
                "instance_id": scoped_instance_id,
                "previous_open_position": position,
                "broker_restart_sync": sync,
                "active_positions": active_positions[:5],
                "open_orders": open_orders[:5],
            },
        )
        return sync

    exit_management = _text(position.get("exit_management") or "SERVER")
    super_order_rows = [row for row in open_orders if _row_matches_local_super_order(row, position)]
    sync = {
        "source": "startup_reconciler",
        "status": "verified",
        "checked_at": checked_at,
        "active_positions_count": len(active_positions),
        "open_orders_count": len(open_orders),
        "matched_positions_count": len(matching_positions),
        "matched_orders_count": len(matching_orders),
        "exit_management": exit_management,
        "super_order_status": "not_applicable",
        "super_order_rearmed": False,
    }
    if exit_management == "DHAN_SUPER":
        sync["super_order_status"] = "verified" if super_order_rows else "legs_not_found"
        sync["super_order_rearmed"] = bool(super_order_rows)

    updated = dict(position)
    updated["broker_restart_sync"] = sync
    live_pnl = dict(updated.get("live_pnl") or {})
    live_pnl.update(
        {
            "status": "broker_verified_after_restart",
            "message": _active_waiting_message(updated, sync),
            "exit_management": exit_management,
            "last_checked_at": checked_at,
        }
    )
    updated["live_pnl"] = live_pnl
    _set_open_position_for_instance(updated, scoped_instance_id)
    if scoped_instance_id is None:
        update_app_state(state="WAITING_EXIT", last_message=_active_waiting_message(updated, sync))
    log_audit_event(
        "STARTUP_OPEN_POSITION_VERIFIED",
        "Startup verified NOVA's tracked open position against Dhan.",
        metadata={"instance_id": scoped_instance_id, "open_position": updated, "broker_restart_sync": sync},
    )
    log_order_event(
        {
            "event": "STARTUP_OPEN_POSITION_VERIFIED",
            "status": sync["status"],
            "instance_id": scoped_instance_id,
            "security_id": position.get("security_id"),
            "trading_symbol": position.get("trading_symbol"),
            "exit_management": exit_management,
            "super_order_status": sync["super_order_status"],
            "super_order_rearmed": sync["super_order_rearmed"],
        }
    )
    return sync
