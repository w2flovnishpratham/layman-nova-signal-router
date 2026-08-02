# ruff: noqa: F811
from __future__ import annotations

import json
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_private_webhook import _make_instance
from app.tests.test_runtime_reliability import runtime  # noqa: F401


def _client(user) -> TestClient:
    from app.auth.dependencies import get_current_user
    from app.routers import strategies
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    app.include_router(strategies.router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(user)
    return TestClient(app)


def _seed() -> None:
    from app.services.strategy_registry import backfill_supertrend

    backfill_supertrend()


def _counts() -> tuple[int, int, int]:
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        return (
            db.scalar(select(func.count()).select_from(models.LiveOrderIntent)),
            db.scalar(select(func.count()).select_from(models.StrategyInstancePosition)),
            db.scalar(select(func.count()).select_from(models.StrategyExecutionJob)),
        )


def test_catalog_returns_deterministic_built_ins_with_honest_availability(mu_db, runtime):
    _seed()
    user = make_user("catalog-builtins@example.com")
    response = _client(user).get("/api/strategies/catalog")
    assert response.status_code == 200
    body = response.json()
    by_key = {item["strategy_key"]: item for item in body["strategies"]}

    assert list(by_key)[:5] == [
        "nova-supertrend",
        "nova-orb",
        "nova-vwap",
        "nova-rsi",
        "nova-scalper",
    ]
    assert by_key["nova-supertrend"]["availability"] == "READY"
    assert by_key["nova-supertrend"]["paper_eligible"] is True
    # Supertrend is the only built-in with a real execution adapter and has
    # been deliberately qualified for live trading (owner sign-off, 2026-07-29).
    assert by_key["nova-supertrend"]["live_eligible"] is True
    for key in ("nova-orb", "nova-vwap", "nova-rsi", "nova-scalper"):
        assert by_key[key]["availability"] == "COMING_SOON"
        assert by_key[key]["paper_eligible"] is False
        assert by_key[key]["disabled_reason"] == "Missing execution adapter"
        assert by_key[key]["live_eligible"] is False


def test_catalog_is_owner_scoped_and_exposes_no_secret_or_source(mu_db, runtime):
    _seed()
    owner = make_user("catalog-owner@example.com")
    stranger = make_user("catalog-stranger@example.com")
    instance_id = _make_instance(owner, label="Owner Pine", status="ready")

    owner_body = _client(owner).get("/api/strategies/catalog").json()
    stranger_body = _client(stranger).get("/api/strategies/catalog").json()
    assert instance_id in {item["strategy_instance_id"] for item in owner_body["strategies"]}
    assert instance_id not in {item["strategy_instance_id"] for item in stranger_body["strategies"]}

    serialized = json.dumps(owner_body).lower()
    assert "token_hash" not in serialized
    assert "pine_source" not in serialized
    assert "access_token" not in serialized


def test_builtin_selection_persists_without_execution_side_effects(mu_db, runtime):
    from app.db import models
    from app.db.engine import session_scope

    _seed()
    user = make_user("catalog-select@example.com")
    client = _client(user)
    before = _counts()
    selected = client.put(
        "/api/strategies/catalog/selection",
        json={"strategy_key": "nova-supertrend"},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["selected_strategy_key"] == "nova-supertrend"
    assert _counts() == before

    catalog = client.get("/api/strategies/catalog").json()
    assert catalog["selected_strategy_key"] == "nova-supertrend"
    selected_entry = next(item for item in catalog["strategies"] if item["strategy_key"] == "nova-supertrend")
    assert selected_entry["selected"] is True
    # Regression: the selected built-in must expose its real instance id, or the
    # client's Start-engine guard reads null and the click silently does nothing.
    assert selected_entry["strategy_instance_id"] is not None
    assert selected_entry["strategy_instance_id"] == catalog["selected_strategy_instance_id"]
    with session_scope() as db:
        subscription = db.scalar(
            select(models.StrategySubscription).where(
                models.StrategySubscription.user_id == user.id,
                models.StrategySubscription.strategy_name == "supertrend",
            )
        )
        assert subscription is not None
        assert subscription.active is False


def test_disabled_builtin_cannot_be_selected(mu_db, runtime):
    _seed()
    user = make_user("catalog-disabled@example.com")
    client = _client(user)
    response = client.put("/api/strategies/catalog/selection", json={"strategy_key": "nova-orb"})
    assert response.status_code == 409
    assert response.json()["reason"] == "STRATEGY_UNAVAILABLE"
    assert client.get("/api/strategies/catalog").json()["selected_strategy_key"] is None


def test_setup_schema_validation_and_atomic_rehydration(mu_db, runtime, monkeypatch):
    from app.routers import engine
    from app.services.user_context import current_user_from_model

    _seed()
    user = make_user("catalog-setup@example.com")
    client = _client(user)
    assert client.put("/api/strategies/catalog/selection", json={"strategy_key": "nova-supertrend"}).status_code == 200
    valid = {
        "direction": "BOTH",
        "lots": 2,
        "stop_loss_percent": 8.5,
        "take_profit_percent": 17,
    }
    saved = client.put(
        "/api/strategies/catalog/setup",
        json={"strategy_key": "nova-supertrend", "mode": "paper", "values": valid},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["values"]["lots"] == 2

    invalid = client.put(
        "/api/strategies/catalog/setup",
        json={
            "strategy_key": "nova-supertrend",
            "mode": "paper",
            "values": {**valid, "lots": 99},
        },
    )
    assert invalid.status_code == 422
    catalog = client.get("/api/strategies/catalog").json()
    supertrend = next(item for item in catalog["strategies"] if item["strategy_key"] == "nova-supertrend")
    assert supertrend["saved_setup"]["paper"]["lots"] == 2
    assert catalog["setup_progress"]["stage"] == "REVIEW"
    assert catalog["setup_progress"]["complete"] is True
    monkeypatch.setattr(engine, "_execution_user", lambda: current_user_from_model(user))
    hydrated = engine._hydrated_runtime_status()
    assert hydrated["config"]["paper"]["configured_lots"] == 2
    assert hydrated["config"]["paper"]["option_sl_percent"] == 8.5
    assert hydrated["config"]["paper"]["option_tp_percent"] == 17
    assert hydrated["config"]["paper"]["allowed_option_side"] == "BOTH"


def test_live_setup_remains_blocked_and_paper_configuration_unchanged(mu_db, runtime, monkeypatch):
    # Supertrend is live-eligible by explicit product decision (2026-07-29).
    # Force it back to ineligible for this test only, so this exercises the
    # LIVE_UNAVAILABLE gate itself rather than depending on which built-in
    # currently is/isn't eligible.
    from app.services import built_in_strategy_registry as built_ins

    patched = tuple(
        {**item, "live_eligible": False} if item["strategy_key"] == "nova-supertrend" else item
        for item in built_ins._BUILT_INS
    )
    monkeypatch.setattr(built_ins, "_BUILT_INS", patched)

    _seed()
    user = make_user("catalog-live@example.com")
    client = _client(user)
    client.put("/api/strategies/catalog/selection", json={"strategy_key": "nova-supertrend"})
    values = {"direction": "CE", "lots": 1, "stop_loss_percent": 10, "take_profit_percent": 20}
    client.put(
        "/api/strategies/catalog/setup",
        json={"strategy_key": "nova-supertrend", "mode": "paper", "values": values},
    )
    blocked = client.put(
        "/api/strategies/catalog/setup",
        json={"strategy_key": "nova-supertrend", "mode": "live", "values": values},
    )
    assert blocked.status_code == 409
    assert blocked.json()["reason"] == "LIVE_UNAVAILABLE"
    supertrend = next(
        item for item in client.get("/api/strategies/catalog").json()["strategies"]
        if item["strategy_key"] == "nova-supertrend"
    )
    assert supertrend["saved_setup"]["paper"]["direction"] == "CE"
    assert "live" not in supertrend["saved_setup"]


def test_builtin_explicit_start_uses_supertrend_adapter(mu_db, runtime, monkeypatch):
    from app.routers import engine
    from app.services import strategy_fanout, strategy_instance_service
    from app.services.user_context import current_user_from_model

    _seed()
    user = make_user("catalog-start@example.com")
    client = _client(user)
    client.put("/api/strategies/catalog/selection", json={"strategy_key": "nova-supertrend"})
    saved_configuration = client.put(
        "/api/setup/configuration",
        json={
            "strategy_key": "nova-supertrend",
            "mode": "paper",
            "setup": {"direction": "PE", "lots": 3, "stop_loss_percent": 9, "take_profit_percent": 18},
            "risk": {
                "max_daily_loss": 25_000,
                "max_trades_per_day": 6,
                "entry_cutoff_ist": "15:15",
            },
            "expected_revision": 0,
        },
    ).json()
    current = current_user_from_model(user)
    calls: list[tuple[str, int, str]] = []
    monkeypatch.setattr(engine, "_execution_user", lambda: current)
    monkeypatch.setattr(engine.entitlements, "require_paper_entitlement_for_user", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        engine.runtime_reliability,
        "runtime_status",
        lambda _user: {
            "engine": {"state": "STOPPED"},
            "position": {"has_open_position": False},
            "exit": {"state": "NONE"},
        },
    )
    monkeypatch.setattr(engine.runtime_reliability, "configure_mode", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(engine, "start_engine", lambda *_args, **_kwargs: {"success": True})
    monkeypatch.setattr(
        strategy_fanout,
        "subscribe_user",
        lambda _user_id, code, *, lots, execution_mode: calls.append((code, lots, execution_mode)),
    )
    monkeypatch.setattr(engine, "_hydrated_runtime_status", lambda: {"ok": True})

    selected = strategy_instance_service.trading_selection_state(current.id)["selected_strategy"]
    result = engine.runtime_start_selected(
        engine.StartSelectedRequest(
            strategy_instance_id=selected["instance_id"],
            strategy_version_id=selected["strategy_version_id"],
            configuration_revision_id=saved_configuration["configuration_revision_id"],
            configuration_revision=saved_configuration["configuration_revision"],
            mode="paper",
        ),
        idempotency_key="catalog-selected-start",
    )
    assert result["ok"] is True
    assert result["start_operation_id"]
    assert calls == [("supertrend", 3, "paper_live_data")]


def test_imported_strategy_key_is_instance_identity_and_other_owner_cannot_select(
    mu_db,
    runtime,
):
    _seed()
    owner = make_user("catalog-import-owner@example.com")
    stranger = make_user("catalog-import-stranger@example.com")
    instance_id = _make_instance(owner, label="Owner Pine", status="ready")

    owner_catalog = _client(owner).get("/api/strategies/catalog").json()
    imported = next(item for item in owner_catalog["strategies"] if item["strategy_instance_id"] == instance_id)
    assert imported["strategy_key"] == instance_id
    refused = _client(stranger).put(
        "/api/strategies/catalog/selection",
        json={"strategy_key": instance_id},
    )
    assert refused.status_code == 404


def test_selection_does_not_create_orders_positions_or_jobs(mu_db, runtime):
    _seed()
    user = make_user("catalog-no-execution@example.com")
    before = _counts()
    response = _client(user).put(
        "/api/strategies/catalog/selection",
        json={"strategy_key": "nova-supertrend"},
    )
    assert response.status_code == 200
    assert _counts() == before


def test_catalog_strategy_change_is_blocked_while_running_or_positioned(mu_db, runtime):
    from app.services import state_store
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    _seed()
    user = make_user("catalog-switch-guard@example.com")
    client = _client(user)
    assert client.put(
        "/api/strategies/catalog/selection",
        json={"strategy_key": "nova-supertrend"},
    ).status_code == 200

    current = current_user_from_model(user)
    with bind_user_execution_context(current):
        state_store.update_app_state(
            engine_lifecycle="RUNNING",
            engine_started=True,
            webhook_trading_enabled=True,
        )
    running = client.put(
        "/api/strategies/catalog/selection",
        json={"strategy_key": str(uuid.uuid4())},
    )
    assert running.status_code == 409
    assert running.json()["reason"] == "RECONFIGURE_REQUIRED"

    with bind_user_execution_context(current):
        state_store.update_app_state(
            engine_lifecycle="STOPPED",
            engine_started=False,
            webhook_trading_enabled=False,
        )
        state_store.set_paper_position(
            {
                "has_open_position": True,
                "security_id": "123",
                "trading_symbol": "NIFTY TEST CE",
                "option_side": "CE",
                "qty": 65,
                "lots": 1,
                "entry_price": 100,
            }
        )
    positioned = client.put(
        "/api/strategies/catalog/selection",
        json={"strategy_key": str(uuid.uuid4())},
    )
    assert positioned.status_code == 409
    assert positioned.json()["reason"] == "RECONFIGURE_REQUIRED"
    assert client.get("/api/strategies/catalog").json()["selected_strategy_key"] == "nova-supertrend"
