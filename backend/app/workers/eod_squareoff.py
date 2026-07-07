"""
C1 — EOD auto-square-off worker.

Dhan auto-squares INTRADAY F&O positions at ~15:20 IST and charges a ₹50
penalty + adverse market slippage. NOVA's risk_manager only blocks new
*entries* outside market hours; it does NOT actively close existing
positions. This worker closes that gap.

Behavior
--------
* Runs as a daemon thread started from main.py's lifespan.
* Wakes every 60 seconds.
* Once per trading day, at >= 15:15 IST and < 15:25 IST, if there is an
  open NOVA position, builds a server-side EXIT signal and routes it
  through route_signal() so the existing risk/order/audit pipeline
  handles the flatten cleanly.
* Records the date of the last successful flatten in app_state so it
  cannot fire twice in one day, and resumes idle until tomorrow.

This worker does NOT touch positions Dhan shows that NOVA didn't open
(see GP1-4 ghost-position detector for that path).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, time as dtime, timedelta, timezone

from app.schemas.signal import NormalizedSignal
from app.services.audit_logger import log_audit_event, log_error_event, log_order_event
from app.services.credential_vault import get_webhook_secret
from app.services.state_store import (
    get_app_state,
    get_engine_mode,
    get_open_position,
    get_runtime_settings,
    update_app_state,
)
from app.config import DEFAULT_EXCHANGE_SEGMENT, DEFAULT_ORDER_TYPE, DEFAULT_PRODUCT_TYPE

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    IST = timezone(timedelta(hours=5, minutes=30))


logger = logging.getLogger("nova_signal_router.eod_squareoff")

# Window during which the EOD flatten may fire. We start at 15:15:00 IST
# to leave ~5 minutes of buffer before Dhan's auto-square at ~15:20, and
# stop trying after 15:25:00 IST (at which point Dhan will have done the
# job for us anyway).
_EOD_WINDOW_START = dtime(15, 15)
_EOD_WINDOW_END = dtime(15, 25)
_POLL_SECONDS = 60.0

_STOP_EVENT = threading.Event()
_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.RLock()

# Runtime-settings key used to remember when we last fired, so we don't fire twice.
_LAST_FIRED_KEY = "eod_squareoff_last_fired_date_ist"


def _normalized_instance_id(instance_id: str | None) -> str | None:
    if instance_id is None:
        return None
    value = str(instance_id).strip()
    return value or None


def _get_open_position_for_instance(instance_id: str | None) -> dict | None:
    scoped_instance_id = _normalized_instance_id(instance_id)
    if scoped_instance_id is None:
        return get_open_position()
    return get_open_position(instance_id=scoped_instance_id)


def _last_fired_key(instance_id: str | None = None) -> str:
    scoped_instance_id = _normalized_instance_id(instance_id)
    if scoped_instance_id is None:
        return _LAST_FIRED_KEY
    return f"{_LAST_FIRED_KEY}:{scoped_instance_id}"


def _instance_eod_routing_allowed(instance_id: str | None) -> bool:
    scoped_instance_id = _normalized_instance_id(instance_id)
    if scoped_instance_id is None:
        return True
    if get_engine_mode() == "paper":
        return True
    log_error_event(
        "EOD_SQUAREOFF_INSTANCE_LIVE_BLOCKED",
        "Instance-scoped EOD square-off is blocked outside paper mode.",
        metadata={"instance_id": scoped_instance_id, "engine_mode": get_engine_mode()},
    )
    return False


def _today_ist_date_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _ist_time_now() -> dtime:
    return datetime.now(IST).time()


def _is_weekday_ist() -> bool:
    return datetime.now(IST).weekday() < 5


def _in_eod_window() -> bool:
    t = _ist_time_now()
    return _EOD_WINDOW_START <= t < _EOD_WINDOW_END


def _already_fired_today(instance_id: str | None = None) -> bool:
    app = get_app_state()
    return app.get(_last_fired_key(instance_id)) == _today_ist_date_str()


def _mark_fired_today(instance_id: str | None = None) -> None:
    update_app_state(**{_last_fired_key(instance_id): _today_ist_date_str()})


def _build_eod_exit_signal(position: dict, *, instance_id: str | None = None) -> NormalizedSignal:
    """Construct an internal EXIT signal mirroring webhook-triggered exits."""
    now_ts = int(time.time())
    sec_id = position.get("security_id") or "UNKNOWN"
    qty = position.get("qty")
    try:
        qty_int = int(qty) if qty not in (None, "") else 1
    except (TypeError, ValueError):
        qty_int = 1

    raw_payload = {
        "server_side_exit": True,
        "exit_reason": "EOD_FLATTEN",
        "triggered_at_ist": datetime.now(IST).isoformat(),
    }
    scoped_instance_id = _normalized_instance_id(instance_id)
    if scoped_instance_id is not None:
        raw_payload["v2_internal"] = True
        raw_payload["instance_id"] = scoped_instance_id
        for key in ("strategy_version_id", "execution_mode", "v2_job_id", "source_signal_id"):
            value = position.get(key)
            if value is not None and str(value).strip():
                raw_payload[key] = str(value)

    return NormalizedSignal(
        payload_format="NOVA",
        secret="" if scoped_instance_id is not None else get_webhook_secret() or "",
        signal_id=f"SERVER_EOD_FLATTEN_{now_ts}_{sec_id}",
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
        qty=qty_int,
        order_type=position.get("order_type") or DEFAULT_ORDER_TYPE,
        product_type=position.get("product_type") or DEFAULT_PRODUCT_TYPE,
        source="server_side_eod_squareoff",
        raw_payload=raw_payload,
    )


def _try_flatten_once(instance_id: str | None = None) -> None:
    """Fire one EOD flatten attempt. Safe to call repeatedly; guards inside."""
    scoped_instance_id = _normalized_instance_id(instance_id)
    already_fired = (
        _already_fired_today()
        if scoped_instance_id is None
        else _already_fired_today(instance_id=scoped_instance_id)
    )
    if already_fired:
        return

    position = _get_open_position_for_instance(scoped_instance_id) or {}
    if not position.get("has_open_position"):
        # No NOVA position. Mark today as "no-op done" so we don't spend
        # the rest of the day polling.
        if scoped_instance_id is None:
            _mark_fired_today()
        else:
            _mark_fired_today(instance_id=scoped_instance_id)
        log_audit_event(
            "EOD_SQUAREOFF_NO_POSITION",
            "EOD window reached and no open NOVA position to flatten.",
            metadata={"date_ist": _today_ist_date_str(), "instance_id": scoped_instance_id},
        )
        return

    if not _instance_eod_routing_allowed(scoped_instance_id):
        return

    log_audit_event(
        "EOD_SQUAREOFF_TRIGGERED",
        "EOD window reached. Sending MARKET SELL to flatten open position before Dhan auto-square.",
        severity="WARNING",
        metadata={
            "date_ist": _today_ist_date_str(),
            "instance_id": scoped_instance_id,
            "trading_symbol": position.get("trading_symbol"),
            "security_id": position.get("security_id"),
            "qty": position.get("qty"),
        },
    )

    try:
        # Local import to avoid module-load cycles.
        from app.services.execution_router import route_signal

        exit_signal = _build_eod_exit_signal(position, instance_id=scoped_instance_id)
        result = route_signal(exit_signal)

        log_order_event(
            {
                "event": "EOD_SQUAREOFF_ROUTED",
                "date_ist": _today_ist_date_str(),
                "instance_id": scoped_instance_id,
                "result_success": bool(result.get("success")),
                "result_status": result.get("status"),
                "order_id": result.get("order_id"),
                "trading_symbol": position.get("trading_symbol"),
                "security_id": position.get("security_id"),
            }
        )

        if result.get("success"):
            log_audit_event(
                "EOD_SQUAREOFF_PLACED",
                "EOD square-off MARKET SELL accepted by Dhan.",
                metadata={
                    "order_id": result.get("order_id"),
                    "status": result.get("status"),
                    "instance_id": scoped_instance_id,
                    "trading_symbol": position.get("trading_symbol"),
                },
            )
            if scoped_instance_id is None:
                _mark_fired_today()
            else:
                _mark_fired_today(instance_id=scoped_instance_id)
        else:
            # Don't mark as fired — retry on the next 60s tick within the window.
            log_error_event(
                "EOD_SQUAREOFF_FAILED",
                f"EOD square-off attempt failed; will retry within the window. "
                f"reason={result.get('reason') or result.get('error') or 'unknown'}",
                metadata={
                    "instance_id": scoped_instance_id,
                    "trading_symbol": position.get("trading_symbol"),
                    "security_id": position.get("security_id"),
                    "result": result,
                },
            )

    except Exception as exc:  # pragma: no cover
        log_error_event(
            "EOD_SQUAREOFF_EXCEPTION",
            f"EOD square-off routing raised an exception: {exc}",
            metadata={"instance_id": scoped_instance_id, "position": position},
        )


def _loop() -> None:
    logger.info("EOD square-off worker started.")
    while not _STOP_EVENT.is_set():
        try:
            if _is_weekday_ist() and _in_eod_window():
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
                            runtime = get_runtime_settings()
                            if bool(runtime.get("eod_squareoff_enabled", True)):
                                _try_flatten_once()
                else:
                    runtime = get_runtime_settings()
                    if bool(runtime.get("eod_squareoff_enabled", True)):
                        _try_flatten_once()
        except Exception as exc:  # pragma: no cover
            logger.exception("EOD square-off loop error: %s", exc)
        # Sleep with quick wake on stop request.
        _STOP_EVENT.wait(_POLL_SECONDS)
    logger.info("EOD square-off worker stopped.")


def start_eod_squareoff_worker() -> None:
    """Start the daemon thread. Idempotent."""
    global _THREAD
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP_EVENT.clear()
        _THREAD = threading.Thread(
            target=_loop,
            name="nova-eod-squareoff",
            daemon=True,
        )
        _THREAD.start()


def stop_eod_squareoff_worker() -> None:
    """Signal the loop to stop and join briefly."""
    global _THREAD
    with _THREAD_LOCK:
        _STOP_EVENT.set()
        if _THREAD is not None and _THREAD.is_alive():
            _THREAD.join(timeout=2.0)
        _THREAD = None
