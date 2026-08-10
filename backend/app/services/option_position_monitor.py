from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from app.config import DEFAULT_EXCHANGE_SEGMENT, DEFAULT_ORDER_TYPE, DEFAULT_PRODUCT_TYPE, DISABLED_OPTION_SL_PRICE_FRACTION
from app.db import backoff as db_backoff
from app.schemas.signal import NormalizedSignal
from app.services.audit_logger import log_audit_event, log_error_event, log_order_event
from app.services.chat_event_publisher import (
    publish_active_trade_from_sync,
    publish_market_snapshot_from_sync,
    publish_tick_pnl_from_sync,
)
from app.services.credential_vault import get_dhan_credentials, get_webhook_secret
from app.services.dhan_client import RealDhanClient, get_broker_client
from app.services.dhan_marketfeed_ws import (
    clear_marketfeed_subscription,
    marketfeed_ws_status,
    stop_marketfeed_ws,
)
from app.services.market_snapshot import get_shared_nifty_snapshot
from app.services.quote_service import get_quote_snapshot
from app.services.risk_manager import _market_is_open
from app.services.shared_market_data import market_data_credentials, shared_market_data_configured
from app.services import position_operations
from app.services.state_store import (
    get_engine_mode,
    get_open_position,
    get_runtime_settings,
    ensure_open_position_identity,
    patch_open_position_cas,
    utc_now,
)
from app.store.redis_session import session_store


logger = logging.getLogger("option_position_monitor")

_STOP_EVENT = threading.Event()
_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.RLock()
# Per-user checks are network I/O (quote fetch, sometimes a second confirming
# fetch) and independent of each other -- fanned out here instead of a
# sequential loop so one busy pass can't starve every other user's SL/TP
# check for the whole cycle. 16 is a guess at "more than enough concurrent
# open positions in practice"; bump it if that stops being true.
_MONITOR_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix="option-monitor")

EXIT_COOLDOWN_SECONDS = 15.0
_LAST_REST_FALLBACK_AT: dict[tuple[str, str], float] = {}
LTP_HISTORY_LIMIT = 24

# Quote freshness/retention thresholds (seconds). LTP_STALE_AFTER_SECONDS mirrors
# the existing display staleness boundary in runtime_reliability: once the last
# valid quote is older than this, the retained value is shown as STALE. Beyond
# LTP_RETENTION_SECONDS (a bounded 4x the stale boundary) we stop trusting the
# retained value and report UNAVAILABLE rather than showing a minutes-old price.
LTP_STALE_AFTER_SECONDS = 15.0
LTP_RETENTION_SECONDS = 60.0


def _seconds_since(iso_ts: Any) -> float | None:
    if not iso_ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, round((datetime.now(timezone.utc) - parsed).total_seconds(), 2))


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _authoritative_quote(*, exchange_segment: str, security_id: str, allow_rest_fallback: bool = True):
    snapshot = get_quote_snapshot(
        exchange_segment=exchange_segment,
        security_id=security_id,
        max_age_seconds=2.0,
        allow_rest_fallback=allow_rest_fallback,
    )
    return SimpleNamespace(
        success=snapshot.get("ltp") is not None and not bool(snapshot.get("stale")),
        ltp=snapshot.get("ltp"),
        source=snapshot.get("source"),
        status=snapshot.get("status"),
        message=snapshot.get("message"),
        error=snapshot.get("error"),
        received_at=snapshot.get("received_at"),
        age_seconds=snapshot.get("age_seconds"),
    )


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
    value = _as_positive_float(runtime.get("option_ltp_poll_seconds"), 0.5)
    return max(0.25, value)


def _ws_stale_seconds(runtime: dict[str, Any]) -> float:
    value = _as_positive_float(runtime.get("option_ws_stale_seconds"), 2.0)
    return max(1.0, value)


def _ltp_source(runtime: dict[str, Any]) -> str:
    source = str(runtime.get("option_ltp_source") or "AUTO").strip().upper()
    return source if source in {"WEBSOCKET", "REST", "AUTO"} else "AUTO"


def _rest_fallback_allowed(runtime: dict[str, Any], exchange_segment: str, security_id: str) -> bool:
    if _ltp_source(runtime) == "REST":
        return True
    if not _runtime_bool(runtime, "option_rest_fallback_enabled", True):
        return False
    cooldown = _as_positive_float(runtime.get("option_rest_fallback_cooldown_seconds"), 3.0)
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


def _cash_loss_stop(position: dict[str, Any], entry_price: float, sl_price: float) -> float:
    rule = position.get("maximum_loss_rule")
    amount = _as_float(rule.get("amount")) if isinstance(rule, dict) else None
    qty = _as_int(position.get("qty"), 0)
    if amount is None or amount <= 0 or qty <= 0:
        return sl_price
    return max(sl_price, entry_price - amount / qty)


def _broker_managed_exit(position: dict[str, Any]) -> bool:
    return str(position.get("exit_management") or "").upper() == "DHAN_SUPER"


def _display_exit_levels(position: dict[str, Any], entry_price: float, runtime: dict[str, Any]) -> tuple[float, float, float, float]:
    sl_percent, tp_percent, sl_price, tp_price = _exit_levels(entry_price, runtime)
    active_levels = _active_exit_levels(position)
    if active_levels is not None:
        sl_price, tp_price = active_levels
        sl_price = _cash_loss_stop(position, entry_price, sl_price)
        sl_percent = max(round(((entry_price - sl_price) / entry_price) * 100, 2), 0.0)
        tp_percent = max(round(((tp_price - entry_price) / entry_price) * 100, 2), 0.0)
        return sl_percent, tp_percent, sl_price, tp_price
    stop_rule = position.get("entry_stop_rule")
    target_rule = position.get("entry_target_rule")
    if isinstance(stop_rule, dict) and isinstance(target_rule, dict):
        mode = str(stop_rule.get("mode") or "CUSTOM_SL_TP").upper()
        stop_value = _as_float(stop_rule.get("value"), 0.0) or 0.0
        target_value = _as_float(target_rule.get("value"), 0.0) or 0.0
        sl_price = 0.1 if mode in {"FLIPS_ONLY", "TARGET_PROFIT"} else (
            max(0.1, entry_price - stop_value)
            if str(stop_rule.get("basis") or "").upper() == "POINTS"
            else max(0.1, entry_price * (1 - stop_value / 100))
        )
        tp_price = 1_000_000.0 if mode == "FLIPS_ONLY" else (
            entry_price + target_value
            if str(target_rule.get("basis") or "").upper() == "POINTS"
            else entry_price * (1 + target_value / 100)
        )
        sl_price = _cash_loss_stop(position, entry_price, sl_price)
        sl_percent = max(round((entry_price - sl_price) / entry_price * 100, 2), 0.0)
        tp_percent = max(round((tp_price - entry_price) / entry_price * 100, 2), 0.0)
        return sl_percent, tp_percent, round(sl_price, 2), round(tp_price, 2)
    if _runtime_bool(runtime, "option_disable_sl", True):
        sl_price = max(0.10, round(entry_price * DISABLED_OPTION_SL_PRICE_FRACTION, 2))
    if _broker_managed_exit(position):
        sl_price = _as_float(position.get("broker_sl_price"), sl_price) or sl_price
        tp_price = _as_float(position.get("broker_tp_price"), tp_price) or tp_price
    sl_price = _cash_loss_stop(position, entry_price, sl_price)
    sl_percent = max(round(((entry_price - sl_price) / entry_price) * 100, 2), 0.0)
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
    checked_at = utc_now()
    # A real quote just resolved: it is the new last-valid observation. Mark it
    # STALE only if the quote itself is already older than the display boundary.
    quote_status = "stale" if (quote_age_seconds is not None and quote_age_seconds > LTP_STALE_AFTER_SECONDS) else "ready"
    return {
        "source": source,
        "status": status,
        "quote_status": quote_status,
        "exit_management": str(position.get("exit_management") or "SERVER").upper(),
        "entry_price": entry_price,
        "ltp": ltp,
        "qty": qty,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "unrealized_pnl": unrealized_pnl,
        "pnl_percent": pnl_percent,
        "ltp_history": _ltp_history(position, ltp),
        "exit_reason": exit_reason,
        "quote_age_seconds": quote_age_seconds,
        "last_checked_at": checked_at,
        # Last-valid observation, carried forward across transient failures so a
        # temporary feed gap never erases the truthful price/P&L (root cause #5).
        "last_valid_ltp": ltp,
        "last_valid_unrealized_pnl": unrealized_pnl,
        "last_valid_pnl_percent": pnl_percent,
        "last_valid_quote_at": checked_at,
        "last_valid_quote_source": source,
    }


def _ensure_position_identity(position: dict[str, Any]) -> dict[str, Any]:
    """Normalize a legacy open position (no position_id/version) exactly once so
    the monitor's compare-and-set writes have an identity to check against."""
    if not position.get("has_open_position") or position.get("position_id"):
        return position
    normalized = ensure_open_position_identity()
    if normalized.get("position_id") and normalized.get("position_id") != position.get("position_id"):
        log_order_event(
            {
                "event": "LEGACY_POSITION_IDENTITY_NORMALIZED",
                "position_id": normalized.get("position_id"),
                "security_id": normalized.get("security_id"),
            }
        )
    return normalized


def _patch_monitor_fields(
    position: dict[str, Any], patch: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """Compare-and-set only monitoring fields onto the position captured at read
    time. If an exit closed or advanced the position since, the stale write is
    rejected (and logged) instead of resurrecting a flat position (root cause #1).
    Never writes lifecycle/identity fields from an old snapshot."""
    position_id = position.get("position_id")
    position_version = position.get("position_version")
    if not position_id:
        return False, get_open_position()
    applied, current = patch_open_position_cas(
        position_id=str(position_id),
        position_version=int(position_version or 0),
        patch=patch,
        reject_when_exit_pending=True,
        # Cosmetic LTP/live_pnl tick: keep the anti-resurrection CAS guard but do
        # NOT advance the version, or every tick would break the user's pending
        # Add Lots / Partial Exit / Edit SL-TP operation.
        bump_version=False,
    )
    if not applied:
        log_order_event(
            {
                "event": "STALE_MONITOR_WRITE_REJECTED",
                "position_id": position_id,
                "expected_version": position_version,
                "current_has_open_position": bool(current.get("has_open_position")),
                "current_position_id": current.get("position_id"),
                "current_position_version": current.get("position_version"),
            }
        )
    return applied, current


def _with_live_pnl(position: dict[str, Any], snapshot: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    return _patch_monitor_fields(position, {"live_pnl": snapshot})


def _retained_live_pnl(position: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """Overlay quote status without erasing the last confirmed price/P&L."""
    previous = position.get("live_pnl")
    retained = dict(previous) if isinstance(previous, dict) else {}
    retained.update(changes)
    return retained


def _stale_or_unavailable_live_pnl(position: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """Quote resolution failed. Preserve the last valid LTP/P&L and mark it STALE
    while it is still within the retention window; only report UNAVAILABLE once no
    valid quote has ever been seen or the retained value has aged past retention.

    Never claims a fresh source (e.g. DHAN_WEBSOCKET) for a retained value — it
    keeps the source of the last valid observation (root cause #5)."""
    previous = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
    retained = dict(previous)
    retained.update(changes)

    last_valid_ltp = previous.get("last_valid_ltp")
    age = _seconds_since(previous.get("last_valid_quote_at"))
    within_retention = (
        last_valid_ltp is not None and age is not None and age <= LTP_RETENTION_SECONDS
    )
    retained["quote_age_seconds"] = age
    # Source must reflect the retained observation, not the failed fetch.
    retained["source"] = previous.get("last_valid_quote_source") or previous.get("source")
    if within_retention:
        retained["quote_status"] = "stale"
        retained["ltp"] = last_valid_ltp
        retained["unrealized_pnl"] = previous.get("last_valid_unrealized_pnl")
        retained["pnl_percent"] = previous.get("last_valid_pnl_percent")
    else:
        retained["quote_status"] = "unavailable"
        retained["ltp"] = None
        retained["unrealized_pnl"] = None
        retained["pnl_percent"] = None
    return retained


def _client() -> Any:
    return get_broker_client(get_engine_mode())


def _market_data_credentials() -> Any:
    if shared_market_data_configured():
        return market_data_credentials()
    return get_dhan_credentials()


def _market_data_client(creds: Any) -> Any:
    if getattr(creds, "source", None) == "shared_market_data":
        return RealDhanClient(proxy_url="")
    return _client()


def _ltp_history(position: dict[str, Any], ltp: float) -> list[float]:
    live_pnl = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
    raw_history = live_pnl.get("ltp_history") if isinstance(live_pnl, dict) else None
    history: list[float] = []
    if isinstance(raw_history, list):
        for item in raw_history[-(LTP_HISTORY_LIMIT - 1) :]:
            value = _as_float(item)
            if value is not None:
                history.append(float(value))
    elif isinstance(live_pnl, dict):
        previous_ltp = _as_float(live_pnl.get("ltp"))
        if previous_ltp is not None:
            history.append(float(previous_ltp))
    history.append(float(ltp))
    return history[-LTP_HISTORY_LIMIT:]


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


def _exit_trigger_for_ltp(ltp: float, sl_price: float, tp_price: float) -> tuple[str | None, str]:
    if ltp <= sl_price:
        return "SL", "sl_hit"
    if ltp >= tp_price:
        return "TP", "tp_hit"
    return None, "tracking"


def _confirm_exit_trigger(
    *,
    client: Any,
    creds: Any,
    position: dict[str, Any],
    exchange_segment: str,
    security_id: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    trigger_reason: str,
    trigger_snapshot: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    quote = _authoritative_quote(
        exchange_segment=exchange_segment,
        security_id=security_id,
    )
    if not quote.success or quote.ltp is None:
        log_order_event(
            {
                "event": "SERVER_SIDE_OPTION_EXIT_CONFIRMATION_FAILED",
                "reason": trigger_reason,
                "message": quote.message,
                "error": quote.error,
                "security_id": security_id,
                "exchange_segment": exchange_segment,
                "trigger_ltp": trigger_snapshot.get("ltp"),
                "trigger_source": trigger_snapshot.get("source"),
            }
        )
        return None, {
            **trigger_snapshot,
            "status": "exit_confirmation_failed",
            "exit_reason": None,
            "confirmation_error": quote.error or quote.message,
        }

    confirmed_ltp = float(quote.ltp)
    confirmed_reason, confirmed_status = _exit_trigger_for_ltp(confirmed_ltp, sl_price, tp_price)
    confirmed_snapshot = _pnl_snapshot(
        position=position,
        ltp=confirmed_ltp,
        entry_price=entry_price,
        sl_price=sl_price,
        tp_price=tp_price,
        status=confirmed_status,
        exit_reason=confirmed_reason,
        source="dhan_ltp_exit_confirmation",
    )
    confirmed_snapshot["trigger_ltp"] = trigger_snapshot.get("ltp")
    confirmed_snapshot["trigger_source"] = trigger_snapshot.get("source")

    if confirmed_reason:
        return confirmed_reason, confirmed_snapshot

    confirmed_snapshot["rejected_exit_reason"] = trigger_reason
    log_order_event(
        {
            "event": "SERVER_SIDE_OPTION_EXIT_CONFIRMATION_REJECTED",
            "reason": trigger_reason,
            "security_id": security_id,
            "exchange_segment": exchange_segment,
            "trigger_ltp": trigger_snapshot.get("ltp"),
            "trigger_source": trigger_snapshot.get("source"),
            "confirmed_ltp": confirmed_ltp,
            "sl_price": sl_price,
            "tp_price": tp_price,
        }
    )
    return None, confirmed_snapshot


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
        _applied, current = _patch_monitor_fields(
            position,
            {
                "live_pnl": _retained_live_pnl(
                    position,
                    source="dhan_order_status",
                    status="waiting_entry_fill",
                    message="Waiting for Dhan to confirm entry fill price.",
                    entry_order_id=order_id,
                    last_checked_at=utc_now(),
                    order_status=poll.order_status,
                    error=poll.error,
                )
            },
        )
        return current

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
    patch = {
        key: value
        for key, value in updated.items()
        if key not in {"has_open_position", "position_id", "position_version"}
        and value != position.get(key)
    }
    applied, result = _patch_monitor_fields(position, patch)
    if not applied:
        return result
    # Ordering: authoritative JSON write first, typed DB operation second.
    position_operations.on_entry_fill_synced(
        result, order_id=str(order_id), fill_price=avg_price
    )
    return result


def _exit_cooldown_active(position: dict[str, Any]) -> bool:
    server_exit = position.get("server_exit")
    if not isinstance(server_exit, dict):
        return False
    last_attempt_at = _as_float(server_exit.get("last_attempt_epoch"))
    if last_attempt_at is None:
        return False
    return (time.time() - last_attempt_at) < EXIT_COOLDOWN_SECONDS


def _mark_exit_attempt(position: dict[str, Any], reason: str, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    applied, updated = _patch_monitor_fields(
        position,
        {
            "live_pnl": snapshot,
            "server_exit": {
                "exit_in_progress": True,
                "last_attempt_at": utc_now(),
                "last_attempt_epoch": time.time(),
                "reason": reason,
            },
        },
    )
    return updated if applied else None


def _unlock_exit_attempt(position: dict[str, Any], result: dict[str, Any]) -> None:
    current = get_open_position()
    if (
        not current.get("has_open_position")
        or current.get("position_id") != position.get("position_id")
    ):
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
    _patch_monitor_fields(current, {"server_exit": server_exit})


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
    if marked is None:
        return
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


def monitor_once(*, force_rest: bool = False) -> None:
    runtime = get_runtime_settings()
    if not _monitor_should_run(runtime):
        return

    position = _ensure_position_identity(get_open_position())
    if not _market_is_open():
        if not force_rest:
            clear_marketfeed_subscription()
        if position.get("has_open_position"):
            _patch_monitor_fields(
                position,
                {
                    "live_pnl": _retained_live_pnl(
                        position,
                        source="market_closed",
                        status="market_closed",
                        message="Market is closed; Dhan WebSocket and REST LTP fetching are disabled.",
                        last_checked_at=utc_now(),
                        ws_status=marketfeed_ws_status(),
                    )
                },
            )
        return

    snapshot_published = _publish_market_snapshot_from_monitor()
    if not position.get("has_open_position"):
        if not force_rest and not snapshot_published:
            clear_marketfeed_subscription()
        return

    order_creds = get_dhan_credentials()
    market_creds = _market_data_credentials()
    if not market_creds:
        return

    security_id = str(position.get("security_id") or "").strip()
    exchange_segment = str(position.get("exchange_segment") or DEFAULT_EXCHANGE_SEGMENT).upper()
    if not security_id:
        return

    order_client = _client()
    market_client = _market_data_client(market_creds)
    entry_price_before_sync = _as_float(position.get("entry_price"))
    if entry_price_before_sync is None and not order_creds:
        return
    sync_creds = order_creds or market_creds
    position = _sync_entry_fill_if_needed(position, order_client, sync_creds.client_id, sync_creds.access_token)
    entry_price = _as_float(position.get("entry_price"))
    if entry_price is None or entry_price <= 0:
        return
    if entry_price_before_sync is None:
        publish_active_trade_from_sync(position, get_engine_mode())

    quote = _authoritative_quote(
        exchange_segment=exchange_segment,
        security_id=security_id,
        allow_rest_fallback=force_rest or _rest_fallback_allowed(runtime, exchange_segment, security_id),
    )

    if not quote.success or quote.ltp is None:
        _patch_monitor_fields(
            position,
            {
                "live_pnl": _stale_or_unavailable_live_pnl(
                    position,
                    status="ltp_error",
                    message=quote.message,
                    error=quote.error,
                    last_checked_at=utc_now(),
                    ws_status=marketfeed_ws_status(),
                )
            },
        )
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
    exit_reason, status = _exit_trigger_for_ltp(ltp, sl_price, tp_price)

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

    accepted_position_levels = _active_exit_levels(position) is not None
    should_route_exit = (
        exit_reason
        and (_runtime_bool(runtime, "server_side_exit_enabled", True) or accepted_position_levels)
        and (get_engine_mode() == "paper" or not _broker_managed_exit(position))
    )
    if should_route_exit:
        confirmed_reason, confirmed_snapshot = _confirm_exit_trigger(
            client=market_client,
            creds=market_creds,
            position=position,
            exchange_segment=exchange_segment,
            security_id=security_id,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            trigger_reason=exit_reason,
            trigger_snapshot=snapshot,
        )
        if confirmed_snapshot is not None:
            snapshot = confirmed_snapshot
        exit_reason = confirmed_reason

    applied, updated = _with_live_pnl(position, snapshot)
    if not applied:
        # The position we were monitoring was exited/closed (or advanced) during
        # quote resolution. Discard this stale iteration: do not resurrect it and
        # do not route a server exit on stale state.
        return
    publish_tick_pnl_from_sync(
        symbol=str(position.get("trading_symbol") or position.get("symbol") or "NIFTY option"),
        security_id=security_id,
        ltp=float(snapshot.get("ltp") or ltp),
        pnl=snapshot["unrealized_pnl"],
        pnl_pct=snapshot["pnl_percent"],
        mode=get_engine_mode(),
    )

    if (
        exit_reason
        and (_runtime_bool(runtime, "server_side_exit_enabled", True) or _active_exit_levels(updated) is not None)
        and (get_engine_mode() == "paper" or not _broker_managed_exit(updated))
    ):
        _route_server_exit(updated, exit_reason, snapshot)


def _publish_market_snapshot_from_monitor() -> bool:
    try:
        # WS-only build: the fast (sub-second) push loop must never call the Dhan
        # REST quote API (1 req/sec limit). It reads only cached WebSocket ticks,
        # so it can fan out as fast as the loop runs with zero REST load.
        return publish_market_snapshot_from_sync(
            snapshot_factory=lambda: get_shared_nifty_snapshot(allow_rest_fallback=False)
        )
    except Exception as exc:
        logger.warning("Market snapshot push failed: %s", exc)
        log_error_event("MARKET_SNAPSHOT_PUSH_FAILED", str(exc))
        return False


def _active_monitor_user_ids(active_routing_user_ids: Any) -> list[uuid.UUID]:
    raw_ids: list[Any] = list(active_routing_user_ids())
    active_session_user_ids = getattr(session_store, "active_user_ids_sync", None)
    if callable(active_session_user_ids):
        raw_ids.extend(active_session_user_ids())

    users: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for raw_id in raw_ids:
        try:
            user_id = raw_id if isinstance(raw_id, uuid.UUID) else uuid.UUID(str(raw_id))
        except (TypeError, ValueError):
            continue
        if user_id in seen:
            continue
        seen.add(user_id)
        users.append(user_id)
    return users


def _monitor_loop() -> None:
    log_audit_event("OPTION_POSITION_MONITOR_STARTED", "Server-side option premium monitor started.")
    while not _STOP_EVENT.is_set():
        runtime = get_runtime_settings()
        try:
            from app.config import settings
            from app.db.engine import database_configured

            if settings.AUTH_REQUIRED and database_configured():
                from app.services.execution_context import bind_user_execution_context
                from app.services.strategy_fanout import (
                    active_routing_user_ids,
                    load_user_context,
                )

                def _check_user(user_id: uuid.UUID) -> None:
                    try:
                        user = load_user_context(user_id)
                        if user is None:
                            return
                        with bind_user_execution_context(user):
                            monitor_once()
                    except Exception as exc:
                        # Isolated per user -- previously an exception here aborted
                        # the whole pass, leaving every other user's position
                        # unchecked until the next cycle.
                        logger.exception("Option position monitor failed for user %s", user_id)
                        log_error_event("OPTION_POSITION_MONITOR_USER_ERROR", str(exc))

                user_ids = _active_monitor_user_ids(active_routing_user_ids)
                list(_MONITOR_EXECUTOR.map(_check_user, user_ids))
            else:
                monitor_once()
        except Exception as exc:
            logger.exception("Option position monitor loop error")
            log_error_event("OPTION_POSITION_MONITOR_ERROR", str(exc))
        # db_backoff, not the raw poll interval: per-user failures are
        # swallowed inside _check_user above, so this loop cannot otherwise
        # tell that the database is unreachable and would keep polling at
        # full rate (0.5s x every user) straight through an outage.
        _STOP_EVENT.wait(db_backoff.poll_delay(_poll_seconds(runtime)))
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
