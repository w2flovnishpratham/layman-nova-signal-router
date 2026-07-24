# ruff: noqa: F811
"""Automations: four editable rules, five protected ones, no execution bypass."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services import automations_overview, risk_manager, state_store
from app.tests.test_runtime_reliability import runtime  # noqa: F401


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
        # Nothing writable: no value, no bounds, nothing the UI could submit.
        assert "value" not in rule
        assert rule["why_protected"]


def test_a_protected_rule_cannot_be_disabled(runtime):
    for attempt in (
        {"eod_squareoff_enabled": False},
        {"server_side_exit_enabled": False},
        {"stale_feed_protection": False},
        {"duplicate_exit_protection": False},
    ):
        with pytest.raises(automations_overview.AutomationError) as excinfo:
            automations_overview.validate_automation_changes(attempt)
        assert "protected system rules" in str(excinfo.value)


def test_no_automation_table_backs_these_rules(runtime):
    assert automations_overview.build_automations_overview()["storage"] == "runtime_settings"


def test_every_editable_rule_declares_its_effect_timing(runtime):
    for rule in automations_overview.build_automations_overview()["editable"]:
        assert rule["effect"] in {
            automations_overview.APPLIES_IMMEDIATELY,
            automations_overview.APPLIES_NEXT_ENTRY,
            automations_overview.REQUIRES_RESTART,
        }
        assert rule["unit"]
        assert rule["basis"]
        assert isinstance(rule["requires_restart"], bool)
        assert isinstance(rule["affects_open_position"], bool)


def test_the_daily_loss_cap_admits_it_can_touch_the_open_position(runtime):
    rules = {r["key"]: r for r in automations_overview.build_automations_overview()["editable"]}
    assert rules["max_daily_loss"]["affects_open_position"] is True
    assert rules["cooldown_after_loss_minutes"]["affects_open_position"] is False


def test_editable_rules_respect_their_bounds(runtime):
    with pytest.raises(automations_overview.AutomationError):
        automations_overview.validate_automation_changes({"cooldown_after_loss_minutes": 5000})
    with pytest.raises(automations_overview.AutomationError):
        automations_overview.validate_automation_changes({"max_trades_per_day": 500})
    with pytest.raises(automations_overview.AutomationError):
        automations_overview.validate_automation_changes({"entry_cutoff_ist": "25:00"})

    clean = automations_overview.validate_automation_changes({
        "cooldown_after_loss_minutes": 45,
        "max_trades_per_day": 8,
        "max_daily_loss": 25000,
        "entry_cutoff_ist": "3:05",
    })
    assert clean["cooldown_after_loss_minutes"] == 45
    assert clean["entry_cutoff_ist"] == "03:05"  # normalised


def test_an_empty_cutoff_disables_it_rather_than_blocking_everything(runtime):
    assert automations_overview.validate_automation_changes({"entry_cutoff_ist": ""})["entry_cutoff_ist"] == ""
    assert risk_manager._entry_cutoff_check({"entry_cutoff_ist": ""}) is None
    # A malformed value must not wedge entries either.
    assert risk_manager._entry_cutoff_check({"entry_cutoff_ist": "not-a-time"}) is None


def test_the_cutoff_blocks_entries_after_the_time_and_never_before(runtime, monkeypatch):
    """Clock is injected, not read.

    Deriving the "future" cutoff from the real clock wrapped past midnight when
    the suite ran at 23:49, turning 00:49 into an *earlier* wall-clock time and
    failing a correct implementation. A cutoff has no date, so the test must not
    depend on what time it happens to run.
    """
    fixed_now = datetime(2026, 7, 24, 14, 30, tzinfo=risk_manager.IST)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(risk_manager, "datetime", FrozenDatetime)

    blocked = risk_manager._entry_cutoff_check({"entry_cutoff_ist": "14:25"})
    assert blocked is not None and blocked.allowed is False
    assert "entries are closed" in blocked.reason
    # Exactly at the cutoff, entries are already closed.
    assert risk_manager._entry_cutoff_check({"entry_cutoff_ist": "14:30"}) is not None
    assert risk_manager._entry_cutoff_check({"entry_cutoff_ist": "15:20"}) is None


def test_a_cooldown_change_reaches_the_entry_gate(runtime):
    """An edited rule must actually change behaviour, not just the display."""
    data = state_store.get_daily_risk()
    data["last_loss_exit_at"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    state_store._write_json(state_store._daily_risk_file(), data)

    assert risk_manager._cooldown_after_loss_check({"cooldown_after_loss_minutes": 5}) is None
    blocked = risk_manager._cooldown_after_loss_check({"cooldown_after_loss_minutes": 45})
    assert blocked is not None and blocked.allowed is False
