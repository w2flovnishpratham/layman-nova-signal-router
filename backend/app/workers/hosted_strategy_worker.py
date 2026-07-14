"""Poll the durable hosted-evaluation queue; no in-memory scheduling state."""
from __future__ import annotations

import logging
import threading

from app.config import settings
from app.db.engine import database_configured
from app.services import hosted_strategy_runtime

logger = logging.getLogger("nova_signal_router.hosted_strategy_worker")
_STOP = threading.Event()
_WAKE = threading.Event()
_THREAD: threading.Thread | None = None
_LOCK = threading.Lock()


def _loop() -> None:
    hosted_strategy_runtime.recover_stale_jobs()
    while not _STOP.is_set():
        try:
            if hosted_strategy_runtime.process_queued_jobs_once(): continue
        except Exception:
            logger.exception("Hosted evaluation worker iteration failed")
        _WAKE.wait(max(settings.HOSTED_STRATEGY_WORKER_POLL_SECONDS, 0.1)); _WAKE.clear()


def wake_hosted_strategy_worker() -> None: _WAKE.set()


def start_hosted_strategy_worker() -> None:
    global _THREAD
    if not database_configured() or not settings.HOSTED_STRATEGY_RUNTIME_ENABLED: return
    with _LOCK:
        if _THREAD and _THREAD.is_alive(): return
        _STOP.clear(); _THREAD = threading.Thread(target=_loop, name="nova-hosted-strategies", daemon=True); _THREAD.start()


def stop_hosted_strategy_worker() -> None:
    global _THREAD
    with _LOCK:
        _STOP.set(); _WAKE.set()
        if _THREAD and _THREAD.is_alive(): _THREAD.join(timeout=3)
        _THREAD = None


def status() -> dict:
    return {"enabled": settings.HOSTED_STRATEGY_RUNTIME_ENABLED, "paper_execution_enabled": settings.HOSTED_STRATEGY_PAPER_EXECUTION_ENABLED, "running": bool(_THREAD and _THREAD.is_alive())}
