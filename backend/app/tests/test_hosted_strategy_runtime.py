from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def constant_ir():
    return {
        "ir_version": 1, "strategy_name": "Synthetic constant fixture", "description": "Verification only",
        "underlying": "NIFTY", "timeframe": "1m", "evaluation": "BAR_CLOSE", "warmup_bars": 1,
        "parameters": {}, "indicators": [],
        "conditions": {"always": {"op": "CONSTANT", "constant": True}},
        "actions": [
            {"action": "EXIT", "when": "always", "priority": 100, "position_states": ["LONG_CE", "LONG_PE"]},
            {"action": "BUY_CE", "when": "always", "priority": 20, "position_states": ["FLAT"]},
        ],
        "session": {"timezone": "Asia/Kolkata", "entry_start": "09:20", "entry_end": "14:45", "force_exit_time": "15:15", "skip_expiry_day": False},
        "risk_metadata": {"purpose": "verification_only"},
    }


def candle(minute=0):
    return {"instrument": "NIFTY", "timeframe": "1m", "close_timestamp": (datetime(2026, 7, 13, 4, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)).isoformat(), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10, "finalized": True}


def setup_runtime(monkeypatch):
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.services import hosted_strategy_runtime as hosted, strategy_instance_service, strategy_registry

    owner = make_user("owner@example.com"); admin = make_user("admin@example.com", is_admin=True)
    strategy_registry.backfill_supertrend()
    instance = strategy_instance_service.create_instance(owner.id, strategy_code="supertrend", source_journey="NOVA_SHARED", label="Hosted fixture", lots=2, execution_mode="paper_live_data")
    strategy_instance_service.activate_instance(owner.id, uuid.UUID(instance["id"]))
    with session_scope() as db:
        strategy_id = db.scalar(select(models.StrategyCatalog.id).where(models.StrategyCatalog.code == "supertrend"))
    ir = hosted.create_ir(strategy_id=strategy_id, document=constant_ir(), creation_source="NOVA_OWNED", admin_user_id=admin.id)
    hosted.approve_ir(uuid.UUID(ir["id"]), admin.id)
    runtime = hosted.link_runtime(owner.id, uuid.UUID(instance["id"]), uuid.UUID(ir["id"]))
    monkeypatch.setattr(settings, "HOSTED_STRATEGY_RUNTIME_ENABLED", True)
    monkeypatch.setattr(settings, "HOSTED_STRATEGY_PAPER_EXECUTION_ENABLED", True)
    hosted.activate(owner.id, uuid.UUID(instance["id"]))
    return owner, admin, instance, runtime, ir


def test_durable_evaluation_is_idempotent_and_enters_existing_pipeline(mu_db, monkeypatch):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import hosted_strategy_runtime as hosted

    owner, _, instance, runtime, _ = setup_runtime(monkeypatch)
    assert hosted.enqueue_finalized_candle([candle()]) == {"created": 1, "duplicates": 0}
    assert hosted.enqueue_finalized_candle([candle()]) == {"created": 0, "duplicates": 1}
    with session_scope() as db:
        job_id = db.scalar(select(models.HostedStrategyEvaluationJob.id))
        job = db.get(models.HostedStrategyEvaluationJob, job_id); job.status = "PROCESSING"; job.attempts = 1; job.locked_at = datetime.now(timezone.utc)
    assert hosted.process_job(job_id, position_override="FLAT") == "SIGNAL_CREATED"
    assert hosted.process_job(job_id, position_override="FLAT") == "SKIPPED"
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(models.HostedStrategyEvaluation)) == 1
        assert db.scalar(select(func.count()).select_from(models.StrategySignal)) == 1
        execution = db.scalar(select(models.StrategyExecutionJob))
        assert execution.user_id == owner.id
        assert execution.execution_mode == "paper_live_data"
        assert execution.lots == 1  # audit placeholder; worker re-reads server-side instance lots
        assert execution.strategy_name == f"instance:{instance['id']}"
        assert execution.signal_payload["source"] == "hosted_strategy"


def test_pause_blocks_entries_but_allows_exit_and_stop_requires_flat(mu_db, monkeypatch):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import hosted_strategy_runtime as hosted

    owner, _, instance, _, _ = setup_runtime(monkeypatch); instance_id = uuid.UUID(instance["id"])
    hosted.pause(owner.id, instance_id)
    hosted.enqueue_finalized_candle([candle(1)])
    with session_scope() as db:
        first = db.scalar(select(models.HostedStrategyEvaluationJob)); first.status = "PROCESSING"; first.attempts = 1; first.locked_at = datetime.now(timezone.utc); first_id = first.id
    assert hosted.process_job(first_id, position_override="FLAT") == "NO_SIGNAL"
    hosted.enqueue_finalized_candle([candle(1), candle(2)])
    with session_scope() as db:
        second = db.scalar(select(models.HostedStrategyEvaluationJob).where(models.HostedStrategyEvaluationJob.status == "QUEUED")); second.status = "PROCESSING"; second.attempts = 1; second.locked_at = datetime.now(timezone.utc); second_id = second.id
    assert hosted.process_job(second_id, position_override="LONG_CE") == "SIGNAL_CREATED"
    with pytest.raises(hosted.HostedStrategyError, match="flat"): hosted.stop(owner.id, instance_id, position_override="LONG_CE")
    assert hosted.stop(owner.id, instance_id, position_override="FLAT")["status"] == "STOPPED"


def test_warmup_error_auto_pause_and_tenant_isolation(mu_db, monkeypatch):
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.services import hosted_strategy_runtime as hosted

    owner, _, instance, _, ir = setup_runtime(monkeypatch); other = make_user("other@example.com")
    with pytest.raises(hosted.HostedStrategyError): hosted.get_runtime(other.id, uuid.UUID(instance["id"]))
    assert hosted.get_ir(other.id, uuid.UUID(ir["id"]))["owner_user_id"] is None  # NOVA-owned artifacts are shared.
    with session_scope() as db:
        personal = models.StrategyCatalog(code="private-fixture", display_name="Private fixture", owner_type="personal", owner_user_id=owner.id, visibility="private", status="active")
        db.add(personal); db.flush(); personal_id = personal.id
    private_ir = hosted.create_ir(strategy_id=personal_id, document=constant_ir(), creation_source="MANUAL_TEST_FIXTURE", admin_user_id=owner.id)
    with pytest.raises(hosted.HostedStrategyError): hosted.get_ir(other.id, uuid.UUID(private_ir["id"]))
    monkeypatch.setattr(settings, "HOSTED_STRATEGY_MAX_CONSECUTIVE_ERRORS", 1)
    with session_scope() as db:
        runtime = db.scalar(select(models.HostedStrategyRuntime)); runtime.status = "ACTIVE"
        job = models.HostedStrategyEvaluationJob(hosted_runtime_id=runtime.id, ir_version_id=runtime.ir_version_id, owner_user_id=runtime.owner_user_id, candle_close_timestamp=datetime(2026,7,13,4,3,tzinfo=timezone.utc), candle_history=[{**candle(3), "close": "bad"}], status="PROCESSING", attempts=1, locked_at=datetime.now(timezone.utc))
        db.add(job); db.flush(); job_id = job.id
    assert hosted.process_job(job_id, position_override="FLAT") == "FAILED"
    with session_scope() as db:
        runtime = db.scalar(select(models.HostedStrategyRuntime)); assert runtime.status == "PAUSED"; assert runtime.safe_error_code == "EVALUATION_FAILED"


def test_stale_lease_recovery_and_queued_ir_pin(mu_db, monkeypatch):
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.services import hosted_strategy_runtime as hosted

    setup_runtime(monkeypatch); monkeypatch.setattr(settings, "HOSTED_STRATEGY_JOB_STALE_SECONDS", 1)
    hosted.enqueue_finalized_candle([candle()])
    with session_scope() as db:
        job = db.scalar(select(models.HostedStrategyEvaluationJob)); pinned = job.ir_version_id; job.status = "PROCESSING"; job.locked_at = datetime.now(timezone.utc)-timedelta(seconds=10)
    assert hosted.recover_stale_jobs() == 1
    with session_scope() as db:
        job = db.scalar(select(models.HostedStrategyEvaluationJob)); assert job.status == "QUEUED"; assert job.ir_version_id == pinned
