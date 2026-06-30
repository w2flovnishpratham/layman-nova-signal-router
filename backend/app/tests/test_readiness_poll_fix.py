"""Regression: default runtime config must pass readiness, and the failure
summary must surface the *specific* blocker, not a vague boolean label.

Background: option_ltp_poll_seconds shipped as 0.5 while risk_settings_valid()
(and the setup API schemas) require >= 1. A user who configured the engine via
the chat flow — which never writes that field — was blocked at engine start
with the misleading "Missing: risk configured" instead of the real reason.
"""
from __future__ import annotations

import json

from app.config import DEFAULT_RUNTIME_SETTINGS
from app.routers.setup import risk_settings_valid
from app.services import state_store


def test_default_runtime_config_passes_risk_validator():
    # Every shipped default must satisfy its own validator, otherwise a chat-only
    # setup (which never writes these fields) is blocked forever.
    ok, issues = risk_settings_valid(dict(DEFAULT_RUNTIME_SETTINGS))
    assert ok, f"default config fails readiness: {issues}"


def test_poll_seconds_default_meets_floor():
    assert float(DEFAULT_RUNTIME_SETTINGS["option_ltp_poll_seconds"]) >= 1.0


def test_persisted_legacy_poll_seconds_self_heals(tmp_path, monkeypatch):
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    state_dir.mkdir()
    log_dir.mkdir()
    monkeypatch.setattr(state_store, "RUNTIME_STATE_DIR", state_dir)
    monkeypatch.setattr(state_store, "RUNTIME_LOG_DIR", log_dir)
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")

    legacy = dict(DEFAULT_RUNTIME_SETTINGS)
    legacy["option_ltp_poll_seconds"] = 0.5
    state_store.SETTINGS_FILE.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = state_store.get_runtime_settings()
    persisted = json.loads(state_store.SETTINGS_FILE.read_text(encoding="utf-8"))

    assert loaded["option_ltp_poll_seconds"] == 1.0
    assert persisted["option_ltp_poll_seconds"] == 1.0


def test_summary_surfaces_specific_issue_over_boolean_key():
    from app.api.ws import _readiness_failure_summary

    detail = {
        "readiness": {
            "ready": False,
            "risk_configured": False,
            "issues": ["Option LTP poll seconds must be at least 1."],
        }
    }
    msg = _readiness_failure_summary(detail)
    assert "Option LTP poll seconds must be at least 1." in msg
    # The vague boolean label is suppressed when specific reasons exist.
    assert "risk configured" not in msg


def test_summary_falls_back_to_boolean_keys_without_specifics():
    from app.api.ws import _readiness_failure_summary

    detail = {"readiness": {"ready": False, "risk_configured": False}}
    msg = _readiness_failure_summary(detail)
    assert "risk configured" in msg
