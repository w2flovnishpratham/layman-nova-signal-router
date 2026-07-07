from __future__ import annotations

import inspect
from datetime import timedelta

from sqlalchemy import select

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
        "signal_id": "phase2c0-signal-001",
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
):
    instance = models.UserStrategyInstance(
        user_id=user_id,
        strategy_id=catalog.id,
        strategy_version_id=version.id,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status=status,
        execution_mode=execution_mode,
        side_preference=side_preference,
        lots=1,
    )
    db.add(instance)
    db.flush()
    return instance


def _persisted_plan(db, payload):
    from app.services.strategy_fanout_v2 import plan_strategy_fanout_v2

    return plan_strategy_fanout_v2(db, payload, payload["strategy_code"], dry_run=False)


def test_create_v2_jobs_from_plan_creates_one_job_per_valid_instance(mu_db, monkeypatch):
    from app.services.strategy_execution_queue_v2 import V2_PENDING, create_v2_jobs_from_plan

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    user = make_user("phase2c0-multi-instance@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        first = _add_instance(db, user_id=user.id, catalog=catalog, version=version)
        second = _add_instance(
            db,
            user_id=user.id,
            catalog=catalog,
            version=version,
            execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
        )
        plan_result = _persisted_plan(db, _raw_payload(signal_id="phase2c0-create"))

        result = create_v2_jobs_from_plan(db, plan_result)

        assert result.ok is True
        assert result.requested_count == 2
        assert result.created_count == 2
        assert result.duplicate_count == 0
        assert result.error_count == 0

        jobs = db.scalars(
            select(models.StrategyExecutionJob).order_by(models.StrategyExecutionJob.created_at.asc())
        ).all()
        assert len(jobs) == 2
        assert {job.status for job in jobs} == {V2_PENDING}
        assert {job.user_id for job in jobs} == {user.id}
        assert {job.instance_id for job in jobs} == {first.id, second.id}
        assert {job.strategy_version_id for job in jobs} == {version.id}
        assert all(job.normalized_signal_id is not None for job in jobs)
        assert all(job.execution_mode in {"signal_only", "paper_live_data"} for job in jobs)
        assert all(job.signal_id != plan_result.signal_id for job in jobs)
        assert {job.signal_payload["source_signal_id"] for job in jobs} == {"phase2c0-create"}
        assert {job.signal_payload["payload_format"] for job in jobs} == {"NOVA_V2_EXECUTION_JOB"}


def test_real_orders_plan_is_skipped_and_creates_no_v2_pending_job(mu_db, monkeypatch):
    from app.services.strategy_execution_queue_v2 import V2_PENDING, create_v2_jobs_from_plan
    from app.services.strategy_fanout_v2 import REAL_ORDERS_NOT_SUPPORTED_YET

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    paper_user = make_user("phase2d6-queue-paper@example.com")
    real_user = make_user("phase2d6-queue-real@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        paper_instance = _add_instance(
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
        plan_result = _persisted_plan(db, _raw_payload(signal_id="phase2d6-real-queue-skip"))

        assert plan_result.planned_count == 1
        assert plan_result.skipped_count == 1
        assert plan_result.skips[0].reason == REAL_ORDERS_NOT_SUPPORTED_YET
        assert plan_result.skips[0].instance_id == real_instance.id

        result = create_v2_jobs_from_plan(db, plan_result)
        jobs = db.scalars(select(models.StrategyExecutionJob)).all()

        assert result.ok is True
        assert result.created_count == 1
        assert result.skipped_count == 1
        assert result.skips[0].reason == REAL_ORDERS_NOT_SUPPORTED_YET
        assert len(jobs) == 1
        assert jobs[0].status == V2_PENDING
        assert jobs[0].instance_id == paper_instance.id
        assert jobs[0].execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value
        assert jobs[0].instance_id != real_instance.id


def test_skipped_and_error_plans_create_no_v2_jobs(mu_db, monkeypatch):
    from app.services.option_intent_mapper import OPTION_SIDE_BLOCKED
    from app.services.strategy_execution_queue_v2 import create_v2_jobs_from_plan

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    active_user = make_user("phase2c0-active@example.com")
    paused_user = make_user("phase2c0-paused@example.com")
    blocked_user = make_user("phase2c0-blocked@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        active = _add_instance(db, user_id=active_user.id, catalog=catalog, version=version)
        paused = _add_instance(
            db,
            user_id=paused_user.id,
            catalog=catalog,
            version=version,
            status=StrategyInstanceStatus.PAUSED.value,
        )
        blocked = _add_instance(
            db,
            user_id=blocked_user.id,
            catalog=catalog,
            version=version,
            side_preference="PE",
        )
        plan_result = _persisted_plan(db, _raw_payload(signal_id="phase2c0-skips-errors"))

        assert plan_result.planned_count == 1
        assert plan_result.skipped_count == 1
        assert plan_result.error_count == 1
        assert plan_result.errors[0].error_code == OPTION_SIDE_BLOCKED

        result = create_v2_jobs_from_plan(db, plan_result)
        jobs = db.scalars(select(models.StrategyExecutionJob)).all()

        assert result.ok is False
        assert result.created_count == 1
        assert result.skipped_count == 1
        assert result.error_count == 1
        assert len(jobs) == 1
        assert jobs[0].instance_id == active.id
        assert all(job.instance_id != paused.id for job in jobs)
        assert all(job.instance_id != blocked.id for job in jobs)


def test_create_v2_jobs_from_plan_is_idempotent(mu_db, monkeypatch):
    from app.services.strategy_execution_queue_v2 import create_v2_jobs_from_plan

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    alice = make_user("phase2c0-idem-a@example.com")
    bob = make_user("phase2c0-idem-b@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=alice.id, catalog=catalog, version=version)
        _add_instance(db, user_id=bob.id, catalog=catalog, version=version)
        plan_result = _persisted_plan(db, _raw_payload(signal_id="phase2c0-idempotent"))

        first = create_v2_jobs_from_plan(db, plan_result)
        second = create_v2_jobs_from_plan(db, plan_result)

        assert first.created_count == 2
        assert second.created_count == 0
        assert second.duplicate_count == 2
        assert db.query(models.StrategyExecutionJob).count() == 2


def test_claim_helper_claims_oldest_v2_job_and_sets_lock_fields(mu_db, monkeypatch):
    from app.services.strategy_execution_queue_v2 import (
        V2_LOCKED,
        claim_v2_job_for_test_or_future_worker,
        create_v2_jobs_from_plan,
    )

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    alice = make_user("phase2c0-oldest-a@example.com")
    bob = make_user("phase2c0-oldest-b@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=alice.id, catalog=catalog, version=version)
        _add_instance(db, user_id=bob.id, catalog=catalog, version=version)
        plan_result = _persisted_plan(db, _raw_payload(signal_id="phase2c0-oldest"))
        create_v2_jobs_from_plan(db, plan_result)
        jobs = db.scalars(
            select(models.StrategyExecutionJob).order_by(models.StrategyExecutionJob.created_at.asc())
        ).all()
        jobs[0].created_at = jobs[0].created_at + timedelta(minutes=10)
        jobs[1].created_at = jobs[1].created_at - timedelta(minutes=10)
        expected_id = jobs[1].id
        db.flush()

        claimed = claim_v2_job_for_test_or_future_worker(db, "phase2c0-worker")

        assert claimed is not None
        assert claimed.id == expected_id
        assert claimed.status == V2_LOCKED
        assert claimed.locked_by == "phase2c0-worker"
        assert claimed.locked_at is not None
        assert claimed.attempts == 1


def test_claim_helper_respects_user_instance_lock(mu_db, monkeypatch):
    from app.services.strategy_execution_queue_v2 import (
        claim_v2_job_for_test_or_future_worker,
        create_v2_jobs_from_plan,
    )

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    user = make_user("phase2c0-user-instance-lock@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=user.id, catalog=catalog, version=version)
        first_plan = _persisted_plan(db, _raw_payload(signal_id="phase2c0-lock-a"))
        create_v2_jobs_from_plan(db, first_plan)
        second_plan = _persisted_plan(db, _raw_payload(signal_id="phase2c0-lock-b"))
        create_v2_jobs_from_plan(db, second_plan)

        first_claim = claim_v2_job_for_test_or_future_worker(db, "worker-a")
        second_claim = claim_v2_job_for_test_or_future_worker(db, "worker-b")

        assert first_claim is not None
        assert second_claim is None


def test_claim_helper_does_not_claim_legacy_queued_jobs(mu_db):
    from app.services.strategy_execution_queue_v2 import claim_v2_job_for_test_or_future_worker

    user = make_user("phase2c0-legacy-queued@example.com")

    with session_scope() as db:
        signal = models.StrategySignal(
            strategy_name="supertrend",
            signal_id="phase2c0-legacy",
            status="queued",
        )
        db.add(signal)
        db.flush()
        db.add(
            models.StrategyExecutionJob(
                strategy_signal_id=signal.id,
                user_id=user.id,
                strategy_name="supertrend",
                signal_id="phase2c0-legacy",
                signal_payload={"payload_format": "NOVA"},
                lots=1,
                execution_mode=StrategyExecutionMode.SIGNAL_ONLY.value,
                status="queued",
            )
        )

        assert claim_v2_job_for_test_or_future_worker(db, "phase2c0-worker") is None


def test_legacy_worker_ignores_v2_pending_jobs(mu_db, monkeypatch):
    from app.config import settings
    from app.services.strategy_execution_queue_v2 import V2_PENDING, create_v2_jobs_from_plan
    from app.workers import strategy_job_worker

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    monkeypatch.setattr(settings, "STRATEGY_JOB_WORKER_ENABLED", True, raising=False)
    calls = []
    monkeypatch.setattr(
        strategy_job_worker.strategy_fanout,
        "dispatch_signal_job",
        lambda **kwargs: calls.append(kwargs) or {"status": "unexpected"},
    )
    user = make_user("phase2c0-worker-ignore@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=user.id, catalog=catalog, version=version)
        plan_result = _persisted_plan(db, _raw_payload(signal_id="phase2c0-worker-ignore"))
        create_v2_jobs_from_plan(db, plan_result)
        job_id = db.scalar(select(models.StrategyExecutionJob.id))

    assert strategy_job_worker.process_queued_jobs_once(limit=1) == 0
    assert calls == []
    with session_scope() as db:
        job = db.get(models.StrategyExecutionJob, job_id)
        assert job is not None
        assert job.status == V2_PENDING


def test_mark_v2_job_completed_and_failed_dry_run(mu_db, monkeypatch):
    from app.services.strategy_execution_queue_v2 import (
        V2_COMPLETED_DRY_RUN,
        V2_FAILED_DRY_RUN,
        create_v2_jobs_from_plan,
        mark_v2_job_completed_dry_run,
        mark_v2_job_failed_dry_run,
    )

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    alice = make_user("phase2c0-complete@example.com")
    bob = make_user("phase2c0-fail@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=alice.id, catalog=catalog, version=version)
        _add_instance(db, user_id=bob.id, catalog=catalog, version=version)
        plan_result = _persisted_plan(db, _raw_payload(signal_id="phase2c0-mark"))
        create_v2_jobs_from_plan(db, plan_result)
        jobs = db.scalars(
            select(models.StrategyExecutionJob).order_by(models.StrategyExecutionJob.created_at.asc())
        ).all()

        completed = mark_v2_job_completed_dry_run(db, jobs[0].id, {"checked": True})
        failed = mark_v2_job_failed_dry_run(db, jobs[1].id, "blocked in dry run", "dry_run_block")

        assert completed is not None
        assert completed.status == V2_COMPLETED_DRY_RUN
        assert completed.completed_at is not None
        assert completed.result_summary["details"] == {"checked": True}
        assert failed is not None
        assert failed.status == V2_FAILED_DRY_RUN
        assert failed.last_error == "blocked in dry run"
        assert failed.dead_letter_reason == "dry_run_block"


def test_strategy_execution_queue_v2_has_no_execution_or_broker_imports():
    import app.services.strategy_execution_queue_v2 as queue_v2

    source = inspect.getsource(queue_v2)

    assert "route_signal" not in source
    assert "execution_router" not in source
    assert "dhan_client" not in source
    assert "RealDhanClient" not in source
    assert "get_broker_client" not in source
    assert "state_store" not in source
