from __future__ import annotations

import importlib

import pytest


@pytest.fixture(autouse=True)
def legacy_routes_use_dev_auth(monkeypatch):
    """Keep tests isolated from credentials and security flags in local .env."""
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_REQUIRED", False, raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", False, raising=False)


@pytest.fixture(autouse=True)
def tests_do_not_use_real_database(monkeypatch):
    """Default tests to no DATABASE_URL unless a DB fixture opts in.

    Local .env files may point at a real Neon database. Unit tests must never
    touch it accidentally; multi-user tests opt back in via the mu_db fixture.
    """
    from app.config import settings
    from app.db import engine as db_engine

    monkeypatch.setattr(settings, "DATABASE_URL", "", raising=False)
    db_engine.reset_engine_for_tests()
    yield
    db_engine.reset_engine_for_tests()


# Every module that binds `_market_is_open` (imported from risk_manager).
_MARKET_GUARD_MODULES = (
    "app.services.risk_manager",
    "app.services.dhan_client",
    "app.services.execution_router",
    "app.services.option_position_monitor",
    "app.services.atm_ltp_service",
    "app.services.paper_broker",
    "app.routers.orders",
    "app.routers.debug",
    "app.routers.market",
)


@pytest.fixture(autouse=True)
def market_hours_open_by_default(monkeypatch):
    """Make the NSE market-hours guard deterministic.

    `_market_is_open()` is wall-clock based, so any test that exercises an order
    or LTP path would otherwise pass only when CI happens to run during Indian
    market hours (09:15-15:30 IST). We default it to "open" everywhere so the
    suite is time-of-day independent.

    Tests that deliberately verify market-closed behaviour already override the
    relevant module's `_market_is_open` to return False, and those explicit
    patches take precedence over this autouse default.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "REQUIRE_MARKET_HOURS", False, raising=False)
    for module_name in _MARKET_GUARD_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        if hasattr(module, "_market_is_open"):
            monkeypatch.setattr(module, "_market_is_open", lambda: True, raising=False)
