from __future__ import annotations

import pytest

from app.config import settings
from app.services import audit_logger, state_store
from app.services.credential_vault import DhanCredentials
from app.services.dhan_client import DhanListResult
from app.workers import ghost_position_watcher


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "EXTERNAL_POSITIONS_FILE", state_dir / "external_positions.json")
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
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(
        ghost_position_watcher,
        "get_dhan_credentials",
        lambda: DhanCredentials("1000000001", "token"),
    )
    state_store.init_runtime_files()
    yield


def test_external_dhan_position_is_persisted(isolated_state, monkeypatch):
    class OpenDhan:
        def get_positions_snapshot(self, *, client_id, access_token):
            return DhanListResult(
                success=True,
                message="positions",
                items=[
                    {
                        "tradingSymbol": "NIFTY 02 JUN 22500 PE",
                        "securityId": "PE456",
                        "netQty": 1,
                        "positionType": "LONG",
                    }
                ],
            )

        def get_order_book(self, *, client_id, access_token):
            return DhanListResult(success=True, message="orders", items=[])

    monkeypatch.setattr(ghost_position_watcher, "RealDhanClient", OpenDhan)

    snapshot = ghost_position_watcher.sync_ghost_positions_once(reason="test")

    assert snapshot["status"] == "external_detected"
    assert snapshot["external_count"] == 1
    assert snapshot["positions"][0]["security_id"] == "PE456"
    assert state_store.get_external_positions()["positions"][0]["trading_symbol"] == "NIFTY 02 JUN 22500 PE"


def test_matching_local_position_is_not_marked_external(isolated_state, monkeypatch):
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TEST",
            "security_id": "CE123",
            "trading_symbol": "NIFTY 02 JUN 22500 CE",
            "qty": 1,
            "entry_order_id": "entry-1",
            "entry_price": 100.0,
            "opened_at": state_store.utc_now(),
        }
    )

    class MatchingDhan:
        def get_positions_snapshot(self, *, client_id, access_token):
            return DhanListResult(
                success=True,
                message="positions",
                items=[{"tradingSymbol": "NIFTY 02 JUN 22500 CE", "securityId": "CE123", "netQty": 1}],
            )

        def get_order_book(self, *, client_id, access_token):
            return DhanListResult(success=True, message="orders", items=[])

    monkeypatch.setattr(ghost_position_watcher, "RealDhanClient", MatchingDhan)

    snapshot = ghost_position_watcher.sync_ghost_positions_once(reason="test")

    assert snapshot["status"] == "ok"
    assert snapshot["external_count"] == 0
    assert snapshot["local_position_matched"] is True
    assert state_store.get_open_position()["has_open_position"] is True


def test_manual_broker_exit_clears_local_tracker_even_with_external_position(isolated_state, monkeypatch):
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TEST",
            "security_id": "CE123",
            "trading_symbol": "NIFTY 02 JUN 22500 CE",
            "qty": 1,
            "entry_order_id": "entry-1",
            "entry_price": 100.0,
            "opened_at": state_store.utc_now(),
        }
    )
    state_store.update_app_state(state="WAITING_EXIT")

    class ExternalOnlyDhan:
        def get_positions_snapshot(self, *, client_id, access_token):
            return DhanListResult(
                success=True,
                message="positions",
                items=[{"tradingSymbol": "NIFTY 02 JUN 22500 PE", "securityId": "PE456", "netQty": 1}],
            )

        def get_order_book(self, *, client_id, access_token):
            return DhanListResult(success=True, message="orders", items=[])

    monkeypatch.setattr(ghost_position_watcher, "RealDhanClient", ExternalOnlyDhan)

    snapshot = ghost_position_watcher.sync_ghost_positions_once(reason="test")

    assert snapshot["manual_exit_detected"] is True
    assert snapshot["external_count"] == 1
    assert snapshot["positions"][0]["security_id"] == "PE456"
    assert state_store.get_open_position()["has_open_position"] is False
    assert state_store.get_open_position()["broker_sync"]["status"] == "broker_missing_local"
    assert state_store.get_app_state()["state"] == "WAITING_ENTRY"


@pytest.mark.parametrize(
    ("stop_loss_price", "target_price", "expected_status", "expected_drift"),
    [
        (70.0, 160.0, "in_sync", False),
        (75.0, 160.0, "drift_detected", True),
    ],
)
def test_dhan_super_order_sl_tp_drift_detection(
    isolated_state,
    monkeypatch,
    stop_loss_price,
    target_price,
    expected_status,
    expected_drift,
):
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TEST",
            "security_id": "CE123",
            "trading_symbol": "NIFTY 02 JUN 22500 CE",
            "qty": 1,
            "entry_order_id": "SUPER1",
            "entry_price": 100.0,
            "exit_management": "DHAN_SUPER",
            "broker_sl_price": 70.0,
            "broker_tp_price": 160.0,
            "opened_at": state_store.utc_now(),
        }
    )

    class SuperOrderDhan:
        def get_positions_snapshot(self, *, client_id, access_token):
            return DhanListResult(
                success=True,
                message="positions",
                items=[{"tradingSymbol": "NIFTY 02 JUN 22500 CE", "securityId": "CE123", "netQty": 1}],
            )

        def get_order_book(self, *, client_id, access_token):
            return DhanListResult(
                success=True,
                message="orders",
                items=[
                    {
                        "parentOrderId": "SUPER1",
                        "orderId": "SUPER1-SL",
                        "orderStatus": "PENDING",
                        "legName": "STOP_LOSS_LEG",
                        "securityId": "CE123",
                        "triggerPrice": stop_loss_price,
                    },
                    {
                        "parentOrderId": "SUPER1",
                        "orderId": "SUPER1-TARGET",
                        "orderStatus": "PENDING",
                        "legName": "TARGET_LEG",
                        "securityId": "CE123",
                        "price": target_price,
                    },
                ],
            )

    monkeypatch.setattr(ghost_position_watcher, "RealDhanClient", SuperOrderDhan)

    snapshot = ghost_position_watcher.sync_ghost_positions_once(reason="test")

    assert snapshot["external_count"] == 0
    assert snapshot["sl_tp_drift"]["status"] == expected_status
    assert snapshot["sl_tp_drift"]["drift_detected"] is expected_drift
    assert snapshot["sl_tp_drift"]["actual"]["stop_loss_price"] == stop_loss_price
