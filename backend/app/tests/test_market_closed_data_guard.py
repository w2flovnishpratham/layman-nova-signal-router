from __future__ import annotations

from app.schemas.signal import NormalizedSignal
from app.services import dhan_client, execution_router, option_position_monitor


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


def test_option_monitor_marks_market_closed_without_ws_or_rest(monkeypatch):
    position = {
        "has_open_position": True,
        "security_id": "56376",
        "exchange_segment": "NSE_FNO",
        "entry_price": 100.0,
        "qty": 65,
    }
    updated = {}

    monkeypatch.setattr(option_position_monitor, "_market_is_open", lambda: False)
    monkeypatch.setattr(option_position_monitor, "get_runtime_settings", lambda: {})
    monkeypatch.setattr(option_position_monitor, "get_engine_mode", lambda: "paper")
    monkeypatch.setattr(option_position_monitor, "get_open_position", lambda: dict(position))
    monkeypatch.setattr(option_position_monitor, "set_open_position", lambda value: updated.update(value) or value)
    monkeypatch.setattr(option_position_monitor, "marketfeed_ws_status", lambda: {"connected": False})
    cleared = {"called": False}
    monkeypatch.setattr(option_position_monitor, "clear_marketfeed_subscription", lambda: cleared.update(called=True))
    monkeypatch.setattr(
        option_position_monitor,
        "get_dhan_credentials",
        lambda: (_ for _ in ()).throw(AssertionError("credentials should not be read")),
    )

    option_position_monitor.monitor_once()

    assert cleared["called"] is True
    assert updated["live_pnl"]["status"] == "market_closed"
    assert updated["live_pnl"]["source"] == "market_closed"
