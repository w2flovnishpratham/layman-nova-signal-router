from __future__ import annotations

import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth.db import _database_url, init_auth_db, session_scope
from app.auth.models import AuthSession, User, utc_now_dt
from app.auth.security import auth_enabled
from app.auth.service import email_allowed, upsert_google_user
from app.config import settings
from app.services import credential_vault, state_store
from app.store.session_token import issue_auth_token


def test_email_allowed_fails_closed_when_admin_emails_empty(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "")

    assert email_allowed("attacker@example.test") is False


def test_email_allowed_requires_configured_email(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "ops@example.test")

    assert email_allowed("ops@example.test") is True
    assert email_allowed("other@example.test") is False


def test_auth_cannot_be_disabled_in_production(monkeypatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)

    with pytest.raises(RuntimeError, match="AUTH_REQUIRED"):
        auth_enabled()


def test_database_url_required_in_production(monkeypatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DATABASE_URL", "")
    monkeypatch.setattr(settings, "AUTH_DATABASE_URL", "")

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        _database_url()


def test_sqlite_database_url_rejected_in_production(monkeypatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///runtime_state/control.sqlite3")
    monkeypatch.setattr(settings, "AUTH_DATABASE_URL", "")

    with pytest.raises(RuntimeError, match="SQLite is not allowed"):
        _database_url()


def test_logout_revokes_auth_session(monkeypatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "local")
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(settings, "SESSION_TOKEN_SECRET", "s" * 32)
    init_auth_db()

    suffix = secrets.token_hex(8)
    user_id = f"u_logout_{suffix}"
    auth_session_id = f"asid_{suffix}"
    user = User(id=user_id, email=f"{suffix}@example.test", google_sub=f"sub-{suffix}")
    auth_session = AuthSession(
        id=auth_session_id,
        user_id=user_id,
        created_at=utc_now_dt(),
        expires_at=utc_now_dt() + timedelta(hours=1),
    )
    with session_scope() as session:
        session.add(user)
        session.add(auth_session)
        session.commit()

    from app.main import app

    stolen_cookie = issue_auth_token(user_id, auth_session_id=auth_session_id)
    with TestClient(app) as client:
        client.cookies.set(settings.AUTH_COOKIE_NAME, stolen_cookie)
        client.cookies.set(settings.CSRF_COOKIE_NAME, "logout-csrf")
        response = client.post(
            "/api/auth/logout",
            headers={settings.CSRF_HEADER_NAME: "logout-csrf"},
        )
        assert response.status_code == 200

        client.cookies.set(settings.AUTH_COOKIE_NAME, stolen_cookie)
        status = client.get("/api/auth/status").json()

    with session_scope() as session:
        refreshed = session.get(AuthSession, auth_session_id)

    assert refreshed is not None
    assert refreshed.revoked_at is not None
    assert status["authenticated"] is False


def test_google_user_upsert_rejects_email_collision(monkeypatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "local")
    init_auth_db()
    suffix = secrets.token_hex(8)
    email = f"{suffix}@example.test"

    with session_scope() as session:
        upsert_google_user(session, email=email, google_sub=f"sub-a-{suffix}", name=None, avatar_url=None)

    with session_scope() as session:
        with pytest.raises(ValueError, match="already linked"):
            upsert_google_user(session, email=email, google_sub=f"sub-b-{suffix}", name=None, avatar_url=None)


def test_auth_enabled_webhook_rejects_legacy_global_secret(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "runtime_state"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
    monkeypatch.setattr(settings, "APP_ENV", "local")
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", False)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    legacy_secret = "R9vQ4nT2sL8xA6mP3zW7cY5kD1hF0bJ"
    credential_vault._LOCAL_MEMORY_PAYLOADS["__global__"] = {
        "version": 1,
        "dhan": None,
        "webhook_secret": legacy_secret,
    }

    from app.main import app

    with TestClient(app) as client:
        response = client.post(
            "/webhook/tradingview",
            json={
                "secret": legacy_secret,
                "signal_id": "legacy-global-secret",
                "action": "ENTRY",
            },
        )

    assert response.status_code == 403
    assert response.json()["status"] == "UNAUTHORIZED"
