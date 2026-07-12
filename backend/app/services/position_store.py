"""Durable PostgreSQL shadow of the JSON position state (Phase 2A).

READ AUTHORITY (do not change without an approved phase):
    JSON (open_position.json / paper_position.json) = execution read authority.
    PostgreSQL (strategy_instance_positions)        = shadow durability and
                                                      parity target only.
Nothing on the execution path reads this table, and there is deliberately no
"read DB if present, else JSON" fallback — that would create two competing
authorities.

Shadow writes are triggered from the single choke point every position writer
already goes through (state_store.set_live_open_position / set_paper_position)
when settings.POSITION_DB_SHADOW_WRITE_ENABLED is on. A shadow failure is
loud (structured error log + best-effort local spool + counter) but NEVER
raises into the execution path and never re-triggers a broker action.

Cross-process serialization: every shadow transaction first takes a
PostgreSQL advisory xact lock derived from (user_id, execution_mode), so two
worker processes racing on an EMPTY slot (first insert) serialize instead of
one failing on the partial unique index.

Bounded latency: on PostgreSQL each shadow transaction runs under SET LOCAL
lock_timeout / statement_timeout; slow or failing writes trip a circuit
breaker (CLOSED -> OPEN -> HALF_OPEN) that skips shadow writes while the DB
is degraded, preserving authoritative JSON latency. Breaker state and
counters are exposed via shadow_health().

The file-backed failure record (audit JSONL) is a BEST-EFFORT LOCAL FALLBACK
SPOOL — per-instance local disk, size-capped, never a durable audit
authority. DESIGN NOTE for Phase 2B: the diff-derived event classifier below
is for shadow observation ONLY. The future PostgreSQL-authoritative store
must be driven by explicit typed operations (open_position, apply_entry_fill,
mark_reversal_requested, apply_partial_exit, mark_exit_pending,
close_position, reconcile_position) — never by inferring business events
from JSON dict differences.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import models
from app.db.engine import database_configured, session_scope

logger = logging.getLogger("nova_signal_router.position_store")

ACTIVE_POSITION_STATES = set(models.ACTIVE_POSITION_STATES)

# Operation families classified from the JSON diff. Each has its own failure
# posture (severity + message) — never one generic policy.
_FAILURE_POLICY = {
    # A missed entry shadow means the DB lags a new broker exposure.
    "entry": ("ERROR", "Position ENTRY shadow-write failed; DB lags a live broker position."),
    # A missed exit/close shadow is the worst case: DB would still show an
    # exposure the broker has already closed. The broker exit itself has
    # ALREADY happened and is never rolled back or retried from here.
    "exit": ("CRITICAL", "Position EXIT/CLOSE shadow-write failed; DB may show a stale open position."),
    "partial_fill": ("ERROR", "Partial-fill shadow-write failed; DB quantity lags the broker."),
    "reversal": ("ERROR", "Reversal-marker shadow-write failed; DB lags the reversal lifecycle."),
    "reconciliation": ("WARNING", "Reconciliation shadow-write failed; DB lags broker sync metadata."),
    "update": ("WARNING", "Position update shadow-write failed; DB lags a non-lifecycle field update."),
    "import": ("ERROR", "JSON position import into the DB shadow failed."),
}

_metrics_lock = threading.Lock()
shadow_write_failures: dict[str, int] = {kind: 0 for kind in _FAILURE_POLICY}
shadow_writes_attempted = 0
shadow_writes_succeeded = 0
shadow_writes_skipped_breaker = 0
shadow_writes_skipped_saturated = 0
shadow_writes_noop_identical = 0
# Slot-scoped lock contention: one user's slot lock timeout must never
# suppress other users' shadow writes, so these do NOT feed the global
# breaker. Keyed "user:mode" -> consecutive timeouts; bounded size.
_slot_lock_failures: dict[str, int] = {}
_SLOT_FAILURES_MAX_KEYS = 256

# Cap for the best-effort local failure spool (audit JSONL). Local disk is
# per-instance and can fill — beyond this size we stop spooling (counters and
# structured logs continue).
_SPOOL_MAX_BYTES = 16 * 1024 * 1024


class _CircuitBreaker:
    """CLOSED -> OPEN after N consecutive failures -> HALF_OPEN probe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = "CLOSED"
        self.consecutive_failures = 0
        self.opened_at: float | None = None
        self.opened_count = 0
        self._probe_in_flight = False

    def allow(self) -> bool:
        import time

        from app.config import settings

        with self._lock:
            if self.state == "CLOSED":
                return True
            if self.state == "OPEN":
                open_for = time.monotonic() - (self.opened_at or 0)
                if open_for >= max(int(settings.POSITION_DB_SHADOW_CIRCUIT_OPEN_SECONDS), 1):
                    self.state = "HALF_OPEN"
                    self._probe_in_flight = True
                    return True  # single recovery probe
                return False
            # HALF_OPEN: only one probe at a time.
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self.state = "CLOSED"
            self.consecutive_failures = 0
            self.opened_at = None
            self._probe_in_flight = False

    def record_failure(self) -> bool:
        """Returns True when this failure OPENED the breaker (for the single
        rate-limited alert)."""
        import time

        from app.config import settings

        with self._lock:
            self.consecutive_failures += 1
            self._probe_in_flight = False
            threshold = max(int(settings.POSITION_DB_SHADOW_FAILURE_THRESHOLD), 1)
            if self.state != "OPEN" and (
                self.state == "HALF_OPEN" or self.consecutive_failures >= threshold
            ):
                self.state = "OPEN"
                self.opened_at = time.monotonic()
                self.opened_count += 1
                return True
            if self.state == "OPEN":
                self.opened_at = time.monotonic()
            return False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "consecutive_failures": self.consecutive_failures,
                "opened_count": self.opened_count,
            }


breaker = _CircuitBreaker()

_executor_guard = threading.Lock()
_executor: ThreadPoolExecutor | None = None


_SHADOW_MAX_WORKERS = 4
# In-flight permit per worker. Abandoned (watchdog-expired) transactions keep
# their permit until their thread actually dies, so when all workers are
# stuck on a black-holed DB, new writes skip IMMEDIATELY instead of queueing
# behind them — the authoritative path never waits for a shadow thread.
_inflight = threading.BoundedSemaphore(_SHADOW_MAX_WORKERS)


def _shadow_executor() -> ThreadPoolExecutor:
    """Small worker pool for watchdog-bounded shadow transactions."""
    global _executor
    with _executor_guard:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=_SHADOW_MAX_WORKERS, thread_name_prefix="pos-shadow"
            )
        return _executor


def shadow_health() -> dict[str, Any]:
    """Breaker state + counters for health/admin diagnostics.

    Aggregate-only: never includes tenant identifiers, snapshots, or paths.
    """
    with _metrics_lock:
        return {
            "enabled": _flag_enabled(),
            "breaker": breaker.snapshot(),
            "writes_attempted": shadow_writes_attempted,
            "writes_succeeded": shadow_writes_succeeded,
            "writes_skipped_breaker_open": shadow_writes_skipped_breaker,
            "writes_skipped_saturated": shadow_writes_skipped_saturated,
            "writes_noop_identical_snapshot": shadow_writes_noop_identical,
            "failures_by_operation": dict(shadow_write_failures),
            "slot_lock_contention": {
                "slots_affected": len(_slot_lock_failures),
                "total_timeouts": sum(_slot_lock_failures.values()),
            },
        }


def _flag_enabled() -> bool:
    from app.config import settings

    return bool(settings.POSITION_DB_SHADOW_WRITE_ENABLED)


def typed_writes_enabled() -> bool:
    from app.config import settings

    return bool(settings.POSITION_DB_TYPED_WRITES_ENABLED)


def _advisory_lock_key(user_id: uuid.UUID, execution_mode: str) -> int:
    """Deterministic signed 64-bit key for pg_advisory_xact_lock."""
    digest = hashlib.sha256(f"position-slot:{user_id}:{execution_mode}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _begin_bounded_slot_txn(db, user_id: uuid.UUID, execution_mode: str) -> None:
    """Apply per-transaction timeouts and take the cross-process slot lock.

    PostgreSQL only; SQLite (tests) serializes via its file lock. Must be the
    FIRST statements of the transaction, before any slot read or insert.
    """
    from sqlalchemy import text as sa_text

    from app.config import settings

    if db.get_bind().dialect.name != "postgresql":
        return
    lock_ms = max(int(settings.POSITION_DB_SHADOW_LOCK_TIMEOUT_MS), 1)
    stmt_ms = max(int(settings.POSITION_DB_SHADOW_WRITE_TIMEOUT_MS), 1)
    db.execute(sa_text(f"SET LOCAL lock_timeout = '{lock_ms}ms'"))
    db.execute(sa_text(f"SET LOCAL statement_timeout = '{stmt_ms}ms'"))
    db.execute(
        sa_text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _advisory_lock_key(user_id, execution_mode)},
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def to_paise(value: Any) -> int | None:
    """Rupee float/str -> integer paise. Never store float money."""
    if value is None or value == "":
        return None
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return None


def _sha256(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def derive_position_state(position: dict[str, Any]) -> str:
    """Map a JSON position dict onto the explicit state set."""
    if not position.get("has_open_position"):
        return "closed"
    if position.get("reversal_exit"):
        return "reversing"
    if position.get("partial_exit"):
        return "exiting"
    live = position.get("live_pnl") or {}
    if position.get("entry_price") is None and live.get("status") in {"waiting_entry_fill", None}:
        return "entering"
    return "open"


def classify_operation(previous: dict[str, Any], new: dict[str, Any]) -> str:
    """Which operation family produced this JSON write (drives failure policy
    and the emitted event type)."""
    was_open = bool(previous.get("has_open_position"))
    is_open = bool(new.get("has_open_position"))
    if not was_open and is_open:
        return "entry"
    if was_open and not is_open:
        return "exit"
    if is_open and new.get("reversal_exit") != previous.get("reversal_exit"):
        return "reversal"
    if is_open and (
        new.get("partial_exit") != previous.get("partial_exit")
        or (new.get("qty") or 0) != (previous.get("qty") or 0)
        or new.get("filled_qty") != previous.get("filled_qty")
    ):
        return "partial_fill"
    if new.get("broker_restart_sync") != previous.get("broker_restart_sync") or new.get(
        "broker_sync"
    ) != previous.get("broker_sync"):
        return "reconciliation"
    return "update"


_EVENT_TYPE_BY_OPERATION = {
    "entry": "entry_filled",
    "exit": "exit_filled",
    "partial_fill": "exit_partial_fill",
    "reversal": "reversal_requested",
    "reconciliation": "reconciliation_adjustment",
    "update": "state_updated",
    "import": "imported_from_json",
}


def normalize_json_position(position: dict[str, Any]) -> dict[str, Any]:
    """Canonical comparable view of a JSON position dict. The DB row is
    normalized to the same shape — one comparison function, two sources."""
    live = position.get("live_pnl") or {}
    return {
        "state": derive_position_state(position),
        "security_id": str(position.get("security_id")) if position.get("security_id") not in (None, "") else None,
        "option_side": (position.get("option_side") or None),
        "qty": int(position.get("qty") or 0),
        "entry_price_paise": to_paise(position.get("entry_price")),
        "entry_order_id": position.get("entry_order_id") or None,
        "exit_order_id": (position.get("partial_exit") or {}).get("order_id")
        or (position.get("reversal_exit") or {}).get("order_id")
        or None,
        "reversal_pending": bool(position.get("reversal_exit")),
        "super_order": {
            "exit_management": position.get("exit_management"),
            "broker_sl_price_paise": to_paise(position.get("broker_sl_price")),
            "broker_tp_price_paise": to_paise(position.get("broker_tp_price")),
        },
    }


def normalize_db_position(row: models.StrategyInstancePosition | None) -> dict[str, Any] | None:
    if row is None:
        return None
    super_order = row.super_order_metadata or {}
    return {
        "state": row.position_state,
        "security_id": row.security_id,
        "option_side": row.option_side,
        "qty": int(row.open_quantity or 0),
        "entry_price_paise": row.avg_entry_price_paise,
        "entry_order_id": row.entry_order_id,
        "exit_order_id": row.exit_order_id,
        "reversal_pending": bool(row.reversal_metadata),
        "super_order": {
            "exit_management": super_order.get("exit_management"),
            "broker_sl_price_paise": super_order.get("broker_sl_price_paise"),
            "broker_tp_price_paise": super_order.get("broker_tp_price_paise"),
        },
    }


def _apply_json_to_row(row: models.StrategyInstancePosition, position: dict[str, Any]) -> None:
    state = derive_position_state(position)
    row.position_state = state
    row.strategy_code = position.get("strategy_code")
    row.security_id = str(position.get("security_id")) if position.get("security_id") not in (None, "") else None
    row.trading_symbol = position.get("trading_symbol")
    row.underlying = "NIFTY"
    row.option_side = position.get("option_side")
    row.strike = str(position.get("strike")) if position.get("strike") not in (None, "") else None
    row.expiry = str(position.get("expiry")) if position.get("expiry") not in (None, "") else None
    row.product_type = position.get("product_type")
    row.entry_quantity = position.get("requested_qty")
    row.filled_entry_quantity = position.get("filled_qty")
    row.open_quantity = int(position.get("qty") or 0)
    filled_entry = int(position.get("filled_qty") or position.get("qty") or 0)
    row.filled_exit_quantity = max(filled_entry - row.open_quantity, 0) if position.get("has_open_position") else filled_entry
    row.avg_entry_price_paise = to_paise(position.get("entry_price"))
    row.entry_order_id = position.get("entry_order_id")
    row.exit_order_id = (position.get("partial_exit") or {}).get("order_id") or (
        position.get("reversal_exit") or {}
    ).get("order_id")
    row.pending_order_metadata = {
        "live_pnl_status": (position.get("live_pnl") or {}).get("status"),
        "partial_fill": bool(position.get("partial_fill")),
        "partial_exit": position.get("partial_exit"),
    }
    row.reversal_metadata = position.get("reversal_exit")
    row.super_order_metadata = {
        "exit_management": position.get("exit_management"),
        "broker_sl_price_paise": to_paise(position.get("broker_sl_price")),
        "broker_tp_price_paise": to_paise(position.get("broker_tp_price")),
        "broker_exit_levels": position.get("broker_exit_levels"),
        "super_order_post_fill_update": position.get("super_order_post_fill_update"),
        "active_exit_levels": position.get("active_exit_levels"),
    }
    sync = position.get("broker_restart_sync") or position.get("broker_sync")
    row.reconciliation_status = (sync or {}).get("status") if isinstance(sync, dict) else None
    if isinstance(sync, dict) and sync.get("checked_at"):
        row.last_broker_reconciled_at = _now()
    row.raw_snapshot = position
    row.snapshot_sha256 = _sha256(position)
    if position.get("has_open_position") and row.opened_at is None:
        row.opened_at = _now()
    if state == "closed" and row.closed_at is None:
        row.closed_at = _now()
    row.updated_at = _now()
    row.version = int(row.version or 0) + 1


def _record_parity(row: models.StrategyInstancePosition, position: dict[str, Any]) -> None:
    mismatched = diff_normalized(normalize_json_position(position), normalize_db_position(row))
    row.parity_status = "match" if not mismatched else ("mismatch:" + ",".join(sorted(mismatched)))[:200]
    row.last_parity_at = _now()


def diff_normalized(json_view: dict[str, Any], db_view: dict[str, Any] | None) -> list[str]:
    if db_view is None:
        return ["missing_db_position"]
    return [key for key in json_view if json_view[key] != db_view.get(key)]


def _active_row(db, user_id: uuid.UUID, execution_mode: str) -> models.StrategyInstancePosition | None:
    return db.scalar(
        select(models.StrategyInstancePosition)
        .where(
            models.StrategyInstancePosition.user_id == user_id,
            models.StrategyInstancePosition.execution_mode == execution_mode,
            models.StrategyInstancePosition.position_state.in_(ACTIVE_POSITION_STATES),
        )
        .with_for_update()
    )


def _event_idempotency_key(
    row: models.StrategyInstancePosition,
    *,
    event_type: str,
    previous_state: str | None,
    snapshot_sha: str | None,
) -> str:
    """Deterministic: identical retries of the same logical transition
    produce the same key and therefore at most one event row."""
    raw = ":".join([
        str(row.user_id), row.execution_mode, event_type,
        previous_state or "-", row.position_state, snapshot_sha or "-",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _append_event(
    db,
    row: models.StrategyInstancePosition,
    *,
    event_type: str,
    source: str,
    previous_state: str | None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> None:
    key = idempotency_key or _event_idempotency_key(
        row, event_type=event_type, previous_state=previous_state, snapshot_sha=row.snapshot_sha256
    )
    # Race-free pre-check: we hold the slot's advisory lock, so a duplicate
    # key can only come from a retried identical transition — skip it.
    exists = db.scalar(
        select(models.PositionEvent.id).where(models.PositionEvent.idempotency_key == key)
    )
    if exists is not None:
        return
    db.add(
        models.PositionEvent(
            position_id=row.id,
            user_id=row.user_id,
            event_type=event_type,
            source=source,
            signal_id=(row.raw_snapshot or {}).get("last_signal_id"),
            broker_order_id=row.exit_order_id or row.entry_order_id,
            quantity=row.open_quantity,
            price_paise=row.avg_entry_price_paise,
            previous_state=previous_state,
            new_state=row.position_state,
            event_metadata=metadata,
            idempotency_key=key,
        )
    )


def shadow_position_write(
    *,
    user_id: uuid.UUID,
    execution_mode: str,
    previous: dict[str, Any],
    new: dict[str, Any],
) -> None:
    """Mirror one JSON position write into the DB shadow.

    Called AFTER the authoritative JSON write succeeded. Must never raise —
    failure is logged per operation-family policy and counted, and the broker
    action that triggered the write is never repeated or rolled back.
    """
    # PRECEDENCE: when typed writes own the transitions, the generic
    # diff-classifier is fully inert (no row writes, no events) so the same
    # transition can never produce duplicate events from both mechanisms.
    if typed_writes_enabled():
        return
    operation = classify_operation(previous, new)
    run_guarded_slot_operation(
        operation_kind=operation,
        user_id=user_id,
        execution_mode=execution_mode,
        txn=lambda: _shadow_write_txn(user_id, execution_mode, operation, new),
    )


def run_guarded_slot_operation(
    *,
    operation_kind: str,
    user_id: uuid.UUID,
    execution_mode: str,
    txn,
) -> Any:
    """Shared safety envelope for every DB position write (generic shadow AND
    typed operations): circuit breaker, saturation guard, wall-clock watchdog,
    per-operation failure policy. Never raises into the execution path.

    Returns the txn result, or None when skipped/failed.
    """
    global shadow_writes_attempted, shadow_writes_succeeded
    global shadow_writes_skipped_breaker, shadow_writes_noop_identical

    if not breaker.allow():
        with _metrics_lock:
            shadow_writes_skipped_breaker += 1
        return None
    with _metrics_lock:
        shadow_writes_attempted += 1
    try:
        if not database_configured():
            raise RuntimeError("DATABASE_URL is not configured for position DB writes.")
        # Wall-clock watchdog: SET LOCAL timeouts bound server-side work, but
        # a frozen DB / hung socket / stalled connect blocks the client
        # indefinitely. Run the transaction in a worker thread and abandon it
        # past the budget so the authoritative JSON path stays bounded; the
        # orphaned thread dies with its connection and the breaker handles
        # repeats.
        from app.config import settings

        budget_seconds = (
            int(settings.POSITION_DB_SHADOW_WRITE_TIMEOUT_MS)
            + int(settings.POSITION_DB_SHADOW_LOCK_TIMEOUT_MS)
            + 500
        ) / 1000.0
        # Saturation guard: if every worker is occupied (e.g. abandoned on a
        # black-holed DB), skip immediately — never queue behind them.
        if not _inflight.acquire(blocking=False):
            with _metrics_lock:
                global shadow_writes_skipped_saturated
                shadow_writes_skipped_saturated += 1
            logger.debug("position DB executor saturated; skipping write")
            return None
        try:
            future = _shadow_executor().submit(txn)
        except BaseException:
            _inflight.release()
            raise
        future.add_done_callback(lambda _f: _release_inflight())
        try:
            outcome = future.result(timeout=budget_seconds)
        except FuturesTimeoutError as exc:
            future.cancel()
            raise RuntimeError(
                f"position DB write exceeded wall-clock budget of {budget_seconds:.1f}s"
            ) from exc
        if outcome == "noop":
            with _metrics_lock:
                shadow_writes_noop_identical += 1
            _record_slot_success(user_id, execution_mode)
            breaker.record_success()
            return outcome
        with _metrics_lock:
            shadow_writes_succeeded += 1
        _record_slot_success(user_id, execution_mode)
        breaker.record_success()
        return outcome
    except ConflictingIdempotencyError:
        # Visible, non-swallowed contract violation: same key, different
        # payload. Reported by the caller; never retried here.
        raise
    except Exception as exc:
        _report_shadow_failure(operation_kind, user_id, execution_mode, exc)
        return None


class ConflictingIdempotencyError(RuntimeError):
    """Same idempotency key reused with a conflicting payload."""


def _release_inflight() -> None:
    try:
        _inflight.release()
    except ValueError:  # pragma: no cover - defensive double-release guard
        pass


def _record_slot_success(user_id: uuid.UUID, execution_mode: str) -> None:
    with _metrics_lock:
        _slot_lock_failures.pop(f"{user_id}:{execution_mode}", None)


def _is_slot_lock_timeout(exc: Exception) -> bool:
    """Lock-wait timeout on one slot's advisory/row lock — contention scoped
    to a single (user, mode), not evidence of database-wide trouble."""
    for candidate in (exc, getattr(exc, "orig", None), exc.__cause__):
        if candidate is None:
            continue
        if "locknotavailable" in type(candidate).__name__.lower():
            return True
        if "lock timeout" in str(candidate).lower():
            return True
    return False


def _shadow_write_txn(
    user_id: uuid.UUID, execution_mode: str, operation: str, new: dict[str, Any]
) -> str:
    """The actual shadow transaction (runs on the watchdog worker thread)."""
    with session_scope() as db:
        # Cross-process serialization + bounded waits BEFORE any slot
        # read, so two workers racing on an empty slot cannot both insert.
        _begin_bounded_slot_txn(db, user_id, execution_mode)
        row = _active_row(db, user_id, execution_mode)
        if row is not None and row.snapshot_sha256 == _sha256(new):
            return "noop"  # identical retry of the same snapshot
        new_state = derive_position_state(new)
        if row is None:
            if new_state == "closed":
                return "noop"  # clearing an already-flat slot: nothing to shadow
            row = models.StrategyInstancePosition(
                user_id=user_id, execution_mode=execution_mode, position_state=new_state
            )
            db.add(row)
            previous_state = None
        else:
            previous_state = row.position_state
        if new_state == "closed":
            # Clears arrive as an empty default dict; keep the closed
            # row's last known identity/prices for audit instead of
            # wiping them, and settle the exit quantity.
            row.position_state = "closed"
            row.filled_exit_quantity = int(row.filled_entry_quantity or row.open_quantity or 0)
            row.open_quantity = 0
            row.closed_at = row.closed_at or _now()
            row.parity_status = "match"
            row.last_parity_at = _now()
            row.updated_at = _now()
            row.version = int(row.version or 0) + 1
        else:
            _apply_json_to_row(row, new)
        db.flush()
        if new_state != "closed":
            _record_parity(row, new)
        _append_event(
            db,
            row,
            event_type=_EVENT_TYPE_BY_OPERATION[operation],
            source=operation,
            previous_state=previous_state,
            metadata={"operation": operation},
        )
    return "written"


def _spool_has_capacity() -> bool:
    """Best-effort size cap for the local fallback spool. Never raises."""
    try:
        from app.services.state_store import LOG_FILES, scoped_runtime_path

        path = scoped_runtime_path(LOG_FILES["audit"])
        return not (path.exists() and path.stat().st_size > _SPOOL_MAX_BYTES)
    except Exception:
        return True


def _report_shadow_failure(operation: str, user_id: uuid.UUID, execution_mode: str, exc: Exception) -> None:
    severity, message = _FAILURE_POLICY.get(operation, _FAILURE_POLICY["update"])
    slot_scoped = _is_slot_lock_timeout(exc)
    with _metrics_lock:
        shadow_write_failures[operation] = shadow_write_failures.get(operation, 0) + 1
        failure_count = shadow_write_failures[operation]
        if slot_scoped and len(_slot_lock_failures) < _SLOT_FAILURES_MAX_KEYS:
            key = f"{user_id}:{execution_mode}"
            _slot_lock_failures[key] = _slot_lock_failures.get(key, 0) + 1
    if slot_scoped:
        # One slot's lock contention never opens the GLOBAL breaker — other
        # users' shadow writes keep flowing. Database-wide problems surface
        # as connection/statement/budget failures, which do trip it.
        just_opened = False
    else:
        just_opened = breaker.record_failure()
    logger.error(
        "position shadow-write failed operation=%s user=%s mode=%s count=%s slot_scoped=%s breaker=%s error=%s",
        operation, user_id, execution_mode, failure_count, slot_scoped, breaker.snapshot()["state"], exc,
    )
    try:
        # Best-effort LOCAL fallback spool (per-instance disk, size-capped) —
        # not a durable audit authority; counters/logs are the primary signal.
        from app.services.audit_logger import log_audit_event, log_error_event

        if _spool_has_capacity():
            log_audit_event(
                "POSITION_SHADOW_WRITE_FAILED",
                message,
                severity=severity,
                metadata={
                    "operation": operation,
                    "user_id": str(user_id),
                    "execution_mode": execution_mode,
                    "error": str(exc)[:500],
                    "failure_count": failure_count,
                },
            )
            log_error_event("position_shadow_write", message, metadata={"operation": operation})
        if just_opened:
            # One rate-limited health alert per breaker opening — the skips
            # that follow increment counters silently instead of flooding.
            log_audit_event(
                "POSITION_SHADOW_CIRCUIT_OPENED",
                "Shadow-write circuit breaker OPEN; skipping DB shadow writes while degraded. "
                "JSON execution authority is unaffected.",
                severity="CRITICAL",
                metadata=breaker.snapshot(),
            )
    except Exception:  # pragma: no cover - disk fallback itself failing is safe
        pass


# ---------------------------------------------------------------------------
# Narrow store API (consumed by later phases; shadow writer uses it too)
# ---------------------------------------------------------------------------

def get_current_position(user_id: uuid.UUID, execution_mode: str) -> dict[str, Any] | None:
    """Shadow-side read for tooling/parity ONLY — never for execution."""
    with session_scope() as db:
        row = db.scalar(
            select(models.StrategyInstancePosition).where(
                models.StrategyInstancePosition.user_id == user_id,
                models.StrategyInstancePosition.execution_mode == execution_mode,
                models.StrategyInstancePosition.position_state.in_(ACTIVE_POSITION_STATES),
            )
        )
        return normalize_db_position(row)


def list_events(position_id: uuid.UUID) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.scalars(
            select(models.PositionEvent)
            .where(models.PositionEvent.position_id == position_id)
            .order_by(models.PositionEvent.event_at)
        ).all()
        return [
            {
                "event_type": r.event_type,
                "source": r.source,
                "previous_state": r.previous_state,
                "new_state": r.new_state,
                "quantity": r.quantity,
                "price_paise": r.price_paise,
                "event_at": r.event_at.isoformat() if r.event_at else None,
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Importer (explicit, idempotent; never runs at import/startup)
# ---------------------------------------------------------------------------

def import_json_position(
    *,
    user_id: uuid.UUID,
    execution_mode: str,
    position: dict[str, Any],
) -> dict[str, Any]:
    """Convert one current JSON position into a DB shadow row.

    Idempotent on the snapshot fingerprint; never overwrites a newer shadow.
    """
    fingerprint = _sha256(position)
    with session_scope() as db:
        _begin_bounded_slot_txn(db, user_id, execution_mode)
        row = _active_row(db, user_id, execution_mode)
        if row is not None:
            if row.snapshot_sha256 == fingerprint:
                return {"status": "unchanged", "position_id": str(row.id)}
            if not row.imported_from_json:
                return {"status": "skipped_newer_shadow", "position_id": str(row.id)}
        if not position.get("has_open_position"):
            return {"status": "skipped_flat"}
        if row is None:
            row = models.StrategyInstancePosition(
                user_id=user_id,
                execution_mode=execution_mode,
                position_state=derive_position_state(position),
            )
            db.add(row)
        previous_state = row.position_state if row.id else None
        _apply_json_to_row(row, position)
        row.imported_from_json = True
        db.flush()
        _record_parity(row, position)
        _append_event(
            db,
            row,
            event_type="imported_from_json",
            source="import",
            previous_state=previous_state,
            metadata={"fingerprint": fingerprint},
            idempotency_key=f"import:{row.user_id}:{execution_mode}:{fingerprint}",
        )
        return {"status": "imported", "position_id": str(row.id)}


# ---------------------------------------------------------------------------
# Parity comparison (report-only; shared normalization with the writer)
# ---------------------------------------------------------------------------

def compare_user_positions(
    user_id: uuid.UUID,
    *,
    json_live: dict[str, Any] | None,
    json_paper: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Compare one user's JSON files against DB shadow rows. Report-only."""
    findings: list[dict[str, Any]] = []
    with session_scope() as db:
        for mode, json_position in (("live", json_live), ("paper", json_paper)):
            rows = db.scalars(
                select(models.StrategyInstancePosition).where(
                    models.StrategyInstancePosition.user_id == user_id,
                    models.StrategyInstancePosition.execution_mode == mode,
                    models.StrategyInstancePosition.position_state.in_(ACTIVE_POSITION_STATES),
                )
            ).all()
            if len(rows) > 1:
                findings.append({
                    "type": "duplicate_active_db_positions",
                    "user_id": str(user_id),
                    "mode": mode,
                    "position_ids": [str(r.id) for r in rows],
                })
            row = rows[0] if rows else None
            if json_position is None:
                if row is not None:
                    findings.append({
                        "type": "missing_json_position",
                        "user_id": str(user_id),
                        "mode": mode,
                        "position_id": str(row.id),
                    })
                    findings.extend(_typed_event_findings(db, row, user_id, mode))
                continue
            json_open = bool(json_position.get("has_open_position"))
            if json_open and row is None:
                findings.append({
                    "type": "missing_db_shadow_position",
                    "user_id": str(user_id),
                    "mode": mode,
                    "security_id": json_position.get("security_id"),
                })
                continue
            if not json_open:
                if row is not None:
                    findings.append({
                        "type": "closed_json_but_db_open",
                        "user_id": str(user_id),
                        "mode": mode,
                        "position_id": str(row.id),
                        "db_state": row.position_state,
                    })
                continue
            mismatch_map = {
                "security_id": "security_id_mismatch",
                "option_side": "side_mismatch",
                "qty": "quantity_mismatch",
                "entry_price_paise": "price_mismatch",
                "entry_order_id": "order_id_mismatch",
                "exit_order_id": "order_id_mismatch",
                "state": "state_mismatch",
                "reversal_pending": "reversal_marker_mismatch",
                "super_order": "super_order_metadata_mismatch",
            }
            json_view = normalize_json_position(json_position)
            db_view = normalize_db_position(row)
            for key in diff_normalized(json_view, db_view):
                findings.append({
                    "type": mismatch_map.get(key, f"{key}_mismatch"),
                    "field": key,
                    "user_id": str(user_id),
                    "mode": mode,
                    "position_id": str(row.id),
                    "json_value": json_view.get(key),
                    "db_value": (db_view or {}).get(key),
                })
            findings.extend(_typed_event_findings(db, row, user_id, mode))
    return findings


_GENERIC_EVENT_SOURCES = set(_FAILURE_POLICY) - {"import"}


def _typed_event_findings(db, row, user_id, mode) -> list[dict[str, Any]]:
    """Typed-event completeness checks (active when typed writes own the
    ledger): every active row must have typed events, the latest event must
    agree with the row state, no generic-derived financial events may appear
    alongside typed ones, and no idempotency key may repeat."""
    if not typed_writes_enabled():
        return []
    findings: list[dict[str, Any]] = []
    events = db.scalars(
        select(models.PositionEvent)
        .where(models.PositionEvent.position_id == row.id)
        .order_by(models.PositionEvent.event_at, models.PositionEvent.id)
    ).all()
    base = {"user_id": str(user_id), "mode": mode, "position_id": str(row.id)}
    typed_events = [e for e in events if (e.event_metadata or {}).get("typed")]
    if not typed_events:
        findings.append({"type": "missing_typed_event", **base, "row_state": row.position_state})
    else:
        last = typed_events[-1]
        if last.new_state != row.position_state:
            findings.append({
                "type": "typed_event_state_lag", **base,
                "last_event_state": last.new_state, "row_state": row.position_state,
            })
    generic_financial = [
        e for e in events
        if not (e.event_metadata or {}).get("typed") and e.source in _GENERIC_EVENT_SOURCES
    ]
    if typed_events and generic_financial:
        findings.append({
            "type": "unexpected_generic_event", **base,
            "generic_event_types": sorted({e.event_type for e in generic_financial}),
        })
    seen_keys: dict[str, int] = {}
    for event in events:
        if event.idempotency_key:
            seen_keys[event.idempotency_key] = seen_keys.get(event.idempotency_key, 0) + 1
    duplicates = [k for k, count in seen_keys.items() if count > 1]
    if duplicates:
        findings.append({"type": "duplicate_typed_event", **base, "keys": duplicates[:5]})
    return findings
