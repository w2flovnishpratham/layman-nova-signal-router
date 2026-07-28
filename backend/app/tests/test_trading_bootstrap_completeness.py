# ruff: noqa: F811
"""/api/trading/bootstrap must return the same complete RuntimeStatus shape
as /api/runtime/status (engine/exit/position/pnl/config/account/safety).

Regression for a real incident: the endpoint used to hand-pick only "engine"
and "position" off the runtime dict, silently omitting pnl/config/account/
safety/exit. Every frontend consumer (Header, EngineConfigCard, ...) trusts
RuntimeStatus is always complete, so the omission surfaced as an uncaught
crash in production once a network-boundary shape guard was added.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _client(user) -> TestClient:
    from app.auth.dependencies import get_current_user
    from app.routers import strategies
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    app.include_router(strategies.router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(user)
    return TestClient(app)


def test_trading_bootstrap_includes_every_runtime_status_field(mu_db):
    user = make_user("bootstrap-completeness@example.com")
    response = _client(user).get("/api/trading/bootstrap")
    assert response.status_code == 200, response.text
    body = response.json()
    for key in ("engine", "exit", "position", "pnl", "config", "account", "safety", "strategy_catalog"):
        assert key in body, f"missing key: {key}"
        assert body[key], f"empty/falsy value for key: {key}"
    # eligible_strategies/selected_strategy/selection_issue can legitimately be
    # empty/None for a fresh user -- only presence matters, not truthiness.
    for key in ("eligible_strategies", "selected_strategy", "selection_issue"):
        assert key in body, f"missing key: {key}"
