from __future__ import annotations

from typing import Any

import pytest

from app.schemas.signal import NormalizedSignal
from app.services import audit_logger, risk_manager, state_store


def _isolate_runtime(monkeypatch, tmp_path):
    state_root = tmp_path / "state"
    log_root = tmp_path / "logs"
    for name, filename in {
        "APP_STATE_FILE": "app_state.json",
        "OPEN_POSITION_FILE": "open_position.json",
        "PAPER_POSITION_FILE": "paper_position.json",
        "OPEN_POSITIONS_BY_INSTANCE_FILE": "open_positions_by_instance.json",
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
    monkeypatch.setattr(risk_manager, "QTY_MODE", "ABSOLUTE")
    monkeypatch.setattr(risk_manager.settings, "REQUIRE_MARKET_HOURS", False, raising=False)

    state_store.init_runtime_files()
    state_store.set_engine_mode("paper")
    state_store.update_app_state(webhook_trading_enabled=True, engine_started=True)
    state_store.update_runtime_settings(
        allow_entry=True,
        allow_exit=True,
        emergency_stop=False,
        global_kill_switch=False,
        max_trades_per_day=0,
        max_daily_loss=0,
        allowed_option_side="BOTH",
    )


def _position(**overrides: Any) -> dict[str, Any]:
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
        "strike": 25000.0,
        "expiry": "2026-07-09",
        "qty": 75,
        "entry_price": 100.5,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
    }
    position.update(overrides)
    return position


def _signal(
    *,
    action: str = "ENTRY",
    side: str | None = None,
    option_side: str = "CE",
    qty: int = 75,
    raw_payload: dict[str, Any] | None = None,
) -> NormalizedSignal:
    payload = raw_payload or {}
    internal = payload.get("v2_internal") is True or (
        isinstance(payload.get("v2"), dict) and payload["v2"].get("internal") is True
    )
    return NormalizedSignal(
        payload_format="NOVA",
        secret="" if internal else "test-secret",
        signal_id="risk-instance-signal",
        strategy_code="TRADINGVIEW_NIFTY_V1",
        action=action,
        side=side or ("BUY" if action == "ENTRY" else "SELL"),
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment="NSE_FNO",
        security_id="57046",
        trading_symbol="NIFTY TEST CE",
        option_side=option_side,
        strike=25000.0,
        expiry="2026-07-09",
        qty=qty,
        order_type="MARKET",
        product_type="INTRADAY",
        source="tradingview",
        raw_payload=payload,
    )


def test_legacy_evaluate_entry_blocks_when_global_open_position_exists(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_open_position(_position(trading_symbol="NIFTY GLOBAL CE"))

    decision = risk_manager.evaluate_entry(_signal(), runtime=state_store.get_runtime_settings())

    assert decision.allowed is False
    assert "open position already exists for NIFTY GLOBAL CE" in decision.reason


def test_legacy_evaluate_entry_allows_when_no_global_open_position_exists(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)

    decision = risk_manager.evaluate_entry(_signal(), runtime=state_store.get_runtime_settings())

    assert decision.allowed is True
    assert decision.final_qty == 75


def test_evaluate_entry_with_instance_id_blocks_only_that_instance(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_open_position(_position(trading_symbol="NIFTY INSTANCE A CE"), instance_id="A")

    decision = risk_manager.evaluate_entry(
        _signal(),
        runtime=state_store.get_runtime_settings(),
        instance_id="A",
    )

    assert decision.allowed is False
    assert "open position already exists for NIFTY INSTANCE A CE" in decision.reason


def test_evaluate_entry_with_instance_id_ignores_other_instance_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_open_position(_position(trading_symbol="NIFTY INSTANCE B CE"), instance_id="B")

    decision = risk_manager.evaluate_entry(
        _signal(),
        runtime=state_store.get_runtime_settings(),
        instance_id="A",
    )

    assert decision.allowed is True
    assert decision.final_qty == 75


def test_evaluate_exit_with_instance_id_uses_that_instance_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_open_position(_position(qty=55), instance_id="A")

    decision = risk_manager.evaluate_exit(
        _signal(action="EXIT", option_side="CE", qty=75),
        runtime=state_store.get_runtime_settings(),
        instance_id="A",
    )

    assert decision.allowed is True
    assert decision.final_qty == 55


def test_evaluate_exit_with_instance_id_does_not_use_other_instance_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_open_position(_position(qty=55), instance_id="B")

    decision = risk_manager.evaluate_exit(
        _signal(action="EXIT", option_side="CE", qty=75),
        runtime=state_store.get_runtime_settings(),
        instance_id="A",
    )

    assert decision.allowed is False
    assert decision.reason == "Exit blocked: no open position exists."


def test_evaluate_reversal_entry_with_instance_id_uses_that_instance_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_open_position(_position(option_side="CE"), instance_id="A")
    state_store.set_open_position(_position(option_side="PE"), instance_id="B")

    decision = risk_manager.evaluate_reversal_entry(
        _signal(option_side="PE"),
        runtime=state_store.get_runtime_settings(),
        instance_id="A",
    )

    assert decision.allowed is True
    assert decision.final_qty == 75


def test_evaluate_reversal_entry_with_instance_id_does_not_use_other_instance_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_open_position(_position(option_side="CE"), instance_id="B")

    decision = risk_manager.evaluate_reversal_entry(
        _signal(option_side="PE"),
        runtime=state_store.get_runtime_settings(),
        instance_id="A",
    )

    assert decision.allowed is False
    assert decision.reason == "Reversal blocked: no open position exists."


@pytest.mark.parametrize(
    "raw_payload",
    [
        {"instance_id": "A"},
        {"strategy_instance_id": "A"},
        {"v2": {"instance_id": "A"}},
    ],
)
def test_external_instance_id_raw_payload_is_ignored(monkeypatch, tmp_path, raw_payload):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_open_position(_position(trading_symbol="NIFTY GLOBAL CE"))
    state_store.set_open_position(_position(trading_symbol="NIFTY INSTANCE A CE"), instance_id="A")

    decision = risk_manager.evaluate_entry(
        _signal(raw_payload=raw_payload),
        runtime=state_store.get_runtime_settings(),
    )

    assert decision.allowed is False
    assert "open position already exists for NIFTY GLOBAL CE" in decision.reason


@pytest.mark.parametrize(
    "raw_payload",
    [
        {"v2_internal": True, "instance_id": "A"},
        {"v2": {"internal": True, "instance_id": "A"}},
    ],
)
def test_trusted_internal_v2_payload_can_provide_instance_id(monkeypatch, tmp_path, raw_payload):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_open_position(_position(trading_symbol="NIFTY GLOBAL CE"))
    state_store.set_open_position(_position(trading_symbol="NIFTY INSTANCE A CE"), instance_id="A")

    decision = risk_manager.evaluate_entry(
        _signal(raw_payload=raw_payload),
        runtime=state_store.get_runtime_settings(),
    )

    assert decision.allowed is False
    assert "open position already exists for NIFTY INSTANCE A CE" in decision.reason


def test_explicit_instance_id_wins_over_signal_raw_payload(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_open_position(_position(trading_symbol="NIFTY INSTANCE A CE"), instance_id="A")

    decision = risk_manager.evaluate_entry(
        _signal(raw_payload={"instance_id": "B"}),
        runtime=state_store.get_runtime_settings(),
        instance_id="A",
    )

    assert decision.allowed is False
    assert "open position already exists for NIFTY INSTANCE A CE" in decision.reason


def test_external_injected_instance_id_cannot_bypass_global_one_open_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_open_position(_position(trading_symbol="NIFTY GLOBAL CE"))

    decision = risk_manager.evaluate_entry(
        _signal(raw_payload={"instance_id": "A"}),
        runtime=state_store.get_runtime_settings(),
    )

    assert decision.allowed is False
    assert "open position already exists for NIFTY GLOBAL CE" in decision.reason


def test_external_payload_with_spoofed_internal_marker_is_ignored(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_open_position(_position(trading_symbol="NIFTY GLOBAL CE"))
    state_store.set_open_position(_position(trading_symbol="NIFTY INSTANCE A CE"), instance_id="A")

    signal = _signal(raw_payload={"v2_internal": True, "instance_id": "A"})
    signal = signal.model_copy(update={"secret": "valid-webhook-secret"})

    decision = risk_manager.evaluate_entry(signal, runtime=state_store.get_runtime_settings())

    assert decision.allowed is False
    assert "open position already exists for NIFTY GLOBAL CE" in decision.reason


def test_no_instance_id_uses_legacy_global_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_open_position(_position(trading_symbol="NIFTY GLOBAL CE"))
    state_store.set_open_position(_position(trading_symbol="NIFTY INSTANCE A CE"), instance_id="A")

    decision = risk_manager.evaluate_entry(
        _signal(),
        runtime=state_store.get_runtime_settings(),
        instance_id=None,
    )

    assert decision.allowed is False
    assert "open position already exists for NIFTY GLOBAL CE" in decision.reason
