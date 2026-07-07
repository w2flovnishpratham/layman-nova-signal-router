from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.feature_flags import (
    CUSTOM_WEBHOOKS,
    FEATURE_FLAGS,
    MULTI_STRATEGY_FANOUT,
    MULTI_STRATEGY_MODEL,
    STRATEGY_CATALOG_UI,
    V2_PAPER_RUNNER_DEBUG,
    feature_flag_states,
    is_feature_enabled,
    parse_feature_flag_value,
)


@pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes", "on", " On "])
def test_parse_feature_flag_true_values(raw):
    assert parse_feature_flag_value(raw) is True


@pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", "off", " Off "])
def test_parse_feature_flag_false_values(raw):
    assert parse_feature_flag_value(raw, default=True) is False


@pytest.mark.parametrize("raw", [None, "", " ", "enabled", "definitely-secret"])
def test_parse_feature_flag_defaults_off_for_missing_empty_or_unknown(raw):
    assert parse_feature_flag_value(raw) is False


def test_feature_flags_default_off(monkeypatch):
    for flag in FEATURE_FLAGS:
        monkeypatch.delenv(flag, raising=False)

    assert feature_flag_states() == {flag: False for flag in FEATURE_FLAGS}


def test_feature_flags_are_env_driven(monkeypatch):
    monkeypatch.setenv(MULTI_STRATEGY_MODEL, "yes")
    monkeypatch.setenv(MULTI_STRATEGY_FANOUT, "0")
    monkeypatch.setenv(V2_PAPER_RUNNER_DEBUG, "true")
    monkeypatch.setenv(CUSTOM_WEBHOOKS, "on")
    monkeypatch.setenv(STRATEGY_CATALOG_UI, "no")

    assert is_feature_enabled(MULTI_STRATEGY_MODEL) is True
    assert is_feature_enabled(MULTI_STRATEGY_FANOUT) is False
    assert is_feature_enabled(V2_PAPER_RUNNER_DEBUG) is True
    assert is_feature_enabled(CUSTOM_WEBHOOKS) is True
    assert is_feature_enabled(STRATEGY_CATALOG_UI) is False


def test_unknown_feature_flag_name_fails():
    with pytest.raises(ValueError):
        is_feature_enabled("NOT_A_REAL_FLAG", env={})


def test_debug_feature_flags_endpoint_returns_only_bool_states(monkeypatch):
    from app.config import settings
    from app.routers import debug

    monkeypatch.setattr(settings, "DEBUG_ENABLED", True, raising=False)
    monkeypatch.setenv(MULTI_STRATEGY_MODEL, "true")
    monkeypatch.setenv(MULTI_STRATEGY_FANOUT, "false")
    monkeypatch.setenv(V2_PAPER_RUNNER_DEBUG, "true")
    monkeypatch.setenv(CUSTOM_WEBHOOKS, "secret-looking-value")
    monkeypatch.delenv(STRATEGY_CATALOG_UI, raising=False)

    app = FastAPI()
    app.include_router(debug.router, prefix="/api/debug")

    response = TestClient(app).get("/api/debug/feature-flags")

    assert response.status_code == 200
    assert response.json() == {
        MULTI_STRATEGY_MODEL: True,
        MULTI_STRATEGY_FANOUT: False,
        V2_PAPER_RUNNER_DEBUG: True,
        CUSTOM_WEBHOOKS: False,
        STRATEGY_CATALOG_UI: False,
    }
    assert all(isinstance(value, bool) for value in response.json().values())
