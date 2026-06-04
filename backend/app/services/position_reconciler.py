from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.config import settings
from app.services.audit_logger import log_audit_event, log_order_event
from app.services.credential_vault import get_dhan_credentials
from app.services.dhan_client import DHAN_OPEN_ORDER_STATUSES, DHAN_TERMINAL_STATUSES, RealDhanClient
from app.services.state_store import (
    default_open_position,
    get_app_state,
    get_engine_mode,
    get_open_position,
    set_open_position,
    update_app_state,
    utc_now,
)


BROKER_SYNC_TTL_SECONDS = 10
EXIT_WAITING_STATES = {"WAITING_EXIT", "EXIT_SIGNAL_RECEIVED", "EXIT_ORDER_SENDING"}


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


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _recently_synced(position: dict[str, Any]) -> bool:
    sync = position.get("broker_sync")
    if not isinstance(sync, dict):
        return False
    checked_at = _parse_iso(sync.get("checked_at"))
    if checked_at is None:
        return False
    now = _parse_iso(utc_now())
    if now is None:
        return False
    return now - checked_at < timedelta(seconds=BROKER_SYNC_TTL_SECONDS)


def _is_active_dhan_position(row: dict[str, Any]) -> bool:
    net_qty = _number(_pick(row, "netQty", "netQuantity", "net_qty"))
    if net_qty is not None and abs(net_qty) > 0:
        return True

    buy_qty = _number(_pick(row, "buyQty", "buyQuantity", "buy_qty"))
    sell_qty = _number(_pick(row, "sellQty", "sellQuantity", "sell_qty"))
    if buy_qty is not None and sell_qty is not None and abs(buy_qty - sell_qty) > 0:
        return True

    position_type = str(_pick(row, "positionType", "position_type") or "").upper()
    return position_type in {"LONG", "SHORT", "OPEN"}


def _is_open_dhan_order(row: dict[str, Any]) -> bool:
    status = str(_pick(row, "orderStatus", "order_status", "status") or "").upper()
    if status in DHAN_OPEN_ORDER_STATUSES:
        return True
    if status in DHAN_TERMINAL_STATUSES:
        return False

    remaining_qty = _number(_pick(row, "remainingQuantity", "remainingQty", "pendingQuantity", "pendingQty"))
    if remaining_qty is not None:
        return remaining_qty > 0

    # Unknown non-terminal statuses are treated as active in live mode.
    return bool(status)


def _summarize_dhan_position(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "trading_symbol": _pick(row, "tradingSymbol", "trading_symbol"),
        "security_id": _pick(row, "securityId", "security_id"),
        "net_qty": _pick(row, "netQty", "netQuantity", "net_qty"),
        "position_type": _pick(row, "positionType", "position_type"),
        "product_type": _pick(row, "productType", "product_type"),
        "exchange_segment": _pick(row, "exchangeSegment", "exchange_segment"),
    }


def _summarize_dhan_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": _pick(row, "orderId", "order_id"),
        "order_status": _pick(row, "orderStatus", "order_status", "status"),
        "trading_symbol": _pick(row, "tradingSymbol", "trading_symbol"),
        "security_id": _pick(row, "securityId", "security_id"),
        "remaining_quantity": _pick(row, "remainingQuantity", "remainingQty", "pendingQuantity", "pendingQty"),
    }


def _with_sync(position: dict[str, Any], sync: dict[str, Any], *, persist: bool) -> dict[str, Any]:
    updated = {**position, "broker_sync": sync}
    if persist:
        return set_open_position(updated)
    return updated


def _sync_metadata(
    *,
    status: str,
    message: str,
    checked_at: str | None = None,
    cleared: bool = False,
    active_positions: list[dict[str, Any]] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    failures: list[str] | None = None,
) -> dict[str, Any]:
    active_positions = active_positions or []
    open_orders = open_orders or []
    failures = failures or []
    return {
        "source": "dhan",
        "status": status,
        "message": message,
        "checked_at": checked_at or utc_now(),
        "cleared": cleared,
        "active_positions_count": len(active_positions),
        "open_orders_count": len(open_orders),
        "active_positions": active_positions[:3],
        "open_orders": open_orders[:3],
        "failures": failures,
    }


def get_reconciled_open_position(*, force: bool = False, reason: str = "positions") -> dict[str, Any]:
    position = get_open_position()
    if not position.get("has_open_position"):
        return position
    if get_engine_mode() == "paper":
        return position

    if settings.DHAN_MODE.upper() != "REAL":
        return _with_sync(
            position,
            _sync_metadata(
                status="skipped",
                message="Broker sync skipped because Dhan mode is not REAL.",
            ),
            persist=True,
        )

    if not force and _recently_synced(position):
        return position

    creds = get_dhan_credentials()
    if not creds:
        return _with_sync(
            position,
            _sync_metadata(
                status="unknown",
                message="Dhan credentials are missing; keeping local open position.",
                failures=["missing_dhan_credentials"],
            ),
            persist=True,
        )

    checked_at = utc_now()
    client = RealDhanClient()
    try:
        positions = client.get_positions_snapshot(client_id=creds.client_id, access_token=creds.access_token)
        orders = client.get_order_book(client_id=creds.client_id, access_token=creds.access_token)
    except Exception as exc:
        return _with_sync(
            position,
            _sync_metadata(
                status="unknown",
                message=f"Dhan sync failed; keeping local open position. {exc}",
                checked_at=checked_at,
                failures=[str(exc)],
            ),
            persist=True,
        )

    active_positions = [_summarize_dhan_position(row) for row in positions.items if _is_active_dhan_position(row)]
    open_orders = [_summarize_dhan_order(row) for row in orders.items if _is_open_dhan_order(row)]
    failures: list[str] = []
    if not positions.success:
        failures.append(positions.message)
    if not orders.success:
        failures.append(orders.message)

    log_order_event(
        {
            "event": "OPEN_POSITION_DHAN_SYNC",
            "reason": reason,
            "positions_success": positions.success,
            "positions_count": len(positions.items),
            "orders_success": orders.success,
            "orders_count": len(orders.items),
            "active_positions": active_positions[:3],
            "open_orders": open_orders[:3],
            "failures": failures,
        }
    )

    if failures:
        return _with_sync(
            position,
            _sync_metadata(
                status="unknown",
                message="Dhan sync could not verify exposure; keeping local open position.",
                checked_at=checked_at,
                active_positions=active_positions,
                open_orders=open_orders,
                failures=failures,
            ),
            persist=True,
        )

    if active_positions or open_orders:
        return _with_sync(
            position,
            _sync_metadata(
                status="broker_open",
                message="Dhan still shows active position or open order; keeping local open position.",
                checked_at=checked_at,
                active_positions=active_positions,
                open_orders=open_orders,
            ),
            persist=True,
        )

    sync = _sync_metadata(
        status="broker_flat",
        message="Dhan positions and open orders are flat; local open position was cleared.",
        checked_at=checked_at,
        cleared=True,
    )
    cleared_position = {**default_open_position(), "broker_sync": sync}
    set_open_position(cleared_position)

    app_state = get_app_state()
    if app_state.get("state") in EXIT_WAITING_STATES:
        update_app_state(
            state="WAITING_ENTRY",
            last_message="Local open position cleared after Dhan confirmed broker is flat.",
        )

    log_audit_event(
        "OPEN_POSITION_RECONCILED_FLAT",
        "Local open position cleared after Dhan positions/orders returned flat.",
        metadata={"reason": reason, "previous_open_position": position, "broker_sync": sync},
    )
    log_order_event(
        {
            "event": "LOCAL_OPEN_POSITION_CLEARED_AFTER_DHAN_SYNC",
            "reason": reason,
            "previous_open_position": position,
            "broker_sync": sync,
        }
    )
    return cleared_position
