from __future__ import annotations

from app.config import settings
from app.services import audit_logger, state_store, startup_reconciler
from app.services.credential_vault import DhanCredentials
from app.services.dhan_client import DhanListResult


def setup_runtime(tmp_path, monkeypatch):
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    log_files = {
        "webhook": log_dir / "webhook_events.jsonl",
        "order": log_dir / "order_events.jsonl",
        "audit": log_dir / "audit_events.jsonl",
        "error": log_dir / "errors.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    monkeypatch.setattr(settings, "APP_ENV", "local")
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
    state_store.init_runtime_files()
    monkeypatch.setattr(
        startup_reconciler,
        "get_dhan_credentials",
        lambda: DhanCredentials(client_id="1000000001", access_token="token"),
    )


def set_local_open_position():
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TRADINGVIEW_NIFTY_V1",
            "symbol": "NIFTY",
            "instrument_type": "OPTIDX",
            "exchange_segment": "NSE_FNO",
            "security_id": "CE123",
            "trading_symbol": "NIFTY 2026-06-04 22500 CE",
            "option_side": "CE",
            "strike": 22500.0,
            "expiry": "2026-06-04",
            "qty": 65,
            "entry_order_id": "SUPER-CE",
            "entry_price": 100.0,
            "exit_management": "DHAN_SUPER",
            "broker_sl_price": 90.0,
            "broker_tp_price": 120.0,
            "opened_at": state_store.utc_now(),
        }
    )


def test_startup_reconciler_verifies_and_rearms_super_order(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    set_local_open_position()

    class FakeDhanClient:
        def get_positions_snapshot(self, *, client_id, access_token):
            return DhanListResult(
                success=True,
                message="positions",
                items=[{"securityId": "CE123", "tradingSymbol": "NIFTY 2026-06-04 22500 CE", "netQty": 65}],
            )

        def get_order_book(self, *, client_id, access_token):
            return DhanListResult(
                success=True,
                message="orders",
                items=[
                    {
                        "orderId": "SUPER-CE",
                        "orderStatus": "PENDING",
                        "securityId": "CE123",
                        "legName": "TARGET_LEG",
                        "remainingQuantity": 65,
                    }
                ],
            )

    monkeypatch.setattr(startup_reconciler, "RealDhanClient", FakeDhanClient)

    result = startup_reconciler.reconcile_open_position_on_startup()

    assert result["status"] == "verified"
    assert result["super_order_status"] == "verified"
    assert result["super_order_rearmed"] is True
    position = state_store.get_open_position()
    assert position["has_open_position"] is True
    assert position["broker_restart_sync"]["status"] == "verified"
    assert position["live_pnl"]["status"] == "broker_verified_after_restart"
    assert state_store.get_app_state()["state"] == "WAITING_EXIT"


def test_startup_reconciler_clears_stale_local_position_when_dhan_flat(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    set_local_open_position()

    class FlatDhanClient:
        def get_positions_snapshot(self, *, client_id, access_token):
            return DhanListResult(success=True, message="positions", items=[])

        def get_order_book(self, *, client_id, access_token):
            return DhanListResult(success=True, message="orders", items=[])

    monkeypatch.setattr(startup_reconciler, "RealDhanClient", FlatDhanClient)

    result = startup_reconciler.reconcile_open_position_on_startup()

    assert result["status"] == "broker_missing_local"
    position = state_store.get_open_position()
    assert position["has_open_position"] is False
    assert position["broker_restart_sync"]["cleared"] is True
    assert state_store.get_app_state()["state"] == "WAITING_ENTRY"


def test_startup_reconciler_keeps_local_position_when_dhan_check_fails(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    set_local_open_position()

    class FailingDhanClient:
        def get_positions_snapshot(self, *, client_id, access_token):
            return DhanListResult(success=False, message="positions unavailable", items=[])

        def get_order_book(self, *, client_id, access_token):
            return DhanListResult(success=True, message="orders", items=[])

    monkeypatch.setattr(startup_reconciler, "RealDhanClient", FailingDhanClient)

    result = startup_reconciler.reconcile_open_position_on_startup()

    assert result["status"] == "failed"
    assert result["reason"] == "dhan_verification_failed"
    position = state_store.get_open_position()
    assert position["has_open_position"] is True
    assert position["broker_restart_sync"]["failures"] == ["positions unavailable"]
    assert state_store.get_app_state()["state"] == "RESTART_RECONCILE_FAILED"
