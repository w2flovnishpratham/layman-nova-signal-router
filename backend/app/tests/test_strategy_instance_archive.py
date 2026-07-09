"""Phase 2H-2 soft archive / restore tests."""
from __future__ import annotations

import json
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


def _seed_catalog(db):
    catalog = models.StrategyCatalog(
        code="SUPERTREND_V1",
        name="Supertrend",
        status=StrategyCatalogStatus.ACTIVE.value,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        metadata_json={"aliases": ["supertrend", "TRADINGVIEW_NIFTY_V1"]},
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
):
    instance = models.UserStrategyInstance(
        user_id=user_id,
        strategy_id=catalog.id,
        strategy_version_id=version.id,
        instance_label="Archive candidate",
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status=status,
        execution_mode=execution_mode,
        lots=1,
        side_preference="BOTH",
        config_json={"created_by": "test", "keep": "unchanged"},
        risk_config={"keep": "unchanged"},
    )
    db.add(instance)
    db.flush()
    return instance.id


def _archive(client: TestClient, instance_id: Any, *, confirm: bool = True):
    return client.post(
        f"/api/debug/v2/instances/{instance_id}/archive",
        json={"confirm_paper_only": confirm},
    )


def _restore(client: TestClient, instance_id: Any, *, confirm: bool = True):
    return client.post(
        f"/api/debug/v2/instances/{instance_id}/restore",
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
        raise AssertionError("route_signal must never be called by archive/restore")

    class _DhanSpy:
        def __init__(self, *args, **kwargs):
            calls["dhan"] += 1
            raise AssertionError("RealDhanClient must never be constructed by archive/restore")

    def _paper_spy(*args, **kwargs):
        calls["paper_broker"] += 1
        raise AssertionError("PaperBroker must never be called by archive/restore")

    monkeypatch.setattr(execution_router, "route_signal", _route_spy)
    monkeypatch.setattr(dhan_client, "RealDhanClient", _DhanSpy)
    for name in ("place_order", "place_super_order", "place_paper_order"):
        if hasattr(paper_broker.PaperBroker, name):
            monkeypatch.setattr(paper_broker.PaperBroker, name, _paper_spy)
    return calls


def test_archive_paused_succeeds_and_writes_only_status_updated_at_and_audit(
    mu_db,
    monkeypatch,
    audit_events,
    execution_spies,
):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("archive-paused@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        instance_id = _seed_instance(db, user_id=user_row.id, catalog=catalog, version=version)

    before = _db_snapshot()
    client = _client(_current_user(user_row), monkeypatch)
    response = _archive(client, instance_id)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["changed"] is True
    assert body["instance"]["status"] == StrategyInstanceStatus.ARCHIVED.value

    after = _db_snapshot()
    before_row = before["instances"][str(instance_id)]
    after_row = after["instances"][str(instance_id)]
    assert after_row["status"] == StrategyInstanceStatus.ARCHIVED.value
    assert after_row["updated_at"] != before_row["updated_at"]
    assert {
        key: value for key, value in after_row.items() if key not in {"status", "updated_at"}
    } == {
        key: value for key, value in before_row.items() if key not in {"status", "updated_at"}
    }
    assert after["signals"] == before["signals"] == 0
    assert after["normalized"] == before["normalized"] == 0
    assert after["jobs"] == before["jobs"] == 0
    assert after["credentials"] == before["credentials"] == []

    archived = [event for event in audit_events if event["event_type"] == "V2_INSTANCE_ARCHIVED"]
    assert len(archived) == 1
    metadata = archived[0]["metadata"]
    assert metadata["actor_user_id"] == str(user_row.id)
    assert metadata["instance_id"] == str(instance_id)
    assert metadata["previous_status"] == StrategyInstanceStatus.PAUSED.value
    assert metadata["new_status"] == StrategyInstanceStatus.ARCHIVED.value
    serialized = str(archived[0])
    assert "confirm_paper_only" not in serialized
    assert "raw_payload" not in serialized
    assert "access_token" not in serialized
    assert sum(execution_spies.values()) == 0


def test_restore_archived_succeeds_and_returns_paused(mu_db, monkeypatch, audit_events, execution_spies):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("restore-archived@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        instance_id = _seed_instance(
            db,
            user_id=user_row.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.ARCHIVED.value,
        )

    client = _client(_current_user(user_row), monkeypatch)
    response = _restore(client, instance_id)

    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert response.json()["instance"]["status"] == StrategyInstanceStatus.PAUSED.value
    with session_scope() as db:
        row = db.get(models.UserStrategyInstance, instance_id)
        assert row.status == StrategyInstanceStatus.PAUSED.value
    restored = [event for event in audit_events if event["event_type"] == "V2_INSTANCE_RESTORED"]
    assert len(restored) == 1
    assert restored[0]["metadata"]["previous_status"] == StrategyInstanceStatus.ARCHIVED.value
    assert restored[0]["metadata"]["new_status"] == StrategyInstanceStatus.PAUSED.value
    assert sum(execution_spies.values()) == 0


def test_archive_restore_illegal_transitions_are_safe_conflicts(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("archive-conflicts@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        active_id = _seed_instance(
            db,
            user_id=user_row.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.ACTIVE.value,
        )
        disabled_id = _seed_instance(
            db,
            user_id=user_row.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.DISABLED.value,
        )
        error_id = _seed_instance(
            db,
            user_id=user_row.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.ERROR.value,
        )
        real_archive_id = _seed_instance(
            db,
            user_id=user_row.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.PAUSED.value,
            execution_mode=StrategyExecutionMode.REAL_ORDERS.value,
        )
        real_restore_id = _seed_instance(
            db,
            user_id=user_row.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.ARCHIVED.value,
            execution_mode=StrategyExecutionMode.REAL_ORDERS.value,
        )

    client = _client(_current_user(user_row), monkeypatch)
    active_archive = _archive(client, active_id)
    assert active_archive.status_code == 409
    assert active_archive.json()["detail"] == "Pause the instance before archiving."
    assert _archive(client, disabled_id).status_code == 409
    assert _archive(client, error_id).status_code == 409
    assert _archive(client, real_archive_id).status_code == 403
    assert _restore(client, real_restore_id).status_code == 403
    assert _restore(client, active_id).status_code == 409


def test_cross_user_unknown_and_confirmation_gates(mu_db, monkeypatch):
    _enable_mutation_flags(monkeypatch)
    owner_row = make_user("archive-owner@example.com", is_admin=True)
    intruder_row = make_user("archive-intruder@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        instance_id = _seed_instance(db, user_id=owner_row.id, catalog=catalog, version=version)

    intruder_client = _client(_current_user(intruder_row), monkeypatch)
    assert _archive(intruder_client, instance_id).status_code == 404
    assert _restore(intruder_client, instance_id).status_code == 404
    assert _archive(intruder_client, uuid.uuid4()).status_code == 404
    assert _restore(intruder_client, "not-a-uuid").status_code == 404

    owner_client = _client(_current_user(owner_row), monkeypatch)
    assert _archive(owner_client, instance_id, confirm=False).status_code == 403


def test_archive_and_restore_are_idempotent_without_extra_audit(mu_db, monkeypatch, audit_events):
    _enable_mutation_flags(monkeypatch)
    user_row = make_user("archive-idempotent@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        archived_id = _seed_instance(
            db,
            user_id=user_row.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.ARCHIVED.value,
        )
        paused_id = _seed_instance(db, user_id=user_row.id, catalog=catalog, version=version)

    client = _client(_current_user(user_row), monkeypatch)
    archive_response = _archive(client, archived_id)
    restore_response = _restore(client, paused_id)

    assert archive_response.status_code == 200
    assert archive_response.json()["changed"] is False
    assert archive_response.json()["instance"]["status"] == StrategyInstanceStatus.ARCHIVED.value
    assert restore_response.status_code == 200
    assert restore_response.json()["changed"] is False
    assert restore_response.json()["instance"]["status"] == StrategyInstanceStatus.PAUSED.value
    assert audit_events == []


def test_planner_skips_archived_without_signals_or_jobs_then_requires_explicit_resume(
    mu_db,
    monkeypatch,
    audit_events,
    execution_spies,
):
    from app.services.strategy_fanout_v2 import ARCHIVED_INSTANCE, PAUSED_INSTANCE, plan_strategy_fanout_v2

    _enable_mutation_flags(monkeypatch)
    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    user_row = make_user("archive-planner@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version = _seed_catalog(db)
        instance_id = _seed_instance(
            db,
            user_id=user_row.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.ARCHIVED.value,
        )
        archived_plan = plan_strategy_fanout_v2(
            db,
            _raw_payload("archive-planner-skipped"),
            "SUPERTREND_V1",
            dry_run=False,
        )

    assert archived_plan.planned_count == 0
    assert archived_plan.skipped_count == 1
    assert archived_plan.skips[0].reason == ARCHIVED_INSTANCE
    assert archived_plan.signal_row_id is None
    assert _db_snapshot()["signals"] == 0
    assert _db_snapshot()["normalized"] == 0
    assert _db_snapshot()["jobs"] == 0

    client = _client(_current_user(user_row), monkeypatch)
    assert _restore(client, instance_id).status_code == 200
    with session_scope() as db:
        paused_plan = plan_strategy_fanout_v2(
            db,
            _raw_payload("archive-planner-paused"),
            "SUPERTREND_V1",
            dry_run=False,
        )

    assert paused_plan.planned_count == 0
    assert paused_plan.skipped_count == 1
    assert paused_plan.skips[0].reason == PAUSED_INSTANCE
    assert paused_plan.signal_row_id is None
    assert _db_snapshot()["signals"] == 0
    assert _db_snapshot()["normalized"] == 0
    assert _db_snapshot()["jobs"] == 0

    assert _resume(client, instance_id).status_code == 200
    with session_scope() as db:
        active_plan = plan_strategy_fanout_v2(
            db,
            _raw_payload("archive-planner-active"),
            "SUPERTREND_V1",
        )

    assert active_plan.planned_count == 1
    assert str(active_plan.plans[0].instance_id) == str(instance_id)
    assert _db_snapshot()["signals"] == 0
    assert _db_snapshot()["normalized"] == 0
    assert _db_snapshot()["jobs"] == 0
    assert sum(execution_spies.values()) == 0


def test_frontend_exposes_only_archive_restore_for_phase2h2_controls():
    catalog_panel = (FRONTEND_DEBUG_DIR / "StrategyCatalogStatusPanel.tsx").read_text(encoding="utf-8")
    api_text = (REPO_ROOT / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")

    assert "archivePaperStrategyInstance" in catalog_panel
    assert "restorePaperStrategyInstance" in catalog_panel
    assert "Show archived" in catalog_panel
    assert "label=\"Archive\"" in catalog_panel
    assert "label=\"Restore\"" in catalog_panel
    assert "/api/debug/v2/instances/${encodeURIComponent(instanceId)}/archive" in api_text
    assert "/api/debug/v2/instances/${encodeURIComponent(instanceId)}/restore" in api_text

    for forbidden in (
        "Delete",
        "Purge",
        "Execute",
        "Run",
        "Retry",
        "Process Queue",
        "Webhook",
        "Live",
        "Dhan",
        "Resume Active",
    ):
        assert f'label="{forbidden}"' not in catalog_panel, forbidden
        assert f">{forbidden}<" not in catalog_panel, forbidden

    archive_section = _slice_after(catalog_panel, "if (instance.status === 'archived')", 450)
    assert "resumePaperStrategyInstance" not in archive_section
    assert "archivePaperStrategyInstance" not in archive_section
    assert "restorePaperStrategyInstance" in archive_section


def _slice_after(text: str, start_marker: str, length: int) -> str:
    start = text.find(start_marker)
    assert start >= 0
    return text[start:start + length]


def _db_snapshot() -> dict[str, Any]:
    with session_scope() as db:
        instances = db.scalars(
            select(models.UserStrategyInstance).order_by(models.UserStrategyInstance.id)
        ).all()
        credentials = db.scalars(
            select(models.UserStrategyWebhookCredential).order_by(models.UserStrategyWebhookCredential.id)
        ).all()
        return {
            "instances": {
                str(row.id): {
                    "user_id": str(row.user_id),
                    "strategy_id": str(row.strategy_id),
                    "strategy_version_id": str(row.strategy_version_id) if row.strategy_version_id else None,
                    "instance_label": row.instance_label,
                    "source_type": row.source_type,
                    "status": row.status,
                    "execution_mode": row.execution_mode,
                    "lots": row.lots,
                    "side_preference": row.side_preference,
                    "config_json": json.dumps(row.config_json, sort_keys=True),
                    "risk_config": json.dumps(row.risk_config, sort_keys=True),
                    "created_at": row.created_at.isoformat(),
                    "updated_at": row.updated_at.isoformat(),
                }
                for row in instances
            },
            "signals": len(db.scalars(select(models.StrategySignal)).all()),
            "normalized": len(db.scalars(select(models.NormalizedOptionSignal)).all()),
            "jobs": len(db.scalars(select(models.StrategyExecutionJob)).all()),
            "credentials": [
                (
                    str(row.id),
                    str(row.instance_id),
                    row.webhook_key_hash,
                    row.message_secret_hash,
                    row.status,
                    row.is_active,
                )
                for row in credentials
            ],
        }
