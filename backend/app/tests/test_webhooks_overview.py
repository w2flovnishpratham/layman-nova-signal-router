"""Webhooks overview: owner scoping, masked secret, no fabricated endpoints."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.engine import session_scope
from app.routers import signals
from app.services import credential_vault, user_credential_vault, webhooks_overview
from app.services.user_context import current_user_from_model, dev_user
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _event(user_id, *, event_id: str, received_at: datetime, processed_status="accepted", signature_ok=True):
    with session_scope() as db:
        db.add(models.WebhookEvent(
            provider="tradingview",
            event_id=event_id,
            user_id=user_id,
            raw_body_sha256="b" * 64,
            signature_ok=signature_ok,
            replay_status="fresh",
            processed_status=processed_status,
            event_metadata={"action": "ENTRY"},
            received_at=received_at,
            updated_at=received_at,
        ))


def test_delivery_stats_are_scoped_to_the_owner(mu_db):  # noqa: F811
    alice = make_user("alice-hooks@example.com")
    bob = make_user("bob-hooks@example.com")
    now = datetime.now(timezone.utc)
    _event(alice.id, event_id="a-1", received_at=now)
    _event(bob.id, event_id="b-1", received_at=now)
    _event(bob.id, event_id="b-2", received_at=now)

    result = webhooks_overview.build_webhooks_overview(alice.id)
    assert result["deliveries"]["counts"]["total"] == 1
    assert [r["event_id"] for r in result["recent"]] == ["a-1"]


def test_secret_is_masked_and_never_raw(mu_db):  # noqa: F811
    user = make_user("secret-hooks@example.com")
    raw_secret = "secret-hooks-value-with-enough-entropy-9274"
    user_credential_vault.save_user_credentials(user.id, webhook_secret=raw_secret)
    result = webhooks_overview.build_webhooks_overview(user.id)
    secret = result["secret"]
    # Only metadata is exposed; there is no field carrying the secret value.
    assert set(secret.keys()) == {"set", "masked", "source"}
    assert "value" not in secret
    assert "webhook_secret" not in repr(result)
    assert raw_secret not in repr(result)
    assert secret["source"] == "account"


def test_rotate_replaces_encrypted_account_secret_and_returns_it_once(mu_db):  # noqa: F811
    user = make_user("rotate-hooks@example.com")
    current_user = current_user_from_model(user)
    user_credential_vault.save_user_credentials(
        user.id, webhook_secret="old-secret-value-with-enough-entropy-9274"
    )

    response = signals.rotate_webhook_secret(current_user)
    body = json.loads(response.body)

    assert response.headers["cache-control"] == "no-store"
    assert body["secret"] == user_credential_vault.get_user_webhook_secret(user.id)
    assert "old-secret" not in body["secret"]


def test_dev_rotation_updates_the_legacy_backend_vault(monkeypatch):
    saved: list[str] = []
    monkeypatch.setattr(
        credential_vault,
        "webhook_secret_metadata",
        lambda: {"set": True, "masked": "old", "source": "vault"},
    )
    monkeypatch.setattr(credential_vault, "save_webhook_secret", lambda secret: saved.append(secret))

    response = signals.rotate_webhook_secret(dev_user())
    body = json.loads(response.body)

    assert saved == [body["secret"]]


def test_local_without_database_keeps_the_legacy_endpoints(monkeypatch):
    monkeypatch.setattr(webhooks_overview.settings, "BACKEND_PUBLIC_BASE_URL", "https://hooks.test")
    monkeypatch.setattr(
        credential_vault,
        "webhook_secret_metadata",
        lambda: {"set": False, "masked": None, "source": None},
    )

    result = webhooks_overview.build_webhooks_overview(dev_user().id)

    assert {endpoint["url"] for endpoint in result["endpoints"]} == {
        "https://hooks.test/webhook/tradingview",
        "https://hooks.test/api/webhooks/private",
    }


def test_endpoints_are_the_real_ones_not_per_strategy(mu_db):  # noqa: F811
    user = make_user("endpoints-hooks@example.com")
    result = webhooks_overview.build_webhooks_overview(user.id)
    keys = {e["key"] for e in result["endpoints"]}
    # One webhook secret per user; there is no per-strategy endpoint model, so
    # the mockup's per-strategy endpoint list must not be fabricated.
    assert keys <= {"tradingview", "private"}
    assert not any("supertrend" in e["url"].lower() for e in result["endpoints"])


def test_counts_and_signature_verified_reflect_stored_rows(mu_db):  # noqa: F811
    user = make_user("counts-hooks@example.com")
    now = datetime.now(timezone.utc)
    _event(user.id, event_id="v-1", received_at=now, signature_ok=True)
    _event(user.id, event_id="v-2", received_at=now - timedelta(seconds=1), signature_ok=True)
    _event(user.id, event_id="v-3", received_at=now - timedelta(seconds=2), processed_status="rejected", signature_ok=False)

    result = webhooks_overview.build_webhooks_overview(user.id)
    assert result["deliveries"]["counts"]["accepted"] == 2
    assert result["deliveries"]["counts"]["rejected"] == 1
    assert result["deliveries"]["signature_verified"] == 2
    assert result["deliveries"]["last_delivery_at"] is not None


def test_empty_owner_gets_truthful_zero_state(mu_db):  # noqa: F811
    user = make_user("empty-hooks@example.com")
    result = webhooks_overview.build_webhooks_overview(user.id)
    assert result["deliveries"]["counts"].get("total", 0) == 0
    assert result["recent"] == []
    assert result["deliveries"]["last_delivery_at"] is None


def test_recent_deliveries_never_leak_payload(mu_db):  # noqa: F811
    user = make_user("leak-hooks@example.com")
    with session_scope() as db:
        db.add(models.WebhookEvent(
            provider="tradingview", event_id="leak-1", user_id=user.id,
            raw_body_sha256="c" * 64, signature_ok=True, replay_status="fresh",
            processed_status="accepted",
            event_metadata={"action": "ENTRY", "secret": "TOP_SECRET", "raw_body": "PAYLOAD_BODY"},
            received_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        ))
    result = webhooks_overview.build_webhooks_overview(user.id)
    blob = repr(result)
    assert "TOP_SECRET" not in blob
    assert "PAYLOAD_BODY" not in blob
