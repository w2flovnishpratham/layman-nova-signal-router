"""Cooldown after a losing trade: blocks entries only, and only after a loss.

The gate reads a stamp written by the exit that actually booked a negative
realised P&L, so these tests drive the real state_store rather than mocking it.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.schemas.signal import NormalizedSignal
from app.services import paper_portfolio, risk_manager, state_store


@pytest.fixture()
def runtime_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store, "RUNTIME_STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(state_store, "APP_STATE_FILE", tmp_path / "app_state.json", raising=False)
    monkeypatch.setattr(state_store, "_scoped_dir", lambda: tmp_path, raising=False)
    return tmp_path


def _stamp(minutes_ago: float) -> None:
    data = state_store.get_daily_risk()
    data["last_loss_exit_at"] = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    state_store._write_json(state_store._daily_risk_file(), data)


def _signal() -> NormalizedSignal:
    return NormalizedSignal(
        payload_format="NOVA",
        secret="unused",
        signal_id="sig-cooldown",
        strategy_code="TRADINGVIEW_NIFTY_V1",
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment="NSE_FNO",
        security_id="MOCK-CE",
        trading_symbol="NIFTY CE",
        option_side="CE",
        strike=25000,
        expiry="2026-06-04",
        qty=65,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload={},
    )


def test_zero_minutes_disables_the_gate(runtime_state):
    _stamp(minutes_ago=0)
    assert risk_manager._cooldown_after_loss_check({"cooldown_after_loss_minutes": 0}) is None


def test_no_recorded_loss_never_blocks(runtime_state):
    assert risk_manager._cooldown_after_loss_check({"cooldown_after_loss_minutes": 30}) is None


def test_entry_is_blocked_inside_the_window(runtime_state):
    _stamp(minutes_ago=5)
    decision = risk_manager._cooldown_after_loss_check({"cooldown_after_loss_minutes": 30})
    assert decision is not None
    assert decision.allowed is False
    assert "cooldown" in decision.reason.lower()


def test_entry_is_allowed_once_the_window_expires(runtime_state):
    _stamp(minutes_ago=31)
    assert risk_manager._cooldown_after_loss_check({"cooldown_after_loss_minutes": 30}) is None


def test_an_unparseable_stamp_does_not_wedge_entries(runtime_state):
    data = state_store.get_daily_risk()
    data["last_loss_exit_at"] = "not-a-timestamp"
    state_store._write_json(state_store._daily_risk_file(), data)
    assert risk_manager._cooldown_after_loss_check({"cooldown_after_loss_minutes": 30}) is None


def test_only_a_losing_exit_stamps_the_cooldown(runtime_state):
    state_store.record_losing_exit(250.0)  # a winning exit
    assert state_store.get_daily_risk().get("last_loss_exit_at") is None
    state_store.record_losing_exit(0.0)  # break-even
    assert state_store.get_daily_risk().get("last_loss_exit_at") is None
    state_store.record_losing_exit(-125.0)
    assert state_store.get_daily_risk().get("last_loss_exit_at") is not None


def test_a_losing_paper_exit_stamps_the_cooldown(runtime_state):
    paper_portfolio.apply_paper_entry(qty=75, price=100.0, charges=0.0, symbol="NIFTY CE", order_id="e1")
    paper_portfolio.apply_paper_exit(qty=75, exit_price=90.0, charges=0.0, symbol="NIFTY CE", order_id="x1")
    assert state_store.get_daily_risk().get("last_loss_exit_at") is not None


def test_a_winning_paper_exit_leaves_entries_open(runtime_state):
    paper_portfolio.apply_paper_entry(qty=75, price=100.0, charges=0.0, symbol="NIFTY CE", order_id="e2")
    paper_portfolio.apply_paper_exit(qty=75, exit_price=130.0, charges=0.0, symbol="NIFTY CE", order_id="x2")
    assert state_store.get_daily_risk().get("last_loss_exit_at") is None
    assert risk_manager._cooldown_after_loss_check({"cooldown_after_loss_minutes": 30}) is None


def test_the_cooldown_gate_is_reached_from_entry_limit_checks(runtime_state):
    _stamp(minutes_ago=1)
    runtime = {"allowed_option_side": "BOTH", "cooldown_after_loss_minutes": 30}
    decision = risk_manager._entry_limit_checks(_signal(), runtime)
    assert decision is not None and decision.allowed is False
    assert "cooldown" in decision.reason.lower()
