"""Bounded PostgreSQL observation of JSON-authoritative position reads."""
from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone

from sqlalchemy import select, text

from app.config import settings
from app.db import models
from app.db.engine import session_scope
from app.services.position_store import ACTIVE_POSITION_STATES, normalize_db_position, normalize_json_position

logger = logging.getLogger("nova_signal_router.position_read_shadow")
_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pos-read-shadow")
_latencies: deque[float] = deque(maxlen=2048)
_counts = Counter()
_mismatches = Counter()
_sample_counter = 0
_breaker_state = "CLOSED"
_breaker_failures = 0
_breaker_opened_at = 0.0
_probe_in_flight = False
_inflight = threading.BoundedSemaphore(4)
_json_write_times: dict[tuple[str, str], float] = {}
_last_success: str | None = None
_last_failure: str | None = None


def _percentile(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(math.ceil(len(ordered) * p) - 1, len(ordered) - 1)], 3)


def _sample() -> bool:
    global _sample_counter
    rate = min(max(float(settings.POSITION_DB_READ_SHADOW_SAMPLE_RATE), 0.0), 1.0)
    with _lock:
        _sample_counter += 1
        return rate >= 1 or (rate > 0 and ((_sample_counter * 2654435761) & 0xFFFFFFFF) / 2**32 < rate)


def _allow() -> bool:
    global _breaker_state, _probe_in_flight
    with _lock:
        if _breaker_state == "CLOSED":
            return True
        if _breaker_state == "OPEN" and time.monotonic() - _breaker_opened_at >= max(settings.POSITION_DB_READ_SHADOW_CIRCUIT_OPEN_SECONDS, 1):
            _breaker_state = "HALF_OPEN"
            _probe_in_flight = True
            return True
        return False


def _breaker_success() -> None:
    global _breaker_state, _breaker_failures, _probe_in_flight
    with _lock:
        if _breaker_state == "HALF_OPEN":
            _counts["half_open_successes"] += 1
        _breaker_state, _breaker_failures, _probe_in_flight = "CLOSED", 0, False


def _breaker_failure() -> None:
    global _breaker_state, _breaker_failures, _breaker_opened_at, _probe_in_flight
    with _lock:
        if _breaker_state == "HALF_OPEN":
            _counts["half_open_failures"] += 1
        _breaker_failures += 1
        _probe_in_flight = False
        if _breaker_state == "HALF_OPEN" or _breaker_failures >= max(settings.POSITION_DB_READ_SHADOW_FAILURE_THRESHOLD, 1):
            _breaker_state, _breaker_opened_at = "OPEN", time.monotonic()


def _query(user_id, mode):
    with session_scope() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text(f"SET LOCAL statement_timeout = '{max(settings.POSITION_DB_READ_SHADOW_TIMEOUT_MS, 1)}ms'"))
            db.execute(text(f"SET LOCAL lock_timeout = '{max(settings.POSITION_DB_READ_SHADOW_LOCK_TIMEOUT_MS, 1)}ms'"))
        return db.scalars(select(models.StrategyInstancePosition).where(
            models.StrategyInstancePosition.user_id == user_id,
            models.StrategyInstancePosition.execution_mode == mode,
            models.StrategyInstancePosition.position_state.in_(ACTIVE_POSITION_STATES),
        )).all()


def _categories(json_position, rows):
    json_open = bool(json_position.get("has_open_position"))
    if len(rows) > 1:
        return ["MULTIPLE_ACTIVE_DB_POSITIONS"]
    if not rows:
        return ["JSON_OPEN_DB_MISSING"] if json_open else ["MATCH"]
    if not json_open:
        return ["JSON_FLAT_DB_ACTIVE"]
    j, d = normalize_json_position(json_position), normalize_db_position(rows[0])
    mapping = {"state": "STATE_MISMATCH", "security_id": "SECURITY_ID_MISMATCH",
               "option_side": "OPTION_SIDE_MISMATCH", "qty": "OPEN_QUANTITY_MISMATCH",
               "entry_price_paise": "AVERAGE_PRICE_MISMATCH", "entry_order_id": "ENTRY_ORDER_ID_MISMATCH",
               "exit_order_id": "EXIT_ORDER_ID_MISMATCH", "reversal_pending": "REVERSAL_METADATA_MISMATCH",
               "super_order": "SUPER_ORDER_METADATA_MISMATCH"}
    found = [category for field, category in mapping.items() if j.get(field) != d.get(field)]
    raw = rows[0].raw_snapshot or {}
    for field, category in (("trading_symbol", "TRADING_SYMBOL_MISMATCH"), ("strike", "STRIKE_MISMATCH"),
                            ("expiry", "EXPIRY_MISMATCH"), ("product_type", "PRODUCT_TYPE_MISMATCH"),
                            ("requested_qty", "ENTRY_QUANTITY_MISMATCH")):
        if (json_position.get(field) or None) != (raw.get(field) or None):
            found.append(category)
    return found or ["MATCH"]


def note_json_write(execution_mode: str) -> None:
    from app.services.execution_context import current_execution_user
    user = current_execution_user()
    if user is not None and not user.is_dev:
        with _lock:
            _json_write_times[(str(user.id), execution_mode)] = time.monotonic()


def observe_json_position(json_position: dict, execution_mode: str) -> dict:
    global _last_success, _last_failure
    from app.services.execution_context import current_execution_user
    if not settings.POSITION_DB_READ_SHADOW_ENABLED:
        return {"category": "DISABLED"}
    user = current_execution_user()
    if user is None or user.is_dev:
        return {"category": "NO_TRUSTED_CONTEXT"}
    if not _sample():
        with _lock: _counts["sample_skips"] += 1
        return {"category": "DB_READ_SKIPPED_BY_SAMPLE"}
    if not _allow():
        with _lock: _counts["circuit_skips"] += 1
        return {"category": "DB_CIRCUIT_OPEN"}
    started = time.perf_counter()
    with _lock: _counts["attempted"] += 1
    if not _inflight.acquire(blocking=False):
        with _lock: _counts["timeouts"] += 1
        _breaker_failure()
        return {"categories": ["DB_READ_TIMEOUT"]}
    try:
        future = _executor.submit(_query, user.id, execution_mode)
        future.add_done_callback(lambda _future: _inflight.release())
        rows = future.result(timeout=max(settings.POSITION_DB_READ_SHADOW_TIMEOUT_MS, 1) / 1000)
        latency = (time.perf_counter() - started) * 1000
        categories = _categories(json_position, rows)
        if categories != ["MATCH"]:
            with _lock:
                written_at = _json_write_times.get((str(user.id), execution_mode), 0.0)
            if (time.monotonic() - written_at) * 1000 <= max(settings.POSITION_DB_READ_SHADOW_GRACE_MS, 0):
                categories = ["EXPECTED_TRANSIENT_MISMATCH", *categories]
        with _lock:
            _counts["successful"] += 1
            _counts["matches" if categories == ["MATCH"] else "mismatches"] += 1
            _latencies.append(latency)
            _mismatches.update(c for c in categories if c != "MATCH")
            _counts["transient_mismatches" if "EXPECTED_TRANSIENT_MISMATCH" in categories else "persistent_mismatches"] += categories != ["MATCH"]
            _last_success = datetime.now(timezone.utc).isoformat()
        _breaker_success()
        return {"categories": categories, "latency_ms": latency}
    except TimeoutError:
        category = "DB_READ_TIMEOUT"
        with _lock: _counts["timeouts"] += 1
    except Exception:
        category = "DB_READ_ERROR"
        with _lock: _counts["errors"] += 1
    _last_failure = datetime.now(timezone.utc).isoformat()
    _breaker_failure()
    return {"categories": [category]}


def health() -> dict:
    with _lock:
        values = list(_latencies)
        return {"enabled": bool(settings.POSITION_DB_READ_SHADOW_ENABLED),
                "sample_rate": min(max(float(settings.POSITION_DB_READ_SHADOW_SAMPLE_RATE), 0), 1),
                "breaker": {"state": _breaker_state, "consecutive_failures": _breaker_failures},
                **dict(_counts), "mismatch_categories": dict(_mismatches),
                "latency_ms": {"p50": _percentile(values, .5), "p95": _percentile(values, .95), "p99": _percentile(values, .99)},
                "last_successful_read_at": _last_success, "last_failure_at": _last_failure}
