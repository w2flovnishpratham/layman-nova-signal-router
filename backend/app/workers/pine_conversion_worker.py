"""Durable claimant and lease recovery for opt-in external Pine conversion."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import settings
from app.db import models
from app.db.engine import database_configured, get_engine, session_scope
from app.services import pine_conversion_service

logger = logging.getLogger("nova_signal_router.pine_conversion")
_STOP = threading.Event()
_WAKE = threading.Event()
_THREAD: threading.Thread | None = None
_LOCK = threading.RLock()


def _now():
    return datetime.now(timezone.utc)


def recover_stale_requests() -> int:
    if not database_configured(): return 0
    cutoff = _now() - timedelta(seconds=max(settings.PINE_CONVERSION_STALE_SECONDS, 30))
    recovered = 0
    with session_scope() as db:
        rows = db.scalars(select(models.PineConversionRequest).where(
            models.PineConversionRequest.status == "processing",
            models.PineConversionRequest.locked_at < cutoff,
        )).all()
        for row in rows:
            if row.worker_attempts >= row.max_attempts:
                row.status = "provider_failed"; row.safe_error_code = "WORKER_RETRIES_EXHAUSTED"; row.completed_at = _now()
            else:
                row.status = "queued"; row.available_at = _now(); row.locked_at = None
            row.updated_at = _now(); recovered += 1
    return recovered


def _claim(limit: int) -> list[dict]:
    now = _now()
    with session_scope() as db:
        if get_engine().dialect.name == "postgresql":
            db.execute(select(func.pg_advisory_xact_lock(0x50494E45)))
        processing = db.scalar(select(func.count(models.PineConversionRequest.id)).where(models.PineConversionRequest.status == "processing")) or 0
        slots = max(min(limit, settings.PINE_CONVERSION_GLOBAL_CONCURRENCY - processing), 0)
        if not slots: return []
        query = select(models.PineConversionRequest).where(
            models.PineConversionRequest.status == "queued",
            models.PineConversionRequest.available_at <= now,
        ).order_by(models.PineConversionRequest.created_at).limit(slots)
        if get_engine().dialect.name == "postgresql": query = query.with_for_update(skip_locked=True)
        rows = list(db.scalars(query).all())
        claims = []
        for row in rows:
            row.status = "processing"; row.started_at = row.started_at or now; row.locked_at = now
            row.worker_attempts += 1; row.updated_at = now
            claims.append({"id": row.id, "model": row.model, "input_source_sha256": row.input_source_sha256})
        db.flush()
        return claims


def process_queued_conversions_once(*, limit: int | None = None) -> int:
    if not database_configured() or not settings.PINE_CONVERSION_AI_ENABLED: return 0
    claims = _claim(max(limit or settings.PINE_CONVERSION_GLOBAL_CONCURRENCY, 1))
    if not claims: return 0
    with ThreadPoolExecutor(max_workers=len(claims), thread_name_prefix="nova-pine-conversion") as pool:
        list(pool.map(pine_conversion_service.process_claim, claims))
    return len(claims)


def _loop():
    logger.info("Pine conversion worker started")
    recover_stale_requests()
    while not _STOP.is_set():
        try: processed = process_queued_conversions_once()
        except Exception:
            logger.exception("Pine conversion worker loop failed"); processed = 0
        if processed: continue
        _WAKE.wait(max(settings.PINE_CONVERSION_WORKER_POLL_SECONDS, .1)); _WAKE.clear()
    logger.info("Pine conversion worker stopped")


def wake_pine_conversion_worker():
    _WAKE.set()


def start_pine_conversion_worker():
    global _THREAD
    if not database_configured() or not settings.PINE_CONVERSION_AI_ENABLED: return
    with _LOCK:
        if _THREAD and _THREAD.is_alive(): return
        _STOP.clear(); _THREAD = threading.Thread(target=_loop, name="nova-pine-conversion", daemon=True); _THREAD.start()


def stop_pine_conversion_worker():
    global _THREAD
    with _LOCK:
        _STOP.set(); _WAKE.set()
        if _THREAD and _THREAD.is_alive(): _THREAD.join(timeout=3)
        _THREAD = None


def pine_conversion_worker_status():
    return {"enabled": settings.PINE_CONVERSION_AI_ENABLED, "running": bool(_THREAD and _THREAD.is_alive())}
