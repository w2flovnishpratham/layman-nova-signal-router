# ruff: noqa: F811
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.db import models
from app.db.engine import session_scope
from app.routers import admin
from app.services import entitlements
from app.services.user_context import current_user_from_model
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _client(user) -> TestClient:
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(user)
    return TestClient(app)


def _latest_entitlement(user_id):
    with session_scope() as db:
        return entitlements.get_user_entitlement(db, user_id)


def test_admin_can_grant_and_revoke_a_short_lived_pilot_entitlement(mu_db):
    operator = make_user("pilot-admin@example.com", is_admin=True)
    target = make_user("pilot-user@example.com")
    client = _client(operator)

    granted = client.post(
        f"/api/admin/users/{target.id}/pilot-entitlement",
        json={
            "duration_hours": 24,
            "live_orders_enabled": True,
            "static_ip_enabled": True,
            "strategy_access_enabled": True,
            "max_strategy_count": 3,
            "reason": "Controlled launch acceptance",
        },
    )
    assert granted.status_code == 200, granted.text
    grant_body = granted.json()["entitlement"]
    assert grant_body["status"] == "trialing"
    assert grant_body["evaluation"] == {
        "valid": True,
        "reason": "valid",
        "status": "trialing",
        "source": "trial",
        "plan_code": "admin_pilot",
        "live_orders_enabled": True,
        "static_ip_enabled": True,
        "strategy_access_enabled": True,
        "max_strategy_count": 3,
    }
    assert entitlements.has_live_entitlement_for_user(target.id) is True
    assert entitlements.has_static_ip_entitlement_for_user(target.id) is True
    assert entitlements.has_strategy_entitlement_for_user(target.id) is True

    revoked = client.post(
        f"/api/admin/users/{target.id}/pilot-entitlement/revoke",
        json={"reason": "Acceptance window closed"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["entitlement"]["evaluation"]["valid"] is False
    assert entitlements.has_live_entitlement_for_user(target.id) is False
    assert entitlements.has_static_ip_entitlement_for_user(target.id) is False
    assert entitlements.has_strategy_entitlement_for_user(target.id) is False

    with session_scope() as db:
        actions = db.scalars(
            select(models.AuditLog.action)
            .where(models.AuditLog.user_id == target.id)
            .order_by(models.AuditLog.created_at)
        ).all()
    assert actions[-2:] == ["PILOT_ENTITLEMENT_GRANTED", "PILOT_ENTITLEMENT_REVOKED"]


def test_non_admin_cannot_grant_pilot_entitlement(mu_db):
    operator = make_user("not-admin@example.com")
    target = make_user("pilot-target@example.com")
    response = _client(operator).post(
        f"/api/admin/users/{target.id}/pilot-entitlement",
        json={
            "duration_hours": 1,
            "strategy_access_enabled": True,
            "reason": "Should be rejected",
        },
    )
    assert response.status_code == 403
    assert _latest_entitlement(target.id) is None


def test_pilot_entitlement_duration_is_bounded(mu_db):
    operator = make_user("bounded-admin@example.com", is_admin=True)
    target = make_user("bounded-user@example.com")
    response = _client(operator).post(
        f"/api/admin/users/{target.id}/pilot-entitlement",
        json={
            "duration_hours": 169,
            "strategy_access_enabled": True,
            "reason": "Too long",
        },
    )
    assert response.status_code == 422
    assert _latest_entitlement(target.id) is None
