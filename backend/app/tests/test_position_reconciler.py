from __future__ import annotations

import pytest

from app.config import settings
from app.services import audit_logger, position_reconciler, state_store
from app.services.credential_vault import DhanCredentials
from app.services.dhan_client import DhanListResult


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
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
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(position_reconciler, "get_dhan_credentials", lambda: DhanCredentials("1000000001", "token"))
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TEST",
            "security_id": "72176",
            "trading_symbol": "NIFTY 26 MAY 23900 PUT",
            "qty": 65,
            "entry_order_id": "entry-1",
            "entry_price": 10.0,
            "opened_at": state_store.utc_now(),
        }
    )
    yield


def test_manual_dhan_close_clears_local_open_position(isolated_state, monkeypatch):
    class FlatDhan:
        def get_positions_snapshot(self, *, client_id, access_token):
            return DhanListResult(success=True, message="positions", items=[])

        def get_order_book(self, *, client_id, access_token):
            return DhanListResult(success=True, message="orders", items=[])

    monkeypatch.setattr(position_reconciler, "RealDhanClient", FlatDhan)

    result = position_reconciler.get_reconciled_open_position(force=True, reason="test")

    assert result["has_open_position"] is False
    assert result["broker_sync"]["status"] == "broker_flat"
    assert result["broker_sync"]["cleared"] is True
    assert state_store.get_open_position()["has_open_position"] is False


def test_dhan_active_position_keeps_local_tracker(isolated_state, monkeypatch):
    class OpenDhan:
        def get_positions_snapshot(self, *, client_id, access_token):
            return DhanListResult(
                success=True,
                message="positions",
                items=[{"tradingSymbol": "NIFTY 26 MAY 23900 PUT", "netQty": 65}],
            )

        def get_order_book(self, *, client_id, access_token):
            return DhanListResult(success=True, message="orders", items=[])

    monkeypatch.setattr(position_reconciler, "RealDhanClient", OpenDhan)

    result = position_reconciler.get_reconciled_open_position(force=True, reason="test")

    assert result["has_open_position"] is True
    assert result["broker_sync"]["status"] == "broker_open"
    assert result["broker_sync"]["active_positions_count"] == 1
    assert state_store.get_open_position()["has_open_position"] is True
