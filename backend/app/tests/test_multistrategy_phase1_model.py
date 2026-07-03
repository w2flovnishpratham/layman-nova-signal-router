from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.core.enums import (
    ExpiryMode,
    OptionIntent,
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
    StrategySourceType,
    StrikeMode,
)
from app.db import models
from app.db.engine import get_engine, session_scope
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _seed_minimal_strategy(db, code: str = "TEST_STRATEGY"):
    catalog = models.StrategyCatalog(
        code=code,
        name=code.title(),
        status=StrategyCatalogStatus.ACTIVE.value,
        source_type=StrategySourceType.BACKEND_HOSTED.value,
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


def test_phase1_migration_imports_cleanly():
    backend_root = Path(__file__).resolve().parents[2]
    path = backend_root / "alembic" / "versions" / "20260703_0006_multistrategy_data_model.py"
    spec = importlib.util.spec_from_file_location("migration_check_phase1", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.revision == "0006_multistrategy_data_model"
    assert module.down_revision == "0005_user_setup_profiles"
    assert hasattr(module, "upgrade")
    assert hasattr(module, "downgrade")


def test_phase1_models_create_and_link(mu_db):
    user = make_user("phase1-model@example.com")
    with session_scope() as db:
        catalog, version = _seed_minimal_strategy(db)
        instance = models.UserStrategyInstance(
            user_id=user.id,
            strategy_id=catalog.id,
            strategy_version_id=version.id,
            source_type=StrategySourceType.BACKEND_HOSTED.value,
            status=StrategyInstanceStatus.ACTIVE.value,
            execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
            lots=2,
            side_preference="CE",
            risk_config={"max_lots_per_order": 2},
        )
        db.add(instance)
        db.flush()

        credential = models.UserStrategyWebhookCredential(
            instance_id=instance.id,
            webhook_key_hash="sha256:webhook-key",
            message_secret_hash="sha256:message-secret",
        )
        db.add(credential)

        signal = models.StrategySignal(
            strategy_name="test-strategy",
            signal_id="signal-001",
            instance_id=instance.id,
            strategy_version_id=version.id,
            payload_version="nova.v1",
            source_type=StrategySourceType.BACKEND_HOSTED.value,
            received_ip="127.0.0.1",
            dedupe_key="test-strategy:signal-001",
            status="accepted",
        )
        db.add(signal)
        db.flush()

        normalized = models.NormalizedOptionSignal(
            strategy_signal_id=signal.id,
            instance_id=instance.id,
            action="ENTRY",
            intent=OptionIntent.BULLISH.value,
            symbol="NIFTY",
            instrument_type="OPTIDX",
            option_side="CE",
            strike_mode=StrikeMode.ATM.value,
            resolved_strike=24500,
            expiry_mode=ExpiryMode.NEXT_WEEKLY.value,
            resolved_expiry=date(2026, 7, 9),
            qty_mode="LOTS",
            lots=2,
            quantity=130,
            order_type="MARKET",
            product_type="INTRADAY",
            raw_mapping_details={"source": "unit_test"},
        )
        db.add(normalized)
        db.flush()

        job = models.StrategyExecutionJob(
            strategy_signal_id=signal.id,
            instance_id=instance.id,
            strategy_version_id=version.id,
            normalized_signal_id=normalized.id,
            user_id=user.id,
            strategy_name="test-strategy",
            signal_id="signal-001",
            signal_payload={"payload_format": "NOVA"},
            lots=2,
            execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
            locked_by="unit-test-worker",
            status="queued",
        )
        db.add(job)
        db.flush()

        assert db.get(models.UserStrategyWebhookCredential, credential.id).message_secret_hash == "sha256:message-secret"
        assert db.get(models.NormalizedOptionSignal, normalized.id).raw_mapping_details == {"source": "unit_test"}
        assert db.get(models.StrategyExecutionJob, job.id).normalized_signal_id == normalized.id


def test_strategy_catalog_seed_is_idempotent(mu_db):
    from scripts.backfill_multistrategy import seed_strategy_catalog

    with session_scope() as db:
        first = seed_strategy_catalog(db)
        second = seed_strategy_catalog(db)
        catalogs = db.scalars(select(models.StrategyCatalog)).all()
        versions = db.scalars(select(models.StrategyVersion)).all()

    assert first["catalog_created"] == 5
    assert first["versions_created"] == 5
    assert second["catalog_created"] == 0
    assert len(catalogs) == 5
    assert len(versions) == 5
    supertrend = next(row for row in catalogs if row.code == "SUPERTREND_V1")
    assert supertrend.status == StrategyCatalogStatus.ACTIVE.value
    assert "TRADINGVIEW_NIFTY_V1" in supertrend.metadata_json["aliases"]


def test_supertrend_backfill_is_idempotent_and_copies_legacy_fields(mu_db):
    from scripts.backfill_multistrategy import backfill_supertrend_instances, seed_strategy_catalog

    user = make_user("phase1-backfill@example.com")
    with session_scope() as db:
        subscription = models.StrategySubscription(
            user_id=user.id,
            strategy_name="supertrend",
            active=True,
            lots=3,
            execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
        )
        db.add(subscription)
        db.add(
            models.UserStrategyRiskControl(
                user_id=user.id,
                strategy_name="supertrend",
                max_lots_per_order=3,
                max_orders_per_day=4,
                max_loss_per_day_paise=500000,
            )
        )
        db.flush()

        seed_strategy_catalog(db)
        first = backfill_supertrend_instances(db)
        second = backfill_supertrend_instances(db)
        instances = db.scalars(select(models.UserStrategyInstance)).all()

    assert first["instances_created"] == 1
    assert second["instances_created"] == 0
    assert second["instances_updated"] == 1
    assert len(instances) == 1
    instance = instances[0]
    assert instance.user_id == user.id
    assert instance.legacy_subscription_id == subscription.id
    assert instance.lots == 3
    assert instance.execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value
    assert instance.status == StrategyInstanceStatus.ACTIVE.value
    assert instance.risk_config["max_lots_per_order"] == 3
    assert instance.risk_config["max_orders_per_day"] == 4


def test_one_active_real_orders_strategy_per_user_constraint(mu_db):
    user = make_user("phase1-unique@example.com")
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            first_catalog, first_version = _seed_minimal_strategy(db, "REAL_A")
            second_catalog, second_version = _seed_minimal_strategy(db, "REAL_B")
            db.add_all(
                [
                    models.UserStrategyInstance(
                        user_id=user.id,
                        strategy_id=first_catalog.id,
                        strategy_version_id=first_version.id,
                        status=StrategyInstanceStatus.ACTIVE.value,
                        execution_mode=StrategyExecutionMode.REAL_ORDERS.value,
                        lots=1,
                    ),
                    models.UserStrategyInstance(
                        user_id=user.id,
                        strategy_id=second_catalog.id,
                        strategy_version_id=second_version.id,
                        status=StrategyInstanceStatus.ACTIVE.value,
                        execution_mode=StrategyExecutionMode.REAL_ORDERS.value,
                        lots=1,
                    ),
                ]
            )


def test_phase1_foreign_keys_and_strategy_subscriptions_unchanged(mu_db):
    inspector = inspect(get_engine())

    subscription_columns = {column["name"] for column in inspector.get_columns("strategy_subscriptions")}
    assert subscription_columns == {
        "id",
        "user_id",
        "strategy_name",
        "active",
        "lots",
        "execution_mode",
        "created_at",
        "updated_at",
    }

    signal_fks = {
        (fk["referred_table"], tuple(fk["constrained_columns"]))
        for fk in inspector.get_foreign_keys("normalized_option_signals")
    }
    assert ("strategy_signals", ("strategy_signal_id",)) in signal_fks
    assert ("user_strategy_instances", ("instance_id",)) in signal_fks

    job_columns = {column["name"] for column in inspector.get_columns("strategy_execution_jobs")}
    assert {"instance_id", "strategy_version_id", "normalized_signal_id", "locked_by", "dead_letter_reason"} <= job_columns
