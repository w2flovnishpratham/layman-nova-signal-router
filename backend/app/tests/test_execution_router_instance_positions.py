from __future__ import annotations

from typing import Any

from app.schemas.signal import NormalizedSignal
from app.services import audit_logger, execution_router, risk_manager, state_store


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
    monkeypatch.setattr(execution_router, "refresh_wallet_snapshot", lambda **_kwargs: {})
    monkeypatch.setattr(execution_router, "_cancel_super_order_exit_legs", lambda _position: None)

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
        option_exit_mode="SERVER",
    )
    return state_root


def _install_fake_order_router(monkeypatch, calls: list[tuple[str, str, int]] | None = None):
    def fake_place_order(
        signal: NormalizedSignal,
        qty: int,
        action: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if calls is not None:
            calls.append((action, signal.signal_id, qty))
        return {
            "success": True,
            "status": "TRADED",
            "order_id": f"PAPER-{action}-{signal.signal_id}",
            "avg_price": 100.0 if action == "ENTRY" else 110.0,
            "security_id": signal.security_id,
            "trading_symbol": signal.trading_symbol,
            "requested_qty": qty,
            "filled_qty": qty,
            "exit_management": "SERVER",
            "order_type": signal.order_type,
        }

    monkeypatch.setattr(execution_router, "_place_order", fake_place_order)


def _runtime() -> dict[str, Any]:
    return state_store.get_runtime_settings()


def _signal(
    *,
    signal_id: str = "router-instance-signal",
    action: str = "ENTRY",
    side: str | None = None,
    option_side: str = "CE",
    qty: int = 75,
    raw_payload: dict[str, Any] | None = None,
) -> NormalizedSignal:
    return NormalizedSignal(
        payload_format="NOVA",
        secret="test-secret",
        signal_id=signal_id,
        strategy_code="TRADINGVIEW_NIFTY_V1",
        action=action,
        side=side or ("BUY" if action == "ENTRY" else "SELL"),
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment="NSE_FNO",
        security_id="57046",
        trading_symbol=f"NIFTY TEST {option_side}",
        option_side=option_side,
        strike=25000.0,
        expiry="2026-07-09",
        qty=qty,
        order_type="MARKET",
        product_type="INTRADAY",
        source="strategy_worker_adapter_v2" if raw_payload else "tradingview",
        raw_payload=raw_payload or {},
    )


def _v2_payload(instance_id: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "instance_id": instance_id,
        "strategy_version_id": "version-1",
        "v2_job_id": "job-1",
        "execution_mode": "paper_live_data",
        "source_signal_id": "source-signal-1",
    }
    payload.update(overrides)
    return payload


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
        "entry_order_id": "PAPER-ENTRY",
        "entry_price": 100.0,
        "exit_management": "SERVER",
        "order_type": "MARKET",
        "product_type": "INTRADAY",
    }
    position.update(overrides)
    return position


def test_legacy_entry_without_instance_writes_legacy_paper_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    _install_fake_order_router(monkeypatch)

    result = execution_router.route_entry_signal(_signal(signal_id="legacy-entry"), runtime=_runtime())

    assert result["status"] == "ORDER_PLACED"
    assert state_store.get_paper_position()["has_open_position"] is True
    assert state_store.get_paper_position()["entry_order_id"] == "PAPER-ENTRY-legacy-entry"
    assert state_store.list_open_positions_by_instance() == {}


def test_legacy_exit_without_instance_clears_legacy_paper_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    _install_fake_order_router(monkeypatch)
    state_store.set_open_position(_position())
    state_store.set_open_position(_position(trading_symbol="NIFTY INSTANCE A CE"), instance_id="A")

    result = execution_router.route_exit_signal(
        _signal(signal_id="legacy-exit", action="EXIT", option_side="CE"),
        runtime=_runtime(),
    )

    assert result["status"] == "ORDER_PLACED"
    assert state_store.get_paper_position()["has_open_position"] is False
    assert state_store.get_open_position("A")["has_open_position"] is True


def test_v2_entry_with_instance_writes_only_instance_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    _install_fake_order_router(monkeypatch)
    captured_instance_ids: list[str | None] = []
    original_evaluate_entry = execution_router.evaluate_entry

    def wrapped_evaluate_entry(signal, runtime=None, instance_id=None):
        captured_instance_ids.append(instance_id)
        return original_evaluate_entry(signal, runtime=runtime, instance_id=instance_id)

    monkeypatch.setattr(execution_router, "evaluate_entry", wrapped_evaluate_entry)

    result = execution_router.route_entry_signal(
        _signal(signal_id="v2-entry-a", raw_payload=_v2_payload("A")),
        runtime=_runtime(),
    )

    assert result["status"] == "ORDER_PLACED"
    assert captured_instance_ids == ["A"]
    assert state_store.get_paper_position()["has_open_position"] is False
    assert state_store.get_live_open_position()["has_open_position"] is False
    position = state_store.get_open_position("A")
    assert position["has_open_position"] is True
    assert position["entry_order_id"] == "PAPER-ENTRY-v2-entry-a"
    assert position["instance_id"] == "A"
    assert position["strategy_version_id"] == "version-1"
    assert position["v2_job_id"] == "job-1"
    assert position["execution_mode"] == "paper_live_data"
    assert position["source_signal_id"] == "source-signal-1"


def test_v2_entry_with_instance_ignores_legacy_open_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    _install_fake_order_router(monkeypatch)
    state_store.set_open_position(_position(trading_symbol="NIFTY LEGACY CE"))

    result = execution_router.route_entry_signal(
        _signal(signal_id="v2-entry-a", raw_payload=_v2_payload("A")),
        runtime=_runtime(),
    )

    assert result["status"] == "ORDER_PLACED"
    assert state_store.get_paper_position()["trading_symbol"] == "NIFTY LEGACY CE"
    assert state_store.get_open_position("A")["entry_order_id"] == "PAPER-ENTRY-v2-entry-a"


def test_v2_exit_with_instance_clears_only_that_instance(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    _install_fake_order_router(monkeypatch)
    state_store.set_open_position(_position(trading_symbol="NIFTY LEGACY CE"))
    state_store.set_open_position(_position(trading_symbol="NIFTY INSTANCE A CE"), instance_id="A")
    state_store.set_open_position(_position(trading_symbol="NIFTY INSTANCE B CE"), instance_id="B")
    captured_instance_ids: list[str | None] = []
    original_evaluate_exit = execution_router.evaluate_exit

    def wrapped_evaluate_exit(signal, runtime=None, instance_id=None):
        captured_instance_ids.append(instance_id)
        return original_evaluate_exit(signal, runtime=runtime, instance_id=instance_id)

    monkeypatch.setattr(execution_router, "evaluate_exit", wrapped_evaluate_exit)

    result = execution_router.route_exit_signal(
        _signal(signal_id="v2-exit-a", action="EXIT", option_side="CE", raw_payload=_v2_payload("A")),
        runtime=_runtime(),
    )

    assert result["status"] == "ORDER_PLACED"
    assert captured_instance_ids == ["A"]
    assert state_store.get_open_position("A") is None
    assert state_store.get_open_position("B")["trading_symbol"] == "NIFTY INSTANCE B CE"
    assert state_store.get_paper_position()["trading_symbol"] == "NIFTY LEGACY CE"


def test_v2_reversal_reads_clears_and_rewrites_same_instance(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    order_calls: list[tuple[str, str, int]] = []
    _install_fake_order_router(monkeypatch, order_calls)
    state_store.set_open_position(_position(option_side="CE", trading_symbol="NIFTY INSTANCE A CE"), instance_id="A")
    state_store.set_open_position(_position(option_side="PE", trading_symbol="NIFTY INSTANCE B PE"), instance_id="B")
    captured_reversal_ids: list[str | None] = []
    captured_exit_ids: list[str | None] = []
    original_reversal = execution_router.evaluate_reversal_entry
    original_exit = execution_router.evaluate_exit

    def wrapped_reversal(signal, runtime=None, instance_id=None):
        captured_reversal_ids.append(instance_id)
        return original_reversal(signal, runtime=runtime, instance_id=instance_id)

    def wrapped_exit(signal, runtime=None, instance_id=None):
        captured_exit_ids.append(instance_id)
        return original_exit(signal, runtime=runtime, instance_id=instance_id)

    monkeypatch.setattr(execution_router, "evaluate_reversal_entry", wrapped_reversal)
    monkeypatch.setattr(execution_router, "evaluate_exit", wrapped_exit)

    result = execution_router.route_reversal_signal(
        _signal(signal_id="v2-reversal-a", option_side="PE", raw_payload=_v2_payload("A")),
        runtime=_runtime(),
    )

    assert result["status"] == "REVERSAL_ORDER_PLACED"
    assert captured_reversal_ids == ["A"]
    assert captured_exit_ids == ["A"]
    assert order_calls[0][0] == "EXIT"
    assert order_calls[1][0] == "ENTRY"
    position_a = state_store.get_open_position("A")
    assert position_a["has_open_position"] is True
    assert position_a["option_side"] == "PE"
    assert position_a["entry_order_id"] == "PAPER-ENTRY-v2-reversal-a"
    assert position_a["instance_id"] == "A"
    assert state_store.get_open_position("B")["trading_symbol"] == "NIFTY INSTANCE B PE"
