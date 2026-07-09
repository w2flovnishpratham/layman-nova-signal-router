"""Phase 2I-1 paper strategy clone/duplicate tests."""
from __future__ import annotations

import uuid
from pathlib import Path
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


REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DEBUG_DIR = REPO_ROOT / "frontend" / "src" / "debug"


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
    monkeypatch.delenv("MULTI_STRATEGY_FANOUT", raising=False)
    monkeypatch.delenv("V2_PAPER_RUNNER_DEBUG", raising=False)


def _seed_catalog(db, *, code: str = "SUPERTREND_V1"):
    catalog = models.StrategyCatalog(
        code=code,
        name="Supertrend",
        status=StrategyCatalogStatus.ACTIVE.value,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        metadata_json={"aliases": ["supertrend"]},
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


def _seed_instance(
    db,
    *,
    user_id,
    catalog,
    version,
    status: str = StrategyInstanceStatus.PAUSED.value,
    execution_mode: str = StrategyExecutionMode.PAPER_LIVE_DATA.value,
    instance_label: str = "Momentum Strategy",
    lots: int = 3,
    side_preference: str = "CE",
):
    instance = models.UserStrategyInstance(
        user_id=user_id,
        strategy_id=catalog.id,
        strategy_version_id=version.id,
        instance_label=instance_label,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status=status,
        execution_mode=execution_mode,
        lots=lots,
        side_preference=side_preference,
        config_json={"created_by": "test", "keep": "unchanged", "secret": "source-secret-value"},
        risk_config={"keep": "unchanged"},
    )
    db.add(instance)
    db.flush()
    return instance.id


def _clone(client: TestClient, instance_id: Any, *, confirm: bool = True):
    return client.post(
        f"/api/debug/v2/instances/{instance_id}/clone",
        json={"confirm_paper_only": confirm},
    )


def _resume(client: TestClient, instance_id: Any):
    return client.post(
        f"/api/debug/v2/instances/{instance_id}/resume",
        json={"confirm_paper_only": True},
    )


def _raw_payload(signal_id: str) -> dict[str, Any]:
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
        "timestamp": "2026-07-09T09:20:00+05:30",
    }


@pytest.fixture
def audit_events(monkeypatch) -> list[dict[str, Any]]:
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
    calls = {"route_signal": 0, "dhan": 0, "paper_broker": 0}

    import app.services.dhan_client as dhan_client
    import app.services.execution_router as execution_router
    import app.services.paper_broker as paper_broker

    def _route_spy(*args, **kwargs):
        calls["route_signal"] += 1
        raise AssertionError("route_signal must never be called by clone")

    class _DhanSpy:
        def __init__(self, *args, **kwargs):
            calls["dhan"] += 1
            raise AssertionError("RealDhanClient must never be constructed by clone")

    def _paper_spy(*args, **kwargs):
        calls["paper_broker"] += 1
        raise AssertionError("PaperBroker must never be called by clone")

    monkeypatch.setattr(execution_router, "route_signal", _route_spy)
    monkeypatch.setattr(dhan_client, "RealDhanClient", _DhanSpy)
    for name in ("place_order", "place_super_order", "place_paper_order"):
        if hasattr(paper_broker.PaperBroker, name):
            monkeypatch.setattr(paper_broker.PaperBroker, name, _paper_spy)
    return calls


def _db_snapshot() -> dict[str, Any]:
    with session_scope() as db:
        instances = db.scalars(
            select(models.UserStrategyInstance).order_by(models.UserStrategyInstance.created_at)
        ).all()
        return {
            "instances": [
                {
                    "id": str(row.id),
                    "user_id": str(row.user_id),
                    "strategy_id": str(row.strategy_id),
                    "strategy_version_id": str(row.strategy_version_id) if row.strategy_version_id else None,
                    "instance_label": row.instance_label,
                    "source_type": row.source_type,
                    "status": row.status,
                    "execution_mode": row.execution_mode,
                    "lots": row.lots,
                    "side_preference": row.side_preference,
                }
                for row in instances
            ],
            "signals": len(db.scalars(select(models.StrategySignal)).all()),
            "normalized": len(db.scalars(select(models.NormalizedOptionSignal)).all()),
            "jobs": len(db.scalars(select(models.StrategyExecutionJob)).all()),
            "credentials": len(db.scalars(select(models.UserStrategyWebhookCredential)).all()),
        }


@pytest.mark.parametrize(
    "source_status",
    [
        StrategyInstanceStatus.PAUSED.value,
        StrategyInstanceStatus.ACTIVE.value,
        StrategyInstanceStatus.ARCHIVED.value,
    ],
)
def test_clone_from_eligible_status_creates_paused_paper_copy(
    mu_db,
    monkeypatch,
    audit_events,
    execution_spies,
    source_status,
):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user(f"clone-{source_status}@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        source_id = _seed_instance(db, user_id=user_row.id, catalog=catalog, version=version, status=source_status)

    before = _db_snapshot()
    client = _client(_current_user(user_row), monkeypatch)
    response = _clone(client, source_id)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["changed"] is True
    clone = body["instance"]
    assert clone["id"] != str(source_id)
    assert clone["status"] == StrategyInstanceStatus.PAUSED.value
    assert clone["execution_mode"] == StrategyExecutionMode.PAPER_LIVE_DATA.value
    assert clone["lots"] == 3
    assert clone["side_preference"] == "CE"
    assert clone["instance_label"] == "Momentum Strategy (Copy)"

    with session_scope() as db:
        clone_row = db.get(models.UserStrategyInstance, uuid.UUID(clone["id"]))
        source_row = db.get(models.UserStrategyInstance, source_id)
        assert clone_row.strategy_id == source_row.strategy_id
        assert clone_row.strategy_version_id == source_row.strategy_version_id
        assert clone_row.source_type == source_row.source_type
        assert clone_row.user_id == user_row.id
        assert clone_row.config_json.get("cloned_from") == str(source_id)
        assert clone_row.config_json.get("secret") is None
        assert clone_row.risk_config == {}
        # Source instance must be completely untouched by cloning it.
        assert source_row.status == source_status
        assert source_row.instance_label == "Momentum Strategy"

    after = _db_snapshot()
    assert after["signals"] == before["signals"] == 0
    assert after["normalized"] == before["normalized"] == 0
    assert after["jobs"] == before["jobs"] == 0
    assert after["credentials"] == before["credentials"] == 0
    assert len(after["instances"]) == len(before["instances"]) + 1

    cloned_events = [event for event in audit_events if event["event_type"] == "V2_INSTANCE_CLONED"]
    assert len(cloned_events) == 1
    metadata = cloned_events[0]["metadata"]
    assert metadata["actor_user_id"] == str(user_row.id)
    assert metadata["instance_id"] == clone["id"]
    assert metadata["source_instance_id"] == str(source_id)
    assert metadata["new_status"] == StrategyInstanceStatus.PAUSED.value
    serialized = str(cloned_events[0])
    assert "source-secret-value" not in serialized
    assert "confirm_paper_only" not in serialized
    assert sum(execution_spies.values()) == 0


def test_clone_real_orders_and_ineligible_statuses_are_blocked(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("clone-blocked@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        real_orders_id = _seed_instance(
            db,
            user_id=user_row.id,
            catalog=catalog,
            version=version,
            execution_mode=StrategyExecutionMode.REAL_ORDERS.value,
        )
        disabled_id = _seed_instance(
            db, user_id=user_row.id, catalog=catalog, version=version, status=StrategyInstanceStatus.DISABLED.value
        )
        error_id = _seed_instance(
            db, user_id=user_row.id, catalog=catalog, version=version, status=StrategyInstanceStatus.ERROR.value
        )

    before = _db_snapshot()
    client = _client(_current_user(user_row), monkeypatch)
    assert _clone(client, real_orders_id).status_code == 403
    assert _clone(client, disabled_id).status_code == 409
    assert _clone(client, error_id).status_code == 409
    assert _db_snapshot() == before


def test_clone_cross_user_unknown_and_confirmation_gates(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    owner_row = make_user("clone-owner@example.com", is_admin=True)
    intruder_row = make_user("clone-intruder@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        instance_id = _seed_instance(db, user_id=owner_row.id, catalog=catalog, version=version)

    before = _db_snapshot()
    intruder_client = _client(_current_user(intruder_row), monkeypatch)
    assert _clone(intruder_client, instance_id).status_code == 404
    assert _clone(intruder_client, uuid.uuid4()).status_code == 404
    assert _clone(intruder_client, "not-a-uuid").status_code == 404
    assert _db_snapshot() == before

    owner_client = _client(_current_user(owner_row), monkeypatch)
    assert _clone(owner_client, instance_id, confirm=False).status_code == 403
    assert _db_snapshot() == before


def test_clone_label_numbering_increments_on_repeated_clones(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("clone-numbering@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        source_id = _seed_instance(db, user_id=user_row.id, catalog=catalog, version=version)

    client = _client(_current_user(user_row), monkeypatch)
    first = _clone(client, source_id)
    second = _clone(client, source_id)
    third = _clone(client, source_id)

    assert first.json()["instance"]["instance_label"] == "Momentum Strategy (Copy)"
    assert second.json()["instance"]["instance_label"] == "Momentum Strategy (Copy 2)"
    assert third.json()["instance"]["instance_label"] == "Momentum Strategy (Copy 3)"

    # Cloning a clone renumbers off the original name rather than chaining suffixes.
    clone_of_clone = _clone(client, uuid.UUID(first.json()["instance"]["id"]))
    assert clone_of_clone.json()["instance"]["instance_label"] == "Momentum Strategy (Copy 4)"


def test_clone_per_user_per_strategy_cap_enforced(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("clone-cap@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        source_id = _seed_instance(db, user_id=user_row.id, catalog=catalog, version=version, instance_label="Cap Source 0")
        for i in range(1, 10):
            _seed_instance(db, user_id=user_row.id, catalog=catalog, version=version, instance_label=f"Cap Source {i}")

    before = _db_snapshot()
    assert len(before["instances"]) == 10
    client = _client(_current_user(user_row), monkeypatch)
    response = _clone(client, source_id)

    assert response.status_code == 409
    assert "Maximum" in response.json()["detail"]
    assert _db_snapshot() == before


def test_clone_is_born_paused_and_planner_skips_until_resumed(
    mu_db,
    monkeypatch,
    audit_events,
    execution_spies,
):
    from app.services.strategy_fanout_v2 import PAUSED_INSTANCE, plan_strategy_fanout_v2

    _enable_mutation_flags(monkeypatch)
    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    user_row = make_user("clone-planner@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        # Source stays paused so the only instance the planner could possibly
        # act on in the first assertion is the clone itself.
        source_id = _seed_instance(
            db, user_id=user_row.id, catalog=catalog, version=version, status=StrategyInstanceStatus.PAUSED.value
        )

    client = _client(_current_user(user_row), monkeypatch)
    clone_response = _clone(client, source_id)
    clone_id = clone_response.json()["instance"]["id"]

    with session_scope() as db:
        paused_plan = plan_strategy_fanout_v2(db, _raw_payload("clone-planner-paused"), "SUPERTREND_V1", dry_run=False)

    clone_skip = [skip for skip in paused_plan.skips if str(skip.instance_id) == clone_id]
    assert bool(clone_skip)
    assert clone_skip[0].reason == PAUSED_INSTANCE
    assert _db_snapshot()["signals"] == 0
    assert _db_snapshot()["jobs"] == 0

    assert _resume(client, clone_id).status_code == 200
    with session_scope() as db:
        active_plan = plan_strategy_fanout_v2(db, _raw_payload("clone-planner-active"), "SUPERTREND_V1")

    clone_planned = [plan for plan in active_plan.plans if str(plan.instance_id) == clone_id]
    assert bool(clone_planned)
    assert _db_snapshot()["signals"] == 0
    assert _db_snapshot()["jobs"] == 0
    assert sum(execution_spies.values()) == 0


def test_clone_history_shows_only_cloned_event(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("clone-history@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        source_id = _seed_instance(db, user_id=user_row.id, catalog=catalog, version=version)

    client = _client(_current_user(user_row), monkeypatch)
    clone_id = _clone(client, source_id).json()["instance"]["id"]

    source_details = client.get(f"/api/debug/v2/instances/{source_id}/details")
    clone_details = client.get(f"/api/debug/v2/instances/{clone_id}/details")

    assert clone_details.status_code == 200
    clone_history = clone_details.json()["history"]
    assert len(clone_history) == 1
    assert clone_history[0]["event_type"] == "V2_INSTANCE_CLONED"
    assert clone_history[0]["summary"] == "Cloned from another instance"

    source_history_types = {row["event_type"] for row in source_details.json()["history"]}
    assert "V2_INSTANCE_CLONED" not in source_history_types


def test_frontend_exposes_clone_for_phase2i1_controls():
    catalog_panel = (FRONTEND_DEBUG_DIR / "StrategyCatalogStatusPanel.tsx").read_text(encoding="utf-8")
    api_text = (REPO_ROOT / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")
    paper_panel = (FRONTEND_DEBUG_DIR / "V2PaperStatusPanel.tsx").read_text(encoding="utf-8")

    assert "clonePaperStrategyInstance" in catalog_panel
    assert 'label="Clone"' in catalog_panel
    assert "/api/debug/v2/instances/${encodeURIComponent(instanceId)}/clone" in api_text
    assert "clonePaperStrategyInstance" not in paper_panel

    details_ui_start = catalog_panel.find("function InstanceDetailsDrawer")
    details_ui_end = catalog_panel.find("function InstanceLifecycleCell")
    assert details_ui_start >= 0 and details_ui_end > details_ui_start
    details_ui = catalog_panel[details_ui_start:details_ui_end]
    assert "clonePaperStrategyInstance" not in details_ui

    for forbidden in ("Delete", "Purge", "Execute", "Process Queue", "Webhook", "Live", "Dhan"):
        assert f'label="{forbidden}"' not in catalog_panel, forbidden
