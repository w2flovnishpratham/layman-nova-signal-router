"""Phase 2B1: explicit typed position operations, transition matrix,
idempotency, generic/typed precedence, parity completeness."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_position_shadow import ENTRY_POSITION, runtime_dirs  # noqa: F401


@pytest.fixture
def typed_enabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "POSITION_DB_TYPED_WRITES_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "POSITION_DB_SHADOW_WRITE_ENABLED", True, raising=False)


@pytest.fixture
def ctx_user(mu_db):
    user = make_user("typed@example.com")
    from app.services.position_operations import OperationContext

    return user, OperationContext(user_id=user.id, execution_mode="live")


def _row(user_id):
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        rows = db.scalars(
            select(models.StrategyInstancePosition)
            .where(models.StrategyInstancePosition.user_id == user_id)
            .order_by(models.StrategyInstancePosition.created_at)
        ).all()
        db.expunge_all()
        return rows


def _events(user_id):
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        rows = db.scalars(
            select(models.PositionEvent)
            .where(models.PositionEvent.user_id == user_id)
            .order_by(models.PositionEvent.event_at, models.PositionEvent.id)
        ).all()
        db.expunge_all()
        return rows


def _entry(ctx, ops, *, order_id="OP-1", price=123.45, partial=False):
    return ops.record_entry(
        ctx, source=ops.STRATEGY_ENTRY, signal_id="SIG-1", order_id=order_id,
        requested_qty=75, filled_qty=75, fill_price=price, partial_fill=partial,
        position_snapshot=dict(ENTRY_POSITION),
    )


# ---------------------------------------------------------------------------
# Transition matrix
# ---------------------------------------------------------------------------

def test_transition_matrix():
    from app.domain.position_transitions import PositionTransitionError, assert_position_transition

    assert_position_transition(None, "entering")
    assert_position_transition(None, "open")
    assert_position_transition("entering", "open")
    assert_position_transition("open", "reversing")
    assert_position_transition("open", "open")  # metadata refresh
    assert_position_transition("exiting", "exiting")  # partial exits
    assert_position_transition("reversing", "closed")
    assert_position_transition("error_reconciling", "closed")
    for current, target in [
        ("closed", "exiting"),   # CLOSED -> partial exit must fail
        ("closed", "open"),
        (None, "exiting"),
        (None, "reversing"),
        ("entering", "entering"),
    ]:
        with pytest.raises(PositionTransitionError):
            assert_position_transition(current, target)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def test_entry_full_fill_and_duplicate(typed_enabled, ctx_user):
    from app.services import position_operations as ops

    user, ctx = ctx_user
    assert _entry(ctx, ops) == "written"
    assert _entry(ctx, ops) == "duplicate"  # identical retry: same order id

    rows = _row(user.id)
    assert len(rows) == 1 and rows[0].position_state == "open"
    assert rows[0].avg_entry_price_paise == 12345
    events = _events(user.id)
    assert [e.event_type for e in events] == ["entry_submitted", "entry_filled"]
    assert all(e.source == "STRATEGY_ENTRY" for e in events)
    assert all((e.event_metadata or {}).get("typed") for e in events)


def test_same_entry_order_with_conflicting_payload_is_visible(typed_enabled, ctx_user):
    from app.services import position_operations as ops

    user, ctx = ctx_user
    assert _entry(ctx, ops) == "written"
    conflicting = dict(ENTRY_POSITION, qty=150, filled_qty=150, security_id="DIFFERENT")
    assert ops.record_entry(
        ctx, source=ops.STRATEGY_ENTRY, signal_id="SIG-1", order_id="OP-1",
        requested_qty=150, filled_qty=150, fill_price=123.45, partial_fill=False,
        position_snapshot=conflicting,
    ) is None
    row = _row(user.id)[0]
    assert (row.security_id, row.open_quantity) == ("12345", 75)
    assert [event.event_type for event in _events(user.id)] == ["entry_submitted", "entry_filled"]


def test_entry_pending_then_fill_sync(typed_enabled, ctx_user):
    from app.services import position_operations as ops

    user, ctx = ctx_user
    pending = dict(ENTRY_POSITION)
    pending["entry_price"] = None
    pending["live_pnl"] = {"status": "waiting_entry_fill"}
    ops.record_entry(
        ctx, source=ops.STRATEGY_ENTRY, signal_id="SIG-2", order_id="OP-2",
        requested_qty=75, filled_qty=75, fill_price=None, partial_fill=False,
        position_snapshot=pending,
    )
    assert _row(user.id)[0].position_state == "entering"

    filled = dict(ENTRY_POSITION)
    assert ops.apply_entry_fill(
        ctx, source=ops.BROKER_RECONCILIATION, order_id="OP-2",
        fill_price=123.45, position_snapshot=filled,
    ) == "written"
    row = _row(user.id)[0]
    assert row.position_state == "open" and row.avg_entry_price_paise == 12345
    assert [e.event_type for e in _events(user.id)] == ["entry_submitted", "entry_filled"]

    # Broker-timeout style retry of the same sync: single event stays single.
    ops.apply_entry_fill(ctx, source=ops.BROKER_RECONCILIATION, order_id="OP-2",
                         fill_price=123.45, position_snapshot=filled)
    assert [e.event_type for e in _events(user.id)] == ["entry_submitted", "entry_filled"]


def test_entry_conflict_on_occupied_slot_is_visible_not_raised(typed_enabled, ctx_user, monkeypatch):
    from app.services import position_operations as ops

    audit = []
    monkeypatch.setattr(
        "app.services.audit_logger.log_audit_event",
        lambda event_type, message, severity="INFO", metadata=None: audit.append(event_type),
    )
    user, ctx = ctx_user
    _entry(ctx, ops, order_id="OP-A")
    result = _entry(ctx, ops, order_id="OP-B")  # different order into occupied slot
    assert result is None  # visible failure, never raised into execution path
    assert "POSITION_TYPED_OPERATION_CONFLICT" in audit
    assert len(_row(user.id)) == 1  # nothing mutated


# ---------------------------------------------------------------------------
# Exit family
# ---------------------------------------------------------------------------

def test_partial_then_full_exit_and_duplicates(typed_enabled, ctx_user):
    from app.services import position_operations as ops

    user, ctx = ctx_user
    _entry(ctx, ops)
    partial_snapshot = dict(ENTRY_POSITION)
    partial_snapshot["qty"] = 25
    for _ in range(2):  # duplicate identical partial-fill delivery
        ops.apply_partial_exit_fill(
            ctx, source=ops.STRATEGY_EXIT, signal_id="SIG-X", exit_order_id="X-1",
            filled_qty=50, remaining_qty=25, position_snapshot=partial_snapshot,
        )
    row = _row(user.id)[0]
    assert row.position_state == "exiting" and row.open_quantity == 25

    for _ in range(2):  # duplicate close delivery
        ops.close_position(ctx, source=ops.STRATEGY_EXIT, signal_id="SIG-X", exit_order_id="X-2")
    row = _row(user.id)[0]
    assert row.position_state == "closed" and row.open_quantity == 0
    types = [e.event_type for e in _events(user.id)]
    assert types == ["entry_submitted", "entry_filled", "exit_partial_fill", "exit_filled"]

    # CLOSED -> partial exit is rejected before any mutation (counted failure).
    from app.services import position_store

    before = dict(position_store.shadow_write_failures)
    ops.apply_partial_exit_fill(
        ctx, source=ops.STRATEGY_EXIT, signal_id="SIG-X", exit_order_id="X-3",
        filled_qty=10, remaining_qty=15, position_snapshot=partial_snapshot,
    )
    assert _row(user.id)[0].position_state == "closed"
    assert position_store.shadow_write_failures["partial_fill"] == before["partial_fill"]  # noop: no active row
    # ... and closing an already-flat slot stays an idempotent no-op.
    assert ops.close_position(ctx, source=ops.MANUAL_EXIT) == "noop"


def test_close_event_types_by_source(typed_enabled, ctx_user):
    from app.services import position_operations as ops

    user, ctx = ctx_user
    expectations = [
        (ops.MANUAL_EXIT, "manual_exit"),
        (ops.EOD_EXIT, "eod_exit"),
        (ops.STOP_LOSS_EXIT, "stop_loss_exit"),
        (ops.TAKE_PROFIT_EXIT, "take_profit_exit"),
        (ops.TRAILING_STOP_EXIT, "trailing_exit"),
        (ops.GHOST_POSITION_RECONCILIATION, "reconciliation_close"),
        (ops.STRATEGY_EXIT, "exit_filled"),
    ]
    for index, (source, expected_type) in enumerate(expectations):
        _entry(ctx, ops, order_id=f"OP-{index}")
        ops.close_position(ctx, source=source, exit_order_id=f"XC-{index}")
        closes = [e for e in _events(user.id) if e.event_type == expected_type]
        assert closes, f"{source} should emit {expected_type}"
        assert closes[-1].source == source


def test_two_exit_sources_racing_single_close(typed_enabled, ctx_user):
    """Manual exit and EOD exit hitting the same position: first close wins,
    second is an idempotent no-op with no second event."""
    from app.services import position_operations as ops

    user, ctx = ctx_user
    _entry(ctx, ops)
    assert ops.close_position(ctx, source=ops.MANUAL_EXIT, exit_order_id="RACE-1") == "written"
    assert ops.close_position(ctx, source=ops.EOD_EXIT, exit_order_id="RACE-2") == "noop"
    close_events = [e for e in _events(user.id) if e.new_state == "closed"]
    assert len(close_events) == 1 and close_events[0].event_type == "manual_exit"


# ---------------------------------------------------------------------------
# Reversal
# ---------------------------------------------------------------------------

def test_reversal_lifecycle_and_duplicate_delivery(typed_enabled, ctx_user):
    from app.services import position_operations as ops

    user, ctx = ctx_user
    _entry(ctx, ops)
    for _ in range(2):  # duplicate reversal delivery
        ops.mark_reversal_requested(ctx, signal_id="REV-1", from_option_side="CE", to_option_side="PE")
    row = _row(user.id)[0]
    assert row.position_state == "reversing"

    # Exit leg submitted but unconfirmed keeps the reversal lifecycle.
    ops.mark_exit_pending(ctx, source=ops.STRATEGY_REVERSAL, signal_id="REV-1",
                          exit_order_id="REV-X", broker_status="TRANSIT")
    assert _row(user.id)[0].position_state == "reversing"

    # Confirmed exit closes; opposite entry opens a NEW row.
    ops.close_position(ctx, source=ops.STRATEGY_REVERSAL, signal_id="REV-1", exit_order_id="REV-X")
    reversed_entry = dict(ENTRY_POSITION)
    reversed_entry["option_side"] = "PE"
    ops.record_entry(ctx, source=ops.STRATEGY_REVERSAL, signal_id="REV-1", order_id="REV-E",
                     requested_qty=75, filled_qty=75, fill_price=95.0, partial_fill=False,
                     position_snapshot=reversed_entry)
    rows = _row(user.id)
    assert [r.position_state for r in rows] == ["closed", "open"]
    assert rows[1].option_side == "PE"
    types = [e.event_type for e in _events(user.id)]
    assert types.count("reversal_requested") == 1
    assert types.count("reversal_exit_filled") == 1


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def test_reconciliation_dedupe_error_and_recovery(typed_enabled, ctx_user):
    from app.services import position_operations as ops

    user, ctx = ctx_user
    _entry(ctx, ops)
    for _ in range(2):  # identical retry of one reconciliation run
        ops.reconcile_position(ctx, source=ops.STARTUP_RECONCILIATION, status="verified",
                               checked_at="2026-07-12T10:00:00+00:00")
    ops.reconcile_position(ctx, source=ops.STARTUP_RECONCILIATION, status="verified",
                           checked_at="2026-07-12T10:05:00+00:00")  # next run stays distinct
    recon = [e for e in _events(user.id) if e.event_type == "reconciliation_adjustment"]
    assert len(recon) == 2

    ops.mark_reconciliation_error(ctx, source=ops.BROKER_RECONCILIATION, reason="ambiguous broker response")
    row = _row(user.id)[0]
    assert row.position_state == "error_reconciling" and row.error_reason.startswith("ambiguous")
    # Recovery: error state may close.
    ops.close_position(ctx, source=ops.BROKER_RECONCILIATION, reason="broker_flat")
    assert _row(user.id)[0].position_state == "closed"


def test_restart_between_json_write_and_typed_write_is_detected(typed_enabled, ctx_user, runtime_dirs):
    """Crash after the JSON write but before the typed DB write: parity must
    surface the missing DB position."""
    from app.services import position_store, state_store
    from app.services.execution_context import bind_execution_context
    from app.services.user_context import current_user_from_model

    user, ctx = ctx_user
    with bind_execution_context(current_user_from_model(user)):
        state_store.set_live_open_position(dict(ENTRY_POSITION))  # generic hook inert (typed on)
        json_live = state_store.get_live_open_position()
    findings = position_store.compare_user_positions(user.id, json_live=json_live, json_paper=None)
    assert [f["type"] for f in findings] == ["missing_db_shadow_position"]


# ---------------------------------------------------------------------------
# Precedence / compatibility
# ---------------------------------------------------------------------------

def test_typed_on_makes_generic_hook_inert(typed_enabled, ctx_user, runtime_dirs):
    from app.services import state_store
    from app.services.execution_context import bind_execution_context
    from app.services.user_context import current_user_from_model

    user, ctx = ctx_user
    with bind_execution_context(current_user_from_model(user)):
        state_store.set_live_open_position(dict(ENTRY_POSITION))
        state_store.clear_live_open_position()
    assert _row(user.id) == []      # no rows from the generic hook
    assert _events(user.id) == []   # and no generic events -> no duplicates possible


def test_no_duplicate_events_when_both_mechanisms_enabled(typed_enabled, ctx_user, runtime_dirs):
    """Full simulated flow with both flags on: JSON write fires the (inert)
    generic hook, the typed adapter fires the typed op — exactly one set of
    financial events exists."""
    from app.services import position_operations as ops
    from app.services import state_store
    from app.services.execution_context import bind_execution_context
    from app.services.user_context import current_user_from_model

    user, ctx = ctx_user
    with bind_execution_context(current_user_from_model(user)):
        state_store.set_live_open_position(dict(ENTRY_POSITION))  # authoritative JSON
        order_result = {"order_id": "OP-1", "requested_qty": 75, "filled_qty": 75}

        class Sig:
            signal_id = "SIG-1"
            source = None
            raw_payload = {}

        ops.on_entry_placed(Sig(), order_result, dict(ENTRY_POSITION))
        state_store.clear_live_open_position()
        ops.on_position_closed(Sig(), {"order_id": "X-1"}, source=ops.STRATEGY_EXIT)

    events = _events(user.id)
    assert [e.event_type for e in events] == ["entry_submitted", "entry_filled", "exit_filled"]
    assert all((e.event_metadata or {}).get("typed") for e in events)


def test_generic_mode_unchanged_when_typed_off(mu_db, runtime_dirs, monkeypatch):
    from app.config import settings
    from app.services import state_store
    from app.services.execution_context import bind_execution_context
    from app.services.user_context import current_user_from_model

    monkeypatch.setattr(settings, "POSITION_DB_SHADOW_WRITE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "POSITION_DB_TYPED_WRITES_ENABLED", False, raising=False)
    user = make_user("generic-only@example.com")
    with bind_execution_context(current_user_from_model(user)):
        state_store.set_live_open_position(dict(ENTRY_POSITION))
    rows = _row(user.id)
    assert len(rows) == 1  # Phase 2A generic shadow still works standalone
    assert all(not (e.event_metadata or {}).get("typed") for e in _events(user.id))


# ---------------------------------------------------------------------------
# Idempotency internals + source mapping + parity completeness
# ---------------------------------------------------------------------------

def test_conflicting_idempotency_payload_raises(typed_enabled, ctx_user):
    from app.db.engine import session_scope
    from app.services import position_operations as ops
    from app.services import position_store
    from app.services.position_store import ConflictingIdempotencyError

    user, ctx = ctx_user
    _entry(ctx, ops)
    with session_scope() as db:
        row = position_store._active_row(db, user.id, "live")
        ops._emit_event(db, row, event_type="probe", source="STRATEGY_EXIT",
                        idempotency_key="fixed-key", previous_state="open")
        db.flush()
        with pytest.raises(ConflictingIdempotencyError):
            ops._emit_event(db, row, event_type="probe", source="STRATEGY_EXIT",
                            idempotency_key="fixed-key", previous_state="entering")


def test_event_source_mapping():
    from app.services.position_operations import (
        EOD_EXIT, MANUAL_EXIT, STOP_LOSS_EXIT, STRATEGY_EXIT,
        TAKE_PROFIT_EXIT, TRAILING_STOP_EXIT, event_source_for_signal,
    )

    assert event_source_for_signal("manual_panel") == MANUAL_EXIT
    assert event_source_for_signal("server_side_eod_squareoff") == EOD_EXIT
    assert event_source_for_signal("server_side_option_monitor", "SL") == STOP_LOSS_EXIT
    assert event_source_for_signal("server_side_option_monitor", "TP") == TAKE_PROFIT_EXIT
    assert event_source_for_signal("dhan_ltp_exit_confirmation", "TRAIL_SL") == TRAILING_STOP_EXIT
    assert event_source_for_signal("tradingview") == STRATEGY_EXIT
    assert event_source_for_signal(None) == STRATEGY_EXIT


@pytest.mark.parametrize("forged_source", [
    "manual_panel", "server_side_eod_squareoff", "server_side_option_monitor",
    "dhan_ltp_exit_confirmation", "BROKER_RECONCILIATION",
])
@pytest.mark.parametrize("forged_reason", ["SL", "TP", "TRAIL_SL", "manual", "eod"])
def test_webhook_cannot_forge_privileged_event_source(forged_source, forged_reason):
    from app.services.position_operations import STRATEGY_EXIT, event_source_for_signal
    from app.services.signal_parser import parse_webhook_payload

    signal = parse_webhook_payload({
        "secret": "test", "strategy_code": "TEST", "action": "EXIT",
        "side": "SELL", "symbol": "NIFTY", "qty": 75,
        "order_type": "MARKET", "product_type": "INTRADAY",
        "source": forged_source, "exit_reason": forged_reason,
        "event_type": "BROKER_RECONCILIATION", "manual": True, "eod": True,
    })
    assert signal.source == "tradingview"
    assert event_source_for_signal(signal.source, signal.raw_payload["exit_reason"]) == STRATEGY_EXIT


def test_parity_typed_completeness_findings(typed_enabled, ctx_user):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import position_operations as ops
    from app.services import position_store

    user, ctx = ctx_user
    _entry(ctx, ops)

    # typed_event_state_lag: row state diverges from the last typed event.
    with session_scope() as db:
        row = db.scalars(select(models.StrategyInstancePosition)).one()
        row.position_state = "reversing"
    findings = position_store.compare_user_positions(user.id, json_live=None, json_paper=None)
    kinds = {f["type"] for f in findings}
    assert "typed_event_state_lag" in kinds
    with session_scope() as db:
        row = db.scalars(select(models.StrategyInstancePosition)).one()
        row.position_state = "open"

    # unexpected_generic_event: a generic-derived financial event alongside typed ones.
    with session_scope() as db:
        row = db.scalars(select(models.StrategyInstancePosition)).one()
        db.add(models.PositionEvent(
            position_id=row.id, user_id=user.id, event_type="exit_filled",
            source="exit", idempotency_key="generic-1",
        ))
    findings = position_store.compare_user_positions(
        user.id, json_live=dict(ENTRY_POSITION), json_paper=None
    )
    assert "unexpected_generic_event" in {f["type"] for f in findings}
