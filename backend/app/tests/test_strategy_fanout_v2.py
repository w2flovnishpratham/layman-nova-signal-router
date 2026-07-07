from __future__ import annotations

import inspect

from app.core.enums import (
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
    StrategySourceType,
)
from app.db import models
from app.db.engine import session_scope
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _raw_payload(**overrides):
    data = {
        "version": "nova.v1",
        "secret": "super-secret-value",
        "signal_id": "phase2b-signal-001",
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
        "timestamp": "2026-07-03T09:15:00+05:30",
    }
    data.update(overrides)
    return data


def _legacy_signal(signal_id: str):
    from app.config import DEFAULT_STRATEGY_CODE
    from app.schemas.signal import NormalizedSignal

    raw_payload = {
        "secret": "legacy-secret",
        "signal_id": signal_id,
        "strategy_code": DEFAULT_STRATEGY_CODE,
        "action": "ENTRY",
        "side": "BUY",
        "symbol": "NIFTY",
        "order_type": "MARKET",
        "product_type": "INTRADAY",
    }
    return NormalizedSignal(
        payload_format="NOVA",
        secret="legacy-secret",
        signal_id=signal_id,
        strategy_code=DEFAULT_STRATEGY_CODE,
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        qty=1,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload=raw_payload,
    )


def _seed_supertrend(db):
    catalog = models.StrategyCatalog(
        code="SUPERTREND_V1",
        name="Supertrend",
        status=StrategyCatalogStatus.ACTIVE.value,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        metadata_json={
            "aliases": ["supertrend", "TRADINGVIEW_NIFTY_V1"],
            "legacy_strategy_names": ["supertrend"],
        },
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


def _add_instance(
    db,
    *,
    user_id,
    catalog,
    version,
    status=StrategyInstanceStatus.ACTIVE.value,
    execution_mode=StrategyExecutionMode.SIGNAL_ONLY.value,
    side_preference=None,
    lots=1,
):
    instance = models.UserStrategyInstance(
        user_id=user_id,
        strategy_id=catalog.id,
        strategy_version_id=version.id,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status=status,
        execution_mode=execution_mode,
        side_preference=side_preference,
        lots=lots,
    )
    db.add(instance)
    db.flush()
    return instance


def test_v2_planner_finds_active_supertrend_instances_and_normalizes(mu_db, monkeypatch):
    from app.services.strategy_fanout_v2 import DRY_RUN_STATUS, plan_strategy_fanout_v2

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    alice = make_user("phase2b-alice@example.com")
    bob = make_user("phase2b-bob@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=alice.id, catalog=catalog, version=version)
        _add_instance(
            db,
            user_id=bob.id,
            catalog=catalog,
            version=version,
            execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
        )

        result = plan_strategy_fanout_v2(
            db,
            _raw_payload(signal_id="phase2b-active"),
            "SUPERTREND_V1",
        )

        assert result.ok is True
        assert result.strategy_code == "SUPERTREND_V1"
        assert result.strategy_version_id == version.id
        assert result.subscriber_count == 2
        assert result.planned_count == 2
        assert result.skipped_count == 0
        assert result.error_count == 0
        assert {plan.user_id for plan in result.plans} == {alice.id, bob.id}
        assert {plan.status for plan in result.plans} == {DRY_RUN_STATUS}
        assert {plan.normalized_draft.option_side for plan in result.plans} == {"CE"}
        assert db.query(models.StrategyExecutionJob).count() == 0


def test_paused_and_disabled_instances_are_skipped(mu_db, monkeypatch):
    from app.services.strategy_fanout_v2 import (
        DISABLED_INSTANCE,
        PAUSED_INSTANCE,
        plan_strategy_fanout_v2,
    )

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    active_user = make_user("phase2b-active@example.com")
    paused_user = make_user("phase2b-paused@example.com")
    disabled_user = make_user("phase2b-disabled@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=active_user.id, catalog=catalog, version=version)
        _add_instance(
            db,
            user_id=paused_user.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.PAUSED.value,
        )
        _add_instance(
            db,
            user_id=disabled_user.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.DISABLED.value,
        )

        result = plan_strategy_fanout_v2(
            db,
            _raw_payload(signal_id="phase2b-paused-disabled"),
            "SUPERTREND_V1",
        )

    assert result.ok is True
    assert result.subscriber_count == 3
    assert result.planned_count == 1
    assert result.skipped_count == 2
    assert {skip.reason for skip in result.skips} == {
        PAUSED_INSTANCE,
        DISABLED_INSTANCE,
    }


def test_real_orders_instances_are_skipped_before_v2_queue_creation(mu_db, monkeypatch):
    from app.services.strategy_fanout_v2 import REAL_ORDERS_NOT_SUPPORTED_YET, plan_strategy_fanout_v2

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    paper_user = make_user("phase2d6-paper@example.com")
    real_user = make_user("phase2d6-real@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(
            db,
            user_id=paper_user.id,
            catalog=catalog,
            version=version,
            execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
        )
        real_instance = _add_instance(
            db,
            user_id=real_user.id,
            catalog=catalog,
            version=version,
            execution_mode=StrategyExecutionMode.REAL_ORDERS.value,
        )

        result = plan_strategy_fanout_v2(
            db,
            _raw_payload(signal_id="phase2d6-real-skip"),
            "SUPERTREND_V1",
            dry_run=False,
        )

    assert result.ok is True
    assert result.planned_count == 1
    assert result.skipped_count == 1
    assert result.skips[0].reason == REAL_ORDERS_NOT_SUPPORTED_YET
    assert result.skips[0].instance_id == real_instance.id
    assert result.plans[0].execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value


def test_alias_tradingview_nifty_resolves_to_supertrend_version(mu_db, monkeypatch):
    from app.services.strategy_fanout_v2 import plan_strategy_fanout_v2

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    user = make_user("phase2b-alias@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=user.id, catalog=catalog, version=version)

        result = plan_strategy_fanout_v2(
            db,
            _raw_payload(
                signal_id="phase2b-alias",
                strategy_code="TRADINGVIEW_NIFTY_V1",
            ),
            "TRADINGVIEW_NIFTY_V1",
        )

    assert result.ok is True
    assert result.strategy_code == "SUPERTREND_V1"
    assert result.strategy_version_id == version.id
    assert result.planned_count == 1
    assert result.plans[0].normalized_draft.strategy_code == "SUPERTREND_V1"


def test_duplicate_signal_id_does_not_create_duplicate_plans(mu_db, monkeypatch):
    from app.services.strategy_fanout_v2 import (
        DUPLICATE_SIGNAL,
        PLANNED_STATUS,
        plan_strategy_fanout_v2,
    )

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    user = make_user("phase2b-dedupe@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=user.id, catalog=catalog, version=version)
        payload = _raw_payload(signal_id="phase2b-dedupe")

        first = plan_strategy_fanout_v2(db, payload, "SUPERTREND_V1", dry_run=False)
        second = plan_strategy_fanout_v2(db, payload, "SUPERTREND_V1", dry_run=False)

        assert first.ok is True
        assert first.planned_count == 1
        assert first.plans[0].status == PLANNED_STATUS
        assert first.signal_row_id is not None
        assert second.duplicate is True
        assert second.planned_count == 0
        assert second.skips[0].reason == DUPLICATE_SIGNAL
        assert db.query(models.StrategySignal).count() == 1
        assert db.query(models.NormalizedOptionSignal).count() == 1
        assert db.query(models.StrategyExecutionJob).count() == 0


def test_v2_planner_uses_phase2a_unresolved_normalization(mu_db, monkeypatch):
    from app.services.strategy_fanout_v2 import plan_strategy_fanout_v2

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    user = make_user("phase2b-unresolved@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=user.id, catalog=catalog, version=version)

        result = plan_strategy_fanout_v2(
            db,
            _raw_payload(
                signal_id="phase2b-unresolved",
                strike_mode="ATM",
                strike=None,
                expiry_mode="NEXT_WEEKLY",
                expiry=None,
            ),
            "SUPERTREND_V1",
        )

    assert result.ok is True
    assert result.planned_count == 1
    plan = result.plans[0]
    assert plan.needs_resolution is True
    assert plan.normalized_draft.resolved_strike is None
    assert plan.normalized_draft.resolved_expiry is None
    assert any("strike_mode ATM" in item for item in plan.normalized_draft.resolution_reasons)
    assert any("expiry_mode NEXT_WEEKLY" in item for item in plan.normalized_draft.resolution_reasons)


def test_side_restriction_blocks_plan_without_creating_job(mu_db, monkeypatch):
    from app.services.option_intent_mapper import OPTION_SIDE_BLOCKED
    from app.services.strategy_fanout_v2 import plan_strategy_fanout_v2

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    user = make_user("phase2b-side-block@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(
            db,
            user_id=user.id,
            catalog=catalog,
            version=version,
            side_preference="PE",
        )

        result = plan_strategy_fanout_v2(
            db,
            _raw_payload(signal_id="phase2b-side-block", intent="BULLISH"),
            "SUPERTREND_V1",
        )

        assert result.ok is False
        assert result.planned_count == 0
        assert result.error_count == 1
        assert result.errors[0].error_code == OPTION_SIDE_BLOCKED
        assert db.query(models.StrategyExecutionJob).count() == 0


def test_strategy_fanout_v2_has_no_execution_or_broker_imports():
    import app.services.strategy_fanout_v2 as fanout_v2

    source = inspect.getsource(fanout_v2)

    assert "route_signal" not in source
    assert "execution_router" not in source
    assert "dhan_client" not in source
    assert "RealDhanClient" not in source
    assert "get_broker_client" not in source
    assert "state_store" not in source


def test_worker_ignores_planned_strategy_execution_jobs(mu_db, monkeypatch):
    from app.config import settings
    from app.services.strategy_fanout_v2 import PLANNED_STATUS
    from app.workers import strategy_job_worker

    monkeypatch.setattr(settings, "STRATEGY_JOB_WORKER_ENABLED", True, raising=False)
    calls = []
    monkeypatch.setattr(
        strategy_job_worker.strategy_fanout,
        "dispatch_signal_job",
        lambda **kwargs: calls.append(kwargs) or {"status": "unexpected"},
    )
    user = make_user("phase2b-worker@example.com")

    with session_scope() as db:
        signal = models.StrategySignal(
            strategy_name="SUPERTREND_V1",
            signal_id="phase2b-worker",
            status=PLANNED_STATUS,
        )
        db.add(signal)
        db.flush()
        job = models.StrategyExecutionJob(
            strategy_signal_id=signal.id,
            user_id=user.id,
            strategy_name="SUPERTREND_V1",
            signal_id="phase2b-worker",
            signal_payload={"planner": "strategy_fanout_v2"},
            lots=1,
            execution_mode=StrategyExecutionMode.SIGNAL_ONLY.value,
            status=PLANNED_STATUS,
        )
        db.add(job)
        db.flush()
        job_id = job.id

    assert strategy_job_worker.process_queued_jobs_once(limit=1) == 0
    assert calls == []
    with session_scope() as db:
        assert db.get(models.StrategyExecutionJob, job_id).status == PLANNED_STATUS


def test_feature_flag_off_does_not_change_legacy_fanout_behavior(mu_db, monkeypatch):
    from app.services import strategy_fanout

    monkeypatch.delenv("MULTI_STRATEGY_FANOUT", raising=False)
    user = make_user("phase2b-legacy@example.com")
    strategy_fanout.subscribe_user(
        user.id,
        "supertrend",
        lots=1,
        execution_mode=StrategyExecutionMode.SIGNAL_ONLY.value,
    )

    result = strategy_fanout.enqueue_strategy_signal(
        "supertrend",
        _legacy_signal("phase2b-legacy"),
    )

    assert result["accepted"] is True
    assert result["subscriber_count"] == 1
    with session_scope() as db:
        jobs = db.query(models.StrategyExecutionJob).all()
        assert len(jobs) == 1
        assert jobs[0].status == "queued"
        assert jobs[0].instance_id is None
        assert db.query(models.NormalizedOptionSignal).count() == 0
