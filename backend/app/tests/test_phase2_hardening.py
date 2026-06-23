from __future__ import annotations

import time

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


def test_strategy_webhook_event_claim_blocks_duplicate_and_body_mismatch(mu_db, monkeypatch):
    from app.config import DEFAULT_STRATEGY_CODE, settings
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
        "strategy_code": DEFAULT_STRATEGY_CODE,
        "action": "ENTRY",
        "side": "BUY",
        "symbol": "NIFTY",
        "order_type": "MARKET",
        "product_type": "INTRADAY",
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
