from __future__ import annotations

import json
from pathlib import Path
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
from app.core.feature_flags import MULTI_STRATEGY_FANOUT, V2_PAPER_RUNNER_DEBUG
from app.db import models
from app.db.engine import session_scope
from app.services.user_context import CurrentUser
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DEBUG_DIR = REPO_ROOT / "frontend" / "src" / "debug"
FRONTEND_APP = REPO_ROOT / "frontend" / "src" / "App.tsx"
FRONTEND_HEADER = REPO_ROOT / "frontend" / "src" / "components" / "Header.tsx"

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

FORBIDDEN_STATIC_TERMS = (
    "method:",
    "POST",
    "PATCH",
    "DELETE",
    "subscribe",
    "unsubscribe",
    "activate",
    "deactivate",
    "run fanout",
    "process jobs",
    "retry jobs",
    "generate webhook",
    "rotate secret",
    "go live",
    "paper trade",
    "square off",
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


def _contains_value(value: Any, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_value(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_contains_value(item, needle) for item in value)
    return str(value) == needle


def _client(user: CurrentUser) -> TestClient:
    from app.routers import strategies

    app = FastAPI()
    app.include_router(strategies.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _debug_client(monkeypatch, *, debug_enabled: bool = True) -> TestClient:
    from app.config import settings
    from app.routers import debug

    monkeypatch.setattr(settings, "DEBUG_ENABLED", debug_enabled, raising=False)
    app = FastAPI()
    app.include_router(debug.router, prefix="/api/debug")
    return TestClient(app)


def _current_user(row) -> CurrentUser:
    return CurrentUser(
        id=row.id,
        email=row.email,
        name=row.name,
        picture_url=None,
        is_admin=True,
        is_dev=False,
    )


def _seed_strategy_rows(db, *, user_id):
    catalog = models.StrategyCatalog(
        code="SUPERTREND_V1",
        name="Supertrend ATM Reversal",
        description="Read-only row",
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status=StrategyCatalogStatus.ACTIVE.value,
        metadata_json={
            "secret": "catalog-secret-value",
            "message_secret_hash": "catalog-hash-value",
        },
    )
    db.add(catalog)
    db.flush()
    version = models.StrategyVersion(
        strategy_id=catalog.id,
        version="v1",
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
        instance_label="Read-only paper",
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status=StrategyInstanceStatus.ACTIVE.value,
        execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
        lots=1,
        side_preference="CE",
        config_json={"secret": "instance-secret-value"},
        risk_config={"raw_payload": "instance-raw-value"},
    )
    db.add(instance)
    db.flush()
    credential = models.UserStrategyWebhookCredential(
        instance_id=instance.id,
        webhook_key_hash="webhook-hash-value",
        message_secret_hash="message-hash-value",
        status="active",
        is_active=True,
    )
    db.add(credential)
    db.flush()
    return catalog, version, instance


def _seed_debug_inspector_rows(db, *, user_id):
    catalog, version, instance = _seed_strategy_rows(db, user_id=user_id)
    signal = models.StrategySignal(
        strategy_name=catalog.code,
        signal_id="readonly-signal",
        instance_id=instance.id,
        strategy_version_id=version.id,
        payload_version="nova.v1",
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status="accepted",
        result_summary={"raw_response": {"secret": "debug-secret-value"}},
    )
    db.add(signal)
    db.flush()
    normalized = models.NormalizedOptionSignal(
        strategy_signal_id=signal.id,
        instance_id=instance.id,
        action="ENTRY",
        intent="BULLISH",
        symbol="NIFTY",
        instrument_type="OPTIDX",
        option_side="CE",
        strike_mode="MANUAL",
        resolved_strike=25000.0,
        expiry_mode="MANUAL",
        resolved_expiry=None,
        qty_mode="LOTS",
        lots=1,
        quantity=75,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_mapping_details={"raw_payload": "debug-raw-value"},
    )
    db.add(normalized)
    db.flush()
    job = models.StrategyExecutionJob(
        strategy_signal_id=signal.id,
        instance_id=instance.id,
        strategy_version_id=version.id,
        normalized_signal_id=normalized.id,
        user_id=user_id,
        strategy_name=catalog.code,
        signal_id="readonly-signal:job",
        signal_payload={"secret": "job-secret-value", "raw_payload": "job-raw-value"},
        lots=1,
        execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
        status="v2_completed_paper",
        attempts=1,
        max_attempts=1,
        result_summary={"access_token": "job-token-value", "status": "v2_completed_paper"},
    )
    db.add(job)
    db.flush()


def test_strategy_readonly_surfaces_exclude_secrets_and_webhook_hashes(mu_db):
    user = make_user("readonly-surfaces-admin@example.com", is_admin=True)
    with session_scope() as db:
        _seed_strategy_rows(db, user_id=user.id)

    client = _client(_current_user(user))
    catalog_body = client.get("/api/strategies/catalog").json()
    instances_body = client.get("/api/strategies/instances").json()

    assert _contains_key(catalog_body, FORBIDDEN_KEYS) is False
    assert _contains_key(instances_body, FORBIDDEN_KEYS) is False
    assert _contains_value(catalog_body, "catalog-secret-value") is False
    assert _contains_value(catalog_body, "version-token-value") is False
    assert _contains_value(instances_body, "webhook-hash-value") is False
    assert _contains_value(instances_body, "message-hash-value") is False


def test_v2_debug_inspector_excludes_sensitive_fields(mu_db, monkeypatch):
    monkeypatch.setenv(MULTI_STRATEGY_FANOUT, "true")
    monkeypatch.setenv(V2_PAPER_RUNNER_DEBUG, "true")
    user = make_user("readonly-debug-admin@example.com", is_admin=True)
    with session_scope() as db:
        _seed_debug_inspector_rows(db, user_id=user.id)

    client = _debug_client(monkeypatch)
    bodies = [
        client.get("/api/debug/v2/paper-fanout/status").json(),
        client.get("/api/debug/v2/paper-fanout/jobs").json(),
        client.get("/api/debug/v2/paper-fanout/instances").json(),
    ]

    assert all(_contains_key(body, FORBIDDEN_KEYS) is False for body in bodies)
    assert all(_contains_value(body, "debug-secret-value") is False for body in bodies)
    assert all(_contains_value(body, "job-token-value") is False for body in bodies)


def test_frontend_debug_panels_have_no_mutation_calls_or_execution_controls():
    for path in FRONTEND_DEBUG_DIR.glob("*.tsx"):
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for term in FORBIDDEN_STATIC_TERMS:
            assert term.lower() not in lowered, f"{term!r} found in {path}"


def test_frontend_internal_flags_default_off_and_gate_routes():
    app_text = FRONTEND_APP.read_text(encoding="utf-8")
    header_text = FRONTEND_HEADER.read_text(encoding="utf-8")

    assert "import.meta.env.VITE_ENABLE_V2_PAPER_STATUS === 'true'" in app_text
    assert "import.meta.env.VITE_ENABLE_STRATEGY_CATALOG_STATUS === 'true'" in app_text
    assert "enabled={canViewV2PaperStatus}" in app_text
    assert "enabled={canViewStrategyCatalogStatus}" in app_text
    assert "if (!enabled)" in (FRONTEND_DEBUG_DIR / "V2PaperStatusPanel.tsx").read_text(encoding="utf-8")
    assert "if (!enabled)" in (FRONTEND_DEBUG_DIR / "StrategyCatalogStatusPanel.tsx").read_text(encoding="utf-8")
    assert "canViewV2PaperStatus ? (" in header_text
    assert "canViewStrategyCatalogStatus ? (" in header_text


def test_lifecycle_mutation_controls_are_flag_gated_and_scoped():
    """Phase 2F-1 static guards.

    Mutation controls exist ONLY in StrategyCatalogStatusPanel, use ONLY the
    three approved lifecycle helpers, never reference runner/execution
    endpoints, and are gated behind VITE_ENABLE_STRATEGY_INSTANCE_MUTATION
    (default OFF) on top of the catalog-status flag.
    """
    app_text = FRONTEND_APP.read_text(encoding="utf-8")
    catalog_panel = (FRONTEND_DEBUG_DIR / "StrategyCatalogStatusPanel.tsx").read_text(encoding="utf-8")
    paper_panel = (FRONTEND_DEBUG_DIR / "V2PaperStatusPanel.tsx").read_text(encoding="utf-8")
    api_text = (REPO_ROOT / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")

    # Frontend flag: literal env check (defaults off) + combined with the
    # catalog-status visibility gate.
    assert "import.meta.env.VITE_ENABLE_STRATEGY_INSTANCE_MUTATION === 'true'" in app_text
    assert "STRATEGY_INSTANCE_MUTATION_UI_ENABLED && canViewStrategyCatalogStatus" in app_text

    # Panel-side double gate + confirmation copy.
    assert "enabled && mutationEnabled" in catalog_panel
    assert "Paper only. No orders will be placed." in catalog_panel

    # Only the approved lifecycle helpers are referenced by the catalog panel.
    approved_helpers = (
        "createPaperStrategyInstance",
        "pausePaperStrategyInstance",
        "resumePaperStrategyInstance",
    )
    for helper in approved_helpers:
        assert helper in catalog_panel
    for forbidden in ("run-once", "process-ready", "paper-fanout", "runV2", "processReady"):
        assert forbidden not in catalog_panel, forbidden

    # The v2 paper status panel remains fully read-only.
    for helper in approved_helpers:
        assert helper not in paper_panel

    # The api helpers call only the debug lifecycle endpoints.
    assert "'/api/debug/v2/instances'" in api_text
    assert "/api/debug/v2/instances/" in api_text
    for forbidden in ("/api/debug/v2/paper-fanout/run-once", "/api/debug/v2/paper-fanout/process-ready"):
        occurrences = api_text.count(forbidden)
        assert occurrences == 0, f"lifecycle helpers must not reference {forbidden}"


def test_strategy_status_get_endpoints_do_not_mutate_db(mu_db):
    user = make_user("readonly-db-admin@example.com", is_admin=True)
    with session_scope() as db:
        _seed_strategy_rows(db, user_id=user.id)
    before = _strategy_db_snapshot()
    client = _client(_current_user(user))

    assert client.get("/api/strategies/catalog").status_code == 200
    assert client.get("/api/strategies/instances").status_code == 200

    assert _strategy_db_snapshot() == before


def _strategy_db_snapshot() -> dict[str, Any]:
    with session_scope() as db:
        catalogs = db.scalars(select(models.StrategyCatalog).order_by(models.StrategyCatalog.id)).all()
        versions = db.scalars(select(models.StrategyVersion).order_by(models.StrategyVersion.id)).all()
        instances = db.scalars(select(models.UserStrategyInstance).order_by(models.UserStrategyInstance.id)).all()
        credentials = db.scalars(
            select(models.UserStrategyWebhookCredential).order_by(models.UserStrategyWebhookCredential.id)
        ).all()
        return {
            "catalogs": [
                (
                    str(row.id),
                    row.status,
                    json.dumps(row.metadata_json, sort_keys=True),
                    row.updated_at.isoformat(),
                )
                for row in catalogs
            ],
            "versions": [
                (
                    str(row.id),
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
                    json.dumps(row.config_json, sort_keys=True),
                    json.dumps(row.risk_config, sort_keys=True),
                    row.updated_at.isoformat(),
                )
                for row in instances
            ],
            "credentials": [
                (
                    str(row.id),
                    row.webhook_key_hash,
                    row.message_secret_hash,
                    row.status,
                    row.is_active,
                )
                for row in credentials
            ],
        }
