# ruff: noqa: F811
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _seed_identity(user, *, mode: str = "paper") -> dict[str, str | int | bool]:
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        catalog = models.StrategyCatalog(
            code=f"start-{user.id.hex[:8]}-{mode}",
            display_name="Start",
            owner_type="personal",
            owner_user_id=user.id,
            status="active",
        )
        db.add(catalog)
        db.flush()
        version = models.StrategyVersion(
            strategy_id=catalog.id,
            version="1.0.0",
            payload_spec_version="nova.v1",
            source_journey="personal_tradingview",
            status="approved",
            execution_kind="external_webhook",
        )
        db.add(version)
        db.flush()
        instance = models.StrategyInstance(
            user_id=user.id,
            strategy_id=catalog.id,
            strategy_version_id=version.id,
            source_journey="PERSONAL_TRADINGVIEW",
            label="Start",
            status="active",
            execution_mode="paper_live_data" if mode == "paper" else "real_orders",
            current_lots=1,
        )
        db.add(instance)
        db.flush()
        revision = models.StrategyConfigurationRevision(
            user_id=user.id,
            strategy_instance_id=instance.id,
            strategy_version_id=version.id,
            mode=mode,
            revision=1,
            configuration_json={
                "lots": 1,
                "direction": "BOTH",
                "stop_loss_percent": 10,
                "take_profit_percent": 20,
            },
            risk_json={},
            status="active",
        )
        db.add(revision)
        db.flush()
        return {
            "strategy_instance_id": str(instance.id),
            "strategy_version_id": str(version.id),
            "configuration_revision_id": str(revision.id),
            "configuration_revision": 1,
            "mode": mode,
            "live_acknowledged": False,
        }


def _body(payload):
    from app.routers.engine import StartSelectedRequest

    return StartSelectedRequest(**payload)


def _install_fake_start(monkeypatch, user, calls):
    from app.routers import engine

    monkeypatch.setattr(engine, "_execution_user", lambda: user)
    monkeypatch.setattr(engine, "log_audit_event", lambda *_args, **_kwargs: None)

    def start_once(_body, _user):
        calls.append(_body.model_dump())
        return {
            "ok": True,
            "engine": {"state": "RUNNING", "mode": _body.mode},
        }, None

    monkeypatch.setattr(engine, "_runtime_start_selected_once", start_once)


def test_live_acknowledgement_is_durable_and_changed_consent_conflicts(
    mu_db,
    monkeypatch,
):
    from app.routers import engine
    from app.services.user_context import current_user_from_model

    user = current_user_from_model(make_user("start-live@example.com"))
    payload = _seed_identity(user, mode="live")
    calls = []
    _install_fake_start(monkeypatch, user, calls)

    with pytest.raises(HTTPException) as missing:
        engine.runtime_start_selected(_body(payload), idempotency_key="live-attempt")
    assert missing.value.status_code == 409
    assert missing.value.detail["reason"] == "LIVE_ACKNOWLEDGEMENT_REQUIRED"
    assert calls == []

    changed = {**payload, "live_acknowledged": True}
    with pytest.raises(HTTPException) as conflict:
        engine.runtime_start_selected(_body(changed), idempotency_key="live-attempt")
    assert conflict.value.status_code == 409
    assert "different" in str(conflict.value.detail).lower()


def test_identical_replay_returns_one_stored_paper_result_without_readiness(
    mu_db,
    monkeypatch,
):
    from app.routers import engine
    from app.services.user_context import current_user_from_model

    user = current_user_from_model(make_user("start-paper-replay@example.com"))
    payload = _seed_identity(user)
    calls = []
    _install_fake_start(monkeypatch, user, calls)

    first = engine.runtime_start_selected(
        _body(payload),
        idempotency_key="paper-replay",
    )
    second = engine.runtime_start_selected(
        _body(payload),
        idempotency_key="paper-replay",
    )

    assert first == second
    assert first["start_operation_id"]
    assert len(calls) == 1


def test_reusing_key_with_changed_revision_conflicts(mu_db, monkeypatch):
    from app.routers import engine
    from app.services.user_context import current_user_from_model

    user = current_user_from_model(make_user("start-changed-revision@example.com"))
    payload = _seed_identity(user)
    calls = []
    _install_fake_start(monkeypatch, user, calls)
    engine.runtime_start_selected(_body(payload), idempotency_key="changed-revision")

    changed = {**payload, "configuration_revision": 2}
    with pytest.raises(HTTPException) as conflict:
        engine.runtime_start_selected(
            _body(changed),
            idempotency_key="changed-revision",
        )
    assert conflict.value.status_code == 409
    assert len(calls) == 1


def test_concurrent_distinct_keys_create_only_one_running_start(mu_db, monkeypatch):
    from app.routers import engine
    from app.services.user_context import current_user_from_model

    user = current_user_from_model(make_user("start-concurrent@example.com"))
    payload = _seed_identity(user)
    state = {"running": False}
    calls = []
    state_guard = threading.Lock()
    monkeypatch.setattr(engine, "_execution_user", lambda: user)
    monkeypatch.setattr(engine, "log_audit_event", lambda *_args, **_kwargs: None)

    def start_once(_body, _user):
        with state_guard:
            if state["running"]:
                raise HTTPException(status_code=409, detail="Engine already running.")
            state["running"] = True
            calls.append(_body.model_dump())
        time.sleep(0.05)
        return {
            "ok": True,
            "engine": {"state": "RUNNING", "mode": "paper"},
        }, None

    monkeypatch.setattr(engine, "_runtime_start_selected_once", start_once)

    def attempt(key):
        try:
            return engine.runtime_start_selected(_body(payload), idempotency_key=key)
        except HTTPException as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, ("concurrent-a", "concurrent-b")))

    assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, HTTPException) for outcome in outcomes) == 1
    assert len(calls) == 1
