"""Live-mode safety guards + per-user webhook HMAC."""
from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _ctx(model):
    from app.services.user_context import current_user_from_model

    return current_user_from_model(model)


class _TokenValidation:
    def __init__(self, *, success: bool, status_code: int | None = None) -> None:
        self.success = success
        self.status_code = status_code


def _allow_real_order_readiness(monkeypatch):
    from app.config import settings
    from app.services import strategy_fanout
    from app.workers import strategy_job_worker

    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True, raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False, raising=False)
    monkeypatch.setattr(settings, "EXECUTION_NODE_ROUTING_ENABLED", True, raising=False)
    monkeypatch.setattr(
        strategy_fanout,
        "user_egress_status",
        lambda user_id: {
            "public_ip": "13.203.58.220",
            "active": True,
            "has_proxy": True,
            "verified": True,
        },
    )
    monkeypatch.setattr(
        strategy_job_worker,
        "strategy_job_worker_status",
        lambda: {"enabled": True, "running": True},
    )


def _patch_token_validation(monkeypatch, *, success: bool, calls: dict[str, int] | None = None, status_code: int | None = 200):
    from app.services import live_engine

    class FakeDhanClient:
        def validate_token(self, *, client_id, access_token):
            if calls is not None:
                calls["validate_token"] = calls.get("validate_token", 0) + 1
                calls["client_id"] = client_id
                calls["access_token"] = access_token
            return _TokenValidation(success=success, status_code=status_code)

    monkeypatch.setattr(live_engine, "RealDhanClient", FakeDhanClient)


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
    from app.services import live_engine, user_credential_vault as vault

    _allow_real_order_readiness(monkeypatch)
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=True, calls=calls)
    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-aaaaaaaaaaaaaaaaa")

    readiness = live_engine.evaluate_live_readiness(_ctx(user), "real_orders")
    assert readiness["ready"] is True
    assert readiness["real_orders_allowed"] is True
    assert readiness["checks"]["dhan_token_profile_valid"] is True
    assert readiness["checks"]["dhan_token_validation_method"] == "dhan_profile"
    assert calls["validate_token"] == 1


def test_real_order_start_rejects_missing_token(mu_db, monkeypatch):
    from app.services import live_engine

    _allow_real_order_readiness(monkeypatch)
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=True, calls=calls)
    user = make_user("missing-token@gmail.com")

    result = live_engine.start_run(_ctx(user), strategy_name="s", execution_mode="real_orders", config={})

    assert result["ok"] is False
    assert result["run"] is None
    assert any("No saved Dhan credentials" in b for b in result["readiness"]["blockers"])
    assert calls == {}


def test_real_order_start_rejects_short_placeholder_token(mu_db, monkeypatch):
    from app.services import live_engine, user_credential_vault as vault

    _allow_real_order_readiness(monkeypatch)
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=True, calls=calls)
    user = make_user("short-token@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="short")

    result = live_engine.start_run(_ctx(user), strategy_name="s", execution_mode="real_orders", config={})

    assert result["ok"] is False
    assert any("basic validation" in b for b in result["readiness"]["blockers"])
    assert calls == {}


def test_real_order_start_rejects_failed_dhan_validation_safely(mu_db, monkeypatch):
    from app.services import live_engine, user_credential_vault as vault

    _allow_real_order_readiness(monkeypatch)
    secret_token = "eyJ0eXA-invalid-or-expired-token"
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=False, calls=calls, status_code=401)
    user = make_user("invalid-token@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token=secret_token)

    result = live_engine.start_run(_ctx(user), strategy_name="s", execution_mode="real_orders", config={})

    assert result["ok"] is False
    readiness = result["readiness"]
    assert readiness["execution_mode"] == "real_orders"
    assert readiness["checks"]["dhan_token_profile_valid"] is False
    assert readiness["checks"]["dhan_token_validation_status_code"] == 401
    assert any("Dhan token validation failed" in b for b in readiness["blockers"])
    serialized = str(result)
    assert secret_token not in serialized
    assert "access_token" not in serialized
    assert calls["validate_token"] == 1


def test_real_order_start_accepts_only_after_safe_dhan_validation(mu_db, monkeypatch):
    from app.services import live_engine, user_credential_vault as vault

    _allow_real_order_readiness(monkeypatch)
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=True, calls=calls)
    user = make_user("validated-token@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-valid-token-abcdef")

    result = live_engine.start_run(_ctx(user), strategy_name="s", execution_mode="real_orders", config={})

    assert result["ok"] is True
    assert result["run"]["execution_mode"] == "real_orders"
    assert result["readiness"]["checks"]["dhan_token_profile_valid"] is True
    assert calls["validate_token"] == 1


def test_non_real_order_mode_does_not_call_dhan_validation(mu_db, monkeypatch):
    from app.services import live_engine, user_credential_vault as vault

    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=False, calls=calls)
    user = make_user("paper-no-validation@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-valid-token-abcdef")

    result = live_engine.start_run(_ctx(user), strategy_name="s", execution_mode="signal_only", config={})

    assert result["ok"] is True
    assert result["run"]["execution_mode"] == "signal_only"
    assert result["readiness"]["checks"]["dhan_token_profile_valid"] is None
    assert calls == {}


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
