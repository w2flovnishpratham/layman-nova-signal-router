from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import models
from app.db.engine import session_scope
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


WEBHOOK_SECRET = "razorpay-webhook-secret-for-tests"
PREMIUM_PLAN = "plan_premium_monthly"
LIVE_PLAN = "plan_live_monthly"
STATIC_PLAN = "plan_static_ip_monthly"
STRATEGY_PLAN = "plan_strategy_monthly"
NOW = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
PERIOD_START = int((NOW - timedelta(days=1)).timestamp())
PERIOD_END = int((NOW + timedelta(days=30)).timestamp())


@pytest.fixture
def razorpay_env(mu_db, monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "razorpay", raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_PREMIUM_MONTHLY", PREMIUM_PLAN, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_LIVE_MONTHLY", LIVE_PLAN, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_STATIC_IP_MONTHLY", STATIC_PLAN, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_STRATEGY_MONTHLY", STRATEGY_PLAN, raising=False)


def _client() -> TestClient:
    from app.routers import payments

    app = FastAPI()
    app.include_router(payments.router)
    return TestClient(app)


def _headers(raw_body: bytes, *, event_id: str = "evt_razorpay_1") -> dict[str, str]:
    signature = hmac.new(WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return {
        "content-type": "application/json",
        "X-Razorpay-Signature": signature,
        "x-razorpay-event-id": event_id,
    }


def _raw_body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=False).encode("utf-8")


def _subscription_payload(
    user_id,
    *,
    event: str = "subscription.activated",
    plan_id: str = LIVE_PLAN,
    subscription_status: str = "active",
    payment_status: str | None = None,
    current_end: int | None = PERIOD_END,
) -> dict:
    subscription = {
        "id": "sub_razorpay_test",
        "plan_id": plan_id,
        "status": subscription_status,
        "current_start": PERIOD_START,
        "created_at": PERIOD_START,
        "notes": {
            "nova_user_id": str(user_id),
            "email": "customer@example.test",
            "contact": "+919999999999",
            "internal_secret": "must-not-store",
        },
    }
    if current_end is not None:
        subscription["current_end"] = current_end
    payload = {"event": event, "payload": {"subscription": {"entity": subscription}}}
    if payment_status is not None:
        payload["payload"]["payment"] = {
            "entity": {
                "id": "pay_razorpay_test",
                "status": payment_status,
                "amount": 19900,
                "currency": "INR",
                "notes": {
                    "nova_user_id": str(user_id),
                    "plan_id": plan_id,
                    "email": "payment@example.test",
                    "contact": "+918888888888",
                },
            }
        }
    return payload


def _payment_payload(user_id, *, event: str = "payment.failed", plan_id: str = LIVE_PLAN) -> dict:
    return {
        "event": event,
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_failed_test",
                    "status": "failed",
                    "amount": 19900,
                    "currency": "INR",
                    "notes": {
                        "nova_user_id": str(user_id),
                        "plan_id": plan_id,
                        "email": "failed@example.test",
                    },
                }
            }
        },
    }


def _post_signed(client: TestClient, payload: dict, *, event_id: str = "evt_razorpay_1"):
    raw = _raw_body(payload)
    return client.post("/api/payments/razorpay/webhook", content=raw, headers=_headers(raw, event_id=event_id))


def _payment_events():
    with session_scope() as db:
        rows = list(db.scalars(select(models.PaymentEvent).order_by(models.PaymentEvent.created_at)))
        return [
            {
                "provider": row.provider,
                "provider_event_id": row.provider_event_id,
                "event_type": row.event_type,
                "event_status": row.event_status,
                "processed": row.processed,
                "user_id": row.user_id,
                "payload_hash": row.payload_hash,
                "metadata": row.metadata_json,
            }
            for row in rows
        ]


def _latest_entitlement(user_id):
    with session_scope() as db:
        row = db.scalar(
            select(models.UserEntitlement)
            .where(models.UserEntitlement.user_id == user_id)
            .order_by(models.UserEntitlement.updated_at.desc(), models.UserEntitlement.created_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        return {
            "plan_code": row.plan_code,
            "status": row.status,
            "source": row.source,
            "expires_at": row.expires_at,
            "live_orders_enabled": row.live_orders_enabled,
            "static_ip_enabled": row.static_ip_enabled,
            "strategy_access_enabled": row.strategy_access_enabled,
            "metadata": row.metadata_json,
        }


def test_missing_razorpay_signature_is_rejected(razorpay_env):
    user = make_user("razorpay-missing-signature@example.com")
    raw = _raw_body(_subscription_payload(user.id))

    response = _client().post(
        "/api/payments/razorpay/webhook",
        content=raw,
        headers={"content-type": "application/json", "x-razorpay-event-id": "evt_missing_sig"},
    )

    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "Missing Razorpay signature."}
    assert _payment_events() == []


def test_invalid_razorpay_signature_is_rejected(razorpay_env):
    user = make_user("razorpay-invalid-signature@example.com")
    raw = _raw_body(_subscription_payload(user.id))

    response = _client().post(
        "/api/payments/razorpay/webhook",
        content=raw,
        headers={
            "content-type": "application/json",
            "X-Razorpay-Signature": "not-a-valid-signature",
            "x-razorpay-event-id": "evt_invalid_sig",
        },
    )

    assert response.status_code == 401
    assert response.json() == {"ok": False, "error": "Invalid Razorpay signature."}
    assert _payment_events() == []


def test_missing_razorpay_event_id_is_rejected_after_valid_signature(razorpay_env):
    user = make_user("razorpay-missing-event-id@example.com")
    raw = _raw_body(_subscription_payload(user.id))
    headers = _headers(raw, event_id="evt_missing_event_id")
    headers.pop("x-razorpay-event-id")

    response = _client().post("/api/payments/razorpay/webhook", content=raw, headers=headers)

    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "Missing Razorpay event id."}
    assert _payment_events() == []


def test_valid_signed_webhook_is_accepted_and_creates_payment_event(razorpay_env):
    user = make_user("razorpay-valid@example.com")
    response = _post_signed(_client(), _subscription_payload(user.id), event_id="evt_valid")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["duplicate"] is False
    assert body["processed"] is True

    events = _payment_events()
    assert len(events) == 1
    assert events[0]["provider"] == "razorpay"
    assert events[0]["provider_event_id"] == "evt_valid"
    assert events[0]["event_type"] == "subscription.activated"
    assert events[0]["event_status"] == "active"


def test_signature_verification_uses_raw_body_not_reserialized_json(razorpay_env):
    user = make_user("razorpay-raw-body@example.com")
    payload = _subscription_payload(user.id)
    raw = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")

    response = _client().post(
        "/api/payments/razorpay/webhook",
        content=raw,
        headers=_headers(raw, event_id="evt_raw_body"),
    )

    assert response.status_code == 200
    assert response.json()["processed"] is True
    assert _payment_events()[0]["payload_hash"] == hashlib.sha256(raw).hexdigest()


def test_duplicate_razorpay_event_is_idempotent_and_does_not_double_apply(razorpay_env):
    user = make_user("razorpay-duplicate@example.com")
    payload = _subscription_payload(user.id)
    client = _client()

    first = _post_signed(client, payload, event_id="evt_duplicate")
    second = _post_signed(client, payload, event_id="evt_duplicate")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert len(_payment_events()) == 1
    with session_scope() as db:
        assert db.query(models.UserEntitlement).count() == 1


def test_duplicate_razorpay_event_with_different_payload_is_rejected(razorpay_env):
    user = make_user("razorpay-duplicate-conflict@example.com")
    client = _client()

    first = _post_signed(client, _subscription_payload(user.id), event_id="evt_conflict")
    second = _post_signed(
        client,
        _subscription_payload(user.id, plan_id=STATIC_PLAN),
        event_id="evt_conflict",
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json() == {"ok": False, "error": "Payment webhook rejected."}
    assert len(_payment_events()) == 1


def test_unknown_plan_stores_payment_event_without_granting_entitlement(razorpay_env):
    user = make_user("razorpay-unknown-plan@example.com")

    response = _post_signed(
        _client(),
        _subscription_payload(user.id, plan_id="plan_unknown"),
        event_id="evt_unknown_plan",
    )

    assert response.status_code == 200
    assert response.json()["processed"] is False
    assert response.json()["reason"] == "unknown_plan"
    assert _latest_entitlement(user.id) is None
    events = _payment_events()
    assert events[0]["processed"] is False
    assert events[0]["metadata"]["reason"] == "unknown_plan"


def test_unresolved_user_stores_payment_event_without_granting_entitlement(razorpay_env):
    missing_user_id = uuid.uuid4()

    response = _post_signed(
        _client(),
        _subscription_payload(missing_user_id),
        event_id="evt_unresolved_user",
    )

    assert response.status_code == 200
    assert response.json()["processed"] is False
    assert response.json()["reason"] == "unresolved_user"
    with session_scope() as db:
        assert db.query(models.UserEntitlement).count() == 0
    events = _payment_events()
    assert events[0]["user_id"] is None
    assert events[0]["metadata"]["reason"] == "unresolved_user"


def test_subscription_activated_known_plan_grants_live_entitlement(razorpay_env):
    user = make_user("razorpay-live-plan@example.com")

    response = _post_signed(
        _client(),
        _subscription_payload(user.id, event="subscription.activated", plan_id=LIVE_PLAN),
        event_id="evt_activate_live",
    )

    assert response.status_code == 200
    entitlement = _latest_entitlement(user.id)
    assert entitlement is not None
    assert entitlement["status"] == "active"
    assert entitlement["source"] == "payment_provider"
    assert entitlement["live_orders_enabled"] is True
    assert entitlement["static_ip_enabled"] is False
    assert entitlement["strategy_access_enabled"] is False
    assert entitlement["expires_at"] is not None


def test_subscription_activated_premium_plan_grants_all_entitlements(razorpay_env):
    user = make_user("razorpay-premium-plan@example.com")

    response = _post_signed(
        _client(),
        _subscription_payload(user.id, event="subscription.activated", plan_id=PREMIUM_PLAN),
        event_id="evt_activate_premium",
    )

    assert response.status_code == 200
    entitlement = _latest_entitlement(user.id)
    assert entitlement is not None
    assert entitlement["status"] == "active"
    assert entitlement["source"] == "payment_provider"
    assert entitlement["plan_code"] == "razorpay_premium_monthly"
    assert entitlement["live_orders_enabled"] is True
    assert entitlement["static_ip_enabled"] is True
    assert entitlement["strategy_access_enabled"] is True
    assert entitlement["expires_at"] is not None


def test_subscription_charged_with_captured_payment_grants_entitlement(razorpay_env):
    user = make_user("razorpay-charged@example.com")

    response = _post_signed(
        _client(),
        _subscription_payload(
            user.id,
            event="subscription.charged",
            plan_id=STATIC_PLAN,
            payment_status="captured",
        ),
        event_id="evt_subscription_charged",
    )

    assert response.status_code == 200
    entitlement = _latest_entitlement(user.id)
    assert entitlement is not None
    assert entitlement["static_ip_enabled"] is True
    assert entitlement["live_orders_enabled"] is False


def test_same_razorpay_plan_id_can_unlock_multiple_feature_flags(razorpay_env, monkeypatch):
    user = make_user("razorpay-bundle-plan@example.com")
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_LIVE_MONTHLY", "plan_bundle", raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_STATIC_IP_MONTHLY", "plan_bundle", raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_STRATEGY_MONTHLY", "plan_bundle", raising=False)

    response = _post_signed(
        _client(),
        _subscription_payload(user.id, plan_id="plan_bundle"),
        event_id="evt_bundle_plan",
    )

    assert response.status_code == 200
    entitlement = _latest_entitlement(user.id)
    assert entitlement is not None
    assert entitlement["live_orders_enabled"] is True
    assert entitlement["static_ip_enabled"] is True
    assert entitlement["strategy_access_enabled"] is True


def test_payment_provider_entitlement_without_period_end_is_not_created(razorpay_env):
    user = make_user("razorpay-no-period-end@example.com")

    response = _post_signed(
        _client(),
        _subscription_payload(user.id, current_end=None),
        event_id="evt_missing_period_end",
    )

    assert response.status_code == 200
    assert response.json()["processed"] is False
    assert response.json()["reason"] == "missing_period_end"
    assert _latest_entitlement(user.id) is None


def test_subscription_cancelled_revokes_entitlement(razorpay_env):
    user = make_user("razorpay-cancelled@example.com")
    client = _client()
    assert _post_signed(client, _subscription_payload(user.id), event_id="evt_cancel_first").status_code == 200

    response = _post_signed(
        client,
        _subscription_payload(
            user.id,
            event="subscription.cancelled",
            subscription_status="cancelled",
        ),
        event_id="evt_cancel_second",
    )

    assert response.status_code == 200
    entitlement = _latest_entitlement(user.id)
    assert entitlement is not None
    assert entitlement["status"] == "cancelled"
    assert entitlement["live_orders_enabled"] is False


@pytest.mark.parametrize(
    ("event", "subscription_status"),
    [
        ("subscription.paused", "paused"),
        ("subscription.halted", "halted"),
    ],
)
def test_subscription_paused_or_halted_blocks_entitlement(razorpay_env, event, subscription_status):
    user = make_user(f"razorpay-{subscription_status}@example.com")
    client = _client()
    assert _post_signed(client, _subscription_payload(user.id), event_id=f"evt_{subscription_status}_first").status_code == 200

    response = _post_signed(
        client,
        _subscription_payload(
            user.id,
            event=event,
            subscription_status=subscription_status,
        ),
        event_id=f"evt_{subscription_status}_second",
    )

    assert response.status_code == 200
    entitlement = _latest_entitlement(user.id)
    assert entitlement is not None
    assert entitlement["status"] == "past_due"
    assert entitlement["live_orders_enabled"] is False


def test_subscription_completed_expires_entitlement(razorpay_env):
    user = make_user("razorpay-completed@example.com")
    client = _client()
    assert _post_signed(client, _subscription_payload(user.id), event_id="evt_completed_first").status_code == 200

    response = _post_signed(
        client,
        _subscription_payload(
            user.id,
            event="subscription.completed",
            subscription_status="completed",
        ),
        event_id="evt_completed_second",
    )

    assert response.status_code == 200
    entitlement = _latest_entitlement(user.id)
    assert entitlement is not None
    assert entitlement["status"] == "expired"
    assert entitlement["live_orders_enabled"] is False


def test_failed_payment_only_event_does_not_activate_entitlement(razorpay_env):
    user = make_user("razorpay-failed-payment@example.com")

    response = _post_signed(_client(), _payment_payload(user.id), event_id="evt_failed_payment")

    assert response.status_code == 200
    assert response.json()["processed"] is False
    assert response.json()["reason"] == "failed_payment"
    assert _latest_entitlement(user.id) is None


def test_unknown_signed_event_is_stored_and_ignored(razorpay_env):
    user = make_user("razorpay-unknown-event@example.com")

    response = _post_signed(
        _client(),
        _subscription_payload(user.id, event="subscription.pending"),
        event_id="evt_unknown_event",
    )

    assert response.status_code == 200
    assert response.json()["processed"] is False
    assert response.json()["reason"] == "ignored_unknown_event"
    assert _latest_entitlement(user.id) is None
    assert _payment_events()[0]["metadata"]["action"] == "ignored_unknown_event"


def test_frontend_payment_status_cannot_create_entitlement_without_signature(razorpay_env):
    user = make_user("razorpay-frontend-status@example.com")
    payload = {
        "user_id": str(user.id),
        "payment_status": "paid",
        "subscription_status": "active",
        "live_orders_enabled": True,
    }

    response = _client().post("/api/payments/razorpay/webhook", json=payload)

    assert response.status_code == 400
    assert response.json()["error"] == "Missing Razorpay signature."
    assert _latest_entitlement(user.id) is None
    assert _payment_events() == []


def test_payment_event_metadata_and_response_do_not_expose_raw_payload_or_pii(razorpay_env):
    user = make_user("razorpay-pii-safe@example.com")

    response = _post_signed(_client(), _subscription_payload(user.id), event_id="evt_pii_safe")

    assert response.status_code == 200
    event = _payment_events()[0]
    serialized = f"{response.json()} {event}"
    forbidden = [
        WEBHOOK_SECRET,
        "customer@example.test",
        "payment@example.test",
        "+919999999999",
        "+918888888888",
        "must-not-store",
        "X-Razorpay-Signature",
        "raw_payload",
        "card",
        "vpa",
        "bank",
        "dhan",
        "proxy_url",
        "credential",
    ]
    for value in forbidden:
        assert value not in serialized
    assert event["metadata"]["razorpay_subscription_id"] == "sub_razorpay_test"
    assert event["metadata"]["resolved_user_id"] == str(user.id)
