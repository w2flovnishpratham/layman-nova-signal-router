from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import select

from app.auth.db import session_scope
from app.auth.models import OrderRouteAttempt, WebhookSignalReceipt
from app.config import settings
from app.schemas.signal import NormalizedSignal
from app.services import audit_logger, credential_vault, execution_router, state_store
from app.services.credential_vault import DhanCredentials
from app.services.dhan_client import DhanListResult, DhanOrderResult
from app.services.execution_router import _place_order
from app.services.signal_parser import PayloadParseError, parse_webhook_payload
from app.services.trading_security import claim_webhook_signal
from app.services.user_context import current_user_id, reset_current_user_id, set_current_user_id
from app.services.worker_policy import run_for_active_users, validate_worker_configuration
from app.tests.security_test_helpers import create_authenticated_user
from app.tests.test_signal_parser import TEST_WEBHOOK_SECRET, nova_payload


def _isolate_runtime(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "logs"
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
        "paper_orders": log_dir / "paper_orders.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    credential_vault._LOCAL_MEMORY_PAYLOAD.clear()
    credential_vault._LOCAL_MEMORY_PAYLOAD.update(
        {"version": 1, "dhan": None, "webhook_secret": None}
    )


def _signed_headers(raw_body: str, secret: str, timestamp: int, nonce: str | None = None) -> dict[str, str]:
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{raw_body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Nova-Timestamp": str(timestamp),
        "X-Nova-Signature": f"sha256={signature}",
    }
    if nonce:
        headers["X-Nova-Nonce"] = nonce
    return headers


@pytest.fixture()
def signed_webhook_client(tmp_path, monkeypatch):
    _isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", True)
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_LEGACY_AUTH_LOCAL", False)
    monkeypatch.setattr(settings, "WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS", 60)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False)
    monkeypatch.setattr("app.main.start_instrument_cache_warmup", lambda: None)

    routed: list[str] = []

    def fake_route(signal: NormalizedSignal) -> dict:
        routed.append(signal.signal_id)
        return {
            "blocked": False,
            "success": True,
            "status": "ORDER_PLACED",
            "order_id": f"PAPER-{signal.signal_id}",
            "correlation_id": f"corr-{signal.signal_id}",
        }

    monkeypatch.setattr("app.routers.webhook.route_signal", fake_route)

    from app.main import app

    with TestClient(app) as client:
        client.post("/api/setup/webhook-secret", json={"webhook_secret": TEST_WEBHOOK_SECRET})
        state_store.set_engine_mode("paper")
        state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
        yield client, routed


def _post_signed(
    client: TestClient,
    payload: dict,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
    secret: str = TEST_WEBHOOK_SECRET,
):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    ts = int(time.time()) if timestamp is None else timestamp
    return client.post(
        "/webhook/tradingview",
        content=raw,
        headers=_signed_headers(raw, secret, ts, nonce),
    )


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        ({}, 403),
        ({"X-Nova-Timestamp": "not-an-integer", "X-Nova-Signature": "sha256=x"}, 403),
    ],
)
def test_webhook_rejects_missing_or_invalid_timestamp(signed_webhook_client, headers, expected_status):
    client, routed = signed_webhook_client
    raw = json.dumps(nova_payload("ENTRY", "BUY", secrets.token_hex(8)), separators=(",", ":"))

    response = client.post(
        "/webhook/tradingview",
        content=raw,
        headers={"Content-Type": "application/json", **headers},
    )

    assert response.status_code == expected_status
    assert response.json()["message"] == "Webhook authentication failed."
    assert routed == []


@pytest.mark.parametrize("offset", [-61, 61])
def test_webhook_rejects_stale_or_future_timestamp(signed_webhook_client, offset):
    client, routed = signed_webhook_client
    response = _post_signed(
        client,
        nova_payload("ENTRY", "BUY", secrets.token_hex(8)),
        timestamp=int(time.time()) + offset,
    )

    assert response.status_code == 403
    assert response.json()["message"] == "Webhook authentication failed."
    assert routed == []


def test_webhook_signature_covers_timestamp_and_body(signed_webhook_client):
    client, routed = signed_webhook_client
    payload = nova_payload("ENTRY", "BUY", "signed-stage2-001")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    timestamp = int(time.time())
    headers = _signed_headers(raw, TEST_WEBHOOK_SECRET, timestamp)
    headers["X-Nova-Timestamp"] = str(timestamp + 1)

    rejected = client.post("/webhook/tradingview", content=raw, headers=headers)
    accepted = _post_signed(client, payload, timestamp=timestamp)

    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert routed == ["signed-stage2-001"]


def test_duplicate_signed_webhook_routes_once_and_is_db_backed(signed_webhook_client):
    client, routed = signed_webhook_client
    payload = nova_payload("ENTRY", "BUY", "db-dedup-stage2-001")

    first = _post_signed(client, payload)
    second = _post_signed(client, payload)

    assert first.json()["accepted"] is True
    assert second.json()["accepted"] is False
    assert "duplicate signal_id" in second.json()["message"]
    assert routed == ["db-dedup-stage2-001"]
    with session_scope() as session:
        rows = session.exec(
            select(WebhookSignalReceipt).where(
                WebhookSignalReceipt.signal_id == "db-dedup-stage2-001"
            )
        ).all()
    assert len(rows) == 1


def test_same_signal_id_with_changed_payload_is_suspicious(signed_webhook_client):
    client, routed = signed_webhook_client
    first_payload = nova_payload("ENTRY", "BUY", "suspicious-stage2-001")
    second_payload = dict(first_payload)
    second_payload["qty"] = 2

    first = _post_signed(client, first_payload)
    second = _post_signed(client, second_payload)

    assert first.status_code == 200
    assert second.status_code == 409
    assert "different order data" in second.json()["message"]
    assert routed == ["suspicious-stage2-001"]


def test_duplicate_nonce_is_rejected_before_second_signal_routes(signed_webhook_client):
    client, routed = signed_webhook_client
    nonce = secrets.token_urlsafe(18)

    first = _post_signed(client, nova_payload("ENTRY", "BUY", "nonce-a"), nonce=nonce)
    second = _post_signed(client, nova_payload("ENTRY", "BUY", "nonce-b"), nonce=nonce)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["status"] == "REPLAY_BLOCKED"
    assert routed == ["nonce-a"]


def test_live_webhook_requires_explicit_signal_id(signed_webhook_client, monkeypatch):
    client, routed = signed_webhook_client
    monkeypatch.setattr(settings, "REQUIRE_SIGNAL_ID_LIVE", True)
    state_store.update_app_state(engine_started=False, webhook_trading_enabled=False)
    state_store.set_engine_mode("live")
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    payload = nova_payload("ENTRY", "BUY", "temporary")
    payload.pop("signal_id")

    response = _post_signed(client, payload)

    assert response.status_code == 422
    assert response.json()["status"] == "SIGNAL_ID_REQUIRED"
    assert routed == []


def test_db_signal_claim_is_atomic_and_isolated_by_user():
    signal = _live_signal("atomic-stage2-001")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _item: claim_webhook_signal("user-a", signal), range(2)))

    assert sum(result.claimed for result in results) == 1
    assert claim_webhook_signal("user-b", signal).claimed is True


def test_worker_web_role_never_enables_trading_workers(monkeypatch):
    monkeypatch.setattr(settings, "WORKER_ROLE", "web")
    monkeypatch.setattr(settings, "ENABLE_TRADING_WORKERS", True)

    assert validate_worker_configuration() is False


def test_trading_worker_requires_explicit_enable_flag(monkeypatch):
    monkeypatch.setattr(settings, "WORKER_ROLE", "trading-worker")
    monkeypatch.setattr(settings, "ENABLE_TRADING_WORKERS", False)

    assert validate_worker_configuration() is False


def test_production_refuses_worker_without_distributed_lock(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "WORKER_ROLE", "trading-worker")
    monkeypatch.setattr(settings, "ENABLE_TRADING_WORKERS", True)
    monkeypatch.setattr(settings, "TRADING_WORKER_DISTRIBUTED_LOCK_ENABLED", False)

    with pytest.raises(RuntimeError, match="distributed leader lock"):
        validate_worker_configuration()


def test_worker_user_iteration_binds_explicit_user_context():
    user_a, _ = create_authenticated_user("worker-a")
    user_b, _ = create_authenticated_user("worker-b")
    observed: dict[str, str | None] = {}

    run_for_active_users(lambda user_id: observed.setdefault(user_id, current_user_id()))

    assert observed[user_a.id] == user_a.id
    assert observed[user_b.id] == user_b.id
    assert current_user_id() is None


def _live_signal(signal_id: str, **updates) -> NormalizedSignal:
    signal = NormalizedSignal(
        payload_format="NOVA",
        secret=TEST_WEBHOOK_SECRET,
        signal_id=signal_id,
        strategy_code="TRADINGVIEW_NIFTY_V1",
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment="NSE_FNO",
        security_id="111",
        trading_symbol="NIFTY 31 DEC 23000 CE",
        option_side="CE",
        strike=23000,
        expiry="2026-12-31",
        qty=1,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload={"signal_id": signal_id},
    )
    return signal.model_copy(update=updates)


class RecordingDhanClient:
    def __init__(self, calls: dict[str, int], before_order=None):
        self.calls = calls
        self.before_order = before_order

    def get_positions_snapshot(self, *, client_id, access_token):
        return DhanListResult(success=True, message="positions", items=[])

    def get_order_book(self, *, client_id, access_token):
        if self.before_order:
            self.before_order()
        return DhanListResult(success=True, message="orders", items=[])

    def place_order(self, *, client_id, access_token, payload):
        self.calls["placed"] = self.calls.get("placed", 0) + 1
        return DhanOrderResult(
            success=True,
            order_id=f"ORDER-{self.calls['placed']}",
            status="TRADED",
            avg_price=100.0,
            raw_response={"orderStatus": "TRADED", "avgPrice": 100.0},
        )


def _prepare_live_order(tmp_path, monkeypatch, *, before_order=None):
    _isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
    monkeypatch.setattr(settings, "UNIQUE_EGRESS_PER_USER_REQUIRED", False)
    monkeypatch.setattr(settings, "EXECUTION_NODE_ROUTING_ENABLED", False)
    monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False)
    monkeypatch.setattr(settings, "REQUIRE_MARKET_HOURS", False)
    monkeypatch.setattr(settings, "REQUIRE_FRESH_EXPIRY_LIVE", True)
    monkeypatch.setattr(
        "app.services.execution_router.get_dhan_credentials",
        lambda: DhanCredentials(client_id="1000000001", access_token="token"),
    )
    state_store.set_engine_mode("live")
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    state_store.update_runtime_settings(
        option_exit_mode="SERVER",
        max_qty_per_order=500,
        allow_entry=True,
        allow_exit=True,
        emergency_stop=False,
        global_kill_switch=False,
    )
    calls: dict[str, int] = {"placed": 0}
    monkeypatch.setattr(
        "app.services.execution_router.RealDhanClient",
        lambda: RecordingDhanClient(calls, before_order=before_order),
    )
    return calls


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        ("global_kill_switch", "global kill switch"),
        ("emergency_stop", "emergency stop"),
    ],
)
def test_final_broker_guard_rechecks_runtime_switches(tmp_path, monkeypatch, setting, expected):
    def flip_switch():
        state_store.update_runtime_settings(**{setting: True})

    calls = _prepare_live_order(tmp_path, monkeypatch, before_order=flip_switch)
    result = _place_order(_live_signal(f"final-{setting}"), 1, "ENTRY")

    assert result["blocked"] is True
    assert expected in result["reason"]
    assert calls["placed"] == 0


def test_final_broker_guard_rechecks_live_enabled_flag(tmp_path, monkeypatch):
    def disable_live():
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False)

    calls = _prepare_live_order(tmp_path, monkeypatch, before_order=disable_live)
    result = _place_order(_live_signal("final-live-disabled"), 1, "ENTRY")

    assert result["blocked"] is True
    assert "ENABLE_LIVE_ORDERS=false" in result["reason"]
    assert calls["placed"] == 0


def test_same_live_signal_never_calls_broker_twice(tmp_path, monkeypatch):
    calls = _prepare_live_order(tmp_path, monkeypatch)
    signal = _live_signal("route-idempotency-001")

    first = _place_order(signal, 1, "ENTRY")
    second = _place_order(signal, 1, "ENTRY")

    assert first["success"] is True
    assert second["blocked"] is True
    assert second["status"] == "LIVE_ORDER_DUPLICATE"
    assert calls["placed"] == 1
    with session_scope() as session:
        attempts = session.exec(
            select(OrderRouteAttempt).where(OrderRouteAttempt.signal_id == signal.signal_id)
        ).all()
    assert len(attempts) == 1
    assert attempts[0].status == "placed"


def test_same_live_signal_changed_order_payload_is_suspicious(tmp_path, monkeypatch):
    calls = _prepare_live_order(tmp_path, monkeypatch)
    signal = _live_signal("route-suspicious-001")

    first = _place_order(signal, 1, "ENTRY")
    second = _place_order(signal.model_copy(update={"qty": 2}), 2, "ENTRY")

    assert first["success"] is True
    assert second["blocked"] is True
    assert "different order data" in second["reason"]
    assert calls["placed"] == 1


def test_same_signal_id_isolated_across_users_for_route_attempts(tmp_path, monkeypatch):
    calls = _prepare_live_order(tmp_path, monkeypatch)
    user_a, _ = create_authenticated_user("route-user-a")
    user_b, _ = create_authenticated_user("route-user-b")
    signal = _live_signal("cross-user-route-001")

    token = set_current_user_id(user_a.id)
    try:
        state_store.set_engine_mode("live")
        state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
        state_store.update_runtime_settings(
            option_exit_mode="SERVER",
            max_qty_per_order=500,
            allow_entry=True,
            allow_exit=True,
            emergency_stop=False,
            global_kill_switch=False,
        )
        first = _place_order(signal, 1, "ENTRY")
    finally:
        reset_current_user_id(token)
    token = set_current_user_id(user_b.id)
    try:
        state_store.set_engine_mode("live")
        state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
        state_store.update_runtime_settings(
            option_exit_mode="SERVER",
            max_qty_per_order=500,
            allow_entry=True,
            allow_exit=True,
            emergency_stop=False,
            global_kill_switch=False,
        )
        second = _place_order(signal, 1, "ENTRY")
    finally:
        reset_current_user_id(token)

    assert first["success"] is True
    assert second["success"] is True
    assert first["correlation_id"] != second["correlation_id"]
    assert calls["placed"] == 2


def _write_scrip_master(path: Path, *, symbol: str = "NIFTY") -> None:
    path.write_text(
        "\n".join(
            (
                "SEM_SMST_SECURITY_ID,UNDERLYING_SYMBOL,SEM_INSTRUMENT_NAME,EXCH_ID,"
                "SEM_SEGMENT,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,"
                "SEM_TRADING_SYMBOL,SEM_LOT_UNITS",
                f"111,{symbol},OPTIDX,NSE,D,2026-12-31,23000,CE,{symbol} 31 DEC 23000 CE,65",
            )
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"option_side": "PE"},
        {"strike": 23100},
        {"expiry": "2026-12-24"},
    ],
)
def test_contract_mismatch_blocks_before_broker(tmp_path, monkeypatch, updates):
    calls = _prepare_live_order(tmp_path, monkeypatch)
    master = tmp_path / "scrip.csv"
    _write_scrip_master(master)
    monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", True)
    monkeypatch.setattr(settings, "DHAN_SCRIP_MASTER_PATH", str(master))
    monkeypatch.setattr("app.services.security_id_resolver.RUNTIME_STATE_DIR", tmp_path / "empty")

    result = _place_order(_live_signal(secrets.token_hex(8), **updates), 65, "ENTRY")

    assert result["blocked"] is True
    assert "does not match" in result["reason"]
    assert calls["placed"] == 0


def test_security_id_for_different_symbol_blocks_before_broker(tmp_path, monkeypatch):
    calls = _prepare_live_order(tmp_path, monkeypatch)
    master = tmp_path / "scrip.csv"
    _write_scrip_master(master, symbol="BANKNIFTY")
    monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", True)
    monkeypatch.setattr(settings, "DHAN_SCRIP_MASTER_PATH", str(master))
    monkeypatch.setattr("app.services.security_id_resolver.RUNTIME_STATE_DIR", tmp_path / "empty")

    result = _place_order(_live_signal("wrong-symbol-security-id"), 65, "ENTRY")

    assert result["blocked"] is True
    assert "does not match" in result["reason"]
    assert calls["placed"] == 0


def test_lot_size_mismatch_blocks_before_broker(tmp_path, monkeypatch):
    calls = _prepare_live_order(tmp_path, monkeypatch)
    master = tmp_path / "scrip.csv"
    _write_scrip_master(master)
    monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", True)
    monkeypatch.setattr(settings, "DHAN_SCRIP_MASTER_PATH", str(master))
    monkeypatch.setattr("app.services.security_id_resolver.RUNTIME_STATE_DIR", tmp_path / "empty")

    result = _place_order(_live_signal("wrong-lot-size"), 1, "ENTRY")

    assert result["blocked"] is True
    assert "not a multiple" in result["reason"]
    assert calls["placed"] == 0


def test_unverified_security_id_blocks_live_route(tmp_path, monkeypatch):
    calls = _prepare_live_order(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "REQUIRE_INSTRUMENT_MASTER_VALIDATION_LIVE", True)

    result = _place_order(_live_signal("unverified-security-id"), 1, "ENTRY")

    assert result["blocked"] is True
    assert "not verified" in result["reason"]
    assert calls["placed"] == 0


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"security_id": None}, "securityId"),
        ({"exchange_segment": "BSE_FNO"}, "unsupported exchange"),
        ({"product_type": "CNC"}, "unsupported product"),
        ({"symbol": "BANKNIFTY"}, "only NIFTY"),
        ({"expiry": "2026-05-01"}, "stale option expiry"),
    ],
)
def test_invalid_live_instrument_fields_never_call_broker(tmp_path, monkeypatch, updates, reason):
    calls = _prepare_live_order(tmp_path, monkeypatch)
    result = _place_order(_live_signal(secrets.token_hex(8), **updates), 1, "ENTRY")

    assert result["blocked"] is True
    assert reason in result["reason"]
    assert calls["placed"] == 0


def test_market_closed_rechecked_before_broker(tmp_path, monkeypatch):
    calls = _prepare_live_order(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "REQUIRE_MARKET_HOURS", True)
    monkeypatch.setattr("app.services.execution_router._market_is_open", lambda: False)

    result = _place_order(_live_signal("market-closed-stage2"), 1, "ENTRY")

    assert result["blocked"] is True
    assert "market-hours" in result["reason"]
    assert calls["placed"] == 0


def test_blocked_live_instrument_writes_audit_event(tmp_path, monkeypatch):
    calls = _prepare_live_order(tmp_path, monkeypatch)
    events: list[str] = []
    monkeypatch.setattr(
        execution_router,
        "log_audit_event",
        lambda event_type, *_args, **_kwargs: events.append(event_type),
    )

    result = _place_order(
        _live_signal("audit-invalid-instrument", exchange_segment="BSE_FNO"),
        1,
        "ENTRY",
    )

    assert result["blocked"] is True
    assert calls["placed"] == 0
    assert "LIVE_INSTRUMENT_BLOCKED" in events


def test_zero_or_negative_quantity_and_malformed_payload_block_before_execution():
    with pytest.raises(ValidationError):
        NormalizedSignal.model_validate(
            {**_live_signal("zero-qty").model_dump(), "qty": 0}
        )

    payload = nova_payload("ENTRY", "BUY", "bad-qty")
    payload["qty"] = -1
    with pytest.raises(PayloadParseError):
        parse_webhook_payload(payload)

    with pytest.raises(PayloadParseError):
        parse_webhook_payload({"secret": TEST_WEBHOOK_SECRET, "strategy_code": "TRADINGVIEW_NIFTY_V1"})
