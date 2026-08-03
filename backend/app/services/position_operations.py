"""Explicit typed position operations (Phase 2B1).

This service owns BUSINESS transitions of the durable PostgreSQL position
ledger; position_store keeps the persistence primitives (slot advisory lock,
bounded timeouts, watchdog, breaker, parity helpers).

AUTHORITY (unchanged): JSON position files remain the EXECUTION READ
AUTHORITY. PostgreSQL is read here only for locking, state validation,
mutation, idempotency and parity — never to decide whether to place an order.

Call ordering at every integration site:
    broker/business transition succeeds
      -> existing authoritative JSON update (unchanged)
      -> typed operation here (never raises into the execution path)
      -> parity stamp inside the same DB transaction

Event sources are supplied by the actual caller (mapped from the signal's
server-side source field) — never inferred from JSON shape. Idempotency keys
are deterministic SHA-256 digests of server-side identifiers (user, mode,
transition, order/signal/fill ids) — never timestamps alone, never hash().
Reusing a key with a conflicting payload raises ConflictingIdempotencyError
(visible + audited), it is never silently absorbed.

Every operation runs atomically under the slot's pg_advisory_xact_lock via
position_store.run_guarded_slot_operation: no event commits without its
position mutation and vice versa.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.db import models
from app.db.engine import session_scope
from app.domain.position_transitions import assert_position_transition
from app.services import position_store
from app.services.position_store import (
    ConflictingIdempotencyError,
    run_guarded_slot_operation,
    typed_writes_enabled,
)

logger = logging.getLogger("nova_signal_router.position_operations")

# Explicit event sources — supplied by callers, never inferred.
STRATEGY_ENTRY = "STRATEGY_ENTRY"
STRATEGY_EXIT = "STRATEGY_EXIT"
STRATEGY_REVERSAL = "STRATEGY_REVERSAL"
MANUAL_EXIT = "MANUAL_EXIT"
EOD_EXIT = "EOD_EXIT"
STOP_LOSS_EXIT = "STOP_LOSS_EXIT"
TAKE_PROFIT_EXIT = "TAKE_PROFIT_EXIT"
TRAILING_STOP_EXIT = "TRAILING_STOP_EXIT"
BROKER_RECONCILIATION = "BROKER_RECONCILIATION"
STARTUP_RECONCILIATION = "STARTUP_RECONCILIATION"
GHOST_POSITION_RECONCILIATION = "GHOST_POSITION_RECONCILIATION"

TYPED_SOURCES = {
    STRATEGY_ENTRY, STRATEGY_EXIT, STRATEGY_REVERSAL, MANUAL_EXIT, EOD_EXIT,
    STOP_LOSS_EXIT, TAKE_PROFIT_EXIT, TRAILING_STOP_EXIT,
    BROKER_RECONCILIATION, STARTUP_RECONCILIATION, GHOST_POSITION_RECONCILIATION,
}

_CLOSE_EVENT_TYPE_BY_SOURCE = {
    MANUAL_EXIT: "manual_exit",
    EOD_EXIT: "eod_exit",
    STOP_LOSS_EXIT: "stop_loss_exit",
    TAKE_PROFIT_EXIT: "take_profit_exit",
    TRAILING_STOP_EXIT: "trailing_exit",
    STRATEGY_REVERSAL: "reversal_exit_filled",
    BROKER_RECONCILIATION: "reconciliation_close",
    STARTUP_RECONCILIATION: "reconciliation_close",
    GHOST_POSITION_RECONCILIATION: "reconciliation_close",
}


def event_source_for_signal(signal_source: str | None, exit_reason: str | None = None) -> str:
    """Map the server-side signal source (never client payload authority)
    onto an explicit event source."""
    source = (signal_source or "").strip().lower()
    reason = (exit_reason or "").strip().upper()
    if source == "manual_panel":
        return MANUAL_EXIT
    if source == "server_side_eod_squareoff":
        return EOD_EXIT
    if source in {"server_side_option_monitor", "dhan_ltp_exit_confirmation"}:
        if reason.startswith("TRAIL"):
            return TRAILING_STOP_EXIT
        if reason == "TP":
            return TAKE_PROFIT_EXIT
        return STOP_LOSS_EXIT
    return STRATEGY_EXIT


@dataclass(frozen=True)
class OperationContext:
    """Server-derived identity for one typed operation. Never accepts client
    payload routing fields."""

    user_id: uuid.UUID
    execution_mode: str  # paper | live

    def __post_init__(self) -> None:
        if self.execution_mode not in {"paper", "live"}:
            raise ValueError(f"Invalid execution_mode {self.execution_mode!r}.")


def _key(*parts: Any) -> str:
    raw = ":".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _payload_sha(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _emit_event(
    db,
    row: models.StrategyInstancePosition,
    *,
    event_type: str,
    source: str,
    idempotency_key: str,
    previous_state: str | None,
    signal_id: str | None = None,
    broker_order_id: str | None = None,
    quantity: int | None = None,
    price_paise: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Insert one typed event. Same key + same payload -> idempotent skip;
    same key + different payload -> ConflictingIdempotencyError."""
    payload = {
        "event_type": event_type,
        "source": source,
        "previous_state": previous_state,
        "new_state": row.position_state,
        "signal_id": signal_id,
        "broker_order_id": broker_order_id,
        "quantity": quantity,
        "price_paise": price_paise,
    }
    payload_sha = _payload_sha(payload)
    existing = db.scalar(
        select(models.PositionEvent).where(models.PositionEvent.idempotency_key == idempotency_key)
    )
    if existing is not None:
        existing_sha = (existing.event_metadata or {}).get("payload_sha")
        if existing_sha == payload_sha:
            return "duplicate"
        raise ConflictingIdempotencyError(
            f"Idempotency key reused with conflicting payload (event_type={event_type})."
        )
    db.add(
        models.PositionEvent(
            position_id=row.id,
            user_id=row.user_id,
            event_type=event_type,
            source=source,
            signal_id=signal_id,
            broker_order_id=broker_order_id,
            quantity=quantity if quantity is not None else row.open_quantity,
            price_paise=price_paise if price_paise is not None else row.avg_entry_price_paise,
            previous_state=previous_state,
            new_state=row.position_state,
            event_metadata={**(metadata or {}), "payload_sha": payload_sha, "typed": True},
            idempotency_key=idempotency_key,
        )
    )
    return "created"


def _stamp(row: models.StrategyInstancePosition) -> None:
    row.updated_at = position_store._now()
    row.version = int(row.version or 0) + 1


def _finish_parity(row: models.StrategyInstancePosition, json_position: dict[str, Any] | None) -> None:
    if json_position is not None:
        position_store._record_parity(row, json_position)


# ---------------------------------------------------------------------------
# Typed operations
# ---------------------------------------------------------------------------

def record_entry(
    ctx: OperationContext,
    *,
    source: str,
    signal_id: str | None,
    order_id: str | None,
    requested_qty: int,
    filled_qty: int,
    fill_price: float | None,
    partial_fill: bool,
    position_snapshot: dict[str, Any],
) -> Any:
    """Confirmed broker entry: creates the position row (entering when the
    fill price is still unknown, open otherwise) and emits entry_submitted
    plus the fill event."""
    if requested_qty <= 0 or filled_qty <= 0:
        raise ValueError("Entry quantities must be positive.")

    def txn() -> str:
        with session_scope() as db:
            position_store._begin_bounded_slot_txn(db, ctx.user_id, ctx.execution_mode)
            row = position_store._active_row(db, ctx.user_id, ctx.execution_mode)
            if row is not None:
                if order_id and row.entry_order_id == order_id:
                    same_entry = (
                        int(row.filled_entry_quantity or 0) == filled_qty
                        and int(row.open_quantity or 0) == filled_qty
                        and row.security_id == str(position_snapshot.get("security_id"))
                        and row.avg_entry_price_paise == position_store.to_paise(fill_price)
                    )
                    if same_entry:
                        return "duplicate"
                    raise ConflictingIdempotencyError(
                        "Entry order id reused with a conflicting position payload."
                    )
                raise ConflictingIdempotencyError(
                    "record_entry on a slot that already has an active position "
                    f"(existing entry_order_id={row.entry_order_id!r}, new={order_id!r})."
                )
            target = "entering" if fill_price is None else "open"
            assert_position_transition(None, target)
            row = models.StrategyInstancePosition(
                user_id=ctx.user_id, execution_mode=ctx.execution_mode, position_state=target
            )
            db.add(row)
            position_store._apply_json_to_row(row, position_snapshot)
            row.entry_order_id = order_id or row.entry_order_id
            row.position_state = target
            db.flush()
            base = (ctx.user_id, ctx.execution_mode, order_id or signal_id)
            _emit_event(
                db, row, event_type="entry_submitted", source=source,
                idempotency_key=_key(*base, "entry_submitted"),
                previous_state=None, signal_id=signal_id, broker_order_id=order_id,
                quantity=requested_qty,
            )
            if fill_price is not None:
                fill_type = "entry_partial_fill" if partial_fill else "entry_filled"
                _emit_event(
                    db, row, event_type=fill_type, source=source,
                    idempotency_key=_key(*base, fill_type, filled_qty),
                    previous_state=None, signal_id=signal_id, broker_order_id=order_id,
                    quantity=filled_qty, price_paise=position_store.to_paise(fill_price),
                )
            _finish_parity(row, position_snapshot)
            return "written"

    return _run("entry", ctx, txn)


def apply_entry_fill(
    ctx: OperationContext,
    *,
    source: str,
    order_id: str | None,
    fill_price: float,
    position_snapshot: dict[str, Any],
) -> Any:
    """Late fill-price confirmation for a pending entry (broker order-status
    sync). entering -> open; open -> open is a legal metadata refresh."""

    def txn() -> str:
        with session_scope() as db:
            position_store._begin_bounded_slot_txn(db, ctx.user_id, ctx.execution_mode)
            row = position_store._active_row(db, ctx.user_id, ctx.execution_mode)
            if row is None:
                return "noop"  # position already closed; nothing to confirm
            if order_id and row.entry_order_id and row.entry_order_id != order_id:
                raise ConflictingIdempotencyError(
                    f"apply_entry_fill order mismatch ({row.entry_order_id!r} != {order_id!r})."
                )
            previous = row.position_state
            assert_position_transition(previous, "open")
            position_store._apply_json_to_row(row, position_snapshot)
            row.position_state = "open"
            _emit_event(
                db, row, event_type="entry_filled", source=source,
                idempotency_key=_key(ctx.user_id, ctx.execution_mode, order_id, "entry_filled_sync"),
                previous_state=previous, broker_order_id=order_id,
                price_paise=position_store.to_paise(fill_price),
            )
            _finish_parity(row, position_snapshot)
            return "written"

    return _run("entry", ctx, txn)


def apply_partial_exit_fill(
    ctx: OperationContext,
    *,
    source: str,
    signal_id: str | None,
    exit_order_id: str | None,
    filled_qty: int,
    remaining_qty: int,
    position_snapshot: dict[str, Any],
) -> Any:
    if filled_qty <= 0 or remaining_qty <= 0:
        raise ValueError("Partial exit requires positive filled and remaining quantities.")

    def txn() -> str:
        with session_scope() as db:
            position_store._begin_bounded_slot_txn(db, ctx.user_id, ctx.execution_mode)
            row = position_store._active_row(db, ctx.user_id, ctx.execution_mode)
            if row is None:
                return "noop"
            event_key = _key(ctx.user_id, ctx.execution_mode, exit_order_id, "exit_partial", filled_qty, remaining_qty)
            existing = db.scalar(select(models.PositionEvent).where(models.PositionEvent.idempotency_key == event_key))
            if existing is not None:
                if (existing.broker_order_id, existing.quantity, existing.signal_id) == (exit_order_id, remaining_qty, signal_id):
                    return "duplicate"
                raise ConflictingIdempotencyError("Partial-fill identity reused with conflicting payload.")
            previous = row.position_state
            assert_position_transition(previous, "exiting")
            previous_qty = int(row.open_quantity or 0)
            position_store._apply_json_to_row(row, position_snapshot)
            row.open_quantity = min(previous_qty, remaining_qty)
            row.position_state = "exiting"
            _emit_event(
                db, row, event_type="exit_partial_fill", source=source,
                # Distinct fill identities stay distinct via (order, filled, remaining).
                idempotency_key=event_key,
                previous_state=previous, signal_id=signal_id, broker_order_id=exit_order_id,
                quantity=remaining_qty,
            )
            _finish_parity(row, position_snapshot)
            return "written"

    return _run("partial_fill", ctx, txn)


def close_position(
    ctx: OperationContext,
    *,
    source: str,
    signal_id: str | None = None,
    exit_order_id: str | None = None,
    reason: str | None = None,
) -> Any:
    """Terminal close from any active state. Closing an already-flat slot is
    an idempotent no-op (never a duplicate sell, never an error)."""

    def txn() -> str:
        with session_scope() as db:
            position_store._begin_bounded_slot_txn(db, ctx.user_id, ctx.execution_mode)
            row = position_store._active_row(db, ctx.user_id, ctx.execution_mode)
            if row is None:
                return "noop"  # CLOSED -> exit is idempotent, not an event
            previous = row.position_state
            assert_position_transition(previous, "closed")
            row.position_state = "closed"
            row.filled_exit_quantity = int(row.filled_entry_quantity or row.open_quantity or 0)
            row.open_quantity = 0
            if exit_order_id:
                row.exit_order_id = exit_order_id
            row.closed_at = row.closed_at or position_store._now()
            row.parity_status = "match"
            row.last_parity_at = position_store._now()
            _stamp(row)
            _emit_event(
                db, row,
                event_type=_CLOSE_EVENT_TYPE_BY_SOURCE.get(source, "exit_filled"),
                source=source,
                idempotency_key=_key(ctx.user_id, ctx.execution_mode, row.id, "close", exit_order_id or signal_id or source),
                previous_state=previous, signal_id=signal_id, broker_order_id=exit_order_id,
                quantity=0, metadata={"reason": reason} if reason else None,
            )
            return "written"

    return _run("exit", ctx, txn)


def mark_reversal_requested(
    ctx: OperationContext,
    *,
    signal_id: str | None,
    from_option_side: str | None,
    to_option_side: str | None,
) -> Any:
    def txn() -> str:
        with session_scope() as db:
            position_store._begin_bounded_slot_txn(db, ctx.user_id, ctx.execution_mode)
            row = position_store._active_row(db, ctx.user_id, ctx.execution_mode)
            if row is None:
                return "noop"
            previous = row.position_state
            assert_position_transition(previous, "reversing")
            row.position_state = "reversing"
            row.reversal_metadata = {
                **(row.reversal_metadata or {}),
                "from_option_side": from_option_side,
                "to_option_side": to_option_side,
                "signal_id": signal_id,
            }
            _stamp(row)
            _emit_event(
                db, row, event_type="reversal_requested", source=STRATEGY_REVERSAL,
                idempotency_key=_key(ctx.user_id, ctx.execution_mode, row.id, "reversal_requested", signal_id),
                previous_state=previous, signal_id=signal_id,
            )
            return "written"

    return _run("reversal", ctx, txn)


def mark_exit_pending(
    ctx: OperationContext,
    *,
    source: str,
    signal_id: str | None,
    exit_order_id: str | None,
    broker_status: str | None,
    position_snapshot: dict[str, Any] | None = None,
) -> Any:
    """Exit submitted but not confirmed TRADED (e.g. reversal exit leg in
    transit). Keeps/advances the lifecycle without claiming the fill."""

    def txn() -> str:
        with session_scope() as db:
            position_store._begin_bounded_slot_txn(db, ctx.user_id, ctx.execution_mode)
            row = position_store._active_row(db, ctx.user_id, ctx.execution_mode)
            if row is None:
                return "noop"
            previous = row.position_state
            target = "reversing" if previous == "reversing" else "exiting"
            assert_position_transition(previous, target)
            if position_snapshot is not None:
                position_store._apply_json_to_row(row, position_snapshot)
            row.position_state = target
            if exit_order_id:
                row.exit_order_id = exit_order_id
            _stamp(row)
            _emit_event(
                db, row, event_type="exit_submitted", source=source,
                idempotency_key=_key(ctx.user_id, ctx.execution_mode, row.id, "exit_submitted", exit_order_id or signal_id),
                previous_state=previous, signal_id=signal_id, broker_order_id=exit_order_id,
                metadata={"broker_status": broker_status},
            )
            if position_snapshot is not None:
                _finish_parity(row, position_snapshot)
            return "written"

    return _run("exit", ctx, txn)


def reconcile_position(
    ctx: OperationContext,
    *,
    source: str,
    status: str,
    message: str | None = None,
    checked_at: str | None = None,
    position_snapshot: dict[str, Any] | None = None,
) -> Any:
    """Broker/startup/ghost reconciliation metadata update (state unchanged).

    checked_at is the broker-check identifier of THIS reconciliation run
    (server-generated), so identical retries dedupe while successive runs
    with the same status stay distinct."""

    def txn() -> str:
        with session_scope() as db:
            position_store._begin_bounded_slot_txn(db, ctx.user_id, ctx.execution_mode)
            row = position_store._active_row(db, ctx.user_id, ctx.execution_mode)
            if row is None:
                return "noop"
            previous = row.position_state
            event_key = _key(ctx.user_id, ctx.execution_mode, row.id, "reconcile", status, checked_at or _payload_sha({"m": message})[:16])
            if db.scalar(select(models.PositionEvent).where(models.PositionEvent.idempotency_key == event_key)) is not None:
                return "duplicate"
            previous_qty = int(row.open_quantity or 0)
            if position_snapshot is not None:
                position_store._apply_json_to_row(row, position_snapshot)
                row.open_quantity = min(previous_qty, int(row.open_quantity or 0))
                row.position_state = previous
            row.reconciliation_status = status[:40]
            row.last_broker_reconciled_at = position_store._now()
            _stamp(row)
            _emit_event(
                db, row, event_type="reconciliation_adjustment", source=source,
                idempotency_key=event_key,
                previous_state=previous,
                metadata={"status": status, "message": (message or "")[:300]},
            )
            if position_snapshot is not None:
                _finish_parity(row, position_snapshot)
            return "written"

    return _run("reconciliation", ctx, txn)


def mark_reconciliation_error(
    ctx: OperationContext,
    *,
    source: str,
    reason: str,
) -> Any:
    def txn() -> str:
        with session_scope() as db:
            position_store._begin_bounded_slot_txn(db, ctx.user_id, ctx.execution_mode)
            row = position_store._active_row(db, ctx.user_id, ctx.execution_mode)
            if row is None:
                return "noop"
            previous = row.position_state
            assert_position_transition(previous, "error_reconciling")
            row.position_state = "error_reconciling"
            row.error_reason = reason[:500]
            _stamp(row)
            _emit_event(
                db, row, event_type="reconciliation_error", source=source,
                idempotency_key=_key(ctx.user_id, ctx.execution_mode, row.id, "reconcile_error", _payload_sha({"r": reason})[:16]),
                previous_state=previous, metadata={"reason": reason[:300]},
            )
            return "written"

    return _run("reconciliation", ctx, txn)


# ---------------------------------------------------------------------------
# Guarded runner + call-site adapters (used by execution_router/workers)
# ---------------------------------------------------------------------------

def _run(operation_kind: str, ctx: OperationContext, txn) -> Any:
    try:
        return run_guarded_slot_operation(
            operation_kind=operation_kind,
            user_id=ctx.user_id,
            execution_mode=ctx.execution_mode,
            txn=txn,
        )
    except ConflictingIdempotencyError as exc:
        # Visible contract violation: audit + count, never raised into the
        # execution path, never triggers a broker retry.
        logger.error("typed position operation conflict user=%s mode=%s: %s", ctx.user_id, ctx.execution_mode, exc)
        try:
            from app.services.audit_logger import log_audit_event

            log_audit_event(
                "POSITION_TYPED_OPERATION_CONFLICT", str(exc), severity="CRITICAL",
                metadata={"operation": operation_kind, "user_id": str(ctx.user_id)},
            )
        except Exception:
            pass
        return None


def _context() -> OperationContext | None:
    """Server-derived context from the bound execution user + engine mode.
    Returns None (skip) when typed writes are off or no tenant is bound."""
    if not typed_writes_enabled():
        return None
    from app.services.execution_context import current_execution_user
    from app.services.state_store import get_engine_mode

    user = current_execution_user()
    if user is None or user.is_dev:
        return None
    mode = get_engine_mode() or "paper"
    return OperationContext(user_id=user.id, execution_mode=mode)


# --- one-line call-site adapters (always safe to call; no-ops when off) -----

def _signal_exit_reason(signal) -> str | None:
    raw = getattr(signal, "raw_payload", None)
    return raw.get("exit_reason") if isinstance(raw, dict) else None


def on_entry_placed(signal, order_result: dict[str, Any], position: dict[str, Any], *, source: str = STRATEGY_ENTRY) -> None:
    ctx = _context()
    if ctx is None:
        return
    record_entry(
        ctx, source=source, signal_id=getattr(signal, "signal_id", None),
        order_id=order_result.get("order_id"),
        requested_qty=int(order_result.get("requested_qty") or position.get("requested_qty") or position.get("qty") or 0),
        filled_qty=int(position.get("qty") or 0),
        fill_price=position.get("entry_price"),
        partial_fill=bool(position.get("partial_fill")),
        position_snapshot=position,
    )


def on_entry_fill_synced(position: dict[str, Any], *, order_id: str | None, fill_price: float) -> None:
    ctx = _context()
    if ctx is None:
        return
    apply_entry_fill(
        ctx, source=BROKER_RECONCILIATION, order_id=order_id,
        fill_price=fill_price, position_snapshot=position,
    )


def on_partial_exit(signal, order_result: dict[str, Any], updated_position: dict[str, Any]) -> None:
    ctx = _context()
    if ctx is None:
        return
    apply_partial_exit_fill(
        ctx,
        source=event_source_for_signal(getattr(signal, "source", None), _signal_exit_reason(signal)),
        signal_id=getattr(signal, "signal_id", None),
        exit_order_id=order_result.get("order_id"),
        filled_qty=int(order_result.get("filled_qty") or 0),
        remaining_qty=int(updated_position.get("qty") or 0),
        position_snapshot=updated_position,
    )


def on_position_closed(
    signal,
    order_result: dict[str, Any] | None,
    *,
    source: str | None = None,
    reason: str | None = None,
) -> None:
    ctx = _context()
    if ctx is None:
        return
    close_position(
        ctx,
        source=source or event_source_for_signal(getattr(signal, "source", None), _signal_exit_reason(signal)),
        signal_id=getattr(signal, "signal_id", None),
        exit_order_id=(order_result or {}).get("order_id"),
        reason=reason,
    )


def on_reversal_marked(signal, *, from_option_side: str | None, to_option_side: str | None) -> None:
    ctx = _context()
    if ctx is None:
        return
    mark_reversal_requested(
        ctx, signal_id=getattr(signal, "signal_id", None),
        from_option_side=from_option_side, to_option_side=to_option_side,
    )


def on_exit_pending(signal, exit_result: dict[str, Any], position: dict[str, Any] | None, *, source: str = STRATEGY_REVERSAL) -> None:
    ctx = _context()
    if ctx is None:
        return
    mark_exit_pending(
        ctx, source=source, signal_id=getattr(signal, "signal_id", None),
        exit_order_id=exit_result.get("order_id"), broker_status=exit_result.get("status"),
        position_snapshot=position,
    )


def on_reconciliation(*, source: str, status: str, message: str | None = None, checked_at: str | None = None, snapshot: dict[str, Any] | None = None) -> None:
    ctx = _context()
    if ctx is None:
        return
    reconcile_position(
        ctx, source=source, status=status, message=message,
        checked_at=checked_at, position_snapshot=snapshot,
    )


def on_reconciliation_error(*, source: str, reason: str) -> None:
    ctx = _context()
    if ctx is None:
        return
    mark_reconciliation_error(ctx, source=source, reason=reason)
