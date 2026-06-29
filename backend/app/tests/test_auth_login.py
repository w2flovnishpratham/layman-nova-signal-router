"""Google login: open to any verified user; ADMIN_EMAILS never blocks login."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401 (fixture import)


def test_non_admin_user_can_login_and_is_not_admin(mu_db):
    user = make_user("normal.person@gmail.com")
    assert user.email == "normal.person@gmail.com"
    assert user.is_admin is False
    # Row persisted with a generated id and timestamps.
    assert user.id is not None
    assert user.created_at is not None
    assert user.last_login_at is not None


def test_admin_email_is_flagged_admin(mu_db, monkeypatch):
    from app.config import settings
    from app.services.user_context import is_admin_user

    monkeypatch.setattr(settings, "ADMIN_EMAILS", "boss@corp.com, second@corp.com", raising=False)
    assert is_admin_user("boss@corp.com") is True
    assert is_admin_user("BOSS@CORP.COM") is True  # case-insensitive
    assert is_admin_user("nobody@gmail.com") is False

    admin = make_user("boss@corp.com", is_admin=is_admin_user("boss@corp.com"))
    assert admin.is_admin is True


def test_me_dev_fallback_when_auth_not_required(monkeypatch):
    """With AUTH_REQUIRED=false and no session, /api/me returns the dev user."""
    from app.config import settings
    from app.auth.google import router

    monkeypatch.setattr(settings, "APP_ENV", "local", raising=False)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False, raising=False)
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "y" * 48, raising=False)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    resp = client.get("/api/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["user"]["is_dev"] is True
    assert body["user"]["is_admin"] is False


def test_me_production_auth_disabled_does_not_return_dev_user(monkeypatch):
    from app.config import settings
    from app.auth.google import router

    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False, raising=False)
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "y" * 48, raising=False)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/api/me")

    assert resp.status_code == 401
    assert resp.json() == {"authenticated": False, "user": None}


def test_dev_user_helper_fails_closed_in_production(monkeypatch):
    from app.config import settings
    from app.services.user_context import dev_user

    monkeypatch.setattr(settings, "APP_ENV", "prod", raising=False)

    with pytest.raises(RuntimeError) as exc:
        dev_user()

    assert str(exc.value) == "Dev user fallback is disabled in production."


def test_me_unauthenticated_when_auth_required(monkeypatch):
    from app.config import settings
    from app.auth.google import router

    monkeypatch.setattr(settings, "APP_ENV", "local", raising=False)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True, raising=False)
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "y" * 48, raising=False)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    resp = client.get("/api/me")
    assert resp.status_code == 401
