"""Pushes NIFTY chart candles over the WS session bus instead of every open
tab independently polling GET /market/nifty/candles.

Same cadence as market_chart_service's own cache TTL -- there's nothing to
gain by checking more often than the underlying data can change, and
get_nifty_candles() is a cheap in-memory read on a cache hit, so this doesn't
add extra Dhan round-trips beyond what the cache already does. Only pushes
when the payload actually changed (updated_at differs from last push), so a
cache hit between fetches is a no-op, not a redundant broadcast.
"""
from __future__ import annotations

import logging
import threading

from app.services.chat_event_publisher import publish_nifty_candles_from_sync
from app.services.market_chart_service import (
    CACHE_TTL_CLOSED_SECONDS,
    CACHE_TTL_OPEN_SECONDS,
    SUPPORTED_INTERVALS,
    get_nifty_candles,
    market_state,
)

logger = logging.getLogger("nova_signal_router.nifty_candle_pusher")

_STOP_EVENT = threading.Event()
_WORKER: threading.Thread | None = None
_WORKER_LOCK = threading.RLock()
# interval -> last-pushed updated_at, so an unchanged cache hit is a no-op.
_LAST_PUSHED_AT: dict[str, str | None] = {}


def _poll_interval_seconds() -> float:
    return CACHE_TTL_OPEN_SECONDS if market_state() == "open" else CACHE_TTL_CLOSED_SECONDS


def _push_if_changed(interval: str) -> None:
    series = get_nifty_candles(interval)
    updated_at = series.get("updated_at")
    if updated_at == _LAST_PUSHED_AT.get(interval):
        return
    if publish_nifty_candles_from_sync(interval=interval, series=series):
        _LAST_PUSHED_AT[interval] = updated_at


def _worker_loop() -> None:
    logger.info("NIFTY candle push worker started.")
    while not _STOP_EVENT.is_set():
        for interval in SUPPORTED_INTERVALS:
            try:
                _push_if_changed(interval)
            except Exception:
                logger.exception("NIFTY candle push failed for interval %s", interval)
        _STOP_EVENT.wait(_poll_interval_seconds())
    logger.info("NIFTY candle push worker stopped.")


def start_nifty_candle_pusher() -> None:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _STOP_EVENT.clear()
        _WORKER = threading.Thread(target=_worker_loop, name="nova-nifty-candle-pusher", daemon=True)
        _WORKER.start()


def stop_nifty_candle_pusher() -> None:
    global _WORKER
    with _WORKER_LOCK:
        _STOP_EVENT.set()
        if _WORKER is not None and _WORKER.is_alive():
            _WORKER.join(timeout=3.0)
        _WORKER = None
