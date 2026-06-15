"""Live-mode safety guards + per-user webhook HMAC."""
from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _ctx(model):
    from app.services.user_context import current_user_from_model

    return current_user_from_model(model)


def test_real_orders_blocked_without_enable_live_orders(mu_db, monkeypatch):
    from app.config import settings
    from app.services import live_engine, user_credential_vault as vault

    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False, raising=False)
    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-aaaaaaaaaaaaaaaaa")

    readiness = live_engine.evaluate_live_readiness(_ctx(user), "real_orders")
    assert readiness["ready"] is False
    assert readiness["real_orders_allowed"] is False
    assert any("ENABLE_LIVE_ORDERS" in b for b in readiness["blockers"])

    result = live_engine.start_run(_ctx(user), strategy_name="s", execution_mode="real_orders", config={})
    assert result["ok"] is False
    assert result["run"] is None


def test_real_orders_allowed_only_when_all_flags_set(mu_db, monkeypatch):
    from app.config import settings
    from app.services import live_engine, user_credential_vault as vault

    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True, raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False, raising=False)
    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-aaaaaaaaaaaaaaaaa")

    readiness = live_engine.evaluate_live_readiness(_ctx(user), "real_orders")
    assert readiness["ready"] is True
    assert readiness["real_orders_allowed"] is True


def test_live_start_blocked_without_credentials(mu_db):
    from app.services import live_engine

    user = make_user("nocreds@gmail.com")
    readiness = live_engine.evaluate_live_readiness(_ctx(user), "signal_only")
    assert readiness["ready"] is False
    assert any("No saved Dhan credentials" in b for b in readiness["blockers"])


def test_signal_only_allowed_with_credentials(mu_db):
    from app.services import live_engine, user_credential_vault as vault

    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-aaaaaaaaaaaaaaaaa")
    result = live_engine.start_run(_ctx(user), strategy_name="s", execution_mode="signal_only", config={})
    assert result["ok"] is True
    assert result["run"]["status"] == "running"


# ---------------------------------------------------------------------------
# Webhook HMAC + replay protection
# ---------------------------------------------------------------------------
def _webhook_client():
    from app.routers import user_webhook

    app = FastAPI()
    app.include_router(user_webhook.router)
    return TestClient(app)


def _signed_body(secret, uid, *, nonce, ts=None, signal="BUY"):
    from app.routers.user_webhook import build_signature

    ts = ts if ts is not None else int(time.time())
    sig = build_signature(secret, user_id=str(uid), strategy="st", symbol="NIFTY", signal=signal, timestamp=ts, nonce=nonce)
    return {
        "user_id": str(uid),
        "strategy": "st",
        "symbol": "NIFTY",
        "signal": signal,
        "timestamp": ts,
        "nonce": nonce,
        "signature": sig,
    }


def test_webhook_valid_signature_accepted_and_replay_rejected(mu_db, monkeypatch):
    from app.config import settings
    from app.services import user_credential_vault as vault

    monkeypatch.setattr(settings, "WEBHOOK_TRADING_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", True, raising=False)
    secret = "Zx9$kLmnPq72! vWeRt8sUaB"
    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, webhook_secret=secret)

    client = _webhook_client()
    body = _signed_body(secret, user.id, nonce="n1")
    assert client.post(f"/api/webhook/user/{user.id}", json=body).status_code == 202
    # Same nonce again -> replay rejected.
    assert client.post(f"/api/webhook/user/{user.id}", json=body).status_code == 409


def test_webhook_rejects_invalid_and_forged_signatures(mu_db, monkeypatch):
    from app.config import settings
    from app.services import user_credential_vault as vault

    monkeypatch.setattr(settings, "WEBHOOK_TRADING_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", True, raising=False)
    secret = "Zx9$kLmnPq72! vWeRt8sUaB"
    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, webhook_secret=secret)
    client = _webhook_client()

    bad = _signed_body(secret, user.id, nonce="n2")
    bad["signature"] = "deadbeef"
    assert client.post(f"/api/webhook/user/{user.id}", json=bad).status_code == 401

    forged = _signed_body("attacker-secret-attacker-secret!", user.id, nonce="n3")
    assert client.post(f"/api/webhook/user/{user.id}", json=forged).status_code == 401

    stale = _signed_body(secret, user.id, nonce="n4", ts=int(time.time()) - 99999)
    assert client.post(f"/api/webhook/user/{user.id}", json=stale).status_code == 400


def test_webhook_rejected_when_hmac_required_but_no_secret(mu_db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "WEBHOOK_TRADING_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", True, raising=False)
    user = make_user("nosecret@gmail.com")  # no webhook secret saved
    client = _webhook_client()
    body = _signed_body("whatever-secret-whatever-secret1", user.id, nonce="n5")
    resp = client.post(f"/api/webhook/user/{user.id}", json=body)
    assert resp.status_code == 401
