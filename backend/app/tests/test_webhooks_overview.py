"""Webhooks overview: owner scoping, masked secret, no fabricated endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.engine import session_scope
from app.services import webhooks_overview
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
    result = webhooks_overview.build_webhooks_overview(user.id)
    secret = result["secret"]
    # Only metadata is exposed; there is no field carrying the secret value.
    assert set(secret.keys()) == {"set", "masked", "source"}
    assert "value" not in secret
    assert "webhook_secret" not in repr(result)


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
