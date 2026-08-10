"""Always-on NIFTY tick-to-candle worker.

REST is used only to seed/reconcile the durable 1m session. Live Dhan ticks are
folded in short batches, persisted once, and broadcast as single-candle deltas.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

from app.services.chat_event_publisher import publish_nifty_candle_upsert_from_sync
from app.services.dhan_marketfeed_ws import (
    MarketFeedPacket,
    add_marketfeed_listener,
    ensure_marketfeed_subscription,
    remove_marketfeed_listener,
)
from app.services.market_chart_service import (
    NIFTY_INDEX_EXCHANGE_SEGMENT,
    NIFTY_INDEX_SECURITY_ID,
    apply_nifty_ticks,
    get_nifty_candles,
    market_state,
    reconcile_nifty_one_minute,
)

logger = logging.getLogger("nova_signal_router.nifty_candle_pusher")

TICK_BATCH_SECONDS = 0.35
RECONCILE_OPEN_SECONDS = 300.0
RECONCILE_CLOSED_SECONDS = 1800.0

_STOP_EVENT = threading.Event()
_TICK_EVENT = threading.Event()
_TICKS: queue.SimpleQueue[tuple[float, int]] = queue.SimpleQueue()
_WORKER: threading.Thread | None = None
_WORKER_LOCK = threading.RLock()


def _queue_tick(packet: MarketFeedPacket) -> None:
    if (
        packet.exchange_segment != NIFTY_INDEX_EXCHANGE_SEGMENT
        or packet.security_id != NIFTY_INDEX_SECURITY_ID
        or packet.ltp is None
    ):
        return
    _TICKS.put((packet.ltp, int(packet.last_trade_time or time.time())))
    _TICK_EVENT.set()


def _drain_ticks() -> list[tuple[float, int]]:
    ticks: list[tuple[float, int]] = []
    while True:
        try:
            ticks.append(_TICKS.get_nowait())
        except queue.Empty:
            return ticks


def _reconcile_interval_seconds() -> float:
    return RECONCILE_OPEN_SECONDS if market_state() == "open" else RECONCILE_CLOSED_SECONDS


def _publish_tick_batch(ticks: list[tuple[float, int]]) -> None:
    for delta in apply_nifty_ticks(ticks):
        publish_nifty_candle_upsert_from_sync(delta=delta)


def _worker_loop() -> None:
    logger.info("NIFTY candle delta worker started.")
    next_reconcile = 0.0
    while not _STOP_EVENT.is_set():
        now = time.monotonic()
        if now >= next_reconcile:
            try:
                reconciled = reconcile_nifty_one_minute()
                if reconciled.get("status") != "ready" and market_state() == "closed":
                    get_nifty_candles("1m")
            except Exception:
                logger.exception("NIFTY candle reconciliation failed.")
            next_reconcile = time.monotonic() + _reconcile_interval_seconds()

        tick_ready = _TICK_EVENT.wait(min(TICK_BATCH_SECONDS, max(0.05, next_reconcile - time.monotonic())))
        _TICK_EVENT.clear()
        if tick_ready and _STOP_EVENT.wait(TICK_BATCH_SECONDS):
            break
        ticks = _drain_ticks()
        if not ticks:
            continue
        try:
            _publish_tick_batch(ticks)
        except Exception:
            logger.exception("NIFTY candle tick batch failed.")
    logger.info("NIFTY candle delta worker stopped.")


def start_nifty_candle_pusher() -> None:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _STOP_EVENT.clear()
        add_marketfeed_listener(_queue_tick)
        ensure_marketfeed_subscription(
            exchange_segment=NIFTY_INDEX_EXCHANGE_SEGMENT,
            security_id=NIFTY_INDEX_SECURITY_ID,
            persistent=True,
        )
        _WORKER = threading.Thread(target=_worker_loop, name="nova-nifty-candle-pusher", daemon=True)
        _WORKER.start()


def stop_nifty_candle_pusher() -> None:
    global _WORKER
    with _WORKER_LOCK:
        _STOP_EVENT.set()
        _TICK_EVENT.set()
        remove_marketfeed_listener(_queue_tick)
        if _WORKER is not None and _WORKER.is_alive():
            _WORKER.join(timeout=3.0)
        _WORKER = None
