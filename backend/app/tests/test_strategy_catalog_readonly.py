from __future__ import annotations

import json
import uuid
from typing import Any

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
    "headers",
    "message_secret_hash",
    "metadata",
    "metadata_json",
    "proxy_url",
    "raw_payload",
    "raw_response",
    "request",
    "response",
    "risk_config",
    "secret",
    "signal_payload",
    "token",
    "webhook_key_hash",
}


def _client(user: CurrentUser) -> TestClient:
    from app.routers import strategies

    app = FastAPI()
    app.include_router(strategies.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _current_user(row, *, is_admin: bool = True) -> CurrentUser:
    return CurrentUser(
        id=row.id,
        email=row.email,
        name=row.name,
        picture_url=None,
        is_admin=is_admin,
        is_dev=False,
    )


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


def _seed_catalog(db):
    active = models.StrategyCatalog(
        code="SUPERTREND_V1",
        name="Supertrend ATM Reversal",
        description="Read-only catalog row",
        status=StrategyCatalogStatus.ACTIVE.value,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        metadata_json={
            "secret": "do-not-return",
            "webhook_key_hash": "do-not-return",
        },
    )
    coming_soon = models.StrategyCatalog(
        code="ORB_BREAKOUT",
        name="ORB Breakout",
        description="Planned strategy",
        status=StrategyCatalogStatus.COMING_SOON.value,
        source_type=StrategySourceType.USER_NO_WEBHOOK.value,
    )
    db.add_all([active, coming_soon])
    db.flush()
    version = models.StrategyVersion(
        strategy_id=active.id,
        version="v1",
        status=StrategyCatalogStatus.ACTIVE.value,
        payload_version="nova.v1",
        config_schema={"access_token": "do-not-return"},
        metadata_json={"raw_payload": "do-not-return"},
    )
    db.add(version)
    db.flush()
    return active, coming_soon, version


def _seed_instance(db, *, user_id, catalog, version):
    row = models.UserStrategyInstance(
        user_id=user_id,
        strategy_id=catalog.id,
        strategy_version_id=version.id,
        instance_label="Local paper",
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status=StrategyInstanceStatus.ACTIVE.value,
        execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
        lots=2,
        side_preference="CE",
        config_json={"secret": "do-not-return"},
        risk_config={"raw_payload": "do-not-return"},
    )
    db.add(row)
    db.flush()
    return row


def _db_snapshot() -> dict[str, Any]:
    with session_scope() as db:
        catalogs = db.scalars(select(models.StrategyCatalog).order_by(models.StrategyCatalog.id)).all()
        versions = db.scalars(select(models.StrategyVersion).order_by(models.StrategyVersion.id)).all()
        instances = db.scalars(select(models.UserStrategyInstance).order_by(models.UserStrategyInstance.id)).all()
        return {
            "catalogs": [
                (
                    str(row.id),
                    row.code,
                    row.name,
                    row.status,
                    json.dumps(row.metadata_json, sort_keys=True),
                    row.updated_at.isoformat(),
                )
                for row in catalogs
            ],
            "versions": [
                (
                    str(row.id),
                    row.version,
                    row.status,
                    json.dumps(row.config_schema, sort_keys=True),
                    json.dumps(row.metadata_json, sort_keys=True),
                    row.updated_at.isoformat(),
                )
                for row in versions
            ],
            "instances": [
                (
                    str(row.id),
                    row.status,
                    row.execution_mode,
                    row.lots,
                    row.side_preference,
                    json.dumps(row.config_json, sort_keys=True),
                    json.dumps(row.risk_config, sort_keys=True),
                    row.updated_at.isoformat(),
                )
                for row in instances
            ],
        }


def test_strategy_catalog_requires_internal_user(monkeypatch):
    user = CurrentUser(
        id=uuid.uuid4(),
        email="readonly-user@example.com",
        is_admin=False,
        is_dev=False,
    )

    response = _client(user).get("/api/strategies/catalog")

    assert response.status_code == 403


def test_strategy_catalog_returns_versions_without_sensitive_fields(mu_db):
    user = make_user("catalog-admin@example.com", is_admin=True)
    with session_scope() as db:
        _seed_catalog(db)

    response = _client(_current_user(user)).get("/api/strategies/catalog")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["database_configured"] is True
    assert {row["code"] for row in body["catalog"]} == {"SUPERTREND_V1", "ORB_BREAKOUT"}
    supertrend = next(row for row in body["catalog"] if row["code"] == "SUPERTREND_V1")
    assert supertrend["status"] == StrategyCatalogStatus.ACTIVE.value
    assert supertrend["versions"][0]["version"] == "v1"
    assert supertrend["versions"][0]["payload_version"] == "nova.v1"
    assert _contains_key(body, FORBIDDEN_KEYS) is False


def test_strategy_instances_are_scoped_to_current_user_and_safe(mu_db):
    user = make_user("instances-admin@example.com", is_admin=True)
    other = make_user("instances-other@example.com", is_admin=True)
    with session_scope() as db:
        catalog, _, version = _seed_catalog(db)
        own = _seed_instance(db, user_id=user.id, catalog=catalog, version=version)
        _seed_instance(db, user_id=other.id, catalog=catalog, version=version)

    response = _client(_current_user(user)).get("/api/strategies/instances")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert len(body["instances"]) == 1
    assert body["instances"][0]["id"] == str(own.id)
    assert body["instances"][0]["strategy_code"] == "SUPERTREND_V1"
    assert body["instances"][0]["execution_mode"] == StrategyExecutionMode.PAPER_LIVE_DATA.value
    assert _contains_key(body, FORBIDDEN_KEYS) is False


def test_strategy_status_endpoints_do_not_mutate_db(mu_db):
    user = make_user("readonly-admin@example.com", is_admin=True)
    with session_scope() as db:
        catalog, _, version = _seed_catalog(db)
        _seed_instance(db, user_id=user.id, catalog=catalog, version=version)
    before = _db_snapshot()
    client = _client(_current_user(user))

    assert client.get("/api/strategies/catalog").status_code == 200
    assert client.get("/api/strategies/instances").status_code == 200

    assert _db_snapshot() == before
