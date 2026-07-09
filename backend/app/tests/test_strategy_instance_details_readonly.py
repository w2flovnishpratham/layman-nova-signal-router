"""Read-only strategy instance details/history tests."""
from __future__ import annotations

import json
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


FORBIDDEN_KEYS = {
    "access_token",
    "client_id",
    "config",
    "config_json",
    "credential_hash",
    "credentials_hash",
    "dhan_access_token",
    "dhan_client_id",
    "headers",
    "message_secret_hash",
    "metadata",
    "metadata_json",
    "password",
    "pin",
    "proxy_url",
    "raw_body",
    "raw_payload",
    "raw_request",
    "raw_response",
    "request",
    "response",
    "risk_config",
    "secret",
    "signal_payload",
    "token",
    "webhook_key_hash",
}


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


def _contains_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in forbidden:
                return True
            if _contains_key(item, forbidden):
                return True
    if isinstance(value, list):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def _contains_value(value: Any, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_value(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_contains_value(item, needle) for item in value)
    return str(value) == needle


@pytest.fixture
def audit_records(monkeypatch) -> list[dict[str, Any]]:
    from app.routers import debug as debug_router

    records: list[dict[str, Any]] = []

    def _read_jsonl(log_name: str, limit: int = 100):
        assert log_name == "audit"
        return list(records[-limit:])

    monkeypatch.setattr(debug_router, "read_jsonl", _read_jsonl)
    return records


@pytest.fixture
def execution_spies(monkeypatch) -> dict[str, int]:
    calls = {"route_signal": 0, "dhan": 0, "paper_broker": 0}

    import app.services.dhan_client as dhan_client
    import app.services.execution_router as execution_router
    import app.services.paper_broker as paper_broker

    def _route_spy(*args, **kwargs):
        calls["route_signal"] += 1
        raise AssertionError("route_signal must never be called by instance details")

    class _DhanSpy:
        def __init__(self, *args, **kwargs):
            calls["dhan"] += 1
            raise AssertionError("RealDhanClient must never be constructed by instance details")

    def _paper_spy(*args, **kwargs):
        calls["paper_broker"] += 1
        raise AssertionError("PaperBroker must never be called by instance details")

    monkeypatch.setattr(execution_router, "route_signal", _route_spy)
    monkeypatch.setattr(dhan_client, "RealDhanClient", _DhanSpy)
    monkeypatch.setattr(paper_broker.PaperBroker, "place_order", _paper_spy)
    monkeypatch.setattr(paper_broker.PaperBroker, "place_super_order", _paper_spy)
    return calls


def _seed_catalog_instance(
    db,
    *,
    user_id,
    status: str = StrategyInstanceStatus.PAUSED.value,
    execution_mode: str = StrategyExecutionMode.PAPER_LIVE_DATA.value,
):
    catalog = models.StrategyCatalog(
        code="SUPERTREND_V1",
        name="Supertrend",
        description="Details row",
        status=StrategyCatalogStatus.ACTIVE.value,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        metadata_json={"secret": "catalog-secret-value"},
    )
    db.add(catalog)
    db.flush()
    version = models.StrategyVersion(
        strategy_id=catalog.id,
        version="1.0.0",
        status=StrategyCatalogStatus.ACTIVE.value,
        payload_version="nova.v1",
        config_schema={"access_token": "version-token-value"},
        metadata_json={"raw_payload": "version-raw-value"},
    )
    db.add(version)
    db.flush()
    instance = models.UserStrategyInstance(
        user_id=user_id,
        strategy_id=catalog.id,
        strategy_version_id=version.id,
        instance_label="My Paper Strategy",
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status=status,
        execution_mode=execution_mode,
        lots=2,
        side_preference="BOTH",
        config_json={"raw_payload": "instance-config-secret", "access_token": "hidden-token"},
        risk_config={"secret": "risk-secret-value"},
    )
    db.add(instance)
    db.flush()
    return catalog, version, instance


def _seed_audit(db, *, user_id, instance_id, action: str, metadata: dict[str, Any]):
    row = models.AuditLog(
        user_id=user_id,
        action=action,
        audit_metadata=metadata,
    )
    db.add(row)
    db.flush()
    return row


def test_owner_internal_user_can_read_safe_details_and_history(
    mu_db,
    monkeypatch,
    audit_records,
    execution_spies,
):
    user_row = make_user("details-owner@example.com", is_admin=True)
    with session_scope() as db:
        catalog, version, instance = _seed_catalog_instance(db, user_id=user_row.id)
        _seed_audit(
            db,
            user_id=user_row.id,
            instance_id=instance.id,
            action="V2_INSTANCE_CREATED",
            metadata={
                "event_type": "V2_INSTANCE_CREATED",
                "actor_user_id": str(user_row.id),
                "instance_id": str(instance.id),
                "strategy_code": catalog.code,
                "previous_status": None,
                "new_status": StrategyInstanceStatus.PAUSED.value,
                "changed_fields": ["status", "secret", "config_json"],
                "old_values": {"secret": "old-secret-value", "headers": {"x": "y"}},
                "new_values": {
                    "status": StrategyInstanceStatus.PAUSED.value,
                    "execution_mode": StrategyExecutionMode.PAPER_LIVE_DATA.value,
                    "access_token": "new-token-value",
                },
                "request": {"raw_payload": "raw-request-value"},
            },
        )
        _seed_audit(
            db,
            user_id=user_row.id,
            instance_id=instance.id,
            action="V2_INSTANCE_SETTINGS_EDITED",
            metadata={
                "actor_user_id": str(user_row.id),
                "instance_id": str(instance.id),
                "strategy_code": catalog.code,
                "execution_mode": StrategyExecutionMode.PAPER_LIVE_DATA.value,
                "changed_fields": ["instance_label", "lots", "side_preference", "proxy_url"],
                "old_values": {
                    "instance_label": "Old",
                    "lots": 1,
                    "side_preference": "CE",
                    "proxy_url": "http://proxy-secret",
                },
                "new_values": {
                    "instance_label": "My Paper Strategy",
                    "lots": 2,
                    "side_preference": "BOTH",
                    "token": "settings-token-value",
                },
                "response": {"client_id": "dhan-client-value"},
            },
        )
        _seed_audit(
            db,
            user_id=user_row.id,
            instance_id=instance.id,
            action="V2_INSTANCE_PAUSED",
            metadata={
                "actor_user_id": str(user_row.id),
                "instance_id": str(instance.id),
                "strategy_code": catalog.code,
                "previous_status": "previous-status-secret",
                "new_status": "new-status-token",
                "changed_fields": ["status", "execution_mode", "lots"],
                "old_values": {
                    "status": "status-token-value",
                    "execution_mode": "headers-secret-value",
                    "lots": 99,
                },
                "new_values": {
                    "status": StrategyInstanceStatus.PAUSED.value,
                    "execution_mode": StrategyExecutionMode.PAPER_LIVE_DATA.value,
                    "lots": 2,
                },
            },
        )
        instance_id = instance.id
        catalog_code = catalog.code
        catalog_name = catalog.name
        version_name = version.version

    audit_records.append(
        {
            "timestamp": "2026-07-09T08:00:00+00:00",
            "event_type": "V2_INSTANCE_RESUMED",
            "metadata": {
                "actor_user_id": str(user_row.id),
                "instance_id": str(instance_id),
                "strategy_code": "SUPERTREND_V1",
                "previous_status": StrategyInstanceStatus.PAUSED.value,
                "new_status": StrategyInstanceStatus.ACTIVE.value,
                "changed_fields": ["status", "headers"],
                "old_values": {"status": StrategyInstanceStatus.PAUSED.value, "headers": "secret-header"},
                "new_values": {"status": StrategyInstanceStatus.ACTIVE.value, "proxy_url": "proxy-secret"},
                "raw_payload": "jsonl-raw-value",
            },
        }
    )

    client = _client(_current_user(user_row), monkeypatch)
    response = client.get(f"/api/debug/v2/instances/{instance_id}/details")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["instance"] == {
        "id": str(instance_id),
        "strategy_code": catalog_code,
        "strategy_name": catalog_name,
        "strategy_version": version_name,
        "status": StrategyInstanceStatus.PAUSED.value,
        "execution_mode": StrategyExecutionMode.PAPER_LIVE_DATA.value,
        "instance_label": "My Paper Strategy",
        "lots": 2,
        "side_preference": "BOTH",
        "created_at": body["instance"]["created_at"],
        "updated_at": body["instance"]["updated_at"],
    }

    history = body["history"]
    event_types = [row["event_type"] for row in history]
    assert "V2_INSTANCE_CREATED" in event_types
    assert "V2_INSTANCE_SETTINGS_EDITED" in event_types
    assert "V2_INSTANCE_RESUMED" in event_types

    settings_row = next(row for row in history if row["event_type"] == "V2_INSTANCE_SETTINGS_EDITED")
    assert settings_row["changed_fields"] == ["instance_label", "lots", "side_preference"]
    assert settings_row["old_values"] == {"instance_label": "Old", "lots": 1, "side_preference": "CE"}
    assert settings_row["new_values"] == {
        "instance_label": "My Paper Strategy",
        "lots": 2,
        "side_preference": "BOTH",
    }
    assert settings_row["actor_type"] == "internal_user"

    resumed_row = next(row for row in history if row["event_type"] == "V2_INSTANCE_RESUMED")
    assert resumed_row["changed_fields"] == ["status"]
    assert resumed_row["old_values"] == {"status": StrategyInstanceStatus.PAUSED.value}
    assert resumed_row["new_values"] == {"status": StrategyInstanceStatus.ACTIVE.value}

    paused_row = next(row for row in history if row["event_type"] == "V2_INSTANCE_PAUSED")
    assert paused_row["previous_status"] is None
    assert paused_row["new_status"] is None
    assert "old_values" not in paused_row
    assert paused_row["new_values"] == {
        "status": StrategyInstanceStatus.PAUSED.value,
        "execution_mode": StrategyExecutionMode.PAPER_LIVE_DATA.value,
        "lots": 2,
    }

    assert _contains_key(body, FORBIDDEN_KEYS) is False
    for hidden_value in (
        "catalog-secret-value",
        "version-token-value",
        "version-raw-value",
        "instance-config-secret",
        "hidden-token",
        "risk-secret-value",
        "old-secret-value",
        "raw-request-value",
        "settings-token-value",
        "dhan-client-value",
        "jsonl-raw-value",
        "previous-status-secret",
        "new-status-token",
        "status-token-value",
        "headers-secret-value",
    ):
        assert _contains_value(body, hidden_value) is False
    assert sum(execution_spies.values()) == 0


def test_non_owner_unknown_and_invalid_instance_get_404(mu_db, monkeypatch, audit_records):
    owner_row = make_user("details-real-owner@example.com", is_admin=True)
    intruder_row = make_user("details-intruder@example.com", is_admin=True)
    with session_scope() as db:
        _, _, instance = _seed_catalog_instance(db, user_id=owner_row.id)
        instance_id = instance.id

    intruder_client = _client(_current_user(intruder_row), monkeypatch)
    assert intruder_client.get(f"/api/debug/v2/instances/{instance_id}/details").status_code == 404
    assert intruder_client.get(f"/api/debug/v2/instances/{uuid.uuid4()}/details").status_code == 404
    assert intruder_client.get("/api/debug/v2/instances/not-a-uuid/details").status_code == 404


def test_debug_disabled_blocks_instance_details(mu_db, monkeypatch, audit_records):
    user_row = make_user("details-debug-off@example.com", is_admin=True)
    with session_scope() as db:
        _, _, instance = _seed_catalog_instance(db, user_id=user_row.id)
        instance_id = instance.id

    client = _client(_current_user(user_row), monkeypatch, debug_enabled=False)
    assert client.get(f"/api/debug/v2/instances/{instance_id}/details").status_code == 404


def test_details_does_not_require_mutation_or_runner_flags(mu_db, monkeypatch, audit_records):
    monkeypatch.delenv("MULTI_STRATEGY_MODEL", raising=False)
    monkeypatch.delenv("STRATEGY_INSTANCE_MUTATION_DEBUG", raising=False)
    monkeypatch.delenv("MULTI_STRATEGY_FANOUT", raising=False)
    monkeypatch.delenv("V2_PAPER_RUNNER_DEBUG", raising=False)
    user_row = make_user("details-flags@example.com", is_admin=True)
    with session_scope() as db:
        _, _, instance = _seed_catalog_instance(db, user_id=user_row.id)
        instance_id = instance.id

    client = _client(_current_user(user_row), monkeypatch)
    response = client.get(f"/api/debug/v2/instances/{instance_id}/details")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_details_does_not_mutate_db_or_create_execution_rows(
    mu_db,
    monkeypatch,
    audit_records,
    execution_spies,
):
    user_row = make_user("details-readonly@example.com", is_admin=True)
    with session_scope() as db:
        _, _, instance = _seed_catalog_instance(db, user_id=user_row.id)
        credential = models.UserStrategyWebhookCredential(
            instance_id=instance.id,
            webhook_key_hash="hash-before",
            message_secret_hash="msg-before",
            status="active",
            is_active=True,
        )
        db.add(credential)
        db.flush()
        instance_id = instance.id

    before = _db_snapshot()
    client = _client(_current_user(user_row), monkeypatch)
    response = client.get(f"/api/debug/v2/instances/{instance_id}/details")

    assert response.status_code == 200
    assert _db_snapshot() == before
    assert sum(execution_spies.values()) == 0


def _db_snapshot() -> dict[str, Any]:
    with session_scope() as db:
        instances = db.scalars(
            select(models.UserStrategyInstance).order_by(models.UserStrategyInstance.id)
        ).all()
        audit_logs = db.scalars(select(models.AuditLog).order_by(models.AuditLog.id)).all()
        credentials = db.scalars(
            select(models.UserStrategyWebhookCredential).order_by(models.UserStrategyWebhookCredential.id)
        ).all()
        return {
            "instances": [
                (
                    str(row.id),
                    row.status,
                    row.execution_mode,
                    row.instance_label,
                    row.lots,
                    row.side_preference,
                    json.dumps(row.config_json, sort_keys=True),
                    json.dumps(row.risk_config, sort_keys=True),
                    row.updated_at.isoformat(),
                )
                for row in instances
            ],
            "audit_logs": [
                (
                    str(row.id),
                    str(row.user_id) if row.user_id else None,
                    row.action,
                    json.dumps(row.audit_metadata, sort_keys=True),
                    row.created_at.isoformat(),
                )
                for row in audit_logs
            ],
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
