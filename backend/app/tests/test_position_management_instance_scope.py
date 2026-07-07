from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.services import audit_logger, state_store


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
    state_store.init_runtime_files()
    state_store.update_app_state(webhook_trading_enabled=False, engine_started=False)
    return state_root


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
        "entry_order_id": "ENTRY-1",
        "entry_price": 100.0,
        "exit_management": "SERVER",
        "order_type": "MARKET",
        "product_type": "INTRADAY",
    }
    position.update(overrides)
    return position


def _seed_legacy_and_instances() -> None:
    state_store.set_open_position(_position(trading_symbol="NIFTY LEGACY CE"))
    state_store.set_open_position(_position(trading_symbol="NIFTY INSTANCE A CE", instance_id="A"), instance_id="A")
    state_store.set_open_position(_position(trading_symbol="NIFTY INSTANCE B CE", instance_id="B"), instance_id="B")


def _install_monitor_market_closed(monkeypatch):
    from app.services import option_position_monitor

    clear_calls: list[bool] = []
    monkeypatch.setattr(option_position_monitor, "_market_is_open", lambda: False)
    monkeypatch.setattr(option_position_monitor, "marketfeed_ws_status", lambda: {"connected": False})
    monkeypatch.setattr(option_position_monitor, "clear_marketfeed_subscription", lambda: clear_calls.append(True))
    return option_position_monitor, clear_calls


def _fake_flat_dhan(monkeypatch, module):
    class Result:
        success = True
        message = "OK"
        items: list[dict[str, Any]] = []

    class FakeDhanClient:
        def get_positions_snapshot(self, **_kwargs):
            return Result()

        def get_order_book(self, **_kwargs):
            return Result()

    monkeypatch.setattr(module.settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(module, "get_dhan_credentials", lambda: SimpleNamespace(client_id="client", access_token="token"))
    monkeypatch.setattr(module, "RealDhanClient", FakeDhanClient)


def test_legacy_monitor_updates_only_legacy_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("paper")
    _seed_legacy_and_instances()
    option_position_monitor, clear_calls = _install_monitor_market_closed(monkeypatch)

    option_position_monitor.monitor_once()

    legacy = state_store.get_open_position()
    assert legacy["live_pnl"]["status"] == "market_closed"
    assert state_store.get_open_position("A").get("live_pnl") is None
    assert state_store.get_open_position("B").get("live_pnl") is None
    assert clear_calls == [True]


def test_monitor_with_instance_updates_only_that_instance(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("paper")
    _seed_legacy_and_instances()
    option_position_monitor, clear_calls = _install_monitor_market_closed(monkeypatch)

    option_position_monitor.monitor_once(instance_id="A")

    assert state_store.get_open_position().get("live_pnl") is None
    assert state_store.get_open_position("A")["live_pnl"]["status"] == "market_closed"
    assert state_store.get_open_position("B").get("live_pnl") is None
    assert clear_calls == []


def test_monitor_with_instance_is_blocked_outside_paper_mode_before_dhan(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("live")
    state_store.set_open_position(
        _position(trading_symbol="NIFTY INSTANCE A CE", instance_id="A", execution_mode="real_orders"),
        instance_id="A",
    )
    from app.services import option_position_monitor

    monkeypatch.setattr(
        option_position_monitor,
        "_publish_market_snapshot_from_monitor",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("snapshot should not be published")),
    )
    monkeypatch.setattr(
        option_position_monitor,
        "get_dhan_credentials",
        lambda: (_ for _ in ()).throw(AssertionError("credentials should not be read")),
    )

    option_position_monitor.monitor_once(instance_id="A")

    assert state_store.get_open_position("A")["has_open_position"] is True
    assert state_store.get_open_position("A").get("live_pnl") is None


def test_legacy_eod_squareoff_signal_clears_only_legacy_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("paper")
    _seed_legacy_and_instances()
    from app.services import execution_router
    from app.workers import eod_squareoff

    routed = []

    def fake_route(signal):
        routed.append(signal)
        assert "instance_id" not in signal.raw_payload
        state_store.clear_open_position()
        return {"success": True, "status": "PAPER_OK", "order_id": "EOD-LEGACY"}

    monkeypatch.setattr(execution_router, "route_signal", fake_route)

    eod_squareoff._try_flatten_once()

    assert len(routed) == 1
    assert state_store.get_open_position()["has_open_position"] is False
    assert state_store.get_open_position("A")["trading_symbol"] == "NIFTY INSTANCE A CE"
    assert state_store.get_open_position("B")["trading_symbol"] == "NIFTY INSTANCE B CE"


def test_eod_squareoff_with_instance_clears_only_that_instance(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("paper")
    _seed_legacy_and_instances()
    from app.services import execution_router
    from app.workers import eod_squareoff

    routed = []

    def fake_route(signal):
        routed.append(signal)
        assert signal.raw_payload["instance_id"] == "A"
        state_store.clear_open_position(instance_id=signal.raw_payload["instance_id"])
        return {"success": True, "status": "PAPER_OK", "order_id": "EOD-A"}

    monkeypatch.setattr(execution_router, "route_signal", fake_route)

    eod_squareoff._try_flatten_once(instance_id="A")

    assert len(routed) == 1
    assert state_store.get_open_position("A") is None
    assert state_store.get_open_position("B")["trading_symbol"] == "NIFTY INSTANCE B CE"
    assert state_store.get_open_position()["trading_symbol"] == "NIFTY LEGACY CE"
    app_state = state_store.get_app_state()
    assert app_state.get("eod_squareoff_last_fired_date_ist") is None
    assert app_state.get("eod_squareoff_last_fired_date_ist:A") == eod_squareoff._today_ist_date_str()


def test_eod_squareoff_with_instance_is_blocked_outside_paper_mode(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("live")
    state_store.set_open_position(
        _position(trading_symbol="NIFTY INSTANCE A CE", instance_id="A", execution_mode="real_orders"),
        instance_id="A",
    )
    from app.services import execution_router
    from app.workers import eod_squareoff

    routed = []
    monkeypatch.setattr(execution_router, "route_signal", lambda signal: routed.append(signal) or {"success": True})

    eod_squareoff._try_flatten_once(instance_id="A")

    assert routed == []
    assert state_store.get_open_position("A")["has_open_position"] is True


def test_legacy_position_reconciler_clears_only_legacy_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("live")
    _seed_legacy_and_instances()
    state_store.update_app_state(state="WAITING_EXIT")
    from app.services import position_reconciler

    _fake_flat_dhan(monkeypatch, position_reconciler)

    result = position_reconciler.get_reconciled_open_position(force=True, reason="test")

    assert result["has_open_position"] is False
    assert result["broker_sync"]["cleared"] is True
    assert state_store.get_open_position()["has_open_position"] is False
    assert state_store.get_open_position("A")["trading_symbol"] == "NIFTY INSTANCE A CE"
    assert state_store.get_open_position("B")["trading_symbol"] == "NIFTY INSTANCE B CE"
    assert state_store.get_app_state()["state"] == "WAITING_ENTRY"


def test_position_reconciler_with_instance_clears_only_that_instance(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("live")
    _seed_legacy_and_instances()
    state_store.update_app_state(state="WAITING_EXIT")
    from app.services import position_reconciler

    _fake_flat_dhan(monkeypatch, position_reconciler)

    result = position_reconciler.get_reconciled_open_position(force=True, reason="test", instance_id="A")

    assert result["has_open_position"] is False
    assert result["broker_sync"]["cleared"] is True
    assert state_store.get_open_position("A") is None
    assert state_store.get_open_position("B")["trading_symbol"] == "NIFTY INSTANCE B CE"
    assert state_store.get_open_position()["trading_symbol"] == "NIFTY LEGACY CE"
    assert state_store.get_app_state()["state"] == "WAITING_EXIT"


def test_startup_reconciler_with_instance_clears_only_that_instance(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("live")
    _seed_legacy_and_instances()
    state_store.update_app_state(state="WAITING_EXIT")
    from app.services import startup_reconciler

    _fake_flat_dhan(monkeypatch, startup_reconciler)

    sync = startup_reconciler.reconcile_open_position_on_startup(instance_id="A")

    assert sync["status"] == "broker_missing_local"
    assert sync["cleared"] is True
    assert state_store.get_open_position("A") is None
    assert state_store.get_open_position("B")["trading_symbol"] == "NIFTY INSTANCE B CE"
    assert state_store.get_open_position()["trading_symbol"] == "NIFTY LEGACY CE"
    assert state_store.get_app_state()["state"] == "WAITING_EXIT"


def test_list_open_positions_by_instance_returns_all_v2_positions(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("paper")
    _seed_legacy_and_instances()

    positions = state_store.list_open_positions_by_instance()

    assert set(positions) == {"A", "B"}
    assert positions["A"]["trading_symbol"] == "NIFTY INSTANCE A CE"
    assert positions["B"]["trading_symbol"] == "NIFTY INSTANCE B CE"
