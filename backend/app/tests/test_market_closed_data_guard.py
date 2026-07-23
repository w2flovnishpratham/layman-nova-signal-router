from __future__ import annotations

from app.schemas.signal import NormalizedSignal
from app.services import audit_logger, dhan_client, execution_router, option_position_monitor, state_store


def _signal() -> NormalizedSignal:
    return NormalizedSignal(
        payload_format="NOVA",
        secret="unused",
        signal_id="market-closed-guard",
        strategy_code="MANUAL",
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment="NSE_FNO",
        security_id="56376",
        trading_symbol="NIFTY 23 JUN 24100 CALL",
        option_side="CE",
        strike=24100.0,
        expiry="2026-06-23",
        qty=65,
        order_type="MARKET",
        product_type="INTRADAY",
        source="test",
        raw_payload={},
    )


def test_real_ltp_returns_market_closed_without_http(monkeypatch):
    monkeypatch.setattr(dhan_client, "_market_is_open", lambda: False)
    monkeypatch.setattr(
        dhan_client.RealDhanClient,
        "_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("HTTP client should not be opened")),
    )

    result = dhan_client.RealDhanClient().get_ltp(
        client_id="client",
        access_token="token",
        exchange_segment="NSE_FNO",
        security_id="56376",
    )

    assert result.success is False
    assert result.error == "market_closed"
    assert result.raw_response == {"market_closed": True}


def test_order_router_blocks_before_broker_when_market_closed(monkeypatch):
    monkeypatch.setattr(execution_router, "_market_is_open", lambda: False)
    monkeypatch.setattr(execution_router, "get_engine_mode", lambda *args, **kwargs: "paper")
    monkeypatch.setattr(execution_router, "get_runtime_settings", lambda: {})
    monkeypatch.setattr(execution_router, "log_order_event", lambda _event: None)
    monkeypatch.setattr(execution_router, "log_audit_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(execution_router, "update_app_state", lambda **_kwargs: None)
    monkeypatch.setattr(
        execution_router,
        "get_broker_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broker should not be created")),
    )

    result = execution_router._place_order(_signal(), 65, "ENTRY", runtime={})

    assert result["blocked"] is True
    assert result["success"] is False
    assert result["normalizedError"]["category"] == "MARKET_CLOSED"


def test_option_monitor_marks_market_closed_without_ws_or_rest(tmp_path, monkeypatch):
    # Exercise the real state_store: the monitor's market-closed write now goes
    # through the atomic compare-and-set primitive, so module-level get/set mocks
    # would be bypassed. Use a real (tmp) runtime dir instead.
    state_dir = tmp_path / "runtime_state"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    log_files = {k: tmp_path / "logs" / f"{k}.jsonl" for k in ("webhook", "order", "audit", "error")}
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    state_store.init_runtime_files()
    state_store.update_app_state(engine_mode="paper")
    state_store.set_open_position(state_store.stamp_new_open_position({
        "has_open_position": True,
        "security_id": "56376",
        "exchange_segment": "NSE_FNO",
        "entry_price": 100.0,
        "qty": 65,
    }))

    monkeypatch.setattr(option_position_monitor, "_market_is_open", lambda: False)
    monkeypatch.setattr(option_position_monitor, "marketfeed_ws_status", lambda: {"connected": False})
    cleared = {"called": False}
    monkeypatch.setattr(option_position_monitor, "clear_marketfeed_subscription", lambda: cleared.update(called=True))
    monkeypatch.setattr(
        option_position_monitor,
        "get_dhan_credentials",
        lambda: (_ for _ in ()).throw(AssertionError("credentials should not be read")),
    )

    option_position_monitor.monitor_once()

    updated = state_store.get_open_position()
    assert cleared["called"] is True
    assert updated["has_open_position"] is True
    assert updated["live_pnl"]["status"] == "market_closed"
    assert updated["live_pnl"]["source"] == "market_closed"
