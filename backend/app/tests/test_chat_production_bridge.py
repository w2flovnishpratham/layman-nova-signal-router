from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.api.ws import _apply_production_command
from app.config import DISABLED_OPTION_SL_PERCENT, settings
from app.domain.state_machine import SetupState, validate_command
from app.routers import orders as orders_router
from app.routers import setup as setup_router
from app.routers.webhook import _safe_raw_body_for_log, _valid_webhook_signature
from app.schemas.signal import NormalizedSignal
from app.services import audit_logger, credential_vault, paper_broker, paper_portfolio, shared_market_data, state_store
from app.services.chat_event_publisher import publish_chat_result
from app.services.credential_vault import DhanCredentials
from app.services.dhan_client import RealDhanClient
from app.services.dhan_client import DhanLtpResult
from app.services.execution_router import _broker_exit_levels, route_entry_signal
from app.store.redis_session import session_store


def _isolate_runtime(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
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
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(settings, "APP_ENV", "local")
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(settings, "REQUIRE_MARKET_HOURS", False)
    monkeypatch.setattr(settings, "DHAN_SHARED_DATA_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "DHAN_SHARED_CLIENT_ID", "shared-client", raising=False)
    monkeypatch.setattr(settings, "DHAN_SHARED_PIN", "1234", raising=False)
    monkeypatch.setattr(settings, "DHAN_SHARED_TOTP_SECRET", "GEZDGNBVGY3TQOJQ", raising=False)
    shared_creds = DhanCredentials("shared-client", "shared-token", "shared_market_data")

    class FakeSharedLtpClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get_ltp(self, **_kwargs) -> DhanLtpResult:
            return DhanLtpResult(success=True, message="quote", ltp=100.0)

    monkeypatch.setattr(shared_market_data, "shared_market_data_configured", lambda: True)
    monkeypatch.setattr(shared_market_data, "get_shared_market_credentials", lambda: shared_creds)
    monkeypatch.setattr(paper_broker, "shared_market_data_configured", lambda: True)
    monkeypatch.setattr(paper_broker, "get_shared_market_credentials", lambda: shared_creds)
    monkeypatch.setattr(paper_broker, "RealDhanClient", FakeSharedLtpClient)
    credential_vault._LOCAL_MEMORY_PAYLOAD.clear()
    credential_vault._LOCAL_MEMORY_PAYLOAD.update({"version": 1, "dhan": None, "webhook_secret": None})
    state_store.init_runtime_files()


def test_chat_session_uses_persistent_production_webhook(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        first = client.post("/api/session/start").json()
        second = client.post("/api/session/start").json()

    assert first["webhookUrl"].endswith("/api/webhook/strategy/supertrend")
    assert first["webhookSecret"] == second["webhookSecret"]
    assert first["webhookSecret"] == "Managed server-side"
    assert credential_vault.get_webhook_secret()


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


def test_production_configuration_rejects_debug_mode(monkeypatch):
    from app.main import validate_production_configuration

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "SESSION_TOKEN_SECRET", "s" * 32)
    monkeypatch.setattr(settings, "DEBUG_ENABLED", True)

    with pytest.raises(RuntimeError, match="DEBUG_ENABLED"):
        validate_production_configuration()


def test_production_configuration_rejects_default_security_id_override(monkeypatch):
    from app.main import validate_production_configuration

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "SESSION_TOKEN_SECRET", "s" * 32)
    monkeypatch.setattr(settings, "DEBUG_ENABLED", False)
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
    monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "123456")

    with pytest.raises(RuntimeError, match="DEFAULT_SECURITY_ID"):
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
        "lots": 1,
        "side": "PE",
    }
    assert snapshot["config"]["exits"]["mode"] == "custom"
    assert snapshot["config"]["exits"]["stopLossPct"] == 12
    assert snapshot["config"]["exits"]["targetPct"] == 35


def test_chat_session_snapshot_masks_dhan_client_id(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("live")
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    credential_vault._LOCAL_MEMORY_PAYLOAD["dhan"] = {
        "client_id": "1000000001",
        "access_token": "raw-dhan-access-token",
        "connected_at": state_store.utc_now(),
    }
    from app.main import app

    with TestClient(app) as client:
        bootstrap = client.post("/api/session/start").json()
        snapshot = client.get(f"/api/session/{bootstrap['sessionId']}").json()

    serialized = json.dumps(snapshot)
    assert snapshot["config"]["broker"]["clientId"] == "******0001"
    assert "1000000001" not in serialized
    assert "raw-dhan-access-token" not in serialized
    assert "access_token" not in serialized


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


@pytest.mark.parametrize("engine_mode", ["paper", "live"])
def test_block_entry_requests_rejects_entries_before_order_routing(tmp_path, monkeypatch, engine_mode):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode(engine_mode)
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    state_store.update_runtime_settings(
        max_qty_per_order=65,
        allowed_option_side="BOTH",
        allow_entry=False,
        allow_exit=True,
    )

    def forbidden_reconcile(_signal):
        raise AssertionError("Blocked entry request should not reconcile or query broker exposure.")

    def forbidden_order(*_args, **_kwargs):
        raise AssertionError("Blocked entry request should not place an order.")

    monkeypatch.setattr("app.services.execution_router._reconcile_tracked_position_before_entry", forbidden_reconcile)
    monkeypatch.setattr("app.services.execution_router._place_order", forbidden_order)

    signal = NormalizedSignal(
        payload_format="NOVA",
        secret="unused",
        signal_id=f"entry-block-{engine_mode}",
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

    result = route_entry_signal(signal)

    assert result["blocked"] is True
    assert result["success"] is False
    assert result["status"] == "ENTRY_REQUEST_BLOCKED"
    assert result["block_code"] == "ENTRY_REQUEST_BLOCKED"


def test_session_pause_resume_commands_are_idempotent_for_running_states():
    state, patch = validate_command(SetupState.LIVE, "session.resume", {})
    assert state == SetupState.LIVE
    assert patch == {}

    state, patch = validate_command(SetupState.PAUSED, "session.pause", {})
    assert state == SetupState.PAUSED
    assert patch == {}


def test_manual_paper_entry_auto_selects_contract_with_real_market_data_settings(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("paper")
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
    monkeypatch.setattr(orders_router, "current_nifty_lot_size", lambda: 65)
    monkeypatch.setattr(
        orders_router,
        "get_atm_option_snapshot",
        lambda **_kwargs: {
            "marketOpen": True,
            "niftySpot": 24110.0,
            "niftySpotSource": "rest",
            "atmStrike": 24100,
            "options": {
                "CE": {
                    "securityId": "AUTO-CE",
                    "tradingSymbol": "NIFTY 2026-06-23 24100 CE",
                    "strike": 24100,
                    "expiry": "2026-06-23",
                    "ltp": 120.5,
                    "ltpSource": "rest",
                    "autoContract": {
                        "security_id": "AUTO-CE",
                        "trading_symbol": "NIFTY 2026-06-23 24100 CE",
                    },
                }
            },
        },
    )

    signal = orders_router._manual_entry_signal(
        option_side="CE",
        lots=1,
        strike=None,
        expiry=None,
        security_id=None,
        trading_symbol=None,
        raw_payload={"manual_order": True},
    )

    assert signal.security_id == "AUTO-CE"
    assert signal.trading_symbol == "NIFTY 2026-06-23 24100 CE"
    assert signal.strike == 24100
    assert signal.expiry == "2026-06-23"
    assert signal.qty == 65
    assert signal.raw_payload["autoAtm"]["atmStrike"] == 24100


def test_manual_live_entry_cannot_bypass_verified_egress_guard(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("live")
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    state_store.update_runtime_settings(max_qty_per_order=65, allowed_option_side="BOTH")
    monkeypatch.setattr(
        "app.services.execution_router.get_dhan_credentials",
        lambda: DhanCredentials("1000000001", "live-token", "test"),
    )
    monkeypatch.setattr(
        "app.services.execution_router.dhan_token_age_metadata",
        lambda: {
            "token_age_minutes": 1,
            "token_warn": False,
            "token_expired": False,
        },
    )
    monkeypatch.setattr("app.services.execution_router._market_is_open", lambda: True)

    def forbidden_http_client(*_args, **_kwargs):
        raise AssertionError("manual live route must not call Dhan without verified egress")

    monkeypatch.setattr("app.services.dhan_client.httpx.Client", forbidden_http_client)

    from app.main import app

    with TestClient(app) as client:
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
        monkeypatch.setattr(settings, "EXECUTION_NODE_ROUTING_ENABLED", True)
        response = client.post(
            "/api/orders/manual-entry",
            json={
                "side": "CE",
                "lots": 1,
                "securityId": "LIVE-CE",
                "tradingSymbol": "NIFTY LIVE CE",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["executionResult"]["blocked"] is True
    assert body["executionResult"]["block_code"] == "LIVE_EGRESS_NOT_VERIFIED"
    serialized = str(body)
    assert "live-token" not in serialized
    assert "proxy_url" not in serialized


def test_active_position_exit_levels_can_be_updated_for_server_managed_position(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("paper")
    state_store.set_open_position(
        {
            "has_open_position": True,
            "symbol": "NIFTY",
            "trading_symbol": "NIFTY 2026-06-23 24100 CE",
            "option_side": "CE",
            "qty": 65,
            "entry_price": 100.0,
            "exit_management": "SERVER",
            "live_pnl": {"entry_price": 100.0, "ltp": 105.0},
        }
    )

    result = orders_router.update_active_position_exit_levels(
        orders_router.ExitLevelsRequest(stopLossPrice=95.0, targetPrice=125.0)
    )
    position = state_store.get_open_position()

    assert result["ok"] is True
    assert result["message"] == "SL/TP levels updated."
    assert position["active_exit_levels"]["source"] == "manual"
    assert position["active_exit_levels"]["stopLossPrice"] == 95.0
    assert position["active_exit_levels"]["targetPrice"] == 125.0
    assert position["live_pnl"]["sl_price"] == 95.0
    assert position["live_pnl"]["tp_price"] == 125.0


# Dhan Super positions are display-only until broker leg modification is wired.
def test_active_position_exit_levels_rejects_display_only_dhan_super_position(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("live")
    state_store.set_open_position(
        {
            "has_open_position": True,
            "symbol": "NIFTY",
            "trading_symbol": "NIFTY 2026-06-23 24100 CE",
            "option_side": "CE",
            "qty": 65,
            "entry_price": 100.0,
            "exit_management": "DHAN_SUPER",
            "live_pnl": {"entry_price": 100.0, "ltp": 105.0},
        }
    )

    result = orders_router.update_active_position_exit_levels(
        orders_router.ExitLevelsRequest(stopLossPrice=95.0, targetPrice=125.0)
    )

    assert result["ok"] is False
    assert state_store.get_open_position().get("active_exit_levels") is None


def test_active_position_quantity_can_be_updated_for_paper_position(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("paper")
    monkeypatch.setattr(orders_router, "current_nifty_lot_size", lambda: 65)
    state_store.update_runtime_settings(max_qty_per_order=130)
    paper_portfolio.reset_paper_portfolio(100000)
    portfolio = paper_portfolio.get_paper_portfolio()
    portfolio.available_balance = 93500.0
    portfolio.utilized_amount = 6500.0
    portfolio.open_trade = {
        "symbol": "NIFTY 2026-06-23 24100 CE",
        "qty": 65,
        "entry_price": 100.0,
        "entry_value": 6500.0,
        "entry_order_id": "PAPER-ENTRY",
    }
    paper_portfolio._write(portfolio)
    state_store.set_open_position(
        {
            "has_open_position": True,
            "symbol": "NIFTY",
            "trading_symbol": "NIFTY 2026-06-23 24100 CE",
            "option_side": "CE",
            "qty": 65,
            "entry_price": 100.0,
            "exit_management": "SERVER",
            "live_pnl": {"entry_price": 100.0, "ltp": 110.0, "qty": 65},
        }
    )

    result = orders_router.update_active_position_quantity(orders_router.QuantityRequest(qty=130))
    position = state_store.get_open_position()
    resized = paper_portfolio.get_paper_portfolio()

    assert result["ok"] is True
    assert result["qty"] == 130
    assert result["lots"] == 2
    assert position["qty"] == 130
    assert position["filled_qty"] == 130
    assert position["live_pnl"]["qty"] == 130
    assert position["live_pnl"]["unrealized_pnl"] == 1300.0
    assert position["live_pnl"]["pnl_percent"] == 10.0
    assert resized.open_trade["qty"] == 130
    assert resized.open_trade["entry_value"] == 13000.0
    assert resized.utilized_amount == 13000.0
    assert resized.available_balance == 87000.0


def test_active_position_quantity_rejects_live_position_update(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("live")
    state_store.set_open_position(
        {
            "has_open_position": True,
            "symbol": "NIFTY",
            "trading_symbol": "NIFTY 2026-06-23 24100 CE",
            "option_side": "CE",
            "qty": 65,
            "entry_price": 100.0,
            "exit_management": "DHAN_SUPER",
            "live_pnl": {"entry_price": 100.0, "ltp": 105.0, "qty": 65},
        }
    )

    result = orders_router.update_active_position_quantity(orders_router.QuantityRequest(qty=130))

    assert result["ok"] is False
    assert state_store.get_open_position()["qty"] == 65


def test_active_position_quantity_requires_whole_lot(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("paper")
    monkeypatch.setattr(orders_router, "current_nifty_lot_size", lambda: 65)
    state_store.update_runtime_settings(max_qty_per_order=130)
    state_store.set_open_position(
        {
            "has_open_position": True,
            "symbol": "NIFTY",
            "trading_symbol": "NIFTY 2026-06-23 24100 CE",
            "option_side": "CE",
            "qty": 65,
            "entry_price": 100.0,
            "exit_management": "SERVER",
            "live_pnl": {"entry_price": 100.0, "ltp": 105.0, "qty": 65},
        }
    )

    result = orders_router.update_active_position_quantity(orders_router.QuantityRequest(qty=66))

    assert result["ok"] is False
    assert state_store.get_open_position()["qty"] == 65


def test_session_exit_open_routes_exit_without_stopping_engine(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("live")
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TRADINGVIEW_NIFTY_V1",
            "symbol": "NIFTY",
            "instrument_type": "OPTIDX",
            "exchange_segment": "NSE_FNO",
            "security_id": "LIVE-CE",
            "trading_symbol": "NIFTY CE",
            "option_side": "CE",
            "strike": 25000,
            "expiry": "2026-06-04",
            "qty": 65,
            "entry_order_id": "LIVE-ENTRY",
            "entry_price": 100,
            "exit_management": "SERVER",
            "order_type": "MARKET",
            "product_type": "INTRADAY",
        }
    )
    called = {"panic_exit": False}

    def fake_panic_exit():
        called["panic_exit"] = True
        return {"ok": True, "execution_result": {"success": True, "status": "TRADED"}}

    def forbidden_stop_engine():
        raise AssertionError("session.exit_open must not stop the engine.")

    monkeypatch.setattr("app.api.ws.panic_exit", fake_panic_exit)
    monkeypatch.setattr("app.api.ws.stop_engine", forbidden_stop_engine)

    state, patch = validate_command(SetupState.LIVE, "session.exit_open", {})
    asyncio.run(_apply_production_command("session.exit_open", {}))

    assert state == SetupState.LIVE
    assert patch == {}
    assert called["panic_exit"] is True
    assert state_store.get_app_state()["webhook_trading_enabled"] is True


def test_v3_entry_stores_tradingview_sr_suggestion(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("paper")
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    state_store.update_runtime_settings(
        allow_entry=True,
        allow_exit=True,
        allowed_option_side="BOTH",
        max_qty_per_order=65,
        option_exit_mode="SERVER",
        server_side_exit_enabled=False,
    )

    def fake_place_order(signal, qty, action, **_kwargs):
        assert action == "ENTRY"
        return {
            "success": True,
            "status": "TRADED",
            "order_id": "PAPER-ENTRY",
            "avg_price": 200.0,
            "requested_qty": qty,
            "filled_qty": qty,
            "exit_management": "SERVER",
            "security_id": signal.security_id,
            "trading_symbol": signal.trading_symbol,
            "raw_response": {},
        }

    monkeypatch.setattr("app.services.execution_router._place_order", fake_place_order)
    monkeypatch.setattr("app.services.execution_router.refresh_wallet_snapshot", lambda **_kwargs: {})
    signal = NormalizedSignal(
        payload_format="NOVA",
        secret="unused",
        signal_id="v3-entry",
        strategy_code="TRADINGVIEW_NIFTY_V3",
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment="NSE_FNO",
        security_id="MOCK-CE",
        trading_symbol="NIFTY CE",
        option_side="CE",
        strike=25000,
        expiry="2026-06-16",
        qty=65,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload={
            "nifty_price": 23500,
            "sl_level": 23450,
            "tp_level": 23600,
            "delta": 0.5,
        },
    )

    result = route_entry_signal(signal)
    position = state_store.get_open_position()

    assert result["success"] is True
    assert result["sr_suggestion"]["available"] is True
    assert result["sr_suggestion"]["stopLossPrice"] == 185.0
    assert result["sr_suggestion"]["targetPrice"] == 240.0
    assert position["sr_suggestion"] == result["sr_suggestion"]
    assert position["active_exit_levels"] is None


def test_apply_sr_suggestion_arms_paper_position_levels(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("paper")
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TRADINGVIEW_NIFTY_V3",
            "symbol": "NIFTY",
            "instrument_type": "OPTIDX",
            "exchange_segment": "NSE_FNO",
            "security_id": "PAPER-CE",
            "trading_symbol": "NIFTY CE",
            "option_side": "CE",
            "strike": 25000,
            "expiry": "2026-06-16",
            "qty": 65,
            "entry_order_id": "PAPER-ENTRY",
            "entry_price": 200.0,
            "exit_management": "SERVER",
            "order_type": "MARKET",
            "product_type": "INTRADAY",
            "sr_suggestion": {
                "available": True,
                "accepted": False,
                "stopLossPrice": 185.0,
                "targetPrice": 240.0,
            },
            "live_pnl": {"entry_price": 200.0},
        }
    )

    state, patch = validate_command(SetupState.LIVE, "session.apply_sr_suggestion", {})
    asyncio.run(_apply_production_command("session.apply_sr_suggestion", {}))
    position = state_store.get_open_position()

    assert state == SetupState.LIVE
    assert patch == {}
    assert position["sr_suggestion"]["accepted"] is True
    assert position["active_exit_levels"]["source"] == "sr_suggestion"
    assert position["active_exit_levels"]["stopLossPrice"] == 185.0
    assert position["active_exit_levels"]["targetPrice"] == 240.0
    assert position["live_pnl"]["sl_price"] == 185.0
    assert position["live_pnl"]["tp_price"] == 240.0


def test_apply_sr_suggestion_rejects_live_mode(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("live")
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TRADINGVIEW_NIFTY_V3",
            "security_id": "LIVE-CE",
            "trading_symbol": "NIFTY CE",
            "option_side": "CE",
            "qty": 65,
            "entry_order_id": "LIVE-ENTRY",
            "entry_price": 200.0,
            "sr_suggestion": {
                "available": True,
                "stopLossPrice": 185.0,
                "targetPrice": 240.0,
            },
        }
    )

    with pytest.raises(ValueError, match="paper testing only"):
        asyncio.run(_apply_production_command("session.apply_sr_suggestion", {}))


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


def test_chat_tp_only_exit_rules_use_disabled_sl_percent(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    data = {
        "mode": "flip_tp",
        "targetProfit": 3500,
        "targetPct": 35,
        "stopLossPct": DISABLED_OPTION_SL_PERCENT,
    }

    state, patch = validate_command(SetupState.RISK_CONFIGURED, "setup.exits", data)
    asyncio.run(_apply_production_command("setup.exits", data))
    runtime = state_store.get_runtime_settings()

    assert state == SetupState.EXITS_CONFIGURED
    assert patch["exits"]["stopLossPct"] == DISABLED_OPTION_SL_PERCENT
    assert runtime["option_exit_mode"] == "DHAN_SUPER"
    assert runtime["option_disable_sl"] is True
    assert runtime["option_sl_percent"] == DISABLED_OPTION_SL_PERCENT
    assert runtime["option_tp_percent"] == 35


def test_disabled_sl_level_ignores_stale_regular_sl_percent():
    sl_percent, _tp_percent, stop_loss_price, _target_price = _broker_exit_levels(
        500.0,
        {"option_disable_sl": True, "option_sl_percent": 10.0, "option_tp_percent": 35.0},
    )

    assert sl_percent == DISABLED_OPTION_SL_PERCENT
    assert stop_loss_price == 0.5


def test_chat_risk_command_does_not_write_backend_quantity_cap(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)

    asyncio.run(
        _apply_production_command(
            "setup.risk",
            {"lots": 2, "side": "BOTH", "maxTrades": 5, "maxLoss": 3000},
        )
    )

    runtime = state_store.get_runtime_settings()
    assert "max_qty_per_order" not in runtime
    assert runtime["allowed_option_side"] == "BOTH"
    assert runtime["max_trades_per_day"] == 5
    assert runtime["max_daily_loss"] == 3000


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
        from app.services.user_context import dev_user

        bootstrap = await start_session(dev_user())
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
