"""force_delete_strategy: permanent deletion regardless of approval/publish/
history state, blocked only while an instance is actively running."""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import models
from app.db.engine import session_scope
from app.services import personal_pine_service, pine_conversion_provider
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_admin_claude_pine_conversion import (
    LAYER,
    BACKTEST_LAYER,
    SOURCE,
    _client as _admin_client,
    _enable,
    _output,
    _submit,
)

pytest_plugins = ("app.tests.conftest_multiuser",)


def _build_full_instance(user, *, status: str = "stopped") -> dict[str, uuid.UUID]:
    """Strategy + approved version + instance + configuration revision +
    engine-start operation + position + position event, all linked --
    exercises every RESTRICT-constrained table force_delete_strategy must
    clear before the strategy/version/instance rows themselves can go."""
    with session_scope() as db:
        strategy = models.StrategyCatalog(
            code=f"pine-{uuid.uuid4().hex[:10]}",
            display_name="Force Delete Target",
            owner_type="personal",
            owner_user_id=user.id,
            status="active",
        )
        db.add(strategy)
        db.flush()
        version = models.StrategyVersion(
            strategy_id=strategy.id,
            version="1.0",
            payload_spec_version="nova.v1",
            source_journey="personal_tradingview",
            status="approved",
            execution_kind="external_webhook",
        )
        db.add(version)
        db.flush()
        instance = models.StrategyInstance(
            user_id=user.id,
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            source_journey="PERSONAL_TRADINGVIEW",
            label="My Pine",
            status=status,
            execution_mode="paper_live_data",
            current_lots=1,
        )
        db.add(instance)
        db.flush()
        revision = models.StrategyConfigurationRevision(
            user_id=user.id,
            strategy_instance_id=instance.id,
            strategy_version_id=version.id,
            mode="paper",
            revision=1,
            configuration_json={"lots": 1, "allowed_option_side": "BOTH"},
            risk_json={},
            status="active",
        )
        db.add(revision)
        db.flush()
        db.add(models.UserEngineConfig(
            user_id=user.id,
            selected_strategy_instance_id=instance.id,
            selected_configuration_revision_id=revision.id,
            selected_configuration_revision=revision.revision,
        ))
        op = models.EngineStartOperation(
            user_id=user.id,
            idempotency_key=f"idem-{uuid.uuid4().hex[:10]}",
            payload_hash="a" * 64,
            mode="paper",
            strategy_instance_id=instance.id,
            strategy_version_id=version.id,
            configuration_revision_id=revision.id,
            configuration_revision=revision.revision,
            status="completed",
        )
        db.add(op)
        position = models.StrategyInstancePosition(
            user_id=user.id,
            strategy_instance_id=instance.id,
            execution_mode="paper",
            position_state="open",
        )
        db.add(position)
        db.flush()
        db.add(models.PositionEvent(
            position_id=position.id, user_id=user.id, event_type="entry_filled",
            source="entry", idempotency_key=f"evt-{uuid.uuid4().hex[:10]}",
        ))
        artifact = models.StrategySourceArtifact(
            strategy_version_id=version.id,
            artifact_type="pine_script",
            content="//@version=6\nindicator(\"x\")",
            content_sha256="b" * 64,
            submitted_by_user_id=user.id,
        )
        db.add(artifact)
        db.flush()
        analysis = models.PineSemanticAnalysis(
            source_artifact_id=artifact.id,
            source_sha256="c" * 64,
            analyzer_version="test-analyzer-v1",
            registry_id="nova.pine-capabilities",
            registry_version="v1",
            registry_sha256="d" * 64,
            analysis_schema_version="test-schema-v1",
            effective_capability_level="L0_DIRECTLY_SUPPORTED",
            confidence="HIGH_CONFIDENCE_MATCH",
            analysis_payload={},
            analysis_payload_sha256="e" * 64,
        )
        db.add(analysis)
        # Durable records that outlive the revision they last referenced --
        # force_delete_strategy must detach (not delete) these.
        signal = models.StrategySignal(strategy_name=strategy.code, signal_id="sig-1")
        db.add(signal)
        db.flush()
        job = models.StrategyExecutionJob(
            strategy_signal_id=signal.id,
            user_id=user.id,
            strategy_name=strategy.code,
            signal_id="sig-1",
            signal_payload={},
            lots=1,
            execution_mode="paper_live_data",
            configuration_revision_id=revision.id,
            configuration_revision=revision.revision,
        )
        db.add(job)
        trade = models.PortfolioTrade(
            user_id=user.id,
            mode="paper",
            strategy_name=strategy.code,
            exit_order_id=f"exit-{uuid.uuid4().hex[:10]}",
            configuration_revision_id=revision.id,
        )
        db.add(trade)
        run = models.UserRun(
            user_id=user.id,
            strategy_version_id=version.id,
            configuration_revision_id=revision.id,
            configuration_revision=revision.revision,
        )
        db.add(run)
        db.flush()
        return {
            "strategy_id": strategy.id,
            "version_id": version.id,
            "instance_id": instance.id,
            "revision_id": revision.id,
            "engine_start_operation_id": op.id,
            "position_id": position.id,
            "artifact_id": artifact.id,
            "analysis_id": analysis.id,
            "job_id": job.id,
            "trade_id": trade.id,
            "run_id": run.id,
        }


def test_force_delete_removes_full_instance_history(mu_db):
    user = make_user("force-delete-owner@example.com")
    ids = _build_full_instance(user, status="stopped")

    result = personal_pine_service.force_delete_strategy(user.id, ids["strategy_id"], is_admin=False)
    assert result["deleted"] is True

    with session_scope() as db:
        assert db.get(models.StrategyCatalog, ids["strategy_id"]) is None
        assert db.get(models.StrategyVersion, ids["version_id"]) is None
        assert db.get(models.StrategyInstance, ids["instance_id"]) is None
        assert db.get(models.StrategyConfigurationRevision, ids["revision_id"]) is None
        assert db.get(models.EngineStartOperation, ids["engine_start_operation_id"]) is None
        assert db.get(models.StrategyInstancePosition, ids["position_id"]) is None
        assert db.scalar(
            select(models.PositionEvent).where(models.PositionEvent.position_id == ids["position_id"])
        ) is None
        engine_config = db.scalar(
            select(models.UserEngineConfig).where(models.UserEngineConfig.user_id == user.id)
        )
        assert engine_config is not None
        assert engine_config.selected_strategy_instance_id is None

        # Deleted alongside the version/instance chain.
        assert db.get(models.StrategySourceArtifact, ids["artifact_id"]) is None
        assert db.get(models.PineSemanticAnalysis, ids["analysis_id"]) is None

        # Durable history rows survive, detached rather than deleted.
        job = db.get(models.StrategyExecutionJob, ids["job_id"])
        assert job is not None
        assert job.configuration_revision_id is None
        trade = db.get(models.PortfolioTrade, ids["trade_id"])
        assert trade is not None
        assert trade.configuration_revision_id is None
        run = db.get(models.UserRun, ids["run_id"])
        assert run is not None
        assert run.strategy_version_id is None
        assert run.configuration_revision_id is None


def test_force_delete_blocked_while_instance_is_running(mu_db):
    user = make_user("force-delete-running@example.com")
    ids = _build_full_instance(user, status="active")

    with pytest.raises(personal_pine_service.PineWorkflowError) as exc_info:
        personal_pine_service.force_delete_strategy(user.id, ids["strategy_id"], is_admin=False)
    assert exc_info.value.code == "INSTANCE_RUNNING"

    with session_scope() as db:
        assert db.get(models.StrategyCatalog, ids["strategy_id"]) is not None


def test_force_delete_rejects_non_owner_non_admin(mu_db):
    owner = make_user("force-delete-real-owner@example.com")
    stranger = make_user("force-delete-stranger@example.com")
    ids = _build_full_instance(owner, status="stopped")

    with pytest.raises(personal_pine_service.PineWorkflowError) as exc_info:
        personal_pine_service.force_delete_strategy(stranger.id, ids["strategy_id"], is_admin=False)
    assert exc_info.value.code == "NOT_FOUND"

    with session_scope() as db:
        assert db.get(models.StrategyCatalog, ids["strategy_id"]) is not None


def test_admin_force_deletes_published_approved_strategy_with_subscribers(mu_db, monkeypatch):
    """The exact scenario this feature exists for: a NOVA_SHARED strategy
    that's been through full admin review, approval and publish, with real
    subscribers -- unpublish_shared() only archives this; force_delete_strategy
    removes it and its subscriptions permanently."""
    from app.services import strategy_fanout

    _enable(monkeypatch)
    admin = make_user("force-delete-admin@example.com", is_admin=True)
    client = _admin_client(admin)
    conversion = _submit(client, source=SOURCE, name="Force Delete Publish Target")
    fake = pine_conversion_provider.FakePineConversionProvider(
        _output(conversion, layer=LAYER, backtest_layer=BACKTEST_LAYER)
    )
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)
    client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert")
    client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/approve",
        json={"reason": "Reviewed for TradingView compile only"},
    )
    published = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/publish",
        json={"catalog_code": "force-delete-publish-target"},
    )
    assert published.status_code == 200, published.text
    strategy_id = uuid.UUID(published.json()["strategy_id"])

    subscriber = make_user("force-delete-subscriber@example.com")
    strategy_fanout.subscribe_user(subscriber.id, "force-delete-publish-target", lots=1, execution_mode="signal_only")

    # subscribe_user mirrors an ACTIVE strategy_instance in the same
    # transaction -- force_delete_strategy correctly blocks on that (see
    # test_force_delete_blocked_while_instance_is_running); deactivating
    # first is the same real-world step unpublish_shared already requires.
    with pytest.raises(personal_pine_service.PineWorkflowError) as blocked:
        personal_pine_service.force_delete_strategy(admin.id, strategy_id, is_admin=True)
    assert blocked.value.code == "INSTANCE_RUNNING"
    strategy_fanout.set_subscription_active(subscriber.id, "force-delete-publish-target", False)

    result = personal_pine_service.force_delete_strategy(admin.id, strategy_id, is_admin=True)
    assert result["deleted"] is True
    assert result["deleted_subscriptions"] == 1

    with session_scope() as db:
        assert db.get(models.StrategyCatalog, strategy_id) is None
        assert db.scalar(
            select(models.StrategySubscription).where(
                models.StrategySubscription.strategy_name == "force-delete-publish-target"
            )
        ) is None
        assert db.scalar(
            select(models.PineConversionRequest).where(
                models.PineConversionRequest.strategy_id == strategy_id
            )
        ) is None


def _owner_client(user_model):
    from app.auth.dependencies import get_current_user
    from app.routers import personal_pine
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    app.include_router(personal_pine.router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(user_model)
    return TestClient(app)


def _admin_router_client(user_model):
    from app.auth.dependencies import require_admin
    from app.routers import admin as admin_router_module
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    app.include_router(admin_router_module.router)
    app.dependency_overrides[require_admin] = lambda: current_user_from_model(user_model)
    return TestClient(app)


def test_owner_force_delete_endpoint_deletes_an_approved_instance_linked_strategy(mu_db):
    user = make_user("force-delete-router-owner@example.com")
    ids = _build_full_instance(user, status="stopped")

    response = _owner_client(user).post(f"/api/personal-pine-strategies/{ids['strategy_id']}/force-delete")

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] is True
    with session_scope() as db:
        assert db.get(models.StrategyCatalog, ids["strategy_id"]) is None


def test_owner_delete_endpoint_still_refuses_the_same_strategy_without_force(mu_db):
    """delete_strategy (the pre-existing, non-force endpoint) must keep
    refusing an approved/instance-linked strategy -- force-delete is an
    addition, not a replacement of the safe default."""
    user = make_user("force-delete-router-safe-default@example.com")
    ids = _build_full_instance(user, status="stopped")

    response = _owner_client(user).delete(f"/api/personal-pine-strategies/{ids['strategy_id']}")

    assert response.status_code == 409
    assert response.json()["reason"] == "APPROVED_VERSION_EXISTS"
    with session_scope() as db:
        assert db.get(models.StrategyCatalog, ids["strategy_id"]) is not None


def test_admin_force_delete_endpoint_works_on_another_users_strategy(mu_db):
    owner = make_user("force-delete-router-target-owner@example.com")
    admin = make_user("force-delete-router-admin@example.com", is_admin=True)
    ids = _build_full_instance(owner, status="stopped")

    response = _admin_router_client(admin).post(f"/api/admin/strategies/{ids['strategy_id']}/force-delete")

    assert response.status_code == 200, response.text
    with session_scope() as db:
        assert db.get(models.StrategyCatalog, ids["strategy_id"]) is None


def test_owner_force_delete_endpoint_rejects_another_users_strategy(mu_db):
    owner = make_user("force-delete-router-owner-only@example.com")
    stranger = make_user("force-delete-router-stranger@example.com")
    ids = _build_full_instance(owner, status="stopped")

    response = _owner_client(stranger).post(f"/api/personal-pine-strategies/{ids['strategy_id']}/force-delete")

    assert response.status_code == 404
    with session_scope() as db:
        assert db.get(models.StrategyCatalog, ids["strategy_id"]) is not None
