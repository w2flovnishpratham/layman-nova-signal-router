from __future__ import annotations

import pytest

from app.schemas.signal import NormalizedSignal
from app.services import audit_logger, portfolio_analytics, risk_manager, state_store


def _isolate_runtime(monkeypatch, tmp_path):
    state_root = tmp_path / "state"
    log_root = tmp_path / "logs"
    for name, filename in {
        "APP_STATE_FILE": "app_state.json",
        "OPEN_POSITION_FILE": "open_position.json",
        "PAPER_POSITION_FILE": "paper_position.json",
        "PAPER_PORTFOLIO_FILE": "paper_portfolio.json",
        "EXTERNAL_POSITIONS_FILE": "external_positions.json",
        "SEEN_SIGNALS_FILE": "seen_signals.json",
        "SETTINGS_FILE": "settings.json",
    }.items():
        monkeypatch.setattr(state_store, name, state_root / filename)
    log_files = {
        "webhook": log_root / "webhook_events.jsonl",
        "order": log_root / "order_events.jsonl",
        "audit": log_root / "audit_events.jsonl",
        "error": log_root / "errors.jsonl",
        "paper_orders": log_root / "paper_orders.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    state_store.init_runtime_files()
    state_store.update_app_state(webhook_trading_enabled=False, engine_started=False)
    return log_files


def _open_position(**overrides):
    position = {
        **state_store.default_open_position(),
        "has_open_position": True,
        "strategy_code": "TRADINGVIEW_NIFTY_V1",
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "exchange_segment": "NSE_FNO",
        "security_id": "57046",
        "trading_symbol": "NIFTY TEST CE",
        "option_side": "CE",
        "strike": 24000.0,
        "expiry": "2026-07-30",
        "qty": 75,
        "entry_order_id": "ORDER-ENTRY-1",
        "entry_price": 100.0,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
    }
    position.update(overrides)
    return position


def _entry_signal(signal_id: str = "legacy-entry-1") -> NormalizedSignal:
    return NormalizedSignal(
        payload_format="NOVA",
        secret="test-secret",
        signal_id=signal_id,
        strategy_code="TRADINGVIEW_NIFTY_V1",
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment="NSE_FNO",
        security_id="57046",
        trading_symbol="NIFTY TEST CE",
        option_side="CE",
        strike=24000.0,
        expiry="2026-07-30",
        qty=75,
        order_type="MARKET",
        product_type="INTRADAY",
        source="tradingview",
        raw_payload={},
    )


@pytest.mark.parametrize(
    ("mode", "legacy_getter"),
    [
        ("live", state_store.get_live_open_position),
        ("paper", state_store.get_paper_position),
    ],
)
def test_legacy_get_and_set_open_position_use_current_engine_mode(monkeypatch, tmp_path, mode, legacy_getter):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode(mode)

    saved = state_store.set_open_position(_open_position(trading_symbol=f"NIFTY TEST {mode.upper()}"))

    assert saved["has_open_position"] is True
    assert state_store.get_open_position()["trading_symbol"] == f"NIFTY TEST {mode.upper()}"
    assert legacy_getter()["trading_symbol"] == f"NIFTY TEST {mode.upper()}"

    if mode == "live":
        assert state_store.get_paper_position()["has_open_position"] is False
    else:
        assert state_store.get_live_open_position()["has_open_position"] is False


@pytest.mark.parametrize("mode", ["live", "paper"])
def test_legacy_clear_open_position_clears_current_engine_mode(monkeypatch, tmp_path, mode):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode(mode)
    state_store.set_open_position(_open_position(trading_symbol=f"NIFTY TEST {mode.upper()}"))

    cleared = state_store.clear_open_position()

    assert cleared["has_open_position"] is False
    assert state_store.get_open_position()["has_open_position"] is False
    if mode == "live":
        assert state_store.get_live_open_position()["has_open_position"] is False
    else:
        assert state_store.get_paper_position()["has_open_position"] is False


def test_risk_manager_blocks_second_legacy_entry_when_open_position_exists(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("paper")
    state_store.update_app_state(webhook_trading_enabled=True, engine_started=True)
    state_store.set_open_position(_open_position())
    monkeypatch.setattr(risk_manager, "QTY_MODE", "ABSOLUTE")
    monkeypatch.setattr(risk_manager.settings, "REQUIRE_MARKET_HOURS", False, raising=False)

    decision = risk_manager.evaluate_entry(_entry_signal(), runtime=state_store.get_runtime_settings())

    assert decision.allowed is False
    assert "open position already exists" in decision.reason


def test_portfolio_analytics_pairs_legacy_entry_exit_stream_without_instance_id():
    events = [
        {
            "timestamp": "2026-07-03T10:00:00+00:00",
            "mode": "live",
            "phase": "after_response",
            "success": True,
            "status": "TRADED",
            "order_id": "LIVE-ENTRY-1",
            "avg_price": 100.0,
            "normalized_action": "ENTRY",
            "normalized_qty": 75,
            "normalized_option_side": "CE",
            "trading_symbol": "NIFTY TEST CE",
        },
        {
            "timestamp": "2026-07-03T10:20:00+00:00",
            "mode": "live",
            "phase": "after_response",
            "success": True,
            "status": "TRADED",
            "order_id": "LIVE-EXIT-1",
            "avg_price": 112.0,
            "normalized_action": "EXIT",
            "normalized_qty": 75,
            "normalized_option_side": "CE",
            "trading_symbol": "NIFTY TEST CE",
        },
    ]

    trades, open_entry = portfolio_analytics._pair_round_trips(events)

    assert open_entry is None
    assert len(trades) == 1
    trade = trades[0]
    assert trade["entry_order_id"] == "LIVE-ENTRY-1"
    assert trade["exit_order_id"] == "LIVE-EXIT-1"
    assert trade["realized_pnl"] == 900.0


def test_existing_order_jsonl_without_instance_id_still_builds_portfolio(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("live")
    monkeypatch.setattr(portfolio_analytics, "_live_wallet_snapshot", lambda: {})

    for event in [
        {
            "timestamp": "2026-07-03T10:00:00+00:00",
            "phase": "after_response",
            "success": True,
            "status": "TRADED",
            "order_id": "LIVE-ENTRY-JSONL",
            "avg_price": 100.0,
            "normalized_action": "ENTRY",
            "normalized_qty": 75,
            "normalized_option_side": "CE",
            "trading_symbol": "NIFTY TEST CE",
        },
        {
            "timestamp": "2026-07-03T10:30:00+00:00",
            "phase": "after_response",
            "success": True,
            "status": "TRADED",
            "order_id": "LIVE-EXIT-JSONL",
            "avg_price": 110.0,
            "normalized_action": "EXIT",
            "normalized_qty": 75,
            "normalized_option_side": "CE",
            "trading_symbol": "NIFTY TEST CE",
        },
    ]:
        audit_logger.log_order_event(event)

    payload = portfolio_analytics.build_portfolio_analytics(persist=False)

    assert payload["kpis"]["total_trades"] == 1
    assert payload["kpis"]["realized_pnl"] == 750.0
    assert payload["trades"][0]["entry_order_id"] == "LIVE-ENTRY-JSONL"
    assert payload["trades"][0]["exit_order_id"] == "LIVE-EXIT-JSONL"
