from __future__ import annotations

import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.config import Settings, settings
from app.services import credential_vault, state_store
from app.services.credential_vault import get_dhan_credentials, save_dhan_credentials, save_webhook_secret
from app.services.user_context import (
    RuntimeScopeError,
    current_user_id,
    reset_current_user_id,
    scoped_file,
    scoped_runtime_dir,
    set_current_user_id,
)
from app.tests.security_test_helpers import authenticate_client, create_authenticated_user, isolate_runtime


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/setup/dhan/connect"),
        ("post", "/api/setup/webhook-secret"),
        ("post", "/api/engine/start"),
        ("post", "/api/control/panic-exit"),
        ("get", "/api/orders"),
        ("get", "/api/positions"),
        ("get", "/api/dashboard"),
    ],
)
def test_protected_endpoints_return_401_without_auth(tmp_path, monkeypatch, method: str, path: str) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        response = getattr(client, method)(path)

    assert response.status_code == 401
    assert response.json()["detail"] == "Login required."


def test_runtime_scope_raises_without_user_when_auth_enabled(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    base_file = tmp_path / "state" / "app_state.json"

    with pytest.raises(RuntimeScopeError, match="authenticated user"):
        scoped_file(base_file)
    with pytest.raises(RuntimeScopeError, match="authenticated user"):
        scoped_runtime_dir()
    with pytest.raises(RuntimeScopeError, match="authenticated user"):
        save_dhan_credentials("DHAN-A", "TOKEN-A")
    with pytest.raises(RuntimeScopeError, match="authenticated user"):
        get_dhan_credentials()

    assert not base_file.exists()


def test_production_runtime_paths_must_be_outside_repository(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(config, "RUNTIME_STATE_DIR", config.BACKEND_DIR / "runtime_state")
    monkeypatch.setattr(config, "RUNTIME_LOG_DIR", tmp_path / "logs")

    with pytest.raises(RuntimeError, match="outside the repository"):
        config.validate_production_runtime_paths()


def test_live_orders_remain_disabled_by_default() -> None:
    assert Settings(_env_file=None).ENABLE_LIVE_ORDERS is False


def test_authenticated_flow_never_writes_global_runtime_file(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    user, _cookie = create_authenticated_user("runtime")
    token = set_current_user_id(user.id)
    try:
        state_store.update_app_state(state="USER_ONLY")
    finally:
        reset_current_user_id(token)

    base_file = tmp_path / "state" / "app_state.json"
    user_file = tmp_path / "state" / "users" / user.id / "app_state.json"
    assert not base_file.exists()
    assert user_file.exists()


def test_csrf_required_for_authenticated_mutation(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _user, auth_cookie = create_authenticated_user("csrf")
    from app.main import app

    with TestClient(app) as client:
        client.cookies.set(settings.AUTH_COOKIE_NAME, auth_cookie)
        missing = client.post("/api/control/resume-entries")

        client.cookies.set(settings.CSRF_COOKIE_NAME, "cookie-token")
        invalid = client.post(
            "/api/control/resume-entries",
            headers={settings.CSRF_HEADER_NAME: "different-token"},
        )

        valid_headers = {settings.CSRF_HEADER_NAME: "cookie-token"}
        valid = client.post("/api/control/resume-entries", headers=valid_headers)

    assert missing.status_code == 403
    assert invalid.status_code == 403
    assert valid.status_code == 200


def test_auth_status_issues_csrf_token_for_authenticated_user(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _user, auth_cookie = create_authenticated_user("status-csrf")
    from app.main import app

    with TestClient(app) as client:
        client.cookies.set(settings.AUTH_COOKIE_NAME, auth_cookie)
        response = client.get("/api/auth/status")

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["csrfToken"]
    assert settings.CSRF_COOKIE_NAME in response.cookies


def test_csrf_rejects_untrusted_origin(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _user, auth_cookie = create_authenticated_user("origin")
    from app.main import app

    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "csrf-origin")
        response = client.post(
            "/api/control/resume-entries",
            headers={**headers, "Origin": "https://evil.example"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Request origin is not allowed."


def test_webhook_is_csrf_exempt_but_requires_webhook_auth(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        response = client.post(
            "/webhook/tradingview",
            json={"secret": "unknown", "signal_id": "unknown", "action": "ENTRY"},
        )

    assert response.status_code == 403
    assert response.json()["status"] == "UNAUTHORIZED"


def test_cross_user_state_credentials_and_api_reads_are_isolated(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    user_a, cookie_a = create_authenticated_user("isolation-a")
    user_b, cookie_b = create_authenticated_user("isolation-b")
    secret_a = secrets.token_urlsafe(32)

    token = set_current_user_id(user_a.id)
    try:
        state_store.set_engine_mode("paper")
        state_store.update_runtime_settings(paper_starting_balance=125000)
        state_store.set_open_position(
            {
                **state_store.default_open_position(),
                "has_open_position": True,
                "trading_symbol": "USER-A-ONLY",
                "qty": 1,
            }
        )
        save_dhan_credentials("DHAN-A", "TOKEN-A")
        save_webhook_secret(secret_a)
    finally:
        reset_current_user_id(token)

    token = set_current_user_id(user_b.id)
    try:
        state_store.set_engine_mode("paper")
    finally:
        reset_current_user_id(token)

    monkeypatch.setattr(
        "app.routers.positions.get_reconciled_open_position",
        lambda **_kwargs: state_store.get_open_position(),
    )
    from app.main import app

    with TestClient(app) as client:
        headers_a = authenticate_client(client, cookie_a, "csrf-a")
        setup_a = client.get("/api/setup/status", headers=headers_a)
        position_a = client.get("/api/positions", headers=headers_a)

        headers_b = authenticate_client(client, cookie_b, "csrf-b")
        setup_b = client.get("/api/setup/status", headers=headers_b)
        position_b = client.get("/api/positions", headers=headers_b)
        orders_b = client.get("/api/orders", headers=headers_b)

    assert setup_a.status_code == 200
    assert setup_a.json()["dhan_client_id_masked"].endswith("AN-A")
    assert position_a.json()["trading_symbol"] == "USER-A-ONLY"
    assert setup_b.status_code == 200
    assert setup_b.json()["dhan_connected"] is False
    assert setup_b.json()["paper_portfolio"]["available_balance"] == 100000.0
    assert position_b.json()["has_open_position"] is False
    assert orders_b.json() == []


def test_webhook_secret_binds_only_owning_user_runtime(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    user_a, _cookie_a = create_authenticated_user("webhook-a")
    user_b, _cookie_b = create_authenticated_user("webhook-b")
    secret_a = secrets.token_urlsafe(32)

    token = set_current_user_id(user_a.id)
    try:
        state_store.set_engine_mode("paper")
        state_store.update_app_state(engine_started=True, webhook_trading_enabled=True, state="WAITING_ENTRY")
        save_webhook_secret(secret_a)
    finally:
        reset_current_user_id(token)

    token = set_current_user_id(user_b.id)
    try:
        state_store.set_engine_mode("paper")
        state_store.update_app_state(engine_started=True, webhook_trading_enabled=True, state="USER_B_UNCHANGED")
    finally:
        reset_current_user_id(token)

    routed_users: list[str | None] = []
    monkeypatch.setattr(
        "app.routers.webhook.route_signal",
        lambda _payload: routed_users.append(current_user_id()) or {"success": True, "message": "paper"},
    )
    from app.main import app

    payload = {
        "secret": secret_a,
        "signal_id": f"isolation-{secrets.token_hex(4)}",
        "strategy_code": "TRADINGVIEW_NIFTY_V1",
        "action": "ENTRY",
        "side": "BUY",
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "exchange_segment": "NSE_FNO",
        "security_id": "123456",
        "trading_symbol": "NIFTY TEST CALL",
        "option_side": "CE",
        "strike": 22500,
        "expiry": "2099-05-28",
        "qty": 1,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
    }
    with TestClient(app) as client:
        response = client.post("/webhook/tradingview", json=payload)

    token = set_current_user_id(user_b.id)
    try:
        user_b_state = state_store.get_app_state()
    finally:
        reset_current_user_id(token)

    assert response.status_code == 200
    assert routed_users == [user_a.id]
    assert user_b_state["state"] == "USER_B_UNCHANGED"
