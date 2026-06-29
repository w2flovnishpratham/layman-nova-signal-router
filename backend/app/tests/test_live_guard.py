"""Live-mode safety guards + per-user webhook HMAC."""
from __future__ import annotations

import time
from datetime import timedelta

import httpx
import pytest
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
    _arm_real_order_flags(monkeypatch)
    _patch_verified_egress(monkeypatch)


def _arm_real_order_flags(monkeypatch):
    from app.config import settings
    from app.workers import strategy_job_worker

    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True, raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False, raising=False)
    monkeypatch.setattr(settings, "EXECUTION_NODE_ROUTING_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(
        strategy_job_worker,
        "strategy_job_worker_status",
        lambda: {"enabled": True, "running": True},
    )


def _patch_verified_egress(monkeypatch, *, verified: bool = True):
    from app.services import strategy_fanout

    proxy_url = "http://nova_user_1:secret@13.203.58.220:3001"
    monkeypatch.setattr(
        strategy_fanout,
        "user_egress_status",
        lambda user_id: {
            "public_ip": "13.203.58.220",
            "active": True,
            "has_proxy": True,
            "verified": verified,
            "last_observed_ip": "13.203.58.220" if verified else "198.51.100.10",
        },
    )
    monkeypatch.setattr(
        strategy_fanout,
        "get_user_egress",
        lambda user_id: {
            "public_ip": "13.203.58.220",
            "expected_egress_ip": "13.203.58.220",
            "proxy_url": proxy_url,
            "active": True,
            "verified": verified,
            "last_observed_ip": "13.203.58.220" if verified else "198.51.100.10",
            "last_verified_at": "2026-06-29T00:00:00+00:00" if verified else None,
        },
    )


def _patch_token_validation(monkeypatch, *, success: bool, calls: dict[str, int] | None = None, status_code: int | None = 200):
    from app.services import live_engine

    class FakeDhanClient:
        def __init__(self, *args, **kwargs) -> None:
            if calls is not None:
                calls["init_args"] = args
                calls["init_kwargs"] = kwargs

        def validate_token(self, *, client_id, access_token):
            if calls is not None:
                calls["validate_token"] = calls.get("validate_token", 0) + 1
                calls["client_id"] = client_id
                calls["access_token"] = access_token
            return _TokenValidation(success=success, status_code=status_code)

    monkeypatch.setattr(live_engine, "RealDhanClient", FakeDhanClient)


def _live_client(user):
    from app.auth.dependencies import get_current_user
    from app.routers import live

    app = FastAPI()
    app.include_router(live.router)
    app.dependency_overrides[get_current_user] = lambda: _ctx(user)
    return TestClient(app)


def _grant_live_entitlement(user, *, status: str = "active") -> None:
    from app.db import models
    from app.db.engine import session_scope

    now = models.utcnow()
    with session_scope() as db:
        db.add(
            models.UserEntitlement(
                user_id=user.id,
                plan_code="nova_live",
                status=status,
                source="payment_provider",
                starts_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=30),
                live_orders_enabled=True,
                static_ip_enabled=False,
                strategy_access_enabled=False,
                metadata_json={"test": "live_guard"},
                created_at=now,
                updated_at=now,
            )
        )


def test_real_orders_blocked_without_enable_live_orders(mu_db, monkeypatch):
    from app.config import settings
    from app.services import live_engine, user_credential_vault as vault

    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False, raising=False)
    user = make_user("alice@gmail.com")
    _grant_live_entitlement(user)
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
    _grant_live_entitlement(user)
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-aaaaaaaaaaaaaaaaa")

    readiness = live_engine.evaluate_live_readiness(_ctx(user), "real_orders")
    assert readiness["ready"] is True
    assert readiness["real_orders_allowed"] is True
    assert readiness["checks"]["live_entitlement_valid"] is True
    assert readiness["checks"]["dhan_token_profile_valid"] is True
    assert readiness["checks"]["dhan_token_validation_method"] == "dhan_profile"
    assert calls["validate_token"] == 1
    assert calls["init_kwargs"]["proxy_url"] == "http://nova_user_1:secret@13.203.58.220:3001"
    assert calls["init_kwargs"]["expected_egress_ip"] == "13.203.58.220"


def test_real_orders_readiness_uses_proxy_not_direct_transport(mu_db, monkeypatch):
    from app.services import live_engine, user_credential_vault as vault

    _allow_real_order_readiness(monkeypatch)
    recorder: dict[str, object] = {}

    class FakeHTTPClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers):
            recorder["url"] = url
            recorder["headers"] = headers
            return httpx.Response(200, json={"dhanClientId": "1100123456"})

    def fake_client(*args, **kwargs):
        recorder["client_args"] = args
        recorder["client_kwargs"] = kwargs
        return FakeHTTPClient()

    monkeypatch.setattr("app.services.dhan_client.httpx.Client", fake_client)
    user = make_user("proxy-bound-readiness@gmail.com")
    _grant_live_entitlement(user)
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-aaaaaaaaaaaaaaaaa")

    readiness = live_engine.evaluate_live_readiness(_ctx(user), "real_orders")

    assert readiness["ready"] is True
    client_kwargs = recorder["client_kwargs"]
    assert client_kwargs["proxy"] == "http://nova_user_1:secret@13.203.58.220:3001"
    assert "transport" not in client_kwargs


def test_real_orders_readiness_fails_without_verified_egress(mu_db, monkeypatch):
    from app.services import live_engine, user_credential_vault as vault

    _arm_real_order_flags(monkeypatch)
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=True, calls=calls)
    user = make_user("no-egress@gmail.com")
    _grant_live_entitlement(user)
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-aaaaaaaaaaaaaaaaa")

    readiness = live_engine.evaluate_live_readiness(_ctx(user), "real_orders")

    assert readiness["ready"] is False
    assert readiness["real_orders_allowed"] is False
    assert any("No Dhan static-IP execution node" in b for b in readiness["blockers"])
    assert calls == {}


def test_real_orders_readiness_fails_when_egress_is_unverified(mu_db, monkeypatch):
    from app.services import live_engine, user_credential_vault as vault

    _arm_real_order_flags(monkeypatch)
    _patch_verified_egress(monkeypatch, verified=False)
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=True, calls=calls)
    user = make_user("unverified-egress@gmail.com")
    _grant_live_entitlement(user)
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-aaaaaaaaaaaaaaaaa")

    readiness = live_engine.evaluate_live_readiness(_ctx(user), "real_orders")

    assert readiness["ready"] is False
    assert any("not verified" in b for b in readiness["blockers"])
    assert calls == {}


def test_live_readiness_endpoint_fails_without_verified_egress(mu_db, monkeypatch):
    from app.services import user_credential_vault as vault

    _arm_real_order_flags(monkeypatch)
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=True, calls=calls)
    user = make_user("readiness-no-egress@gmail.com")
    _grant_live_entitlement(user)
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-aaaaaaaaaaaaaaaaa")

    response = _live_client(user).get("/api/live/readiness", params={"execution_mode": "real_orders"})

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["real_orders_allowed"] is False
    assert any("No Dhan static-IP execution node" in b for b in body["blockers"])
    assert calls == {}


def test_live_start_endpoint_fails_without_verified_egress(mu_db, monkeypatch):
    from app.services import user_credential_vault as vault

    _arm_real_order_flags(monkeypatch)
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=True, calls=calls)
    user = make_user("start-no-egress@gmail.com")
    _grant_live_entitlement(user)
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-aaaaaaaaaaaaaaaaa")

    response = _live_client(user).post(
        "/api/live/start",
        json={"strategy_name": "s", "execution_mode": "real_orders"},
    )

    assert response.status_code == 412
    body = response.json()
    readiness = body["detail"]["readiness"]
    assert readiness["execution_mode"] == "real_orders"
    assert readiness["real_orders_allowed"] is False
    assert any("No Dhan static-IP execution node" in b for b in readiness["blockers"])
    assert calls == {}


def test_real_order_start_rejects_missing_token(mu_db, monkeypatch):
    from app.services import live_engine

    _allow_real_order_readiness(monkeypatch)
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=True, calls=calls)
    user = make_user("missing-token@gmail.com")
    _grant_live_entitlement(user)

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
    _grant_live_entitlement(user)
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
    _grant_live_entitlement(user)
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
    assert "nova_user_1" not in serialized
    assert "secret@13.203.58.220" not in serialized
    assert calls["validate_token"] == 1
    assert calls["init_kwargs"]["proxy_url"] == "http://nova_user_1:secret@13.203.58.220:3001"


def test_real_order_start_accepts_only_after_safe_dhan_validation(mu_db, monkeypatch):
    from app.services import live_engine, user_credential_vault as vault

    _allow_real_order_readiness(monkeypatch)
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=True, calls=calls)
    user = make_user("validated-token@gmail.com")
    _grant_live_entitlement(user)
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
    assert result["readiness"]["checks"]["requires_dhan_credentials"] is False
    assert result["readiness"]["checks"]["requires_dhan_profile_validation"] is False
    assert result["readiness"]["checks"]["requires_verified_egress"] is False
    assert calls == {}


def test_signal_only_start_succeeds_without_dhan_credentials(mu_db, monkeypatch):
    from app.services import live_engine

    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=False, calls=calls)
    user = make_user("signal-only-nocreds@gmail.com")
    readiness = live_engine.evaluate_live_readiness(_ctx(user), "signal_only")
    result = live_engine.start_run(_ctx(user), strategy_name="s", execution_mode="signal_only", config={})

    assert readiness["ready"] is True
    assert readiness["real_orders_allowed"] is False
    assert readiness["checks"]["has_saved_credentials"] is False
    assert readiness["checks"]["places_real_orders"] is False
    assert readiness["checks"]["offline_compatible_mode"] is True
    assert readiness["checks"]["requires_dhan_credentials"] is False
    assert readiness["checks"]["requires_dhan_profile_validation"] is False
    assert readiness["checks"]["requires_verified_egress"] is False
    assert readiness["checks"]["dhan_token_profile_valid"] is None
    assert not any("No saved Dhan credentials" in b for b in readiness["blockers"])
    assert result["ok"] is True
    assert result["run"]["execution_mode"] == "signal_only"
    assert result["readiness"]["real_orders_allowed"] is False
    assert calls == {}


def test_paper_live_data_start_succeeds_without_dhan_credentials(mu_db, monkeypatch):
    from app.services import live_engine

    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=False, calls=calls)
    user = make_user("paper-live-data-nocreds@gmail.com")

    readiness = live_engine.evaluate_live_readiness(_ctx(user), "paper_live_data")
    result = live_engine.start_run(_ctx(user), strategy_name="s", execution_mode="paper_live_data", config={})

    assert readiness["ready"] is True
    assert readiness["real_orders_allowed"] is False
    assert readiness["checks"]["has_saved_credentials"] is False
    assert readiness["checks"]["places_real_orders"] is False
    assert readiness["checks"]["offline_compatible_mode"] is True
    assert readiness["checks"]["requires_dhan_credentials"] is False
    assert readiness["checks"]["requires_dhan_profile_validation"] is False
    assert readiness["checks"]["requires_verified_egress"] is False
    assert readiness["checks"]["dhan_token_profile_valid"] is None
    assert not any("No saved Dhan credentials" in b for b in readiness["blockers"])
    assert result["ok"] is True
    assert result["run"]["execution_mode"] == "paper_live_data"
    assert result["readiness"]["real_orders_allowed"] is False
    assert calls == {}


def test_real_order_start_without_credentials_does_not_downgrade(mu_db, monkeypatch):
    from app.services import live_engine

    _allow_real_order_readiness(monkeypatch)
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=True, calls=calls)
    user = make_user("real-nocreds-no-downgrade@gmail.com")
    _grant_live_entitlement(user)

    result = live_engine.start_run(_ctx(user), strategy_name="s", execution_mode="real_orders", config={})

    assert result["ok"] is False
    assert result["run"] is None
    assert result["readiness"]["execution_mode"] == "real_orders"
    assert result["readiness"]["real_orders_allowed"] is False
    assert result["readiness"]["checks"]["places_real_orders"] is True
    assert result["readiness"]["checks"]["requires_dhan_credentials"] is True
    assert any("No saved Dhan credentials" in b for b in result["readiness"]["blockers"])
    assert calls == {}


def test_real_orders_readiness_requires_live_entitlement(mu_db, monkeypatch):
    from app.services import live_engine, user_credential_vault as vault

    _allow_real_order_readiness(monkeypatch)
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=True, calls=calls)
    user = make_user("live-entitlement-required@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-valid-token-abcdef")

    readiness = live_engine.evaluate_live_readiness(_ctx(user), "real_orders")

    assert readiness["ready"] is False
    assert readiness["real_orders_allowed"] is False
    assert readiness["checks"]["live_entitlement_valid"] is False
    assert "Live entitlement is required." in readiness["blockers"]
    assert calls == {}


@pytest.mark.parametrize("status", ["expired", "cancelled", "revoked", "past_due"])
def test_real_orders_readiness_blocks_invalid_entitlement_statuses(mu_db, monkeypatch, status):
    from app.services import live_engine, user_credential_vault as vault

    _allow_real_order_readiness(monkeypatch)
    calls: dict[str, int] = {}
    _patch_token_validation(monkeypatch, success=True, calls=calls)
    user = make_user(f"live-entitlement-{status}@gmail.com")
    _grant_live_entitlement(user, status=status)
    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-valid-token-abcdef")

    readiness = live_engine.evaluate_live_readiness(_ctx(user), "real_orders")

    assert readiness["ready"] is False
    assert readiness["real_orders_allowed"] is False
    assert "Live entitlement is required." in readiness["blockers"]
    assert calls == {}


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
