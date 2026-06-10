from __future__ import annotations

import logging
import threading
import time
from typing import Any

from app.config import DEFAULT_EXCHANGE_SEGMENT, DEFAULT_ORDER_TYPE, DEFAULT_PRODUCT_TYPE, DISABLED_OPTION_SL_PRICE_FRACTION
from app.schemas.signal import NormalizedSignal
from app.services.audit_logger import log_audit_event, log_error_event, log_order_event
from app.services.chat_event_publisher import publish_active_trade_from_sync, publish_tick_pnl_from_sync
from app.services.credential_vault import get_dhan_credentials, get_webhook_secret
from app.services.dhan_client import get_broker_client
from app.services.dhan_marketfeed_ws import (
    clear_marketfeed_subscription,
    ensure_marketfeed_subscription,
    get_marketfeed_ltp,
    marketfeed_ws_status,
    stop_marketfeed_ws,
)
from app.services.state_store import get_engine_mode, get_open_position, get_runtime_settings, set_open_position, utc_now


logger = logging.getLogger("option_position_monitor")

_STOP_EVENT = threading.Event()
_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.RLock()

EXIT_COOLDOWN_SECONDS = 15.0
_LAST_REST_FALLBACK_AT: dict[tuple[str, str], float] = {}


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_positive_float(value: Any, default: float) -> float:
    parsed = _as_float(value, default)
    if parsed is None or parsed <= 0:
        return default
    return parsed


def _as_int(value: Any, default: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _runtime_bool(runtime: dict[str, Any], key: str, default: bool) -> bool:
    value = runtime.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _monitor_should_run(runtime: dict[str, Any]) -> bool:
    return get_engine_mode() in {"paper", "live"}


def _poll_seconds(runtime: dict[str, Any]) -> float:
    value = _as_positive_float(runtime.get("option_ltp_poll_seconds"), 1.0)
    return max(1.0, value)


def _ws_stale_seconds(runtime: dict[str, Any]) -> float:
    value = _as_positive_float(runtime.get("option_ws_stale_seconds"), 5.0)
    return max(1.0, value)


def _ltp_source(runtime: dict[str, Any]) -> str:
    source = str(runtime.get("option_ltp_source") or "AUTO").strip().upper()
    return source if source in {"WEBSOCKET", "REST", "AUTO"} else "AUTO"


def _rest_fallback_allowed(runtime: dict[str, Any], exchange_segment: str, security_id: str) -> bool:
    if _ltp_source(runtime) == "REST":
        return True
    if not _runtime_bool(runtime, "option_rest_fallback_enabled", True):
        return False
    cooldown = _as_positive_float(runtime.get("option_rest_fallback_cooldown_seconds"), 15.0)
    key = (exchange_segment, security_id)
    last_at = _LAST_REST_FALLBACK_AT.get(key)
    if last_at is not None and time.time() - last_at < cooldown:
        return False
    _LAST_REST_FALLBACK_AT[key] = time.time()
    return True


def _exit_levels(entry_price: float, runtime: dict[str, Any]) -> tuple[float, float, float, float]:
    sl_percent = _as_positive_float(runtime.get("option_sl_percent"), 10.0)
    tp_percent = _as_positive_float(runtime.get("option_tp_percent"), 20.0)
    sl_price = round(entry_price * (1 - sl_percent / 100), 2)
    tp_price = round(entry_price * (1 + tp_percent / 100), 2)
    return sl_percent, tp_percent, sl_price, tp_price


def _level_value(levels: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _as_float(levels.get(key))
        if value is not None and value > 0:
            return value
    return None


def _active_exit_levels(position: dict[str, Any]) -> tuple[float, float] | None:
    levels = position.get("active_exit_levels")
    if not isinstance(levels, dict):
        return None
    sl_price = _level_value(levels, "stopLossPrice", "stop_loss_price", "sl")
    tp_price = _level_value(levels, "targetPrice", "target_price", "tp")
    if sl_price is None or tp_price is None:
        return None
    return sl_price, tp_price


def _broker_managed_exit(position: dict[str, Any]) -> bool:
    return str(position.get("exit_management") or "").upper() == "DHAN_SUPER"


def _display_exit_levels(position: dict[str, Any], entry_price: float, runtime: dict[str, Any]) -> tuple[float, float, float, float]:
    sl_percent, tp_percent, sl_price, tp_price = _exit_levels(entry_price, runtime)
    active_levels = _active_exit_levels(position)
    if active_levels is not None:
        sl_price, tp_price = active_levels
        sl_percent = max(round(((entry_price - sl_price) / entry_price) * 100, 2), 0.0)
        tp_percent = max(round(((tp_price - entry_price) / entry_price) * 100, 2), 0.0)
        return sl_percent, tp_percent, sl_price, tp_price
    if _runtime_bool(runtime, "option_disable_sl", True):
        sl_price = max(0.10, round(entry_price * DISABLED_OPTION_SL_PRICE_FRACTION, 2))
    if _broker_managed_exit(position):
        sl_price = _as_float(position.get("broker_sl_price"), sl_price) or sl_price
        tp_price = _as_float(position.get("broker_tp_price"), tp_price) or tp_price
    return sl_percent, tp_percent, sl_price, tp_price


def _pnl_snapshot(
    *,
    position: dict[str, Any],
    ltp: float,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    status: str,
    exit_reason: str | None = None,
    source: str = "dhan_marketfeed_ws",
    quote_age_seconds: float | None = None,
) -> dict[str, Any]:
    qty = _as_int(position.get("qty"), 0)
    unrealized_pnl = round((ltp - entry_price) * qty, 2)
    pnl_percent = round(((ltp - entry_price) / entry_price) * 100, 2) if entry_price > 0 else None
    return {
        "source": source,
        "status": status,
        "exit_management": str(position.get("exit_management") or "SERVER").upper(),
        "entry_price": entry_price,
        "ltp": ltp,
        "qty": qty,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "unrealized_pnl": unrealized_pnl,
        "pnl_percent": pnl_percent,
        "exit_reason": exit_reason,
        "quote_age_seconds": quote_age_seconds,
        "last_checked_at": utc_now(),
    }


def _with_live_pnl(position: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    updated = dict(position)
    updated["live_pnl"] = snapshot
    return set_open_position(updated)


def _client() -> Any:
    return get_broker_client(get_engine_mode())


def _ltp_error_snapshot(*, quote: Any, status: str, ws_status: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "source": getattr(quote, "source", "dhan_marketfeed_ws"),
        "status": status,
        "message": getattr(quote, "message", "LTP unavailable."),
        "error": getattr(quote, "error", None),
        "ltp": getattr(quote, "ltp", None),
        "last_checked_at": utc_now(),
        "ws_status": ws_status,
    }


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _entry_price_from_order_book(order_id: str, items: list[dict[str, Any]]) -> tuple[float | None, str | None]:
    for row in items:
        row_order_id = str(_pick(row, "orderId", "order_id", "id") or "")
        if row_order_id != str(order_id):
            continue
        price = _as_float(_pick(row, "avgPrice", "averageTradedPrice", "avgTradedPrice", "tradedPrice", "TradedPrice"))
        if price is not None and price > 0:
            return price, str(_pick(row, "orderStatus", "order_status", "status") or "ORDER_BOOK").upper()
    return None, None


def _entry_price_from_positions(position: dict[str, Any], items: list[dict[str, Any]]) -> tuple[float | None, str | None]:
    security_id = str(position.get("security_id") or "").strip()
    for row in items:
        row_security_id = str(_pick(row, "securityId", "security_id") or "").strip()
        if security_id and row_security_id != security_id:
            continue

        price = _as_float(
            _pick(
                row,
                "buyAvg",
                "buyAverage",
                "buyAvgPrice",
                "buyAveragePrice",
                "averagePrice",
                "costPrice",
                "netAvg",
                "netAveragePrice",
            )
        )
        if price is not None and price > 0:
            return price, "POSITIONS"

        buy_value = _as_float(_pick(row, "dayBuyValue", "buyValue", "day_buy_value"))
        buy_qty = _as_float(_pick(row, "dayBuyQty", "buyQty", "buyQuantity", "day_buy_qty"))
        if buy_value is not None and buy_qty is not None and buy_qty > 0:
            return round(buy_value / buy_qty, 2), "POSITIONS"
    return None, None


def _broker_entry_price_fallback(
    position: dict[str, Any],
    client: Any,
    client_id: str,
    access_token: str,
) -> tuple[float | None, str | None, str | None]:
    order_id = str(position.get("entry_order_id") or "")
    try:
        orders = client.get_order_book(client_id=client_id, access_token=access_token)
        if orders.success:
            price, source_status = _entry_price_from_order_book(order_id, orders.items)
            if price is not None:
                return price, "dhan_order_book", source_status

        positions = client.get_positions_snapshot(client_id=client_id, access_token=access_token)
        if positions.success:
            price, source_status = _entry_price_from_positions(position, positions.items)
            if price is not None:
                return price, "dhan_positions", source_status
    except Exception as exc:
        return None, "dhan_broker_snapshot", str(exc)
    return None, None, None


def _sync_entry_fill_if_needed(position: dict[str, Any], client: Any, client_id: str, access_token: str) -> dict[str, Any]:
    if _as_float(position.get("entry_price")) is not None:
        return position

    order_id = position.get("entry_order_id")
    if not order_id:
        return position

    poll = client.poll_order_status(
        client_id=client_id,
        access_token=access_token,
        order_id=str(order_id),
        max_polls=1,
        poll_delay=0,
    )
    avg_price = poll.avg_price if poll.is_filled and poll.avg_price is not None else None
    fill_source = "dhan_order_status"
    source_status = poll.order_status
    if avg_price is None:
        fallback_price, fallback_source, fallback_status = _broker_entry_price_fallback(position, client, client_id, access_token)
        if fallback_price is not None:
            avg_price = fallback_price
            fill_source = fallback_source or fill_source
            source_status = fallback_status or source_status

    if avg_price is None:
        updated = dict(position)
        updated["live_pnl"] = {
            "source": "dhan_order_status",
            "status": "waiting_entry_fill",
            "message": "Waiting for Dhan to confirm entry fill price.",
            "entry_order_id": order_id,
            "last_checked_at": utc_now(),
            "order_status": poll.order_status,
            "error": poll.error,
        }
        return set_open_position(updated)

    updated = dict(position)
    updated["entry_price"] = avg_price
    updated["entry_fill_synced_at"] = utc_now()
    updated["entry_fill_source"] = fill_source
    if _broker_managed_exit(updated) and not updated.get("super_order_post_fill_update"):
        try:
            from app.services.execution_router import _sync_super_order_exit_levels

            levels, sync_result = _sync_super_order_exit_levels(
                client=client,
                client_id=client_id,
                access_token=access_token,
                order_id=str(order_id),
                fill_price=avg_price,
                runtime=get_runtime_settings(),
                current_levels=updated.get("broker_exit_levels"),
            )
            if levels:
                updated["broker_exit_levels"] = levels
                updated["broker_sl_price"] = levels.get("stop_loss_price")
                updated["broker_tp_price"] = levels.get("target_price")
            if sync_result:
                updated["super_order_post_fill_update"] = sync_result
        except Exception as exc:
            updated["super_order_post_fill_update"] = {
                "attempted": True,
                "success": False,
                "error": str(exc),
                "checked_at": utc_now(),
            }
    log_order_event(
        {
            "event": "ENTRY_FILL_PRICE_SYNCED",
            "entry_order_id": order_id,
            "avg_price": avg_price,
            "source": fill_source,
            "source_status": source_status,
            "trading_symbol": position.get("trading_symbol"),
            "security_id": position.get("security_id"),
        }
    )
    return set_open_position(updated)


def _exit_cooldown_active(position: dict[str, Any]) -> bool:
    server_exit = position.get("server_exit")
    if not isinstance(server_exit, dict):
        return False
    last_attempt_at = _as_float(server_exit.get("last_attempt_epoch"))
    if last_attempt_at is None:
        return False
    return (time.time() - last_attempt_at) < EXIT_COOLDOWN_SECONDS


def _mark_exit_attempt(position: dict[str, Any], reason: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    updated = dict(position)
    updated["live_pnl"] = snapshot
    updated["server_exit"] = {
        "exit_in_progress": True,
        "last_attempt_at": utc_now(),
        "last_attempt_epoch": time.time(),
        "reason": reason,
    }
    return set_open_position(updated)


def _unlock_exit_attempt(position: dict[str, Any], result: dict[str, Any]) -> None:
    current = get_open_position()
    if not current.get("has_open_position"):
        return
    server_exit = current.get("server_exit")
    if not isinstance(server_exit, dict):
        server_exit = {}
    server_exit.update(
        {
            "exit_in_progress": False,
            "last_result": {
                "status": result.get("status"),
                "success": result.get("success"),
                "blocked": result.get("blocked"),
                "reason": result.get("reason") or result.get("error"),
                "checked_at": utc_now(),
            },
        }
    )
    updated = dict(current)
    updated["server_exit"] = server_exit
    set_open_position(updated)


def _exit_signal(position: dict[str, Any], reason: str, snapshot: dict[str, Any]) -> NormalizedSignal:
    return NormalizedSignal(
        payload_format="NOVA",
        secret=get_webhook_secret() or "",
        signal_id=f"SERVER_EXIT_{reason}_{int(time.time())}_{position.get('security_id') or 'UNKNOWN'}",
        strategy_code=position.get("strategy_code") or "TRADINGVIEW_NIFTY_V1",
        action="EXIT",
        side="SELL",
        symbol=position.get("symbol") or "NIFTY",
        instrument_type=position.get("instrument_type") or "OPTIDX",
        exchange_segment=position.get("exchange_segment") or DEFAULT_EXCHANGE_SEGMENT,
        security_id=position.get("security_id"),
        trading_symbol=position.get("trading_symbol"),
        option_side=position.get("option_side"),
        strike=position.get("strike"),
        expiry=position.get("expiry"),
        qty=_as_int(position.get("qty"), 1),
        order_type=position.get("order_type") or DEFAULT_ORDER_TYPE,
        product_type=position.get("product_type") or DEFAULT_PRODUCT_TYPE,
        source="server_side_option_monitor",
        raw_payload={
            "server_side_exit": True,
            "exit_reason": reason,
            "live_pnl": snapshot,
        },
    )


def _route_server_exit(position: dict[str, Any], reason: str, snapshot: dict[str, Any]) -> None:
    if _exit_cooldown_active(position):
        return

    marked = _mark_exit_attempt(position, reason, snapshot)
    log_audit_event(
        "SERVER_SIDE_OPTION_EXIT_TRIGGERED",
        f"Server-side option {reason} triggered from Dhan LTP.",
        severity="WARNING",
        metadata={
            "reason": reason,
            "trading_symbol": marked.get("trading_symbol"),
            "security_id": marked.get("security_id"),
            "live_pnl": snapshot,
        },
    )
    log_order_event(
        {
            "event": "SERVER_SIDE_OPTION_EXIT_TRIGGERED",
            "reason": reason,
            "trading_symbol": marked.get("trading_symbol"),
            "security_id": marked.get("security_id"),
            "live_pnl": snapshot,
        }
    )

    try:
        from app.services.execution_router import route_signal

        result = route_signal(_exit_signal(marked, reason, snapshot))
    except Exception as exc:
        log_error_event(
            "SERVER_SIDE_OPTION_EXIT_FAILED",
            f"Server-side option exit routing failed: {exc}",
            metadata={"reason": reason, "position": marked, "live_pnl": snapshot},
        )
        _unlock_exit_attempt(marked, {"success": False, "status": "ERROR", "error": str(exc)})
        return

    if result.get("success"):
        log_audit_event(
            "SERVER_SIDE_OPTION_EXIT_SENT",
            f"Server-side option {reason} exit order sent to Dhan.",
            metadata={
                "reason": reason,
                "order_id": result.get("order_id"),
                "status": result.get("status"),
                "live_pnl": snapshot,
            },
        )
        return

    _unlock_exit_attempt(marked, result)


def monitor_once() -> None:
    runtime = get_runtime_settings()
    if not _monitor_should_run(runtime):
        return

    position = get_open_position()
    if not position.get("has_open_position"):
        clear_marketfeed_subscription()
        return

    creds = get_dhan_credentials()
    if not creds:
        return

    security_id = str(position.get("security_id") or "").strip()
    exchange_segment = str(position.get("exchange_segment") or DEFAULT_EXCHANGE_SEGMENT).upper()
    if not security_id:
        return

    client = _client()
    entry_price_before_sync = _as_float(position.get("entry_price"))
    position = _sync_entry_fill_if_needed(position, client, creds.client_id, creds.access_token)
    entry_price = _as_float(position.get("entry_price"))
    if entry_price is None or entry_price <= 0:
        return
    if entry_price_before_sync is None:
        publish_active_trade_from_sync(position, get_engine_mode())

    source = _ltp_source(runtime)
    quote: Any = None
    if source in {"WEBSOCKET", "AUTO"} and _runtime_bool(runtime, "marketfeed_ws_enabled", True):
        ensure_marketfeed_subscription(exchange_segment=exchange_segment, security_id=security_id)
        quote = get_marketfeed_ltp(
            exchange_segment=exchange_segment,
            security_id=security_id,
            max_age_seconds=_ws_stale_seconds(runtime),
        )

    if quote is None or not quote.success:
        if _rest_fallback_allowed(runtime, exchange_segment, security_id):
            quote = client.get_ltp(
                client_id=creds.client_id,
                access_token=creds.access_token,
                exchange_segment=exchange_segment,
                security_id=security_id,
            )
        else:
            current = dict(get_open_position())
            if current.get("has_open_position"):
                current["live_pnl"] = _ltp_error_snapshot(
                    quote=quote,
                    status="ws_waiting" if quote is None or quote.error == "ws_tick_missing" else "ws_stale",
                    ws_status=marketfeed_ws_status(),
                )
                set_open_position(current)
            return

    if not quote.success or quote.ltp is None:
        current = dict(get_open_position())
        if current.get("has_open_position"):
            current["live_pnl"] = {
                "source": getattr(quote, "source", "dhan_marketfeed_ltp"),
                "status": "ltp_error",
                "message": quote.message,
                "error": quote.error,
                "last_checked_at": utc_now(),
                "ws_status": marketfeed_ws_status(),
            }
            set_open_position(current)
        log_order_event(
            {
                "event": "OPTION_LTP_FETCH_FAILED",
                "message": quote.message,
                "error": quote.error,
                "security_id": security_id,
                "exchange_segment": exchange_segment,
                "source": getattr(quote, "source", "unknown"),
            }
        )
        return

    _sl_percent, _tp_percent, sl_price, tp_price = _display_exit_levels(position, entry_price, runtime)
    ltp = float(quote.ltp)
    exit_reason: str | None = None
    status = "tracking"
    if ltp <= sl_price:
        exit_reason = "SL"
        status = "sl_hit"
    elif ltp >= tp_price:
        exit_reason = "TP"
        status = "tp_hit"

    snapshot = _pnl_snapshot(
        position=position,
        ltp=ltp,
        entry_price=entry_price,
        sl_price=sl_price,
        tp_price=tp_price,
        status=status,
        exit_reason=exit_reason,
        source=getattr(quote, "source", "dhan_marketfeed_ltp"),
        quote_age_seconds=getattr(quote, "age_seconds", None),
    )
    updated = _with_live_pnl(position, snapshot)
    publish_tick_pnl_from_sync(
        symbol=str(position.get("trading_symbol") or position.get("symbol") or "NIFTY option"),
        security_id=security_id,
        ltp=ltp,
        pnl=snapshot["unrealized_pnl"],
        pnl_pct=snapshot["pnl_percent"],
        mode=get_engine_mode(),
    )

    accepted_position_levels = _active_exit_levels(updated) is not None
    if (
        exit_reason
        and (_runtime_bool(runtime, "server_side_exit_enabled", True) or accepted_position_levels)
        and (get_engine_mode() == "paper" or not _broker_managed_exit(updated))
    ):
        _route_server_exit(updated, exit_reason, snapshot)


def _monitor_loop() -> None:
    log_audit_event("OPTION_POSITION_MONITOR_STARTED", "Server-side option premium monitor started.")
    while not _STOP_EVENT.is_set():
        runtime = get_runtime_settings()
        try:
            monitor_once()
        except Exception as exc:
            logger.exception("Option position monitor loop error")
            log_error_event("OPTION_POSITION_MONITOR_ERROR", str(exc))
        _STOP_EVENT.wait(_poll_seconds(runtime))
    log_audit_event("OPTION_POSITION_MONITOR_STOPPED", "Server-side option premium monitor stopped.")


def start_option_position_monitor() -> None:
    global _THREAD
    with _THREAD_LOCK:
        if _THREAD and _THREAD.is_alive():
            return
        _STOP_EVENT.clear()
        _THREAD = threading.Thread(target=_monitor_loop, name="option-position-monitor", daemon=True)
        _THREAD.start()


def stop_option_position_monitor(timeout: float = 2.0) -> None:
    with _THREAD_LOCK:
        thread = _THREAD
        if not thread:
            return
        _STOP_EVENT.set()
    thread.join(timeout=timeout)
    stop_marketfeed_ws(timeout=timeout)
