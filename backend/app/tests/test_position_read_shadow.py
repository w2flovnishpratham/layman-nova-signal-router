from __future__ import annotations

import importlib

import pytest

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_position_shadow import ENTRY_POSITION, runtime_dirs  # noqa: F401


@pytest.fixture
def read_shadow(mu_db, runtime_dirs, monkeypatch):
    from app.config import settings
    from app.services import position_read_shadow
    shadow = importlib.reload(position_read_shadow)
    monkeypatch.setattr(settings, "POSITION_DB_READ_SHADOW_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "POSITION_DB_READ_SHADOW_SAMPLE_RATE", 1.0, raising=False)
    monkeypatch.setattr(settings, "POSITION_DB_READ_SHADOW_FAILURE_THRESHOLD", 2, raising=False)
    return shadow


def _bound(user):
    from app.services.execution_context import bind_execution_context
    from app.services.user_context import current_user_from_model
    return bind_execution_context(current_user_from_model(user))


def _seed(user):
    from app.services import position_operations as ops
    ctx = ops.OperationContext(user.id, "live")
    return ops.record_entry(ctx, source=ops.STRATEGY_ENTRY, signal_id="READ-1", order_id="ORD-1",
        requested_qty=75, filled_qty=75, fill_price=123.45, partial_fill=False,
        position_snapshot=dict(ENTRY_POSITION))


def test_disabled_returns_json_without_shadow_call(mu_db, runtime_dirs, monkeypatch):
    from app.config import settings
    from app.services import state_store
    monkeypatch.setattr(settings, "POSITION_DB_READ_SHADOW_ENABLED", False, raising=False)
    expected = dict(ENTRY_POSITION)
    state_store.set_live_open_position(expected)
    assert state_store.get_live_open_position() == expected


def test_match_returns_original_json(read_shadow, monkeypatch):
    from app.config import settings
    from app.services import state_store
    user = make_user("read-match@example.com")
    monkeypatch.setattr(settings, "POSITION_DB_READ_SHADOW_ENABLED", False, raising=False)
    with _bound(user): state_store.set_live_open_position(dict(ENTRY_POSITION))
    _seed(user)
    monkeypatch.setattr(settings, "POSITION_DB_READ_SHADOW_ENABLED", True, raising=False)
    with _bound(user): result = state_store.get_live_open_position()
    assert result == ENTRY_POSITION
    assert read_shadow.health()["matches"] == 1


def test_mismatch_and_sampling_never_change_json(read_shadow, monkeypatch):
    from app.config import settings
    from app.services import state_store
    user = make_user("read-mismatch@example.com")
    json_position = dict(ENTRY_POSITION, qty=50)
    monkeypatch.setattr(settings, "POSITION_DB_READ_SHADOW_ENABLED", False, raising=False)
    with _bound(user): state_store.set_live_open_position(json_position)
    _seed(user)
    monkeypatch.setattr(settings, "POSITION_DB_READ_SHADOW_ENABLED", True, raising=False)
    with _bound(user): assert state_store.get_live_open_position() == json_position
    assert read_shadow.health()["mismatch_categories"]["OPEN_QUANTITY_MISMATCH"] == 1
    monkeypatch.setattr(settings, "POSITION_DB_READ_SHADOW_SAMPLE_RATE", 0.0, raising=False)
    with _bound(user): assert state_store.get_live_open_position() == json_position
    assert read_shadow.health()["sample_skips"] == 1


def test_failure_opens_independent_read_breaker(read_shadow, monkeypatch):
    from app.services import position_store
    user = make_user("read-failure@example.com")
    original_write_state = position_store.breaker.snapshot()["state"]
    monkeypatch.setattr(read_shadow, "_query", lambda *a: (_ for _ in ()).throw(RuntimeError("db down")))
    with _bound(user):
        read_shadow.observe_json_position(dict(ENTRY_POSITION), "live")
        read_shadow.observe_json_position(dict(ENTRY_POSITION), "live")
        result = read_shadow.observe_json_position(dict(ENTRY_POSITION), "live")
    assert result["category"] == "DB_CIRCUIT_OPEN"
    assert position_store.breaker.snapshot()["state"] == original_write_state


def test_health_endpoint_admin_only_and_redacted(read_shadow, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.auth.dependencies import get_current_user
    from app.config import settings
    from app.routers import debug
    from app.services.user_context import current_user_from_model
    regular, admin = make_user("read-user@example.com"), make_user("read-admin@example.com", is_admin=True)
    current = {"user": regular}
    app = FastAPI(); app.include_router(debug.router, prefix="/api/debug")
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(current["user"])
    client = TestClient(app)
    monkeypatch.setattr(settings, "DEBUG_ENABLED", True, raising=False)
    assert client.get("/api/debug/position-read-shadow/health").status_code == 403
    current["user"] = admin
    response = client.get("/api/debug/position-read-shadow/health")
    assert response.status_code == 200
    for banned in ("user_id", "security_id", "postgres", "password", "database_url", "/users/"):
        assert banned not in response.text.lower()
