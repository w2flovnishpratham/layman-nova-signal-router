"""The OAuth callback is browser-facing: it must never render JSON at a user.

The browser reaches /api/auth/google/callback via the address bar, not
fetch(), so an HTTPException there paints a raw {"detail": ...} page on the
API domain with no way back into the app. Reported from production twice:
"Internal Server Error" during a database outage, and
{"detail": "Invalid OAuth state (possible CSRF)."} on a stale retry.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import google as google_auth
from app.config import settings


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client-id", raising=False)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-client-secret", raising=False)
    monkeypatch.setattr(
        settings, "GOOGLE_REDIRECT_URI", "https://api.example.com/api/auth/google/callback", raising=False
    )
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://app.example.com", raising=False)
    monkeypatch.setattr(google_auth, "database_configured", lambda: True)

    app = FastAPI()
    app.include_router(google_auth.router)
    return TestClient(app, follow_redirects=False)


def _assert_redirects_into_the_app(response, reason: str) -> None:
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith("https://app.example.com")
    assert f"auth_error={reason}" in location


def test_stale_or_mismatched_state_sends_the_user_back_to_the_app(client):
    """Previously: {"detail": "Invalid OAuth state (possible CSRF)."} rendered
    as raw JSON. It is nearly always a stale retry, and "CSRF" is both
    alarming and unactionable for the person reading it."""
    response = client.get("/api/auth/google/callback", params={"code": "abc", "state": "not-the-cookie"})

    _assert_redirects_into_the_app(response, "expired")
    assert "CSRF" not in response.text


def test_provider_error_is_not_echoed_back(client):
    response = client.get("/api/auth/google/callback", params={"error": "access_denied"})

    _assert_redirects_into_the_app(response, "provider")
    assert "access_denied" not in response.headers["location"]


def test_missing_code_or_state_redirects_rather_than_erroring(client):
    response = client.get("/api/auth/google/callback")

    _assert_redirects_into_the_app(response, "invalid_request")


def test_no_callback_failure_path_returns_a_json_body(client):
    """Whatever the failure, the user gets navigation back into the product --
    never a JSON document on the API domain."""
    for params in (
        {},
        {"error": "access_denied"},
        {"code": "abc"},
        {"state": "xyz"},
        {"code": "abc", "state": "mismatched"},
    ):
        response = client.get("/api/auth/google/callback", params=params)
        assert response.status_code == 302, params
        assert "detail" not in response.text
