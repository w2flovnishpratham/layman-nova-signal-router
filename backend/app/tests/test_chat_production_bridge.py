from __future__ import annotations

import asyncio
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from app.api.ws import _apply_production_command
from app.config import settings
from app.domain.state_machine import SetupState, validate_command
from app.routers import setup as setup_router
from app.routers.webhook import _safe_raw_body_for_log, _valid_webhook_signature
from app.schemas.signal import NormalizedSignal
from app.services import audit_logger, credential_vault, state_store
from app.services.chat_event_publisher import publish_chat_result
from app.services.dhan_client import RealDhanClient
from app.services.execution_router import route_entry_signal
from app.store.redis_session import session_store


def _isolate_runtime(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "EXTERNAL_POSITIONS_FILE", state_dir / "external_positions.json")
    monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
    log_files = {
        "webhook": log_dir / "webhook_events.jsonl",
        "order": log_dir / "order_events.jsonl",
        "audit": log_dir / "audit_events.jsonl",
        "error": log_dir / "errors.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    monkeypatch.setattr(settings, "APP_ENV", "local")
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(settings, "REQUIRE_MARKET_HOURS", False)
    credential_vault._LOCAL_MEMORY_PAYLOAD.clear()
    credential_vault._LOCAL_MEMORY_PAYLOAD.update({"version": 1, "dhan": None, "webhook_secret": None})
    state_store.init_runtime_files()


def test_chat_session_uses_persistent_production_webhook(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        first = client.post("/api/session/start").json()
        second = client.post("/api/session/start").json()

    assert first["webhookUrl"].endswith("/webhook/tradingview")
    assert first["webhookSecret"] == second["webhookSecret"]
    assert first["webhookSecret"] == credential_vault.get_webhook_secret()


def test_session_bootstrap_exposes_current_lot_size(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr("app.api.session.current_nifty_lot_size", lambda: 75)
    from app.main import app

    with TestClient(app) as client:
        bootstrap = client.post("/api/session/start").json()

    assert bootstrap["lotSize"] == 75


def test_current_lot_size_uses_nearest_non_expired_nifty_expiry(tmp_path, monkeypatch):
    scrip_master = tmp_path / "scrip-master.csv"
    scrip_master.write_text(
        "\n".join(
            [
                "UNDERLYING_SYMBOL,INSTRUMENT,OPTION_TYPE,SM_EXPIRY_DATE,LOT_SIZE",
                "NIFTY,OPTIDX,CE,2099-01-01,75",
                "NIFTY,OPTIDX,PE,2099-01-01,75",
                "NIFTY,OPTIDX,CE,2100-01-01,65",
                "NIFTY,OPTIDX,PE,2100-01-01,65",
                "NIFTY,OPTIDX,CE,2100-01-01,65",
            ]
        ),
        encoding="utf-8",
    )
    setup_router._NIFTY_LOT_SIZE_CACHE.update({"key": None, "lot_size": None})
    monkeypatch.setattr(setup_router, "_scrip_master_candidates", lambda: [scrip_master])

    assert setup_router.current_nifty_lot_size() == 75


def test_production_configuration_rejects_placeholder_session_secret(monkeypatch):
    from app.main import validate_production_configuration

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "SESSION_TOKEN_SECRET", "change-me-in-production")

    with pytest.raises(RuntimeError, match="SESSION_TOKEN_SECRET"):
        validate_production_configuration()


def test_production_configuration_rejects_mock_routing(monkeypatch):
    from app.main import validate_production_configuration

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
    monkeypatch.setattr(settings, "SESSION_TOKEN_SECRET", "s" * 32)

    with pytest.raises(RuntimeError, match="DHAN_MODE=REAL"):
        validate_production_configuration()


def test_production_readiness_rejects_local_http_webhook_url(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "BACKEND_PUBLIC_BASE_URL", "http://127.0.0.1:8001")
    state_store.set_engine_mode("paper")

    readiness = setup_router.setup_readiness(check_dhan_ping=False)

    assert "BACKEND_PUBLIC_BASE_URL must use HTTPS in production." in readiness["issues"]


def test_chat_session_recovers_persisted_paused_engine_state(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    state_store.update_runtime_settings(
        allow_entry=False,
        max_qty_per_order=130,
        allowed_option_side="PE",
        max_trades_per_day=7,
        max_daily_loss=4500,
        option_exit_mode="DHAN_SUPER",
        option_disable_sl=False,
        option_sl_percent=12,
        option_tp_percent=35,
    )
    from app.main import app

    with TestClient(app) as client:
        bootstrap = client.post("/api/session/start").json()
        snapshot = client.get(f"/api/session/{bootstrap['sessionId']}").json()

    assert snapshot["state"] == "PAUSED"
    assert snapshot["config"]["risk"] == {
        "maxTrades": 7,
        "maxLoss": 4500.0,
        "lots": 2,
        "side": "PE",
    }
    assert snapshot["config"]["exits"]["mode"] == "custom"
    assert snapshot["config"]["exits"]["stopLossPct"] == 12
    assert snapshot["config"]["exits"]["targetPct"] == 35


def test_side_filter_closes_opposite_position_without_opening_disallowed_side(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    state_store.update_runtime_settings(
        max_qty_per_order=65,
        allowed_option_side="CE",
        allow_entry=True,
        allow_exit=True,
        option_exit_mode="SERVER",
    )
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TRADINGVIEW_NIFTY_V1",
            "symbol": "NIFTY",
            "instrument_type": "OPTIDX",
            "exchange_segment": "NSE_FNO",
            "security_id": "MOCK-CE",
            "trading_symbol": "NIFTY CE",
            "option_side": "CE",
            "strike": 25000,
            "expiry": "2026-06-04",
            "qty": 65,
            "entry_order_id": "MOCK-ENTRY",
            "entry_price": 100,
            "exit_management": "SERVER",
            "order_type": "MARKET",
            "product_type": "INTRADAY",
        }
    )
    signal = NormalizedSignal(
        payload_format="NOVA",
        secret="unused",
        signal_id="side-filter-exit-only",
        strategy_code="TRADINGVIEW_NIFTY_V1",
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment="NSE_FNO",
        security_id="MOCK-PE",
        trading_symbol="NIFTY PE",
        option_side="PE",
        strike=25000,
        expiry="2026-06-04",
        qty=65,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload={},
    )

    result = route_entry_signal(signal)

    assert result["status"] == "SIDE_FILTER_EXIT_ONLY"
    assert result["side_filter_exit_only"] is True
    assert state_store.get_open_position()["has_open_position"] is False


def test_real_dhan_client_applies_global_request_and_response_hooks():
    client = RealDhanClient()._client(timeout=1)
    try:
        assert client.event_hooks["request"]
        assert client.event_hooks["response"]
    finally:
        client.close()


def test_webhook_logging_redacts_secrets_and_hmac_validation_contract():
    secret = "strong-webhook-secret-value-123456"
    raw_body = f'{{"secret":"{secret}","nested":{{"access_token":"daily-token"}},"action":"ENTRY"}}'
    signature = hmac.new(secret.encode(), raw_body.encode(), hashlib.sha256).hexdigest()

    logged = _safe_raw_body_for_log(raw_body)

    assert secret not in logged
    assert "daily-token" not in logged
    assert logged.count("[REDACTED]") == 2
    assert _valid_webhook_signature(raw_body, secret, f"sha256={signature}") is True
    assert _valid_webhook_signature(raw_body, secret, "sha256=wrong") is False


def test_chat_custom_exit_rules_configure_dhan_super_order_sl_and_tp(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    data = {
        "mode": "custom",
        "targetProfit": 2500,
        "targetPct": 35,
        "stopLossPct": 12,
    }

    state, patch = validate_command(SetupState.RISK_CONFIGURED, "setup.exits", data)
    asyncio.run(_apply_production_command("setup.exits", data))
    runtime = state_store.get_runtime_settings()

    assert state == SetupState.EXITS_CONFIGURED
    assert patch["exits"]["stopLossPct"] == 12
    assert runtime["option_exit_mode"] == "DHAN_SUPER"
    assert runtime["option_disable_sl"] is False
    assert runtime["option_sl_percent"] == 12
    assert runtime["option_tp_percent"] == 35


def test_chat_risk_command_uses_current_scrip_master_lot_size(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr("app.api.ws.current_nifty_lot_size", lambda: 75)

    asyncio.run(
        _apply_production_command(
            "setup.risk",
            {"lots": 2, "side": "BOTH", "maxTrades": 5, "maxLoss": 3000},
        )
    )

    assert state_store.get_runtime_settings()["max_qty_per_order"] == 150


def test_successful_server_exit_publishes_exact_trade_exit_event(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("live")

    async def scenario():
        session = await session_store.create(state=SetupState.LIVE)
        await session_store.update_active_trade(
            session.id,
            {
                "symbol": "NIFTY CE",
                "qty": 75,
                "orderId": "ENTRY-1",
                "status": "OPEN",
            },
        )
        signal = NormalizedSignal(
            payload_format="NOVA",
            secret="unused",
            signal_id="server-exit-test",
            strategy_code="TRADINGVIEW_NIFTY_V1",
            action="EXIT",
            side="SELL",
            symbol="NIFTY",
            instrument_type="OPTIDX",
            exchange_segment="NSE_FNO",
            security_id="123",
            trading_symbol="NIFTY CE",
            option_side="CE",
            strike=25000,
            expiry="2026-06-04",
            qty=75,
            order_type="MARKET",
            product_type="INTRADAY",
            source="server_side_option_monitor",
            raw_payload={"exit_reason": "TP", "live_pnl": {"unrealized_pnl": 450}},
        )

        await publish_chat_result(
            signal,
            {"success": True, "status": "ORDER_PLACED", "order_id": "EXIT-1", "avg_price": 106},
        )
        updated = await session_store.get(session.id)
        assert updated is not None
        return updated

    updated = asyncio.run(scenario())

    assert updated.active_trade is None
    assert updated.events[-1].type == "trade.exit"
    assert updated.events[-1].data["orderId"] == "EXIT-1"
    assert updated.events[-1].data["exitPrice"] == 106
    assert updated.events[-1].data["grossPnl"] == 450


def test_live_traded_entry_publishes_order_filled_directly(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("live")

    async def scenario():
        session = await session_store.create(state=SetupState.LIVE)
        signal = NormalizedSignal(
            payload_format="NOVA",
            secret="unused",
            signal_id="live-fill-test",
            strategy_code="TRADINGVIEW_NIFTY_V1",
            action="ENTRY",
            side="BUY",
            symbol="NIFTY",
            instrument_type="OPTIDX",
            exchange_segment="NSE_FNO",
            security_id="LIVE-123",
            trading_symbol="NIFTY LIVE CE",
            option_side="CE",
            strike=25000,
            expiry="2026-06-25",
            qty=65,
            order_type="MARKET",
            product_type="INTRADAY",
            raw_payload={},
        )

        await publish_chat_result(
            signal,
            {
                "success": True,
                "status": "ORDER_PLACED",
                "broker_status": "TRADED",
                "order_id": "LIVE-ENTRY-1",
                "avg_price": 102.5,
                "filled_qty": 65,
                "security_id": "LIVE-123",
                "trading_symbol": "NIFTY LIVE CE",
                "exit_management": "DHAN_SUPER",
                "raw_response": {"exchangeOrderId": "EXCHANGE-1"},
            },
        )
        updated = await session_store.get(session.id)
        assert updated is not None
        await session_store.update_state(session.id, SetupState.ENDED, {})
        return updated

    updated = asyncio.run(scenario())
    fills = [item for item in updated.events if item.type == "order.filled"]

    assert len(fills) == 1
    assert fills[0].data["mode"] == "live"
    assert fills[0].data["avgPrice"] == 102.5
    assert fills[0].data["securityId"] == "LIVE-123"
    assert fills[0].data["exchOrderId"] == "EXCHANGE-1"
    assert updated.active_trade == fills[0].data


def test_new_session_hydrates_open_trade_without_legacy_poller(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr("app.api.session.current_nifty_lot_size", lambda: 65)
    state_store.set_engine_mode("live")
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    state_store.set_wallet_snapshot(
        {
            "success": True,
            "available_balance": 100000,
            "utilized_amount": 6500,
            "session_pnl": 250,
        }
    )
    state_store.set_open_position(
        {
            "has_open_position": True,
            "symbol": "NIFTY",
            "exchange_segment": "NSE_FNO",
            "security_id": "LIVE-123",
            "trading_symbol": "NIFTY LIVE CE",
            "option_side": "CE",
            "strike": 25000,
            "expiry": "2026-06-25",
            "qty": 65,
            "entry_order_id": "LIVE-ENTRY-1",
            "entry_price": 100,
            "exit_management": "DHAN_SUPER",
            "live_pnl": {
                "ltp": 104,
                "unrealized_pnl": 260,
                "pnl_percent": 4,
            },
        }
    )

    async def scenario():
        from app.api.session import start_session

        bootstrap = await start_session()
        session = await session_store.get(str(bootstrap["sessionId"]))
        assert session is not None
        await session_store.update_state(session.id, SetupState.ENDED, {})
        return session

    session = asyncio.run(scenario())
    event_types = [item.type for item in session.events]

    assert session.active_trade is not None
    assert session.active_trade["mode"] == "live"
    assert session.active_trade["ltp"] == 104
    assert session.active_trade["pnl"] == 260
    assert "funds.update" in event_types
    assert "position.update" in event_types
