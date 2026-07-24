# ruff: noqa: F811
"""Preferences: validated, owner scoped, and never touching engine settings."""
from __future__ import annotations

import pytest

from app.services import state_store, user_preferences
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_runtime_reliability import runtime  # noqa: F401


def test_an_owner_with_no_row_gets_defaults(mu_db):
    user = make_user("pref-default@example.com")
    prefs = user_preferences.get_preferences(user.id)
    assert prefs["timezone"] == "Asia/Kolkata"
    assert prefs["table_density"] == "comfortable"
    assert prefs["stored"] is False
    assert prefs["revision"] == 0


def test_preferences_survive_a_reload(mu_db):
    user = make_user("pref-save@example.com")
    user_preferences.save_preferences(user.id, {"table_density": "compact", "reduced_motion": True})

    restored = user_preferences.get_preferences(user.id)
    assert restored["table_density"] == "compact"
    assert restored["reduced_motion"] is True
    assert restored["stored"] is True
    assert restored["revision"] == 1


def test_each_save_advances_the_revision(mu_db):
    user = make_user("pref-rev@example.com")
    first = user_preferences.save_preferences(user.id, {"table_density": "compact"})
    second = user_preferences.save_preferences(user.id, {"default_chart_timeframe": "15m"})
    assert (first["revision"], second["revision"]) == (1, 2)
    # A partial save leaves the other fields alone.
    assert second["table_density"] == "compact"


def test_unsupported_values_are_rejected(mu_db):
    user = make_user("pref-bad@example.com")
    for bad in (
        {"table_density": "microscopic"},
        {"default_chart_timeframe": "7s"},
        {"timezone": "Mars/Olympus"},
        {"notification_preferences": {"telegram_dm": True}},
    ):
        with pytest.raises(user_preferences.PreferenceError):
            user_preferences.save_preferences(user.id, bad)


def test_notification_channels_admit_they_deliver_nothing(mu_db):
    user = make_user("pref-notify@example.com")
    channels = user_preferences.get_preferences(user.id)["channels"]
    assert channels, "channels must be declared so the page cannot invent its own"
    for channel in channels:
        assert channel["available"] is False
        assert "stored only" in channel["reason"]


def test_one_owner_never_reads_another(mu_db):
    alice = make_user("pref-alice@example.com")
    bob = make_user("pref-bob@example.com")
    user_preferences.save_preferences(alice.id, {"table_density": "compact"})
    assert user_preferences.get_preferences(bob.id)["table_density"] == "comfortable"


def test_saving_a_preference_never_touches_engine_settings(mu_db, runtime):
    """A presentation change must not bump the configuration revision."""
    user = make_user("pref-isolation@example.com")
    before = dict(state_store.get_runtime_settings())

    user_preferences.save_preferences(user.id, {"reduced_motion": True, "table_density": "compact"})

    after = state_store.get_runtime_settings()
    assert after["_version"] == before["_version"]
    assert after["configuration_revision"] == before["configuration_revision"]
    assert after["max_daily_loss"] == before["max_daily_loss"]
