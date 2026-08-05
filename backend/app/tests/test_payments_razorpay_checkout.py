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
from app.services.user_context import CurrentUser
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


WEBHOOK_SECRET = "checkout-webhook-secret"
KEY_ID = "rzp_test_checkout_key"
KEY_SECRET = "checkout-key-secret-should-not-leak"
PREMIUM_PLAN = "plan_premium_checkout"
LIVE_PLAN = "plan_live_checkout"
STATIC_PLAN = "plan_static_checkout"
STRATEGY_PLAN = "plan_strategy_checkout"
PAPER_PLAN = "plan_paper_checkout"
NOW = datetime.now(timezone.utc)
PERIOD_START = int((NOW - timedelta(days=1)).timestamp())
PERIOD_END = int((NOW + timedelta(days=30)).timestamp())


@pytest.fixture
def checkout_env(mu_db, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "razorpay", raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", KEY_ID, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", KEY_SECRET, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_PREMIUM_MONTHLY", PREMIUM_PLAN, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_LIVE_MONTHLY", LIVE_PLAN, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_STATIC_IP_MONTHLY", STATIC_PLAN, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_STRATEGY_MONTHLY", STRATEGY_PLAN, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_PAPER_PREMIUM", PAPER_PLAN, raising=False)


class FakeRazorpayClient:
    calls: list[dict] = []
    response_payload: dict = {
        "id": "sub_checkout_test",
        "status": "created",
        "short_url": "https://rzp.io/i/test-checkout",
        "customer_details": {
            "email": "customer@example.test",
            "contact": "+919999999999",
        },
        "card": {"last4": "1111"},
        "raw_provider_field": "must-not-return",
    }

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    # Paper Premium is created as a one-time Payment Link, which reads the
    # configured plan for its price first and authenticates on the client
    # rather than per-request.
    plan_payload: dict = {
        "id": "plan_paper_premium_test",
        "item": {"amount": 10000, "currency": "INR"},
    }
    payment_link_payload: dict = {
        "id": "plink_checkout_test",
        "status": "created",
        "short_url": "https://rzp.io/i/test-payment-link",
        "raw_provider_field": "must-not-return",
    }

    def get(self, url, **kwargs):
        import httpx

        self.__class__.calls.append({"method": "GET", "url": url})
        return httpx.Response(
            200,
            json=self.__class__.plan_payload,
            request=httpx.Request("GET", url),
        )

    def post(self, url, *, json, auth=None):
        import httpx

        self.__class__.calls.append(
            {
                "method": "POST",
                "url": url,
                "auth": auth if auth is not None else self.kwargs.get("auth"),
                "json": json,
                "timeout": self.kwargs.get("timeout"),
            }
        )
        payload = (
            self.__class__.payment_link_payload
            if "payment_links" in url
            else self.__class__.response_payload
        )
        return httpx.Response(
            200,
            json=payload,
            request=httpx.Request("POST", url),
        )


def _auth_client(user: CurrentUser | None = None) -> tuple[TestClient, CurrentUser | None]:
    from app.auth.dependencies import get_current_user
    from app.routers import payments

    app = FastAPI()
    app.include_router(payments.router)
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app), user


def _current_user(user_model) -> CurrentUser:
    return CurrentUser(id=user_model.id, email=user_model.email, name=user_model.name)


def _raw_body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=False).encode("utf-8")


def _headers(raw_body: bytes, *, event_id: str) -> dict[str, str]:
    signature = hmac.new(WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return {
        "content-type": "application/json",
        "X-Razorpay-Signature": signature,
        "x-razorpay-event-id": event_id,
    }


def _subscription_payload(notes: dict, *, plan_id: str = PREMIUM_PLAN) -> dict:
    return {
        "event": "subscription.activated",
        "payload": {
            "subscription": {
                "entity": {
                    "id": "sub_checkout_test",
                    "plan_id": plan_id,
                    "status": "active",
                    "current_start": PERIOD_START,
                    "current_end": PERIOD_END,
                    "notes": notes,
                }
            }
        },
    }


def _entitlements_count() -> int:
    with session_scope() as db:
        return db.query(models.UserEntitlement).count()


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
            "status": row.status,
            "live_orders_enabled": row.live_orders_enabled,
            "static_ip_enabled": row.static_ip_enabled,
            "strategy_access_enabled": row.strategy_access_enabled,
        }


def test_unauthenticated_user_cannot_create_subscription(checkout_env):
    client, _user = _auth_client()

    response = client.post("/api/payments/razorpay/create-subscription", json={"plan_code": "premium_monthly"})

    assert response.status_code == 401


def test_entitlement_status_returns_safe_server_owned_flags(checkout_env):
    user_model = make_user("checkout-status@example.com")
    user = _current_user(user_model)
    now = models.utcnow()
    with session_scope() as db:
        db.add(
            models.UserEntitlement(
                user_id=user.id,
                plan_code="static_ip_monthly",
                status="active",
                source="payment_provider",
                starts_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(days=30),
                live_orders_enabled=False,
                static_ip_enabled=True,
                strategy_access_enabled=False,
                metadata_json={"provider_event_id": "evt_safe"},
                created_at=now,
                updated_at=now,
            )
        )
    client, _ = _auth_client(user)

    response = client.get("/api/payments/entitlements/status")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["entitlement"]["valid"] is True
    assert body["entitlement"]["static_ip_enabled"] is True
    assert body["entitlement"]["live_orders_enabled"] is False
    assert body["entitlement"]["strategy_access_enabled"] is False
    assert body["entitlement"]["paper_trading_enabled"] is False
    serialized = json.dumps(body)
    assert KEY_SECRET not in serialized
    assert WEBHOOK_SECRET not in serialized
    assert "provider_event_id" not in serialized


def test_authenticated_user_can_request_configured_plan(checkout_env, monkeypatch):
    from app.services import razorpay_checkout

    user_model = make_user("checkout-live@example.com")
    user = _current_user(user_model)
    FakeRazorpayClient.calls = []
    monkeypatch.setattr(razorpay_checkout.httpx, "Client", FakeRazorpayClient)
    client, _ = _auth_client(user)

    response = client.post("/api/payments/razorpay/create-subscription", json={"plan_code": "premium_monthly"})

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "ok": True,
        "provider": "razorpay",
        "checkout_url": "https://rzp.io/i/test-checkout",
        "short_url": "https://rzp.io/i/test-checkout",
        "subscription_id": "sub_checkout_test",
        "plan_code": "premium_monthly",
        "status": "created",
        "message": "Razorpay subscription created. Complete checkout to activate entitlement.",
    }
    assert FakeRazorpayClient.calls[0]["url"] == "https://api.razorpay.com/v1/subscriptions"
    assert FakeRazorpayClient.calls[0]["auth"] == (KEY_ID, KEY_SECRET)
    assert FakeRazorpayClient.calls[0]["json"]["plan_id"] == PREMIUM_PLAN
    assert FakeRazorpayClient.calls[0]["json"]["customer_notify"] is True


def test_paper_premium_checkout_creates_a_one_time_payment_link(checkout_env, monkeypatch):
    """paper_premium is a genuine one-time purchase, so it is created as a
    Payment Link rather than a Subscription on a long-cycle plan.

    The notes carry what the webhook needs to grant the entitlement: the owner
    (nova_user_id) and a Razorpay plan id, since a payment link has no plan_id
    of its own and _features_for_plan resolves entitlement features from one.
    """
    from app.services import razorpay_checkout

    user = _current_user(make_user("checkout-paper@example.com"))
    FakeRazorpayClient.calls = []
    monkeypatch.setattr(razorpay_checkout.httpx, "Client", FakeRazorpayClient)
    client, _ = _auth_client(user)

    response = client.post("/api/payments/razorpay/create-subscription", json={"plan_code": "paper_premium"})

    assert response.status_code == 200
    body = response.json()
    assert body["plan_code"] == "paper_premium"
    assert body["short_url"] == "https://rzp.io/i/test-payment-link"
    assert body["checkout_url"] == "https://rzp.io/i/test-payment-link"
    assert "raw_provider_field" not in body

    posts = [call for call in FakeRazorpayClient.calls if call.get("method") == "POST"]
    assert len(posts) == 1
    assert "payment_links" in posts[0]["url"]
    assert "subscriptions" not in posts[0]["url"]

    sent = posts[0]["json"]
    # Price is taken from the configured Razorpay plan, not hardcoded here.
    assert sent["amount"] == 10000
    assert sent["currency"] == "INR"
    assert sent["accept_partial"] is False
    assert sent["notes"]["nova_user_id"] == str(user.id)
    assert sent["notes"]["nova_plan_code"] == "paper_premium"
    assert sent["notes"]["razorpay_plan_id"] == PAPER_PLAN


def test_paper_premium_never_creates_a_subscription(checkout_env, monkeypatch):
    """Regression guard: the Subscriptions API must not be used for a product
    the UI presents as a one-time purchase."""
    from app.services import razorpay_checkout

    user = _current_user(make_user("checkout-paper-no-sub@example.com"))
    FakeRazorpayClient.calls = []
    monkeypatch.setattr(razorpay_checkout.httpx, "Client", FakeRazorpayClient)
    client, _ = _auth_client(user)

    client.post("/api/payments/razorpay/create-subscription", json={"plan_code": "paper_premium"})

    assert not any(
        "subscriptions" in call["url"] for call in FakeRazorpayClient.calls
    )


def test_frontend_user_id_and_payment_fields_are_ignored(checkout_env, monkeypatch):
    from app.services import razorpay_checkout

    server_user = _current_user(make_user("checkout-server-user@example.com"))
    attacker_user_id = str(uuid.uuid4())
    FakeRazorpayClient.calls = []
    monkeypatch.setattr(razorpay_checkout.httpx, "Client", FakeRazorpayClient)
    client, _ = _auth_client(server_user)

    response = client.post(
        "/api/payments/razorpay/create-subscription",
        json={
            "plan_code": "premium_monthly",
            "user_id": attacker_user_id,
            "payment_status": "paid",
            "subscription_status": "active",
            "is_paid": True,
            "entitlement": "active",
            "expires_at": "2099-01-01T00:00:00Z",
            "live_orders_enabled": True,
            "static_ip_enabled": True,
            "strategy_access_enabled": True,
        },
    )

    assert response.status_code == 200
    sent_payload = FakeRazorpayClient.calls[0]["json"]
    assert sent_payload["plan_id"] == PREMIUM_PLAN
    assert sent_payload["notes"] == {
        "nova_user_id": server_user.id_str,
        "nova_plan_code": "premium_monthly",
    }
    serialized_sent = str(sent_payload)
    assert attacker_user_id not in serialized_sent
    assert "payment_status" not in serialized_sent
    assert "subscription_status" not in serialized_sent
    assert _entitlements_count() == 0


@pytest.mark.parametrize("plan_code", ["live_monthly", "static_ip_monthly", "strategy_monthly"])
def test_legacy_split_plan_checkout_codes_are_not_user_facing(checkout_env, monkeypatch, plan_code):
    from app.services import razorpay_checkout

    user = _current_user(make_user(f"checkout-legacy-{plan_code}@example.com"))
    FakeRazorpayClient.calls = []
    monkeypatch.setattr(razorpay_checkout.httpx, "Client", FakeRazorpayClient)
    client, _ = _auth_client(user)

    response = client.post("/api/payments/razorpay/create-subscription", json={"plan_code": plan_code})

    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "Payment plan is not configured."}
    assert FakeRazorpayClient.calls == []


def test_missing_plan_config_fails_safely_before_razorpay_call(checkout_env, monkeypatch):
    from app.services import razorpay_checkout

    user = _current_user(make_user("checkout-missing-plan@example.com"))
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_PREMIUM_MONTHLY", "", raising=False)
    FakeRazorpayClient.calls = []
    monkeypatch.setattr(razorpay_checkout.httpx, "Client", FakeRazorpayClient)
    client, _ = _auth_client(user)

    response = client.post("/api/payments/razorpay/create-subscription", json={"plan_code": "premium_monthly"})

    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "Payment plan is not configured."}
    assert FakeRazorpayClient.calls == []


def test_checkout_response_does_not_return_secret_or_raw_razorpay_response(checkout_env, monkeypatch):
    from app.services import razorpay_checkout

    user = _current_user(make_user("checkout-safe-response@example.com"))
    FakeRazorpayClient.calls = []
    monkeypatch.setattr(razorpay_checkout.httpx, "Client", FakeRazorpayClient)
    client, _ = _auth_client(user)

    response = client.post("/api/payments/razorpay/create-subscription", json={"plan_code": "premium_monthly"})

    assert response.status_code == 200
    serialized = str(response.json())
    forbidden = [
        KEY_SECRET,
        WEBHOOK_SECRET,
        "customer@example.test",
        "+919999999999",
        "card",
        "raw_provider_field",
        "proxy_url",
        "dhan",
        "credential",
    ]
    for value in forbidden:
        assert value not in serialized


def test_checkout_creation_does_not_grant_entitlement_directly(checkout_env, monkeypatch):
    from app.services import razorpay_checkout

    user = _current_user(make_user("checkout-no-direct-grant@example.com"))
    FakeRazorpayClient.calls = []
    monkeypatch.setattr(razorpay_checkout.httpx, "Client", FakeRazorpayClient)
    client, _ = _auth_client(user)

    response = client.post("/api/payments/razorpay/create-subscription", json={"plan_code": "premium_monthly"})

    assert response.status_code == 200
    assert _latest_entitlement(user.id) is None


def test_entitlement_is_granted_only_after_verified_webhook(checkout_env, monkeypatch):
    from app.services import razorpay_checkout

    user = _current_user(make_user("checkout-webhook-grant@example.com"))
    FakeRazorpayClient.calls = []
    monkeypatch.setattr(razorpay_checkout.httpx, "Client", FakeRazorpayClient)
    client, _ = _auth_client(user)

    created = client.post("/api/payments/razorpay/create-subscription", json={"plan_code": "premium_monthly"})
    assert created.status_code == 200
    assert _latest_entitlement(user.id) is None

    notes = FakeRazorpayClient.calls[0]["json"]["notes"]
    payload = _subscription_payload(notes)
    raw = _raw_body(payload)
    webhook = client.post(
        "/api/payments/razorpay/webhook",
        content=raw,
        headers=_headers(raw, event_id="evt_checkout_webhook_grant"),
    )

    assert webhook.status_code == 200
    entitlement = _latest_entitlement(user.id)
    assert entitlement is not None
    assert entitlement["status"] == "active"
    assert entitlement["live_orders_enabled"] is True
    assert entitlement["static_ip_enabled"] is True
    assert entitlement["strategy_access_enabled"] is True


def test_legacy_bundle_plan_checkout_code_is_not_user_facing(checkout_env, monkeypatch):
    from app.services import razorpay_checkout

    user = _current_user(make_user("checkout-bundle-alias@example.com"))
    FakeRazorpayClient.calls = []
    monkeypatch.setattr(razorpay_checkout.httpx, "Client", FakeRazorpayClient)
    client, _ = _auth_client(user)

    response = client.post("/api/payments/razorpay/create-subscription", json={"plan_code": "bundle_monthly"})

    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "Payment plan is not configured."}
    assert FakeRazorpayClient.calls == []
