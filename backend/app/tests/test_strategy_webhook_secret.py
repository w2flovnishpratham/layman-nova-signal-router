"""Managed strategy (broadcast catalog) webhook secret: admin-only view/
reveal/rotate. Vault-first (not env-first, unlike the legacy single-user
secret) so rotating through the admin UI actually takes effect immediately
instead of silently doing nothing while STRATEGY_WEBHOOK_SECRET stays set."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import credential_vault
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401

pytest_plugins = ("app.tests.conftest_multiuser",)


def _admin_client(user_model):
    from app.auth.dependencies import require_admin
    from app.routers import strategies
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    app.include_router(strategies.router)
    app.dependency_overrides[require_admin] = lambda: current_user_from_model(user_model)
    return TestClient(app)


def _public_client():
    from app.routers import strategies

    app = FastAPI()
    app.include_router(strategies.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_vault():
    credential_vault.clear_all_credentials()
    yield
    credential_vault.clear_all_credentials()


def test_metadata_reports_unset_until_first_rotate(mu_db):
    admin = _admin_client(make_user("swh-admin1@example.com", is_admin=True))

    before = admin.get("/api/admin/strategy-webhook-secret").json()["secret"]
    assert before["set"] is False

    rotated = admin.post("/api/admin/strategy-webhook-secret/rotate").json()
    assert rotated["ok"] is True
    assert len(rotated["secret"]) >= 32

    after = admin.get("/api/admin/strategy-webhook-secret").json()["secret"]
    assert after["set"] is True
    assert after["source"] == "vault"
    assert after["masked"] != rotated["secret"]  # masked view never leaks the raw value


def test_reveal_returns_the_exact_current_secret(mu_db):
    admin = _admin_client(make_user("swh-admin2@example.com", is_admin=True))
    rotated = admin.post("/api/admin/strategy-webhook-secret/rotate").json()["secret"]
    revealed = admin.post("/api/admin/strategy-webhook-secret/reveal").json()["secret"]
    assert revealed == rotated


def test_rotate_immediately_invalidates_the_previous_secret_on_the_live_endpoint(mu_db):
    admin = _admin_client(make_user("swh-admin3@example.com", is_admin=True))
    public = _public_client()

    first = admin.post("/api/admin/strategy-webhook-secret/rotate").json()["secret"]
    accepted = public.post("/api/webhook/strategy/orb", json={"secret": first, "action": "HOLD"})
    assert accepted.status_code != 401

    second = admin.post("/api/admin/strategy-webhook-secret/rotate").json()["secret"]
    assert second != first

    stale = public.post("/api/webhook/strategy/orb", json={"secret": first, "action": "HOLD"})
    assert stale.status_code == 401
    assert stale.json()["error"] == "Invalid secret."

    fresh = public.post("/api/webhook/strategy/orb", json={"secret": second, "action": "HOLD"})
    assert fresh.status_code != 401
