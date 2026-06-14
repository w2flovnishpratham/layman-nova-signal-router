from __future__ import annotations

import secrets

from fastapi.testclient import TestClient

from app.main import app
from app.services.credential_vault import save_dhan_credentials, save_webhook_secret
from app.services.user_context import reset_current_user_id, set_current_user_id
from app.tests.security_test_helpers import (
    authenticate_client,
    create_authenticated_user,
    isolate_runtime,
)


def test_safety_status_requires_auth(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.get("/api/safety/status")
    assert response.status_code == 401


def test_safety_status_is_non_secret_and_blocks_live(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    user, auth_cookie = create_authenticated_user("stage3-status")
    webhook_secret = f"stage3-{secrets.token_urlsafe(24)}"
    runtime_token = set_current_user_id(user.id)
    try:
        save_dhan_credentials("1100110011", "raw-dhan-token-must-never-render")
        save_webhook_secret(webhook_secret)
    finally:
        reset_current_user_id(runtime_token)

    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage3-csrf")
        response = client.get("/api/safety/status", headers=headers)

    assert response.status_code == 200
    body = response.json()
    serialized = response.text
    assert "raw-dhan-token-must-never-render" not in serialized
    assert webhook_secret not in serialized
    assert body["broker"]["client_id_masked"].endswith("0011")
    assert body["broker"]["access_token_present"] is True
    assert body["webhook"]["secret_set"] is True
    assert body["signing_relay_configured"] is False
    assert body["executor_egress_verified"] is False
    assert body["single_operator_live_allowed"] is False
    assert body["public_live_launch_allowed"] is False
    assert body["reasons_live_blocked"]
    assert "path" not in serialized.lower()


def test_webhook_secret_is_returned_only_when_created(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _user, auth_cookie = create_authenticated_user("stage3-secret")

    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage3-csrf")
        first = client.post("/api/session/start", headers=headers)
        second = client.post("/api/session/start", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["webhookSecret"]
    assert first.json()["webhookSecretAvailableOnce"] is True
    assert second.json()["webhookSecret"] is None
    assert second.json()["webhookSecretAvailableOnce"] is False
