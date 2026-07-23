from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import engine
from app.services import strategy_instance_service
from app.services.user_context import dev_user


def _runtime(*, state: str = "STOPPED", position_open: bool = False) -> dict:
    return {
        "engine": {"state": state},
        "position": {"has_open_position": position_open},
    }


def test_selected_paper_engine_starts_only_after_explicit_endpoint_call(monkeypatch):
    user = dev_user()
    started = []
    selected = {
        "instance_id": "selected-owner-instance",
        "selectable": True,
        "paper_eligible": True,
        "source_type": "PERSONAL_TRADINGVIEW",
    }
    deactivated = []
    hydrated = {
        "ok": True,
        "selected_strategy": selected,
        "engine": {"state": "RUNNING", "mode": "paper"},
    }
    monkeypatch.setattr(engine, "_execution_user", lambda: user)
    monkeypatch.setattr(
        strategy_instance_service,
        "require_selected_engine_strategy",
        lambda user_id: selected,
    )
    monkeypatch.setattr(
        engine.strategy_catalog_service,
        "apply_selected_setup",
        lambda user_id, current: {
            "direction": "BOTH",
            "lots": 1,
            "stop_loss_percent": 10,
            "take_profit_percent": 20,
        },
    )
    monkeypatch.setattr(engine.runtime_reliability, "runtime_status", lambda current: _runtime())
    monkeypatch.setattr(engine.runtime_reliability, "configure_mode", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        engine.strategy_fanout,
        "set_subscription_active",
        lambda user_id, code, active: deactivated.append((code, active)),
    )
    monkeypatch.setattr(engine, "start_engine", lambda body: started.append(body))
    monkeypatch.setattr(engine, "_hydrated_runtime_status", lambda: hydrated)

    # Merely hydrating/validating the selection has no start side effect.
    strategy_instance_service.require_selected_engine_strategy(user.id)
    assert started == []

    result = engine.runtime_start_selected(engine.StartSelectedRequest(strategy_instance_id="selected-owner-instance"))
    assert result is hydrated
    assert len(started) == 1
    assert started[0].engine_mode == "paper"
    assert started[0].confirm_live_orders is False
    assert deactivated == [("supertrend", False)]


def test_selected_engine_start_fails_closed_when_selection_is_not_ready(monkeypatch):
    user = dev_user()
    started = []
    monkeypatch.setattr(engine, "_execution_user", lambda: user)

    def blocked(_user_id):
        raise strategy_instance_service.InstanceError(
            "The selected strategy is no longer Paper-ready.",
            status_code=409,
            code="INSTALLATION_SUSPENDED",
        )

    monkeypatch.setattr(strategy_instance_service, "require_selected_engine_strategy", blocked)
    monkeypatch.setattr(engine, "start_engine", lambda body: started.append(body))
    with pytest.raises(HTTPException) as caught:
        engine.runtime_start_selected(engine.StartSelectedRequest(strategy_instance_id="selected-owner-instance"))
    assert caught.value.status_code == 409
    assert caught.value.detail["reason"] == "INSTALLATION_SUSPENDED"
    assert started == []


@pytest.mark.parametrize(
    ("state", "position_open"),
    [("RUNNING", False), ("STARTING", False), ("STOPPED", True)],
)
def test_selected_engine_start_obeys_runtime_switch_guards(monkeypatch, state, position_open):
    user = dev_user()
    started = []
    monkeypatch.setattr(engine, "_execution_user", lambda: user)
    monkeypatch.setattr(
        strategy_instance_service,
        "require_selected_engine_strategy",
        lambda user_id: {"instance_id": "selected", "paper_eligible": True, "selectable": True},
    )
    monkeypatch.setattr(
        engine.strategy_catalog_service,
        "apply_selected_setup",
        lambda user_id, current: {
            "direction": "BOTH",
            "lots": 1,
            "stop_loss_percent": 10,
            "take_profit_percent": 20,
        },
    )
    monkeypatch.setattr(
        engine.runtime_reliability,
        "runtime_status",
        lambda current: _runtime(state=state, position_open=position_open),
    )
    monkeypatch.setattr(engine, "start_engine", lambda body: started.append(body))
    with pytest.raises(HTTPException) as caught:
        engine.runtime_start_selected(engine.StartSelectedRequest(strategy_instance_id="selected"))
    assert caught.value.status_code == 409
    assert started == []


def test_selected_engine_start_requires_matching_confirmed_identity(monkeypatch):
    user = dev_user()
    monkeypatch.setattr(engine, "_execution_user", lambda: user)
    monkeypatch.setattr(
        strategy_instance_service,
        "require_selected_engine_strategy",
        lambda _user_id: {
            "instance_id": "current-selection",
            "paper_eligible": True,
            "selectable": True,
        },
    )
    monkeypatch.setattr(
        engine.strategy_catalog_service,
        "apply_selected_setup",
        lambda *_args: {
            "direction": "BOTH",
            "lots": 1,
            "stop_loss_percent": 10,
            "take_profit_percent": 20,
        },
    )
    with pytest.raises(HTTPException) as caught:
        engine.runtime_start_selected(
            engine.StartSelectedRequest(strategy_instance_id="stale-selection")
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["reason"] == "STRATEGY_CONFIRMATION_MISMATCH"
