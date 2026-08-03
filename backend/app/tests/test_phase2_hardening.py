# ruff: noqa: F811
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _signal(signal_id: str = "phase2-signal", *, ltp: str | None = None):
    from app.config import DEFAULT_STRATEGY_CODE
    from app.schemas.signal import NormalizedSignal

    payload = {
        "secret": "shared-secret",
        "signal_id": signal_id,
        "strategy_code": DEFAULT_STRATEGY_CODE,
        "action": "ENTRY",
        "side": "BUY",
        "symbol": "NIFTY",
        "qty": 1,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
    }
    if ltp is not None:
        payload["ltp"] = ltp
    return NormalizedSignal(
        payload_format="NOVA",
        secret=payload["secret"],
        signal_id=signal_id,
        strategy_code=DEFAULT_STRATEGY_CODE,
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        qty=1,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload=payload,
    )


def _user_webhook_client():
    from app.routers import user_webhook

    app = FastAPI()
    app.include_router(user_webhook.router)
    return TestClient(app)


def _strategy_webhook_client():
    from app.routers.strategies import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/api/docs", "/api/redoc", "/openapi.json"])
def test_production_disables_public_api_documentation(monkeypatch, path):
    from app.config import settings
    from app.main import _api_documentation_urls

    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)

    app = FastAPI(**_api_documentation_urls())
    response = TestClient(app).get(path)

    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/api/docs", "/api/redoc", "/openapi.json"])
def test_non_production_keeps_api_documentation_available(monkeypatch, path):
    from app.config import settings
    from app.main import _api_documentation_urls

    monkeypatch.setattr(settings, "APP_ENV", "local", raising=False)

    app = FastAPI(**_api_documentation_urls())
    response = TestClient(app).get(path)

    assert response.status_code == 200


def test_chat_websocket_accepts_session_token_subprotocol_without_query_token(monkeypatch):
    from app.api import ws as chat_ws
    from app.auth.dependencies import get_current_websocket_user
    from app.config import settings
    from app.domain.state_machine import SetupState
    from app.services.user_context import CurrentUser
    from app.store.redis_session import session_store
    from app.store.session_token import issue_session_token

    monkeypatch.setattr(settings, "AUTH_REQUIRED", True, raising=False)
    user = CurrentUser(id=uuid.uuid4(), email="ws-token@example.com")
    session = asyncio.run(
        session_store.create(user_id=user.id_str, state=SetupState.IDLE)
    )
    token = issue_session_token(session.id)

    app = FastAPI()
    app.include_router(chat_ws.router)
    app.dependency_overrides[get_current_websocket_user] = lambda: user

    with TestClient(app).websocket_connect(
        f"/ws/session/{session.id}",
        subprotocols=["nova-session", f"nova-session-token.{token}"],
    ):
        pass


def test_chat_confirm_live_cannot_synthesize_consent_or_start_over_websocket(
    monkeypatch,
):
    from app.api import ws as chat_ws
    from app.services.user_context import CurrentUser

    user = CurrentUser(id=uuid.uuid4(), email="ws-live-entitlement@example.com")
    session = SimpleNamespace(config={"strategy": "supertrend", "risk": {"lots": 1}})
    monkeypatch.setattr(chat_ws, "get_engine_mode", lambda legacy_fallback=True: "live")

    with pytest.raises(
        ValueError,
        match="durable /api/runtime/start-selected",
    ):
        asyncio.run(
            chat_ws._apply_production_command(
                "setup.confirm_live",
                {},
                user=user,
                session=session,
            )
        )


def _signed_user_body(secret, uid, *, nonce, ts=None, signal="BUY"):
    from app.routers.user_webhook import build_signature

    ts = ts if ts is not None else int(time.time())
    signature = build_signature(
        secret,
        user_id=str(uid),
        strategy="st",
        symbol="NIFTY",
        signal=signal,
        timestamp=ts,
        nonce=nonce,
    )
    return {
        "user_id": str(uid),
        "strategy": "st",
        "symbol": "NIFTY",
        "signal": signal,
        "timestamp": ts,
        "nonce": nonce,
        "signature": signature,
    }


def _shared_strategy_body(
    secret: str,
    *,
    signal_id: str = "phase2-prod-shared-signal",
    timestamp: int | None = None,
    nonce: str | None = "phase2-prod-nonce",
):
    body = {
        "secret": secret,
        "signal_id": signal_id,
        "action": "BUY_CE",
        "signal_time": "2026-07-31T09:00:00Z",
    }
    if timestamp is not None:
        body["timestamp"] = timestamp
    if nonce is not None:
        body["nonce"] = nonce
    return body


def test_user_webhook_nonce_replay_is_durable_after_cache_clear(mu_db, monkeypatch):
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.routers import user_webhook
    from app.services import user_credential_vault as vault

    monkeypatch.setattr(settings, "WEBHOOK_TRADING_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", True, raising=False)
    secret = "phase2-user-webhook-secret-123"
    user = make_user("phase2-nonce@gmail.com")
    vault.save_user_credentials(user.id, webhook_secret=secret)
    client = _user_webhook_client()
    body = _signed_user_body(secret, user.id, nonce="nonce-after-restart")

    assert client.post(f"/api/webhook/user/{user.id}", json=body).status_code == 202
    user_webhook._SEEN_NONCES.clear()

    replay = client.post(f"/api/webhook/user/{user.id}", json=body)
    assert replay.status_code == 409
    with session_scope() as db:
        assert db.query(models.WebhookNonce).count() == 1
        assert db.query(models.WebhookEvent).count() == 1


@pytest.mark.usefixtures("ready_default_strategy")
def test_strategy_webhook_event_claim_blocks_duplicate_and_body_mismatch(mu_db, monkeypatch):
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.routers.strategies import router
    from app.services import strategy_fanout

    secret = "phase2-strategy-secret-1234567890"
    monkeypatch.setattr(settings, "STRATEGY_WEBHOOK_SECRET", secret, raising=False)
    user = make_user("phase2-strategy-event@gmail.com")
    strategy_fanout.subscribe_user(user.id, "supertrend", lots=1, execution_mode="signal_only")

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    body = {
        "secret": secret,
        "signal_id": "phase2-shared-signal",
        "action": "BUY_CE",
        "signal_time": "2026-07-31T09:00:00Z",
    }

    assert client.post("/api/webhook/strategy/supertrend", json=body).status_code == 202
    assert client.post("/api/webhook/strategy/supertrend", json=body).status_code == 409
    tampered = {**body, "alert_note": "tampered-body"}
    mismatch = client.post("/api/webhook/strategy/supertrend", json=tampered)
    assert mismatch.status_code == 409
    assert "different body" in mismatch.json()["error"]

    with session_scope() as db:
        assert db.query(models.WebhookEvent).count() == 1
        assert db.query(models.StrategySignal).count() == 1
        assert db.query(models.StrategyExecutionJob).count() == 1


@pytest.mark.parametrize(
    ("missing_field", "expected_error"),
    [
        ("timestamp", "Webhook timestamp is required."),
        ("nonce", "Webhook nonce is required."),
    ],
)
def test_production_strategy_webhook_requires_timestamp_and_nonce(
    mu_db,
    monkeypatch,
    missing_field,
    expected_error,
):
    from app.config import settings

    secret = "phase2-prod-strategy-secret-1234567890"
    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(settings, "STRATEGY_WEBHOOK_SECRET", secret, raising=False)
    body = _shared_strategy_body(
        secret,
        signal_id=f"phase2-missing-{missing_field}",
        timestamp=int(time.time()),
        nonce=f"nonce-missing-{missing_field}",
    )
    body.pop(missing_field)

    response = _strategy_webhook_client().post("/api/webhook/strategy/supertrend", json=body)

    assert response.status_code == 401
    assert response.json()["error"] == expected_error
    assert secret not in str(response.json())


@pytest.mark.parametrize("offset_seconds", [-301, 301])
def test_production_strategy_webhook_rejects_stale_or_future_timestamp(
    mu_db,
    monkeypatch,
    offset_seconds,
):
    from app.config import settings

    secret = "phase2-prod-strategy-secret-1234567890"
    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(settings, "STRATEGY_WEBHOOK_SECRET", secret, raising=False)
    body = _shared_strategy_body(
        secret,
        signal_id=f"phase2-window-{offset_seconds}",
        timestamp=int(time.time()) + offset_seconds,
        nonce=f"nonce-window-{offset_seconds}",
    )

    response = _strategy_webhook_client().post("/api/webhook/strategy/supertrend", json=body)

    assert response.status_code == 401
    assert response.json()["error"] == "Webhook timestamp is outside the allowed window."
    assert secret not in str(response.json())


def test_production_strategy_webhook_requires_durable_replay_store(monkeypatch):
    from app.config import settings

    secret = "phase2-prod-strategy-secret-1234567890"
    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(settings, "STRATEGY_WEBHOOK_SECRET", secret, raising=False)
    body = _shared_strategy_body(
        secret,
        signal_id="phase2-no-replay-store",
        timestamp=int(time.time()),
        nonce="nonce-no-replay-store",
    )

    response = _strategy_webhook_client().post("/api/webhook/strategy/supertrend", json=body)

    assert response.status_code == 503
    assert response.json()["error"] == "Webhook replay store unavailable."
    assert secret not in str(response.json())


@pytest.mark.usefixtures("ready_default_strategy")
def test_production_strategy_webhook_rejects_replayed_nonce(mu_db, monkeypatch):
    from app.config import settings
    from app.services import strategy_fanout

    secret = "phase2-prod-strategy-secret-1234567890"
    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(settings, "STRATEGY_WEBHOOK_SECRET", secret, raising=False)
    user = make_user("phase2-prod-nonce@gmail.com")
    strategy_fanout.subscribe_user(user.id, "supertrend", lots=1, execution_mode="signal_only")
    client = _strategy_webhook_client()
    body = _shared_strategy_body(
        secret,
        signal_id="phase2-prod-nonce-signal",
        timestamp=int(time.time()),
        nonce="nonce-prod-replay",
    )

    assert client.post("/api/webhook/strategy/supertrend", json=body).status_code == 202
    replay = client.post("/api/webhook/strategy/supertrend", json=body)

    assert replay.status_code == 409
    assert replay.json()["error"] == "Duplicate webhook signal."
    assert secret not in str(replay.json())


@pytest.mark.usefixtures("ready_default_strategy")
def test_production_strategy_webhook_rejects_duplicate_signal_identity(mu_db, monkeypatch):
    from app.config import settings
    from app.services import strategy_fanout

    secret = "phase2-prod-strategy-secret-1234567890"
    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(settings, "STRATEGY_WEBHOOK_SECRET", secret, raising=False)
    user = make_user("phase2-prod-signal-identity@gmail.com")
    strategy_fanout.subscribe_user(user.id, "supertrend", lots=1, execution_mode="signal_only")
    client = _strategy_webhook_client()
    signal_id = "phase2-prod-duplicate-identity"
    first_body = _shared_strategy_body(
        secret,
        signal_id=signal_id,
        timestamp=int(time.time()),
        nonce="nonce-prod-identity-1",
    )
    second_body = _shared_strategy_body(
        secret,
        signal_id=signal_id,
        timestamp=int(time.time()),
        nonce="nonce-prod-identity-2",
    )

    assert client.post("/api/webhook/strategy/supertrend", json=first_body).status_code == 202
    duplicate = client.post("/api/webhook/strategy/supertrend", json=second_body)

    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "Duplicate webhook signal."
    assert secret not in str(duplicate.json())


def test_strategy_risk_kill_switch_blocks_only_its_scope(mu_db, monkeypatch):
    from app.services import strategy_fanout, strategy_risk

    blocked_user = make_user("phase2-risk-blocked@gmail.com")
    allowed_user = make_user("phase2-risk-allowed@gmail.com")
    strategy_risk.set_user_risk_control(blocked_user.id, kill_switch=True)

    calls = []
    monkeypatch.setattr(strategy_fanout, "route_signal", lambda signal: calls.append(signal.signal_id) or {"success": True})
    monkeypatch.setattr(strategy_fanout, "init_runtime_files", lambda: None)
    monkeypatch.setattr(strategy_fanout, "_quantity_for_subscription", lambda lots: lots * 65)

    blocked = strategy_fanout.dispatch_signal_job(
        user_id=blocked_user.id,
        strategy_name="supertrend",
        lots=1,
        execution_mode="paper_live_data",
        signal=_signal("phase2-user-kill"),
    )
    allowed = strategy_fanout.dispatch_signal_job(
        user_id=allowed_user.id,
        strategy_name="supertrend",
        lots=1,
        execution_mode="paper_live_data",
        signal=_signal("phase2-user-allowed"),
    )

    assert blocked["status"] == "blocked"
    assert blocked["risk"]["code"] == "per_user_kill_switch"
    assert allowed["status"] == "completed"
    assert calls == ["phase2-user-allowed"]


def test_strategy_risk_lot_and_notional_caps_fail_closed_before_routing(mu_db, monkeypatch):
    from app.services import strategy_fanout, strategy_risk

    user = make_user("phase2-cap-user@gmail.com")
    strategy_risk.set_user_strategy_risk_control(
        user.id,
        "supertrend",
        max_lots_per_order=1,
        max_notional_per_trade_paise=10_000,
    )
    monkeypatch.setattr(strategy_fanout, "route_signal", lambda signal: (_ for _ in ()).throw(AssertionError("risk blocks must not route")))
    monkeypatch.setattr(strategy_fanout, "init_runtime_files", lambda: None)
    monkeypatch.setattr(strategy_fanout, "_quantity_for_subscription", lambda lots: lots * 65)

    lot_block = strategy_fanout.dispatch_signal_job(
        user_id=user.id,
        strategy_name="supertrend",
        lots=2,
        execution_mode="paper_live_data",
        signal=_signal("phase2-lots"),
    )
    notional_block = strategy_fanout.dispatch_signal_job(
        user_id=user.id,
        strategy_name="supertrend",
        lots=1,
        execution_mode="paper_live_data",
        signal=_signal("phase2-notional", ltp="2.00"),
    )

    assert lot_block["status"] == "blocked"
    assert lot_block["risk"]["code"] == "max_lots_per_order"
    assert notional_block["status"] == "blocked"
    assert notional_block["risk"]["code"] == "max_notional_per_trade"


def test_strategy_daily_order_and_loss_caps_are_durable(mu_db, monkeypatch):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import strategy_fanout, strategy_risk

    user = make_user("phase2-daily-cap@gmail.com")
    strategy_risk.set_user_strategy_risk_control(
        user.id,
        "supertrend",
        max_orders_per_day=1,
        max_loss_per_day_paise=10_000,
    )
    monkeypatch.setattr(strategy_fanout, "route_signal", lambda signal: {"success": True})
    monkeypatch.setattr(strategy_fanout, "init_runtime_files", lambda: None)
    monkeypatch.setattr(strategy_fanout, "_quantity_for_subscription", lambda lots: lots * 65)

    first = strategy_fanout.dispatch_signal_job(
        user_id=user.id,
        strategy_name="supertrend",
        lots=1,
        execution_mode="paper_live_data",
        signal=_signal("phase2-first"),
    )
    second = strategy_fanout.dispatch_signal_job(
        user_id=user.id,
        strategy_name="supertrend",
        lots=1,
        execution_mode="paper_live_data",
        signal=_signal("phase2-second"),
    )

    assert first["status"] == "completed"
    assert second["status"] == "blocked"
    assert second["risk"]["code"] == "max_orders_per_day"

    with session_scope() as db:
        counter = db.query(models.UserStrategyDailyRiskCounter).one()
        counter.orders_count = 0
        counter.realized_pnl_paise = -10_000

    loss_block = strategy_fanout.dispatch_signal_job(
        user_id=user.id,
        strategy_name="supertrend",
        lots=1,
        execution_mode="paper_live_data",
        signal=_signal("phase2-loss"),
    )
    assert loss_block["status"] == "blocked"
    assert loss_block["risk"]["code"] == "max_loss_per_day"


def test_recovered_strategy_retry_does_not_double_reserve_daily_counter(mu_db, monkeypatch):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import strategy_fanout, strategy_risk
    from app.workers.strategy_job_worker import (
        process_queued_jobs_once,
        recover_stale_jobs,
    )

    user = make_user("phase2-reservation-retry@gmail.com")
    signal = _signal("phase2-reservation-retry")
    today = strategy_risk.trade_date_ist()
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    monkeypatch.setattr(
        strategy_fanout,
        "route_signal",
        lambda signal: (_ for _ in ()).throw(AssertionError("recovered retry must not route")),
    )

    with session_scope() as db:
        signal_row = models.StrategySignal(
            strategy_name="supertrend",
            signal_id=signal.signal_id,
            status="processing",
        )
        db.add(signal_row)
        db.flush()
        db.add(
            models.StrategyExecutionJob(
                strategy_signal_id=signal_row.id,
                user_id=user.id,
                strategy_name="supertrend",
                signal_id=signal.signal_id,
                signal_payload=signal.model_dump(mode="json"),
                lots=1,
                execution_mode="paper_live_data",
                status="running",
                attempts=1,
                max_attempts=2,
                locked_at=stale_time,
                available_at=stale_time,
            )
        )
        db.add(
            models.UserStrategyDailyRiskCounter(
                user_id=user.id,
                strategy_name="supertrend",
                trade_date_ist=today,
                orders_count=1,
                notional_used_paise=0,
                realized_pnl_paise=0,
            )
        )

    assert recover_stale_jobs() == 1
    assert process_queued_jobs_once(limit=1) == 1

    with session_scope() as db:
        counter = db.query(models.UserStrategyDailyRiskCounter).one()
        job = db.query(models.StrategyExecutionJob).one()
        assert counter.orders_count == 1
        assert job.status == "failed"
        assert "Manual review required" in (job.last_error or "")


def _assert_no_raw_dhan_surface(payload):
    forbidden_text = [
        "1000000001",
        "9999999999",
        "raw-token-secret",
        "refresh-token-secret",
        "proxy-secret",
        "abc%40123",
        "http://nova_user_1:proxy-secret@13.203.58.220:3001",
    ]
    text = str(payload)
    for forbidden in forbidden_text:
        assert forbidden not in text

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                assert key not in {"raw_response", "response_text", "response_json", "proxy_url"}
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)


def test_setup_dhan_connect_redacts_identity_mismatch(monkeypatch):
    from app.config import settings
    from app.routers import setup as setup_router
    from app.services.dhan_client import DhanValidationResult

    class FakeDhanClient:
        def validate_token(self, *, client_id, access_token):
            return DhanValidationResult(
                success=False,
                message="Dhan token validation failed: client identity mismatch.",
                status_code=200,
                raw_response={"client_identity_mismatch": True, "dhanClientId": "9999999999"},
            )

    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False, raising=False)
    monkeypatch.setattr(setup_router, "vault_status", lambda: {"ready": True, "local_mock_allowed": False})
    monkeypatch.setattr(setup_router, "RealDhanClient", FakeDhanClient)
    setup_router._DHAN_CONNECT_RATE_LIMIT.clear()

    app = FastAPI()
    app.include_router(setup_router.router)
    response = TestClient(app).post(
        "/setup/dhan/connect",
        json={"client_id": "1000000001", "access_token": "raw-token-secret"},
        headers={"x-real-ip": "203.0.113.10"},
    )

    assert response.status_code == 400
    body = response.json()
    assert "client identity mismatch" in str(body)
    _assert_no_raw_dhan_surface(body)


def test_setup_dhan_connect_returns_user_safe_auth_failure(monkeypatch):
    from app.config import settings
    from app.routers import setup as setup_router
    from app.services.dhan_client import DhanValidationResult

    class FakeDhanClient:
        def validate_token(self, *, client_id, access_token):
            return DhanValidationResult(
                success=False,
                message="Dhan token validation request failed: Client ID or user generated access token is invalid or expired.",
                status_code=401,
                raw_response={
                    "errorCode": "DH-901",
                    "errorMessage": "Client ID or user generated access token is invalid or expired.",
                    "access_token": "raw-token-secret",
                },
            )

    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False, raising=False)
    monkeypatch.setattr(setup_router, "vault_status", lambda: {"ready": True, "local_mock_allowed": False})
    monkeypatch.setattr(setup_router, "RealDhanClient", FakeDhanClient)
    setup_router._DHAN_CONNECT_RATE_LIMIT.clear()

    app = FastAPI()
    app.include_router(setup_router.router)
    response = TestClient(app).post(
        "/setup/dhan/connect",
        json={"client_id": "1000000001", "access_token": "raw-token-secret"},
        headers={"x-real-ip": "203.0.113.11"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == {
        "message": (
            "Dhan authentication failed. The access token is invalid or expired. "
            "Generate a fresh Dhan access token for this Client ID and try again."
        ),
        "error_code": "TOKEN_EXPIRED",
    }
    assert "raw-token-secret" not in str(body)
    assert "interpreted_error" not in str(body)
    assert "debugPack" not in str(body)
    assert "rawStatusCode" not in str(body)
    _assert_no_raw_dhan_surface(body)


def test_wallet_refresh_sanitizes_raw_dhan_response(monkeypatch):
    from app.services import wallet_service
    from app.services.credential_vault import DhanCredentials
    from app.services.dhan_client import DhanFundsResult
    from app.services.state_store import default_wallet_snapshot, utc_now

    class FakeDhanClient:
        def get_fund_limit(self, *, client_id, access_token):
            return DhanFundsResult(
                success=True,
                message="Dhan fund limit fetched.",
                status_code=200,
                client_id="1000000001",
                available_balance=12345.0,
                raw_response={
                    "dhanClientId": "1000000001",
                    "access_token": "raw-token-secret",
                    "proxy_url": "http://nova_user_1:proxy-secret@13.203.58.220:3001",
                },
            )

    monkeypatch.setattr(wallet_service, "get_engine_mode", lambda legacy_fallback=False: "live")
    monkeypatch.setattr(wallet_service, "get_wallet_snapshot", default_wallet_snapshot)
    monkeypatch.setattr(
        wallet_service,
        "set_wallet_snapshot",
        lambda snapshot: {**snapshot, "last_checked_at": utc_now(), "raw_response": {"dhanClientId": "1000000001"}},
    )
    monkeypatch.setattr(wallet_service, "get_dhan_credentials", lambda: DhanCredentials("1000000001", "raw-token-secret"))
    monkeypatch.setattr(wallet_service, "RealDhanClient", FakeDhanClient)

    snapshot = wallet_service.refresh_wallet_snapshot(force=True)

    assert snapshot["client_id"] == "******0001"
    assert snapshot["available_balance"] == 12345.0
    _assert_no_raw_dhan_surface(snapshot)


def test_broker_routes_sanitize_wallet_and_profile_payload(monkeypatch):
    from app.routers import broker
    from app.services.credential_vault import DhanCredentials
    from app.services.dhan_client import DhanValidationResult

    class FakeDhanClient:
        def __init__(self, *, proxy_url=None, expected_egress_ip=None):
            pass

        def validate_token(self, *, client_id, access_token):
            return DhanValidationResult(
                success=True,
                message="Dhan token valid.",
                status_code=200,
                raw_response={"dhanClientId": "1000000001"},
            )

    raw_wallet = {
        "success": True,
        "message": "wallet ok",
        "client_id": "1000000001",
        "available_balance": 100.0,
        "raw_response": {"dhanClientId": "1000000001", "access_token": "raw-token-secret"},
        "proxy_url": "http://nova_user_1:proxy-secret@13.203.58.220:3001",
    }
    monkeypatch.setattr(broker, "get_dhan_credentials", lambda: DhanCredentials("1000000001", "raw-token-secret"))
    monkeypatch.setattr(broker, "RealDhanClient", FakeDhanClient)
    monkeypatch.setattr(broker, "refresh_wallet_snapshot", lambda **_kwargs: raw_wallet)
    monkeypatch.setattr(broker, "fetch_dhan_wallet_snapshot", lambda **_kwargs: raw_wallet)

    app = FastAPI()
    app.include_router(broker.router)
    client = TestClient(app)
    test_body = client.post("/dhan/test").json()
    funds_body = client.get("/dhan/funds").json()

    assert test_body["wallet"]["client_id"] == "******0001"
    assert funds_body["client_id"] == "******0001"
    _assert_no_raw_dhan_surface(test_body)
    _assert_no_raw_dhan_surface(funds_body)


def test_dashboard_sanitizes_wallet_state_and_log_surfaces(monkeypatch):
    from app.routers import dashboard

    raw_wallet = {
        "success": True,
        "client_id": "1000000001",
        "raw_response": {"dhanClientId": "1000000001", "access_token": "raw-token-secret"},
        "proxy_url": "http://nova_user_1:proxy-secret@13.203.58.220:3001",
    }
    raw_log = {
        "event": "DHAN_ORDER_RESPONSE",
        "raw_response": {"dhanClientId": "9999999999", "refresh_token": "refresh-token-secret"},
        "response_text": "client ID 9999999999 access-token=raw-token-secret",
        "proxy_url": "http://nova_user_1:proxy-secret@13.203.58.220:3001",
    }
    monkeypatch.setattr(dashboard, "get_reconciled_open_position", lambda reason: None)
    monkeypatch.setattr(dashboard, "get_app_state", lambda: {"wallet": raw_wallet, "nested": raw_log})
    monkeypatch.setattr(dashboard, "get_runtime_settings", dict)
    monkeypatch.setattr(dashboard, "tradingview_webhook_url", lambda: "https://example.test/webhook")
    monkeypatch.setattr(dashboard, "get_dhan_credentials", lambda: None)
    monkeypatch.setattr(dashboard, "dhan_metadata", lambda: {"connected": True, "client_id_masked": "******0001"})
    monkeypatch.setattr(dashboard, "webhook_secret_metadata", lambda: {"set": True})
    monkeypatch.setattr(dashboard, "get_external_positions", list)
    monkeypatch.setattr(dashboard, "shared_market_data_status", dict)
    monkeypatch.setattr(dashboard, "read_jsonl", lambda _name, limit=1: [raw_log])

    summary = dashboard.dashboard_summary()
    logs = dashboard.logs()

    assert summary["wallet"]["client_id"] == "******0001"
    _assert_no_raw_dhan_surface(summary)
    _assert_no_raw_dhan_surface(logs)


def test_debug_ping_safe_never_returns_raw_dhan_payload(monkeypatch):
    import httpx

    from app.config import settings
    from app.routers import debug
    from app.services.credential_vault import DhanCredentials

    class FakeHTTPClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, endpoint, headers):
            return httpx.Response(
                403,
                json={
                    "dhanClientId": "9999999999",
                    "access_token": "raw-token-secret",
                    "proxy_url": "http://nova_user_1:proxy-secret@13.203.58.220:3001",
                },
            )

    monkeypatch.setattr(settings, "DEBUG_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(debug, "get_dhan_credentials", lambda: DhanCredentials("1000000001", "raw-token-secret"))
    monkeypatch.setattr(debug, "get_outgoing_ip", lambda timeout=2.0: {"outgoing_ip": "13.203.58.220", "ok": True})
    monkeypatch.setattr(debug.httpx, "Client", FakeHTTPClient)

    app = FastAPI()
    app.include_router(debug.router)
    response = TestClient(app).post("/dhan/ping-safe")

    assert response.status_code == 200
    body = response.json()
    assert "response_text" not in body
    assert "response_json" not in body
    assert "safe_error" in body
    _assert_no_raw_dhan_surface(body)
