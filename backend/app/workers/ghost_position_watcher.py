"""
GP1-GP3: broker-only position watcher.

This worker polls Dhan read-only position/order endpoints and persists any
broker exposure that NOVA is not tracking locally. It also clears NOVA's local
open-position tracker when the broker no longer shows that position, covering
manual exits from the Dhan app.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import settings
from app.services.audit_logger import log_audit_event, log_error_event, log_order_event
from app.services.credential_vault import get_dhan_credentials
from app.services.dhan_client import DHAN_OPEN_ORDER_STATUSES, DHAN_TERMINAL_STATUSES, RealDhanClient
from app.services.state_store import (
    default_external_positions,
    default_open_position,
    get_app_state,
    get_engine_mode,
    get_external_positions,
    get_open_position,
    set_external_positions,
    set_open_position,
    update_app_state,
    utc_now,
)


logger = logging.getLogger("nova_signal_router.ghost_position_watcher")

POLL_SECONDS = 30.0
EXIT_WAITING_STATES = {"WAITING_EXIT", "EXIT_SIGNAL_RECEIVED", "EXIT_ORDER_SENDING"}
PRICE_DRIFT_TOLERANCE = 0.051

_STOP_EVENT = threading.Event()
_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.RLock()


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _price(value: Any) -> float | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return round(number, 2)


def _text(value: Any) -> str:
    return str(value or "").strip().upper()


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


def _summarize_dhan_position(row: dict[str, Any], *, checked_at: str) -> dict[str, Any]:
    return {
        "source": "dhan_positions",
        "detected_at": checked_at,
        "trading_symbol": _pick(row, "tradingSymbol", "trading_symbol"),
        "security_id": _pick(row, "securityId", "security_id"),
        "net_qty": _pick(row, "netQty", "netQuantity", "net_qty"),
        "position_type": _pick(row, "positionType", "position_type"),
        "product_type": _pick(row, "productType", "product_type"),
        "exchange_segment": _pick(row, "exchangeSegment", "exchange_segment"),
    }


def _summarize_dhan_order(row: dict[str, Any], *, checked_at: str) -> dict[str, Any]:
    return {
        "source": "dhan_orders",
        "detected_at": checked_at,
        "order_id": _pick(row, "orderId", "order_id"),
        "order_status": _pick(row, "orderStatus", "order_status", "status"),
        "trading_symbol": _pick(row, "tradingSymbol", "trading_symbol"),
        "security_id": _pick(row, "securityId", "security_id"),
        "remaining_quantity": _pick(row, "remainingQuantity", "remainingQty", "pendingQuantity", "pendingQty"),
    }


def _row_matches_local(row: dict[str, Any], local_position: dict[str, Any]) -> bool:
    if not local_position.get("has_open_position"):
        return False

    row_security_id = _text(_pick(row, "securityId", "security_id"))
    local_security_id = _text(local_position.get("security_id"))
    if row_security_id and local_security_id and row_security_id == local_security_id:
        return True

    row_symbol = _text(_pick(row, "tradingSymbol", "trading_symbol"))
    local_symbol = _text(local_position.get("trading_symbol"))
    return bool(row_symbol and local_symbol and row_symbol == local_symbol)


def _leg_name(row: dict[str, Any]) -> str:
    return _text(
        _pick(
            row,
            "legName",
            "leg_name",
            "superOrderLegName",
            "super_order_leg_name",
            "orderLeg",
            "order_leg",
            "leg",
        )
    )


def _row_matches_local_super_order(row: dict[str, Any], local_position: dict[str, Any]) -> bool:
    entry_order_id = _text(local_position.get("entry_order_id"))
    if entry_order_id:
        for key in ("orderId", "order_id", "parentOrderId", "parent_order_id", "superOrderId", "super_order_id"):
            if _text(_pick(row, key)) == entry_order_id:
                return True
    return _row_matches_local(row, local_position)


def _extract_leg_price(row: dict[str, Any], leg: str) -> float | None:
    if leg == "stop_loss":
        return _price(
            _pick(
                row,
                "stopLossPrice",
                "stop_loss_price",
                "triggerPrice",
                "trigger_price",
                "price",
            )
        )
    return _price(_pick(row, "targetPrice", "target_price", "price"))


def _detect_sl_tp_drift(
    *,
    local_position: dict[str, Any],
    order_rows: list[dict[str, Any]],
    checked_at: str,
) -> dict[str, Any]:
    base = {
        "status": "not_applicable",
        "drift_detected": False,
        "message": "No Dhan Super Order position is tracked locally.",
        "checked_at": checked_at,
        "expected": {},
        "actual": {},
        "items": [],
    }
    if not local_position.get("has_open_position"):
        return base
    if _text(local_position.get("exit_management")) != "DHAN_SUPER":
        return base

    expected_sl = _price(local_position.get("broker_sl_price"))
    expected_tp = _price(local_position.get("broker_tp_price"))
    base["expected"] = {"stop_loss_price": expected_sl, "target_price": expected_tp}
    if expected_sl is None and expected_tp is None:
        base.update(
            {
                "status": "unknown",
                "message": "NOVA has no recorded broker SL/TP prices to compare.",
            }
        )
        return base

    matching_rows = [row for row in order_rows if _row_matches_local_super_order(row, local_position)]
    actual: dict[str, float | None] = {"stop_loss_price": None, "target_price": None}
    source_rows: dict[str, dict[str, Any]] = {}

    for row in matching_rows:
        leg_name = _leg_name(row)
        if "STOP" in leg_name or "SL" in leg_name:
            price = _extract_leg_price(row, "stop_loss")
            if price is not None:
                actual["stop_loss_price"] = price
                source_rows["stop_loss"] = row
        elif "TARGET" in leg_name or "PROFIT" in leg_name:
            price = _extract_leg_price(row, "target")
            if price is not None:
                actual["target_price"] = price
                source_rows["target"] = row

    items: list[dict[str, Any]] = []
    for leg, expected, actual_value in (
        ("stop_loss", expected_sl, actual["stop_loss_price"]),
        ("target", expected_tp, actual["target_price"]),
    ):
        if expected is None:
            continue
        drift = actual_value is not None and abs(actual_value - expected) > PRICE_DRIFT_TOLERANCE
        row = source_rows.get(leg) or {}
        items.append(
            {
                "leg": leg,
                "expected_price": expected,
                "actual_price": actual_value,
                "drift": drift,
                "order_id": _pick(row, "orderId", "order_id"),
                "leg_name": _pick(row, "legName", "leg_name", "superOrderLegName", "orderLeg", "leg"),
            }
        )

    base.update({"actual": actual, "items": items})
    if not matching_rows or all(item["actual_price"] is None for item in items):
        base.update(
            {
                "status": "not_found",
                "message": "Dhan order book did not expose matching Super Order SL/TP legs.",
            }
        )
        return base

    drift_detected = any(item["drift"] for item in items)
    base.update(
        {
            "status": "drift_detected" if drift_detected else "in_sync",
            "drift_detected": drift_detected,
            "message": (
                "Dhan broker-side SL/TP differs from NOVA recorded levels."
                if drift_detected
                else "Dhan broker-side SL/TP matches NOVA recorded levels."
            ),
        }
    )
    return base


def _snapshot_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            _text(row.get("source")),
            _text(row.get("security_id")),
            _text(row.get("trading_symbol")),
            _text(row.get("order_id")),
            _text(row.get("net_qty") or row.get("remaining_quantity")),
        ]
    )


def _snapshot_keys(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(key for key in (_snapshot_key(row) for row in rows) if key.strip("|"))


def _previous_keys(snapshot: dict[str, Any]) -> list[str]:
    rows: list[dict[str, Any]] = []
    if isinstance(snapshot.get("positions"), list):
        rows.extend(snapshot["positions"])
    if isinstance(snapshot.get("open_orders"), list):
        rows.extend(snapshot["open_orders"])
    return _snapshot_keys(rows)


def _clear_stale_local_position(
    *,
    local_position: dict[str, Any],
    checked_at: str,
    active_positions: list[dict[str, Any]],
    open_orders: list[dict[str, Any]],
    reason: str,
) -> None:
    sync = {
        "source": "ghost_position_watcher",
        "status": "broker_missing_local",
        "message": "Dhan no longer shows NOVA's tracked position; local tracker was cleared.",
        "checked_at": checked_at,
        "cleared": True,
        "active_positions_count": len(active_positions),
        "open_orders_count": len(open_orders),
    }
    set_open_position({**default_open_position(), "broker_sync": sync})

    app_state = get_app_state()
    if app_state.get("state") in EXIT_WAITING_STATES:
        update_app_state(
            state="WAITING_ENTRY",
            last_message="Manual broker exit detected; local open position cleared.",
        )

    log_audit_event(
        "MANUAL_BROKER_EXIT_DETECTED",
        "NOVA's tracked position is no longer visible at Dhan; local open position was cleared.",
        severity="WARNING",
        metadata={
            "reason": reason,
            "previous_open_position": local_position,
            "broker_sync": sync,
            "active_positions": active_positions[:5],
            "open_orders": open_orders[:5],
        },
    )


def _unknown_snapshot(message: str, *, checked_at: str, failures: list[str]) -> dict[str, Any]:
    previous = get_external_positions()
    snapshot = default_external_positions()
    snapshot.update(
        {
            "status": "unknown",
            "message": message,
            "last_checked_at": checked_at,
            "stale": True,
            "positions": previous.get("positions") if isinstance(previous.get("positions"), list) else [],
            "open_orders": previous.get("open_orders") if isinstance(previous.get("open_orders"), list) else [],
            "external_count": int(previous.get("external_count") or 0),
            "sl_tp_drift": previous.get("sl_tp_drift") if isinstance(previous.get("sl_tp_drift"), dict) else {},
            "failures": failures,
        }
    )
    snapshot["broker_active_count"] = len(snapshot["positions"])
    snapshot["broker_open_order_count"] = len(snapshot["open_orders"])
    return snapshot


def sync_ghost_positions_once(*, reason: str = "worker") -> dict[str, Any]:
    checked_at = utc_now()

    if get_engine_mode() == "paper":
        snapshot = default_external_positions()
        snapshot.update(
            {
                "status": "skipped",
                "message": "Broker ghost-position sync is not applicable in Paper mode.",
                "last_checked_at": checked_at,
            }
        )
        return set_external_positions(snapshot)

    if settings.DHAN_MODE.upper() != "REAL":
        snapshot = default_external_positions()
        snapshot.update(
            {
                "status": "skipped",
                "message": "Broker ghost-position sync skipped because DHAN_MODE is not REAL.",
                "last_checked_at": checked_at,
            }
        )
        return set_external_positions(snapshot)

    creds = get_dhan_credentials()
    if not creds:
        snapshot = _unknown_snapshot(
            "Dhan credentials are missing; broker ghost-position sync could not run.",
            checked_at=checked_at,
            failures=["missing_dhan_credentials"],
        )
        return set_external_positions(snapshot)

    previous = get_external_positions()
    client = RealDhanClient()
    try:
        positions_result = client.get_positions_snapshot(client_id=creds.client_id, access_token=creds.access_token)
        orders_result = client.get_order_book(client_id=creds.client_id, access_token=creds.access_token)
    except Exception as exc:
        snapshot = _unknown_snapshot(
            f"Dhan ghost-position sync failed: {exc}",
            checked_at=checked_at,
            failures=[str(exc)],
        )
        log_error_event("GHOST_POSITION_SYNC_EXCEPTION", str(exc), metadata={"reason": reason})
        return set_external_positions(snapshot)

    failures: list[str] = []
    if not positions_result.success:
        failures.append(positions_result.message)
    if not orders_result.success:
        failures.append(orders_result.message)
    if failures:
        snapshot = _unknown_snapshot(
            "Dhan ghost-position sync could not verify broker exposure.",
            checked_at=checked_at,
            failures=failures,
        )
        return set_external_positions(snapshot)

    raw_active_positions = [row for row in positions_result.items if _is_active_dhan_position(row)]
    raw_open_orders = [row for row in orders_result.items if _is_open_dhan_order(row)]
    active_positions = [_summarize_dhan_position(row, checked_at=checked_at) for row in raw_active_positions]
    open_orders = [_summarize_dhan_order(row, checked_at=checked_at) for row in raw_open_orders]

    local_position = get_open_position()
    local_position_present = bool(local_position.get("has_open_position"))
    sl_tp_drift = _detect_sl_tp_drift(
        local_position=local_position,
        order_rows=orders_result.items,
        checked_at=checked_at,
    )
    raw_local_matches = [
        row for row in raw_active_positions + raw_open_orders if _row_matches_local(row, local_position)
    ]
    local_position_matched = bool(raw_local_matches)

    external_positions = [
        item
        for row, item in zip(raw_active_positions, active_positions)
        if not _row_matches_local(row, local_position)
    ]
    external_orders = [
        item
        for row, item in zip(raw_open_orders, open_orders)
        if not _row_matches_local(row, local_position)
    ]
    external_count = len(external_positions) + len(external_orders)

    manual_exit_detected = False
    if local_position_present and not local_position_matched:
        manual_exit_detected = True
        _clear_stale_local_position(
            local_position=local_position,
            checked_at=checked_at,
            active_positions=active_positions,
            open_orders=open_orders,
            reason=reason,
        )

    status = "external_detected" if external_count else "ok"
    message = (
        f"{external_count} broker-only position/order item(s) detected at Dhan."
        if external_count
        else "No broker-only Dhan positions detected."
    )
    snapshot = {
        "status": status,
        "message": message,
        "last_checked_at": checked_at,
        "stale": False,
        "external_count": external_count,
        "positions": external_positions,
        "open_orders": external_orders,
        "broker_active_count": len(active_positions),
        "broker_open_order_count": len(open_orders),
        "local_position_present": local_position_present,
        "local_position_matched": local_position_matched,
        "manual_exit_detected": manual_exit_detected,
        "sl_tp_drift": sl_tp_drift,
        "failures": [],
    }

    previous_keys = _previous_keys(previous)
    current_keys = _previous_keys(snapshot)
    if external_count and current_keys != previous_keys:
        log_audit_event(
            "BROKER_ONLY_POSITION_DETECTED",
            message,
            severity="WARNING",
            metadata={
                "reason": reason,
                "positions": external_positions,
                "open_orders": external_orders,
            },
        )
    elif not external_count and previous_keys:
        log_audit_event(
            "BROKER_ONLY_POSITION_CLEARED",
            "No broker-only Dhan positions remain.",
            metadata={"reason": reason, "previous_keys": previous_keys},
        )

    previous_drift = previous.get("sl_tp_drift") if isinstance(previous.get("sl_tp_drift"), dict) else {}
    if sl_tp_drift.get("drift_detected") and previous_drift.get("status") != sl_tp_drift.get("status"):
        log_audit_event(
            "BROKER_SL_TP_DRIFT_DETECTED",
            str(sl_tp_drift.get("message") or "Dhan broker-side SL/TP drift detected."),
            severity="WARNING",
            metadata={
                "reason": reason,
                "trading_symbol": local_position.get("trading_symbol"),
                "security_id": local_position.get("security_id"),
                "drift": sl_tp_drift,
            },
        )
    elif previous_drift.get("drift_detected") and sl_tp_drift.get("status") == "in_sync":
        log_audit_event(
            "BROKER_SL_TP_DRIFT_CLEARED",
            "Dhan broker-side SL/TP is back in sync with NOVA recorded levels.",
            metadata={"reason": reason, "drift": sl_tp_drift},
        )

    log_order_event(
        {
            "event": "GHOST_POSITION_SYNC",
            "reason": reason,
            "status": status,
            "external_count": external_count,
            "broker_active_count": len(active_positions),
            "broker_open_order_count": len(open_orders),
            "manual_exit_detected": manual_exit_detected,
            "sl_tp_drift_status": sl_tp_drift.get("status"),
            "sl_tp_drift_detected": bool(sl_tp_drift.get("drift_detected")),
        }
    )

    update_app_state(
        ghost_position_alert=(
            {
                "external_count": external_count,
                "last_checked_at": checked_at,
                "message": message,
            }
            if external_count
            else None
        )
    )
    return set_external_positions(snapshot)


def _loop() -> None:
    logger.info("Ghost-position watcher started.")
    while not _STOP_EVENT.is_set():
        try:
            from app.config import settings
            from app.db.engine import database_configured

            if settings.AUTH_REQUIRED and database_configured():
                from app.services.execution_context import bind_user_execution_context
                from app.services.strategy_fanout import (
                    active_routing_user_ids,
                    load_user_context,
                )

                for user_id in active_routing_user_ids(real_orders_only=True):
                    user = load_user_context(user_id)
                    if user is None:
                        continue
                    with bind_user_execution_context(user):
                        sync_ghost_positions_once(reason="multi_user_worker")
            else:
                sync_ghost_positions_once(reason="worker")
        except Exception as exc:  # pragma: no cover
            logger.exception("Ghost-position watcher loop error: %s", exc)
            log_error_event("GHOST_POSITION_WATCHER_EXCEPTION", str(exc))
        _STOP_EVENT.wait(POLL_SECONDS)
    logger.info("Ghost-position watcher stopped.")


def start_ghost_position_watcher() -> None:
    global _THREAD
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP_EVENT.clear()
        _THREAD = threading.Thread(
            target=_loop,
            name="nova-ghost-position-watcher",
            daemon=True,
        )
        _THREAD.start()


def stop_ghost_position_watcher() -> None:
    global _THREAD
    with _THREAD_LOCK:
        _STOP_EVENT.set()
        if _THREAD is not None and _THREAD.is_alive():
            _THREAD.join(timeout=2.0)
        _THREAD = None
