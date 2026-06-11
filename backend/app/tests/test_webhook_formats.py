import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services import audit_logger, credential_vault, state_store
from app.services import risk_manager
from app.tests.test_signal_parser import TEST_WEBHOOK_SECRET, nova_payload, pine_payload


@pytest.fixture()
def client(tmp_path, monkeypatch):
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
    credential_vault._LOCAL_MEMORY_PAYLOAD.clear()
    credential_vault._LOCAL_MEMORY_PAYLOAD.update({"version": 1, "dhan": None, "webhook_secret": None})
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
    monkeypatch.setattr(settings, "WEBHOOK_TRADING_ENABLED", True)
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", False)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(settings, "REQUIRE_MARKET_HOURS", False)

    from app.main import app

    with TestClient(app) as test_client:
        test_client.post("/api/control/reset-state")
        test_client.post("/api/control/clear-seen-signals")
        test_client.post("/api/setup/webhook-secret", json={"webhook_secret": TEST_WEBHOOK_SECRET})
        test_client.post(
            "/api/setup/risk",
            json={
                "max_qty_per_order": 1,
                "allow_entry": True,
                "allow_exit": True,
            },
        )
        state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
        yield test_client


def test_wrong_secret_rejected_after_parse(client):
    payload = pine_payload("B")
    payload["secret"] = "wrong"

    response = client.post("/webhook/tradingview", json=payload)

    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "UNAUTHORIZED"
    assert body["payload_format"] == "PINE_MULTI_LEG"


def test_unsupported_payload_format_rejected(client):
    response = client.post("/webhook/tradingview", json={"secret": TEST_WEBHOOK_SECRET, "foo": "bar"})

    assert response.status_code == 400
    assert response.json()["status"] == "UNSUPPORTED_PAYLOAD_FORMAT"


def test_backend_max_qty_blocks_oversized_pine_alert(client):
    response = client.post("/webhook/tradingview", json=pine_payload("B", quantity="2"))

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is False
    assert body["message"] == "Quantity exceeds MAX_QTY_PER_ORDER."
    assert body["payload_format"] == "PINE_MULTI_LEG"


def test_duplicate_signal_logic_works_for_nova_signal_id(client):
    payload = nova_payload("ENTRY", "BUY", "duplicate-nova-001")

    first = client.post("/webhook/tradingview", json=payload)
    second = client.post("/webhook/tradingview", json=payload)

    assert first.status_code == 200
    assert first.json()["accepted"] is True
    assert second.status_code == 200
    assert second.json()["accepted"] is False
    assert second.json()["status"] == "BLOCKED"
    assert "duplicate signal_id duplicate-nova-001" in second.json()["message"]


def test_duplicate_signal_logic_works_for_generated_pine_signal_id(client):
    payload = pine_payload("B")

    first = client.post("/webhook/tradingview", json=payload)
    second = client.post("/webhook/tradingview", json=payload)

    assert first.status_code == 200
    assert first.json()["accepted"] is True
    assert second.status_code == 200
    assert second.json()["accepted"] is False
    assert second.json()["payload_format"] == "PINE_MULTI_LEG"
    assert "duplicate signal_id" in second.json()["message"]


def test_same_strategy_signals_are_serialized(client, monkeypatch):
    from app.routers import webhook as webhook_router

    with webhook_router._PROCESSING_LOCK:
        webhook_router._PROCESSING_SIGNAL_IDS.clear()
    with webhook_router._STRATEGY_LOCKS_GUARD:
        webhook_router._STRATEGY_LOCKS.clear()

    active_count = 0
    max_active_count = 0
    route_lock = threading.Lock()
    routed_signal_ids: list[str] = []

    def fake_route_signal(signal):
        nonlocal active_count, max_active_count
        with route_lock:
            active_count += 1
            max_active_count = max(max_active_count, active_count)
            routed_signal_ids.append(signal.signal_id)
        time.sleep(0.1)
        with route_lock:
            active_count -= 1
        return {
            "blocked": False,
            "success": True,
            "status": "ORDER_PLACED",
            "order_id": f"MOCK-{signal.signal_id}",
        }

    monkeypatch.setattr(webhook_router, "route_signal", fake_route_signal)

    first_payload = nova_payload("ENTRY", "BUY", "same-strategy-001")
    second_payload = nova_payload("ENTRY", "BUY", "same-strategy-002")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(client.post, "/webhook/tradingview", json=first_payload)
        second_future = executor.submit(client.post, "/webhook/tradingview", json=second_payload)
        first = first_future.result()
        second = second_future.result()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["accepted"] is True
    assert second.json()["accepted"] is True
    assert sorted(routed_signal_ids) == ["same-strategy-001", "same-strategy-002"]
    assert max_active_count == 1


def test_market_hours_check_falls_back_when_zoneinfo_is_missing(monkeypatch):
    def missing_zoneinfo(_name):
        raise risk_manager.ZoneInfoNotFoundError("No time zone found")

    monkeypatch.setattr(risk_manager, "ZoneInfo", missing_zoneinfo)

    assert isinstance(risk_manager._market_is_open(), bool)
