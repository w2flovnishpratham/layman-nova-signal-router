# ruff: noqa: F811
"""Automation edits create one immutable revision and never touch open state."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import automations_overview, risk_manager, state_store
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_runtime_reliability import runtime  # noqa: F401

SETUP = {
    "direction": "BOTH",
    "lots": 2,
    "stop_loss_percent": 8.5,
    "take_profit_percent": 17,
}
RISK = {"max_daily_loss": 10_000, "max_trades_per_day": 3, "entry_cutoff_ist": "15:15"}


def _client(user) -> TestClient:
    from app.auth.dependencies import get_current_user
    from app.routers import signals, strategies
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    app.include_router(strategies.router)
    app.include_router(signals.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(user)
    return TestClient(app)


def _ready(email: str):
    from app.services.strategy_registry import backfill_supertrend

    backfill_supertrend()
    user = make_user(email)
    client = _client(user)
    assert client.put(
        "/api/strategies/catalog/selection",
        json={"strategy_key": "nova-supertrend"},
    ).status_code == 200
    saved = client.put(
        "/api/setup/configuration",
        json={
            "strategy_key": "nova-supertrend",
            "mode": "paper",
            "setup": SETUP,
            "risk": RISK,
            "expected_revision": 0,
        },
    )
    assert saved.status_code == 200, saved.text
    return user, client, saved.json()


def test_protected_rules_are_visible_but_carry_no_toggle(runtime):
    overview = automations_overview.build_automations_overview()
    keys = {rule["key"] for rule in overview["protected"]}
    assert keys == {
        "stale_feed_protection",
        "duplicate_exit_protection",
        "confirmed_flat_handling",
        "daily_loss_breaker",
        "eod_square_off",
    }
    for rule in overview["protected"]:
        assert rule["effect"] == automations_overview.PROTECTED
        assert "value" not in rule
        assert rule["why_protected"]


def test_editable_rules_apply_to_future_entries_only(runtime):
    rules = {rule["key"]: rule for rule in automations_overview.build_automations_overview()["editable"]}
    assert set(rules) == {"max_trades_per_day", "max_daily_loss", "entry_cutoff_ist"}
    assert all(rule["effect"] == automations_overview.APPLIES_NEXT_ENTRY for rule in rules.values())
    assert all(rule["affects_open_position"] is False for rule in rules.values())


def test_protected_or_removed_rules_cannot_be_submitted(runtime):
    for attempt in (
        {"eod_squareoff_enabled": False},
        {"server_side_exit_enabled": False},
        {"cooldown_after_loss_minutes": 30},
        {"stale_feed_protection": False},
    ):
        with pytest.raises(automations_overview.AutomationError):
            automations_overview.validate_automation_changes(attempt)


def test_editable_rules_validate_and_normalize(runtime):
    with pytest.raises(automations_overview.AutomationError):
        automations_overview.validate_automation_changes({"max_trades_per_day": 500})
    with pytest.raises(automations_overview.AutomationError):
        automations_overview.validate_automation_changes({"entry_cutoff_ist": "25:00"})
    clean = automations_overview.validate_automation_changes(
        {"max_trades_per_day": 8, "max_daily_loss": 25_000, "entry_cutoff_ist": "3:05"}
    )
    assert clean == {
        "max_trades_per_day": 8,
        "max_daily_loss": 25_000.0,
        "entry_cutoff_ist": "03:05",
    }


def test_empty_or_malformed_cutoff_never_wedges_entries(runtime):
    assert automations_overview.validate_automation_changes(
        {"entry_cutoff_ist": ""}
    )["entry_cutoff_ist"] == ""
    assert risk_manager._entry_cutoff_check({"entry_cutoff_ist": ""}) is None
    assert risk_manager._entry_cutoff_check({"entry_cutoff_ist": "not-a-time"}) is None


def test_one_confirmed_batch_creates_exactly_one_successor(mu_db, runtime):
    _, client, saved = _ready("automation-batch@example.com")
    response = client.put(
        "/api/automations",
        json={
            "configuration_id": saved["configuration_revision_id"],
            "expected_revision": saved["configuration_revision"],
            "changes": {"max_daily_loss": 12_000, "max_trades_per_day": 4},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configuration_revision"] == 2
    assert body["confirmed_revision"]["revision"] == 2
    assert {rule["key"]: rule["value"] for rule in body["editable"]}["max_daily_loss"] == 12_000


def test_stale_confirmation_creates_no_revision(mu_db, runtime):
    _, client, saved = _ready("automation-stale@example.com")
    payload = {
        "configuration_id": saved["configuration_revision_id"],
        "expected_revision": 1,
        "changes": {"max_daily_loss": 12_000},
    }
    assert client.put("/api/automations", json=payload).status_code == 200
    stale = client.put("/api/automations", json=payload)
    assert stale.status_code == 409
    assert client.get("/api/automations").json()["configuration_revision"] == 2


def test_confirmed_revision_does_not_write_runtime_or_position(mu_db, runtime):
    _, client, saved = _ready("automation-position@example.com")
    state_store.set_live_open_position(
        {
            **state_store.default_open_position(),
            "status": "OPEN",
            "symbol": "NIFTY TEST CE",
            "qty": 65,
            "entry_price": 100.0,
            "stop_loss_price": 90.0,
            "target_price": 120.0,
            "position_version": 7,
        }
    )
    position_path = Path(state_store.scoped_runtime_path(state_store.OPEN_POSITION_FILE))
    position_before = position_path.read_bytes()
    runtime_before = dict(state_store.get_runtime_settings())

    response = client.put(
        "/api/automations",
        json={
            "configuration_id": saved["configuration_revision_id"],
            "expected_revision": 1,
            "changes": {"max_daily_loss": 12_000, "entry_cutoff_ist": "14:55"},
        },
    )
    assert response.status_code == 200, response.text
    assert position_path.read_bytes() == position_before
    assert state_store.get_runtime_settings() == runtime_before
