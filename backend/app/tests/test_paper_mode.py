from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import audit_logger, credential_vault, paper_broker, paper_portfolio, state_store
from app.services.dhan_client import DhanLtpResult, RealDhanClient, get_broker_client
from app.services.paper_broker import PaperBroker, _simulated_charges


def _isolate_paper_runtime(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
    log_files = {
        "webhook": log_dir / "webhook_events.jsonl",
        "order": log_dir / "order_events.jsonl",
        "paper_orders": log_dir / "paper_orders.jsonl",
        "audit": log_dir / "audit_events.jsonl",
        "error": log_dir / "errors.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(paper_broker, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    credential_vault._LOCAL_MEMORY_PAYLOAD.clear()
    credential_vault._LOCAL_MEMORY_PAYLOAD.update({"version": 1, "dhan": None, "webhook_secret": None})


class _QuoteClient:
    ltp = 100.0

    def get_ltp(self, **_kwargs) -> DhanLtpResult:
        return DhanLtpResult(success=True, message="quote", ltp=self.ltp)


def test_paper_broker_applies_slippage_charges_and_updates_portfolio(tmp_path, monkeypatch):
    _isolate_paper_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(paper_broker, "RealDhanClient", _QuoteClient)
    state_store.set_engine_mode("paper")
    state_store.update_runtime_settings(paper_starting_balance=100000, paper_slippage_percent=0.10)
    paper_portfolio.reset_paper_portfolio(100000)

    broker = PaperBroker()
    entry = broker.place_order(
        client_id="client",
        access_token="token",
        payload={
            "transactionType": "BUY",
            "quantity": 10,
            "tradingSymbol": "NIFTY TEST CE",
            "securityId": "123",
            "exchangeSegment": "NSE_FNO",
        },
    )

    assert entry.success is True
    assert entry.order_id and entry.order_id.startswith("PAPER-")
    assert entry.avg_price == 100.10
    entry_charges = _simulated_charges(10, 100.10, "BUY")
    after_entry = paper_portfolio.get_paper_portfolio()
    assert after_entry.available_balance == round(100000 - 1001 - entry_charges, 2)
    assert after_entry.utilized_amount == 1001

    _QuoteClient.ltp = 110.0
    exit_result = broker.place_order(
        client_id="client",
        access_token="token",
        payload={
            "transactionType": "SELL",
            "quantity": 10,
            "tradingSymbol": "NIFTY TEST CE",
            "securityId": "123",
            "exchangeSegment": "NSE_FNO",
        },
    )

    assert exit_result.success is True
    assert exit_result.avg_price == 109.90
    exit_charges = _simulated_charges(10, 109.90, "SELL")
    expected_pnl = round(1099 - 1001 - entry_charges - exit_charges, 2)
    after_exit = paper_portfolio.get_paper_portfolio()
    assert after_exit.utilized_amount == 0
    assert after_exit.realized_pnl == expected_pnl
    assert after_exit.closed_trades[-1]["realized_pnl"] == expected_pnl


def test_paper_broker_rejects_unreachable_super_order_methods():
    broker = PaperBroker()

    placed = broker.place_super_order(client_id="client", access_token="token", payload={})
    modified = broker.modify_super_order(client_id="client", access_token="token", order_id="PAPER-SUPER", payload={})
    cancelled = broker.cancel_super_order_leg(
        client_id="client",
        access_token="token",
        order_id="PAPER-SUPER",
        leg_name="STOP_LOSS_LEG",
    )

    assert placed.success is False
    assert modified.success is False
    assert cancelled.success is False
    assert placed.status == modified.status == cancelled.status == "REJECTED"


def test_paper_portfolio_rebases_session_pnl_on_new_day(tmp_path, monkeypatch):
    _isolate_paper_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("paper")
    monkeypatch.setattr(paper_portfolio, "_today", lambda: "2026-06-03")
    portfolio = paper_portfolio.reset_paper_portfolio(100000)
    portfolio.available_balance = 100500
    portfolio.session_pnl = 500
    paper_portfolio._write(portfolio)

    monkeypatch.setattr(paper_portfolio, "_today", lambda: "2026-06-04")
    rebased = paper_portfolio.get_paper_portfolio()

    assert rebased.session_start_date_ist == "2026-06-04"
    assert rebased.session_start_balance == 100500
    assert rebased.session_pnl == 0


def test_mode_switch_is_blocked_with_open_position_or_running_engine(tmp_path, monkeypatch):
    _isolate_paper_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("paper")
    state_store.set_paper_position({"has_open_position": True, "trading_symbol": "NIFTY TEST CE"})

    with pytest.raises(ValueError, match="position is open"):
        state_store.set_engine_mode("live")

    state_store.clear_paper_position()
    state_store.update_app_state(webhook_trading_enabled=True)
    with pytest.raises(ValueError, match="Stop the engine"):
        state_store.set_engine_mode("live")

    state_store.update_app_state(webhook_trading_enabled=False)
    assert state_store.set_engine_mode("live")["engine_mode"] == "live"


def test_broker_factory_requires_explicit_mode(tmp_path, monkeypatch):
    _isolate_paper_runtime(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="engine_mode not set"):
        get_broker_client()

    state_store.set_engine_mode("paper")
    assert isinstance(get_broker_client(), PaperBroker)
    state_store.set_engine_mode("live")
    assert isinstance(get_broker_client(), RealDhanClient)


def test_webhook_rejects_when_mode_is_not_selected(tmp_path, monkeypatch):
    _isolate_paper_runtime(tmp_path, monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        response = client.post("/webhook/tradingview", json={"secret": "unused"})

    assert response.status_code == 422
    assert response.json()["status"] == "ENGINE_MODE_NOT_SET"


def test_reconfigure_stops_flat_engine_and_clears_mode(tmp_path, monkeypatch):
    _isolate_paper_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("paper")
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    from app.main import app

    with TestClient(app) as client:
        response = client.post("/api/engine/reconfigure")

    assert response.status_code == 200
    assert state_store.get_engine_mode(legacy_fallback=False) is None
    assert state_store.get_app_state()["webhook_trading_enabled"] is False


def test_reconfigure_rejects_open_paper_position(tmp_path, monkeypatch):
    _isolate_paper_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("paper")
    state_store.set_paper_position({"has_open_position": True, "trading_symbol": "NIFTY TEST CE"})
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    from app.main import app

    with TestClient(app) as client:
        response = client.post("/api/engine/reconfigure")

    assert response.status_code == 409
    assert state_store.get_engine_mode(legacy_fallback=False) == "paper"
    assert state_store.get_app_state()["webhook_trading_enabled"] is True


def test_eod_squareoff_routes_the_selected_paper_position(tmp_path, monkeypatch):
    _isolate_paper_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("paper")
    state_store.set_paper_position(
        {
            "has_open_position": True,
            "strategy_code": "TRADINGVIEW_NIFTY_V1",
            "symbol": "NIFTY",
            "instrument_type": "OPTIDX",
            "exchange_segment": "NSE_FNO",
            "security_id": "123",
            "trading_symbol": "NIFTY TEST CE",
            "option_side": "CE",
            "qty": 65,
            "order_type": "MARKET",
            "product_type": "INTRADAY",
        }
    )
    from app.services import execution_router
    from app.workers import eod_squareoff

    routed = []
    monkeypatch.setattr(eod_squareoff, "_already_fired_today", lambda: False)
    monkeypatch.setattr(eod_squareoff, "_mark_fired_today", lambda: None)
    monkeypatch.setattr(
        execution_router,
        "route_signal",
        lambda signal: routed.append(signal) or {"success": True, "status": "TRADED", "order_id": "PAPER-EOD"},
    )

    eod_squareoff._try_flatten_once()

    assert len(routed) == 1
    assert routed[0].action == "EXIT"
    assert routed[0].qty == 65
    assert routed[0].raw_payload["exit_reason"] == "EOD_FLATTEN"
