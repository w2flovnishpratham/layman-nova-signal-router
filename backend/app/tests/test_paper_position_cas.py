"""Deterministic proof for the Paper position CAS + idempotent-exit fix.

Reproduces the duplicate-exit / resurrection incident at the state-authority
level: a monitor iteration reads (position_id, version); an exit clears the
position at a higher version; the monitor resumes with its stale snapshot and
its compare-and-set write must be rejected — the position must stay flat, with
exactly one realized-P&L booking and one closed trade.
"""
from __future__ import annotations

import pytest

from app.services import audit_logger, paper_portfolio, state_store
from app.services.paper_portfolio import NoOpenPaperTradeError


def setup_runtime(tmp_path, monkeypatch):
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    log_files = {
        "webhook": log_dir / "webhook_events.jsonl",
        "order": log_dir / "order_events.jsonl",
        "audit": log_dir / "audit_events.jsonl",
        "error": log_dir / "errors.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    state_store.init_runtime_files()
    state_store.update_app_state(engine_mode="paper")


def _open_position(**over):
    pos = {
        "strategy_code": "supertrend",
        "security_id": "45678",
        "trading_symbol": "NIFTY 21 JUL 24250 CALL",
        "qty": 65,
        "entry_order_id": "PAPER-FCB6BB1C1701",
        "entry_price": 135.0,
        "opened_at": state_store.utc_now(),
        "live_pnl": None,
    }
    pos.update(over)
    return state_store.set_open_position(state_store.stamp_new_open_position(pos))


def test_entry_stamps_stable_id_and_version_one(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    opened = _open_position()
    assert opened["has_open_position"] is True
    assert isinstance(opened["position_id"], str) and opened["position_id"]
    assert opened["position_version"] == 1


def test_stale_monitor_write_after_exit_is_rejected_and_never_resurrects(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    opened = _open_position()
    # 1) monitor iteration reads the open position identity+version
    read_id, read_version = opened["position_id"], opened["position_version"]
    # 2) an exit completes and persists confirmed-flat at a higher version
    closed, flat = state_store.close_open_position_cas(
        position_id=read_id, position_version=read_version
    )
    assert closed is True
    assert flat["has_open_position"] is False
    assert flat["last_closed_position_id"] == read_id
    # 3) monitor resumes with its STALE pre-exit snapshot and attempts a write
    applied, current = state_store.patch_open_position_cas(
        position_id=read_id,
        position_version=read_version,
        patch={"live_pnl": {"ltp": 134.80, "unrealized_pnl": -13.0}},
    )
    # Stale write rejected; position remains flat — no resurrection.
    assert applied is False
    assert current["has_open_position"] is False
    assert state_store.get_open_position()["has_open_position"] is False


def test_duplicate_close_is_idempotent_no_second_clear(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    opened = _open_position()
    ok1, _ = state_store.close_open_position_cas(
        position_id=opened["position_id"], position_version=opened["position_version"]
    )
    ok2, current = state_store.close_open_position_cas(
        position_id=opened["position_id"], position_version=opened["position_version"]
    )
    assert ok1 is True and ok2 is False  # second close is a no-op (ALREADY_FLAT)
    assert current["has_open_position"] is False


def test_durable_exit_claim_is_exclusive_and_only_owner_can_confirm_flat(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    opened = _open_position()
    pid = opened["position_id"]

    status, claimed = state_store.claim_exit_operation(
        position_id=pid,
        position_version=opened["position_version"],
        operation_id="EXIT-1",
        signal_id="SIGNAL-1",
        source="manual",
    )
    assert status == "CLAIMED"
    assert claimed["exit_operation"]["status"] == "PENDING"

    duplicate_status, _ = state_store.claim_exit_operation(
        position_id=pid,
        position_version=opened["position_version"],
        operation_id="EXIT-2",
        signal_id="SIGNAL-2",
        source="monitor",
    )
    assert duplicate_status == "EXIT_IN_PROGRESS"

    wrong_owner_status, still_open = state_store.complete_exit_operation(
        position_id=pid,
        operation_id="EXIT-2",
        order_id="PAPER-WRONG",
    )
    assert wrong_owner_status == "POSITION_CHANGED"
    assert still_open["has_open_position"] is True

    completed_status, flat = state_store.complete_exit_operation(
        position_id=pid,
        operation_id="EXIT-1",
        order_id="PAPER-EXIT-1",
    )
    assert completed_status == "COMPLETED"
    assert flat["has_open_position"] is False
    assert flat["last_exit_operation"]["operation_id"] == "EXIT-1"
    assert flat["last_exit_operation"]["status"] == "COMPLETED"


def test_patch_applies_on_match_then_rejects_stale_version(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    opened = _open_position()
    pid, ver = opened["position_id"], opened["position_version"]
    applied, updated = state_store.patch_open_position_cas(
        position_id=pid, position_version=ver, patch={"live_pnl": {"ltp": 133.35}}
    )
    assert applied is True
    assert updated["position_version"] == ver + 1
    assert updated["live_pnl"]["ltp"] == 133.35
    # a second write at the now-stale version must be rejected (monotonic)
    applied2, _ = state_store.patch_open_position_cas(
        position_id=pid, position_version=ver, patch={"live_pnl": {"ltp": 999.0}}
    )
    assert applied2 is False
    assert state_store.get_open_position()["live_pnl"]["ltp"] == 133.35


def test_apply_paper_exit_rejects_when_no_open_trade(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    paper_portfolio.reset_paper_portfolio(100000.0)
    with pytest.raises(NoOpenPaperTradeError):
        paper_portfolio.apply_paper_exit(
            qty=65, exit_price=134.80, charges=1.0, symbol="NIFTY CALL", order_id="PAPER-517BD16F238F"
        )


def test_duplicate_paper_exit_books_exactly_one_realized_pnl(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    paper_portfolio.reset_paper_portfolio(100000.0)
    paper_portfolio.apply_paper_entry(
        qty=65, price=135.0, charges=1.0, symbol="NIFTY CALL", order_id="PAPER-FCB6BB1C1701"
    )
    first = paper_portfolio.apply_paper_exit(
        qty=65, exit_price=133.35, charges=1.0, symbol="NIFTY CALL", order_id="PAPER-08DA5DB0A793"
    )
    assert len(first.closed_trades) == 1
    realized_after_first = first.realized_pnl
    # the phantom second exit from the incident must be rejected, no false profit
    with pytest.raises(NoOpenPaperTradeError):
        paper_portfolio.apply_paper_exit(
            qty=65, exit_price=134.80, charges=1.0, symbol="NIFTY CALL", order_id="PAPER-517BD16F238F"
        )
    after = paper_portfolio.get_paper_portfolio()
    assert len(after.closed_trades) == 1  # exactly one closed trade
    assert after.realized_pnl == realized_after_first  # no second realized-P&L
