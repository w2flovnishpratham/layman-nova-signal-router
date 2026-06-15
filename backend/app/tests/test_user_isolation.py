"""Cross-user isolation: one user can never read or control another's data."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _client(current):
    from app.auth.dependencies import get_current_user
    from app.routers import live, user_credentials

    app = FastAPI()
    app.include_router(user_credentials.router)
    app.include_router(live.router)
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    return TestClient(app)


def _current_user(model):
    from app.services.user_context import current_user_from_model

    return current_user_from_model(model)


def test_user_a_cannot_read_user_b_credentials(mu_db):
    from app.services import user_credential_vault as vault

    alice = make_user("alice@gmail.com")
    bob = make_user("bob@gmail.com")
    vault.save_user_credentials(alice.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-aaaaaaaaaaaaaaaaa")

    current = {"user": _current_user(bob)}
    client = _client(current)
    status = client.get("/api/user/credentials/status").json()
    assert status["has_dhan_access_token"] is False
    assert status["dhan_client_id_masked"] is None
    # Direct server-side read for Bob also returns nothing.
    assert vault.get_user_dhan_credentials(bob.id) is None


def test_user_a_cannot_stop_user_b_run(mu_db, monkeypatch):
    from app.config import settings
    from app.services import live_engine, user_credential_vault as vault

    # Both users need credentials so signal_only is allowed.
    alice = make_user("alice@gmail.com")
    bob = make_user("bob@gmail.com")
    for u in (alice, bob):
        vault.save_user_credentials(u.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-aaaaaaaaaaaaaaaaa")

    alice_ctx = _current_user(alice)
    bob_ctx = _current_user(bob)

    started = live_engine.start_run(alice_ctx, strategy_name="s", execution_mode="signal_only", config={})
    assert started["ok"] is True
    run_id = started["run"]["run_id"]

    # Bob tries to stop Alice's run -> denied.
    current = {"user": bob_ctx}
    client = _client(current)
    resp = client.post("/api/live/stop", json={"run_id": run_id})
    assert resp.status_code == 404

    # Alice's run is still running, and Bob sees none of her runs.
    assert live_engine.get_user_status(alice_ctx)["active_run_count"] == 1
    assert live_engine.get_user_status(bob_ctx)["active_run_count"] == 0

    # Alice can stop her own run.
    current["user"] = alice_ctx
    resp = client.post("/api/live/stop", json={"run_id": run_id})
    assert resp.status_code == 200


def test_user_a_cannot_see_user_b_logs(mu_db):
    from app.services import live_engine, user_credential_vault as vault

    alice = make_user("alice@gmail.com")
    bob = make_user("bob@gmail.com")
    vault.save_user_credentials(alice.id, dhan_client_id="1100123456", dhan_access_token="eyJ0eXA-aaaaaaaaaaaaaaaaa")
    alice_ctx = _current_user(alice)
    bob_ctx = _current_user(bob)
    live_engine.start_run(alice_ctx, strategy_name="s", execution_mode="signal_only", config={})

    assert len(live_engine.get_user_logs(alice_ctx)) > 0
    assert live_engine.get_user_logs(bob_ctx) == []
