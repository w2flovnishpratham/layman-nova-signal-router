"""Phase 2F-1: paper strategy instance lifecycle mutation tests.

Covers gates, born-paused create, pause/resume idempotency, ownership (404 for
non-owner), real_orders rejection, planner interaction, audit safety, and the
no-execution guarantee (route_signal / Dhan / PaperBroker never touched).
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.core.enums import (
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
    StrategySourceType,
)
from app.db import models
from app.db.engine import session_scope
from app.services.user_context import CurrentUser
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _current_user(row, *, is_admin: bool = True, is_dev: bool = False) -> CurrentUser:
    return CurrentUser(
        id=row.id,
        email=row.email,
        name=row.name,
        picture_url=None,
        is_admin=is_admin,
        is_dev=is_dev,
    )


def _client(user: CurrentUser, monkeypatch, *, debug_enabled: bool = True) -> TestClient:
    from app.config import settings
    from app.routers import debug as debug_router

    monkeypatch.setattr(settings, "DEBUG_ENABLED", debug_enabled, raising=False)
    app = FastAPI()
    app.include_router(debug_router.router, prefix="/api/debug")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=True)


def _enable_mutation_flags(monkeypatch) -> None:
    monkeypatch.setenv("MULTI_STRATEGY_MODEL", "1")
    monkeypatch.setenv("STRATEGY_INSTANCE_MUTATION_DEBUG", "1")


def _seed_catalog(db, *, code: str = "SUPERTREND_V1", status: str | None = None):
    catalog = models.StrategyCatalog(
        code=code,
        name="Supertrend ATM Reversal",
        description="Lifecycle test row",
        status=status or StrategyCatalogStatus.ACTIVE.value,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
    )
    db.add(catalog)
    db.flush()
    version = models.StrategyVersion(
        strategy_id=catalog.id,
        version="v1",
        status=StrategyCatalogStatus.ACTIVE.value,
        payload_version="nova.v1",
    )
    db.add(version)
    db.flush()
    return catalog, version


def _seed_instance(db, *, user_id, catalog, version, status: str, execution_mode: str):
    row = models.UserStrategyInstance(
        user_id=user_id,
        strategy_id=catalog.id,
        strategy_version_id=version.id,
        instance_label="Seeded",
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status=status,
        execution_mode=execution_mode,
        lots=1,
    )
    db.add(row)
    db.flush()
    return row.id


def _create_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "catalog_code": "SUPERTREND_V1",
        "instance_label": "My Supertrend Paper",
        "lots": 1,
        "side_preference": "BOTH",
        "confirm_paper_only": True,
    }
    body.update(overrides)
    return body


@pytest.fixture
def audit_events(monkeypatch) -> list[dict[str, Any]]:
    """Capture audit events without touching runtime log files."""
    from app.routers import debug as debug_router

    captured: list[dict[str, Any]] = []

    def _capture(event_type, message, *, severity="INFO", metadata=None):
        captured.append(
            {
                "event_type": event_type,
                "message": message,
                "severity": severity,
                "metadata": metadata or {},
            }
        )
        return {}

    monkeypatch.setattr(debug_router, "log_audit_event", _capture)
    return captured


@pytest.fixture
def execution_spies(monkeypatch) -> dict[str, int]:
    """Fail loudly if any lifecycle mutation reaches execution code."""
    calls = {"route_signal": 0, "dhan": 0, "paper_broker": 0}

    import app.services.execution_router as execution_router
    import app.services.dhan_client as dhan_client
    import app.services.paper_broker as paper_broker

    def _route_spy(*args, **kwargs):
        calls["route_signal"] += 1
        raise AssertionError("route_signal must never be called by lifecycle mutations")

    class _DhanSpy:
        def __init__(self, *args, **kwargs):
            calls["dhan"] += 1
            raise AssertionError("RealDhanClient must never be constructed by lifecycle mutations")

    def _paper_spy(*args, **kwargs):
        calls["paper_broker"] += 1
        raise AssertionError("PaperBroker must never be called by lifecycle mutations")

    monkeypatch.setattr(execution_router, "route_signal", _route_spy)
    monkeypatch.setattr(dhan_client, "RealDhanClient", _DhanSpy)
    for name in ("place_order", "place_paper_order"):
        if hasattr(paper_broker, name):
            monkeypatch.setattr(paper_broker, name, _paper_spy)
    return calls


# ---------------------------------------------------------------------------
# Gate matrix
# ---------------------------------------------------------------------------


def test_debug_disabled_blocks_mutation(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("gate-debug@example.com", is_admin=True)
    client = _client(_current_user(user_row), monkeypatch, debug_enabled=False)
    response = client.post("/api/debug/v2/instances", json=_create_body())
    assert response.status_code == 404
    with session_scope() as db:
        assert db.scalars(select(models.UserStrategyInstance)).all() == []


def test_multi_strategy_model_off_blocks_mutation(mu_db, monkeypatch):
    monkeypatch.setenv("STRATEGY_INSTANCE_MUTATION_DEBUG", "1")
    monkeypatch.delenv("MULTI_STRATEGY_MODEL", raising=False)
    user_row = make_user("gate-model@example.com", is_admin=True)
    client = _client(_current_user(user_row), monkeypatch)
    response = client.post("/api/debug/v2/instances", json=_create_body())
    assert response.status_code == 403
    with session_scope() as db:
        assert db.scalars(select(models.UserStrategyInstance)).all() == []


def test_mutation_flag_off_blocks_mutation(mu_db, monkeypatch):
    monkeypatch.setenv("MULTI_STRATEGY_MODEL", "1")
    monkeypatch.delenv("STRATEGY_INSTANCE_MUTATION_DEBUG", raising=False)
    user_row = make_user("gate-flag@example.com", is_admin=True)
    client = _client(_current_user(user_row), monkeypatch)
    response = client.post("/api/debug/v2/instances", json=_create_body())
    assert response.status_code == 403


def test_missing_or_false_confirmation_blocks_mutation(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("gate-confirm@example.com", is_admin=True)
    client = _client(_current_user(user_row), monkeypatch)
    with session_scope() as db:
        _seed_catalog(db)
    for body in (_create_body(confirm_paper_only=False), {k: v for k, v in _create_body().items() if k != "confirm_paper_only"}):
        response = client.post("/api/debug/v2/instances", json=body)
        assert response.status_code == 403
    with session_scope() as db:
        assert db.scalars(select(models.UserStrategyInstance)).all() == []


def test_non_internal_user_blocked(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("gate-user@example.com", is_admin=False)
    client = _client(_current_user(user_row, is_admin=False, is_dev=False), monkeypatch)
    response = client.post("/api/debug/v2/instances", json=_create_body())
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Create: born paused, hardcoded paper, no side effects
# ---------------------------------------------------------------------------


def test_create_is_born_paused_paper(mu_db, monkeypatch, audit_events, execution_spies):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("create@example.com", is_admin=True)
    with session_scope() as db:
        _seed_catalog(db)
    client = _client(_current_user(user_row), monkeypatch)

    response = client.post("/api/debug/v2/instances", json=_create_body())
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["changed"] is True
    instance = body["instance"]
    assert instance["status"] == StrategyInstanceStatus.PAUSED.value
    assert instance["execution_mode"] == StrategyExecutionMode.PAPER_LIVE_DATA.value
    assert instance["strategy_code"] == "SUPERTREND_V1"
    assert instance["lots"] == 1
    assert instance["side_preference"] == "BOTH"

    with session_scope() as db:
        row = db.get(models.UserStrategyInstance, uuid.UUID(instance["id"]))
        assert row is not None
        assert row.user_id == user_row.id
        assert row.status == StrategyInstanceStatus.PAUSED.value
        assert row.execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value

    assert sum(execution_spies.values()) == 0


def test_create_rejects_client_supplied_mode_status_user(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("create-forbid@example.com", is_admin=True)
    with session_scope() as db:
        _seed_catalog(db)
    client = _client(_current_user(user_row), monkeypatch)

    for smuggled in (
        {"execution_mode": "real_orders"},
        {"mode": "live"},
        {"status": "active"},
        {"user_id": str(uuid.uuid4())},
        {"live": True},
        {"real": True},
        {"real_orders": True},
        {"webhook_secret": "x"},
        {"secret": "x"},
        {"credential": "x"},
    ):
        response = client.post("/api/debug/v2/instances", json=_create_body(**smuggled))
        assert response.status_code == 422, smuggled

    with session_scope() as db:
        assert db.scalars(select(models.UserStrategyInstance)).all() == []


def test_create_writes_only_instance_table(mu_db, monkeypatch, audit_events):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("create-clean@example.com", is_admin=True)
    with session_scope() as db:
        _seed_catalog(db)
    client = _client(_current_user(user_row), monkeypatch)

    response = client.post("/api/debug/v2/instances", json=_create_body())
    assert response.status_code == 200

    with session_scope() as db:
        assert len(db.scalars(select(models.UserStrategyInstance)).all()) == 1
        assert db.scalars(select(models.StrategySignal)).all() == []
        assert db.scalars(select(models.NormalizedOptionSignal)).all() == []
        assert db.scalars(select(models.StrategyExecutionJob)).all() == []
        assert db.scalars(select(models.UserStrategyWebhookCredential)).all() == []


def test_create_audit_event_is_safe(mu_db, monkeypatch, audit_events):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("create-audit@example.com", is_admin=True)
    with session_scope() as db:
        _seed_catalog(db)
    client = _client(_current_user(user_row), monkeypatch)

    response = client.post("/api/debug/v2/instances", json=_create_body(instance_label="Label with secret text"))
    assert response.status_code == 200

    created = [event for event in audit_events if event["event_type"] == "V2_INSTANCE_CREATED"]
    assert len(created) == 1
    metadata = created[0]["metadata"]
    assert metadata["actor_user_id"] == str(user_row.id)
    assert metadata["new_status"] == StrategyInstanceStatus.PAUSED.value
    assert metadata["execution_mode"] == StrategyExecutionMode.PAPER_LIVE_DATA.value
    assert metadata["changed"] is True
    serialized = str(created[0])
    assert "confirm_paper_only" not in serialized  # request body is never logged
    assert "instance_label" not in serialized


def test_create_unknown_or_disabled_catalog(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("create-missing@example.com", is_admin=True)
    with session_scope() as db:
        _seed_catalog(db, code="DISABLED_ONE", status=StrategyCatalogStatus.DISABLED.value)
    client = _client(_current_user(user_row), monkeypatch)

    assert client.post("/api/debug/v2/instances", json=_create_body(catalog_code="NOPE")).status_code == 404
    assert client.post("/api/debug/v2/instances", json=_create_body(catalog_code="DISABLED_ONE")).status_code == 409


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------


def _lifecycle(client: TestClient, instance_id, action: str):
    return client.post(
        f"/api/debug/v2/instances/{instance_id}/{action}",
        json={"confirm_paper_only": True},
    )


def test_pause_resume_transitions_and_idempotency(mu_db, monkeypatch, audit_events, execution_spies):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("lifecycle@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        instance_id = _seed_instance(
            db,
            user_id=user_row.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.ACTIVE.value,
            execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
        )
    client = _client(_current_user(user_row), monkeypatch)

    paused = _lifecycle(client, instance_id, "pause")
    assert paused.status_code == 200
    assert paused.json()["changed"] is True
    assert paused.json()["instance"]["status"] == "paused"

    paused_again = _lifecycle(client, instance_id, "pause")
    assert paused_again.status_code == 200
    assert paused_again.json()["ok"] is True
    assert paused_again.json()["changed"] is False

    resumed = _lifecycle(client, instance_id, "resume")
    assert resumed.status_code == 200
    assert resumed.json()["changed"] is True
    assert resumed.json()["instance"]["status"] == "active"

    resumed_again = _lifecycle(client, instance_id, "resume")
    assert resumed_again.status_code == 200
    assert resumed_again.json()["changed"] is False

    events = [event["event_type"] for event in audit_events]
    assert events.count("V2_INSTANCE_PAUSED") == 1  # idempotent repeats are not audited as changes
    assert events.count("V2_INSTANCE_RESUMED") == 1
    assert sum(execution_spies.values()) == 0


def test_signal_only_instance_may_pause_resume(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("signal-only@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        instance_id = _seed_instance(
            db,
            user_id=user_row.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.ACTIVE.value,
            execution_mode=StrategyExecutionMode.SIGNAL_ONLY.value,
        )
    client = _client(_current_user(user_row), monkeypatch)
    assert _lifecycle(client, instance_id, "pause").status_code == 200


def test_real_orders_instance_mutation_forbidden(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("real-orders@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        instance_id = _seed_instance(
            db,
            user_id=user_row.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.PAUSED.value,
            execution_mode=StrategyExecutionMode.REAL_ORDERS.value,
        )
    client = _client(_current_user(user_row), monkeypatch)
    assert _lifecycle(client, instance_id, "pause").status_code == 403
    assert _lifecycle(client, instance_id, "resume").status_code == 403
    with session_scope() as db:
        row = db.get(models.UserStrategyInstance, instance_id)
        assert row.status == StrategyInstanceStatus.PAUSED.value


def test_non_owner_gets_404(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    owner_row = make_user("owner@example.com", is_admin=True)
    intruder_row = make_user("intruder@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        instance_id = _seed_instance(
            db,
            user_id=owner_row.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.ACTIVE.value,
            execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
        )
    intruder_client = _client(_current_user(intruder_row), monkeypatch)
    assert _lifecycle(intruder_client, instance_id, "pause").status_code == 404
    assert _lifecycle(intruder_client, uuid.uuid4(), "pause").status_code == 404
    assert _lifecycle(intruder_client, "not-a-uuid", "pause").status_code == 404
    with session_scope() as db:
        row = db.get(models.UserStrategyInstance, instance_id)
        assert row.status == StrategyInstanceStatus.ACTIVE.value


def test_error_status_instance_returns_safe_conflict(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("error-status@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        instance_id = _seed_instance(
            db,
            user_id=user_row.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.ERROR.value,
            execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
        )
    client = _client(_current_user(user_row), monkeypatch)
    assert _lifecycle(client, instance_id, "resume").status_code == 409


# ---------------------------------------------------------------------------
# Planner interaction
# ---------------------------------------------------------------------------


def _nova_v1_payload(signal_id: str) -> dict[str, Any]:
    return {
        "version": "nova.v1",
        "secret": "super-secret-value",
        "signal_id": signal_id,
        "strategy_code": "SUPERTREND_V1",
        "action": "ENTRY",
        "intent": "BULLISH",
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "option_side": "AUTO",
        "strike_mode": "MANUAL",
        "strike": 24500,
        "expiry_mode": "MANUAL",
        "expiry": "2026-07-09",
        "qty_mode": "LOTS",
        "lots": 1,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
        "source": "tradingview",
        "timestamp": "2026-07-08T09:20:00+05:30",
    }


def test_created_paused_instance_is_skipped_then_planned_after_resume(mu_db, monkeypatch):
    from app.services.strategy_fanout_v2 import plan_strategy_fanout_v2

    _enable_mutation_flags(monkeypatch)
    user_row = make_user("planner@example.com", is_admin=True)
    with session_scope() as db:
        _seed_catalog(db)
    client = _client(_current_user(user_row), monkeypatch)

    created = client.post("/api/debug/v2/instances", json=_create_body())
    assert created.status_code == 200
    instance_id = created.json()["instance"]["id"]

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    with session_scope() as db:
        plan = plan_strategy_fanout_v2(db, _nova_v1_payload("lifecycle-paused"), "SUPERTREND_V1")
    assert plan.planned_count == 0
    assert any(str(skip.instance_id) == instance_id for skip in plan.skips)

    assert _lifecycle(client, instance_id, "resume").status_code == 200
    with session_scope() as db:
        plan = plan_strategy_fanout_v2(db, _nova_v1_payload("lifecycle-active"), "SUPERTREND_V1")
    assert plan.planned_count == 1
    assert str(plan.plans[0].instance_id) == instance_id
    assert plan.plans[0].execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value
