from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.core.enums import (
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
    StrategySourceType,
)
from app.db import models
from app.db.engine import session_scope
from app.schemas.signal import NormalizedSignal
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _raw_payload(**overrides):
    data = {
        "version": "nova.v1",
        "secret": "super-secret-value",
        "signal_id": "phase2c1-signal-001",
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
        "lots": 2,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
        "source": "tradingview",
        "timestamp": "2026-07-03T09:15:00+05:30",
    }
    data.update(overrides)
    return data


def _legacy_signal(signal_id: str):
    from app.config import DEFAULT_STRATEGY_CODE

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
        raw_payload={"signal_id": signal_id},
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
):
    instance = models.UserStrategyInstance(
        user_id=user_id,
        strategy_id=catalog.id,
        strategy_version_id=version.id,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status=status,
        execution_mode=execution_mode,
        lots=2,
    )
    db.add(instance)
    db.flush()
    return instance


def _create_v2_job(db, payload):
    from app.services.strategy_execution_queue_v2 import create_v2_jobs_from_plan
    from app.services.strategy_fanout_v2 import plan_strategy_fanout_v2

    plan_result = plan_strategy_fanout_v2(db, payload, payload["strategy_code"], dry_run=False)
    job_result = create_v2_jobs_from_plan(db, plan_result)
    assert job_result.created_count == 1
    return db.get(models.StrategyExecutionJob, job_result.jobs[0].job_id)


def test_process_next_v2_job_dry_run_claims_and_completes_job(mu_db, monkeypatch):
    from app.services.strategy_execution_queue_v2 import V2_COMPLETED_DRY_RUN
    from app.services.strategy_worker_adapter_v2 import process_next_v2_job_dry_run

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    monkeypatch.setattr("app.services.strategy_worker_adapter_v2._current_lot_size", lambda: 65)
    user = make_user("phase2c1-next@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        instance = _add_instance(db, user_id=user.id, catalog=catalog, version=version)
        job = _create_v2_job(db, _raw_payload(signal_id="phase2c1-next"))

        result = process_next_v2_job_dry_run(db, "phase2c1-worker")

        assert result is not None
        assert result.ok is True
        assert result.status == V2_COMPLETED_DRY_RUN
        assert result.normalized_signal_preview is not None
        preview = result.normalized_signal_preview
        assert preview["payload_format"] == "NOVA"
        assert preview["signal_id"] == job.signal_id
        assert preview["strategy_code"] == "SUPERTREND_V1"
        assert preview["action"] == "ENTRY"
        assert preview["side"] == "BUY"
        assert preview["symbol"] == "NIFTY"
        assert preview["instrument_type"] == "OPTIDX"
        assert preview["exchange_segment"] == "NSE_FNO"
        assert preview["option_side"] == "CE"
        assert preview["strike"] == 24500
        assert preview["expiry"] == "2026-07-09"
        assert preview["qty"] == 130
        assert preview["raw_payload"]["instance_id"] == str(instance.id)
        assert preview["raw_payload"]["strategy_version_id"] == str(version.id)
        assert preview["raw_payload"]["v2_job_id"] == str(job.id)
        assert preview["raw_payload"]["dry_run_adapter"] is True

        saved = db.get(models.StrategyExecutionJob, job.id)
        assert saved is not None
        assert saved.status == V2_COMPLETED_DRY_RUN
        assert saved.locked_by == "phase2c1-worker"
        assert saved.result_summary["details"]["normalized_signal_preview"]["qty"] == 130


def test_build_normalized_signal_from_v2_job_returns_existing_schema(mu_db, monkeypatch):
    from app.services.strategy_worker_adapter_v2 import build_normalized_signal_from_v2_job

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    monkeypatch.setattr("app.services.strategy_worker_adapter_v2._current_lot_size", lambda: 75)
    user = make_user("phase2c1-build@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=user.id, catalog=catalog, version=version)
        job = _create_v2_job(
            db,
            _raw_payload(
                signal_id="phase2c1-build",
                intent="BEARISH",
                strike=24600,
            ),
        )

        signal = build_normalized_signal_from_v2_job(db, job)

    assert isinstance(signal, NormalizedSignal)
    assert signal.payload_format == "NOVA"
    assert signal.action == "ENTRY"
    assert signal.side == "BUY"
    assert signal.option_side == "PE"
    assert signal.strike == 24600
    assert signal.qty == 150
    assert signal.source == "tradingview"
    assert signal.security_id is None
    assert signal.trading_symbol is None


def test_adapter_marks_job_failed_when_normalized_signal_is_missing(mu_db, monkeypatch):
    from app.services.strategy_execution_queue_v2 import V2_FAILED_DRY_RUN
    from app.services.strategy_worker_adapter_v2 import (
        MISSING_NORMALIZED_SIGNAL_ID,
        process_next_v2_job_dry_run,
    )

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    user = make_user("phase2c1-missing-normalized@example.com")

    with session_scope() as db:
        signal = models.StrategySignal(
            strategy_name="SUPERTREND_V1",
            signal_id="phase2c1-missing-normalized",
            status="planned",
        )
        db.add(signal)
        db.flush()
        job = models.StrategyExecutionJob(
            strategy_signal_id=signal.id,
            user_id=user.id,
            strategy_name="SUPERTREND_V1",
            signal_id="phase2c1-missing-normalized:v2",
            signal_payload={"payload_format": "NOVA_V2_EXECUTION_JOB"},
            lots=1,
            execution_mode=StrategyExecutionMode.SIGNAL_ONLY.value,
            status="v2_pending",
        )
        db.add(job)
        db.flush()
        job_id = job.id

        result = process_next_v2_job_dry_run(db, "phase2c1-worker")

        assert result is not None
        assert result.ok is False
        assert result.status == V2_FAILED_DRY_RUN
        assert result.error_code == MISSING_NORMALIZED_SIGNAL_ID
        saved = db.get(models.StrategyExecutionJob, job_id)
        assert saved is not None
        assert saved.status == V2_FAILED_DRY_RUN
        assert saved.dead_letter_reason == MISSING_NORMALIZED_SIGNAL_ID


def test_adapter_rejects_unsupported_action(mu_db):
    from app.services.strategy_execution_queue_v2 import V2_FAILED_DRY_RUN
    from app.services.strategy_worker_adapter_v2 import UNSUPPORTED_ACTION, process_v2_job_dry_run

    user = make_user("phase2c1-unsupported-action@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        instance = _add_instance(db, user_id=user.id, catalog=catalog, version=version)
        strategy_signal = models.StrategySignal(
            strategy_name="SUPERTREND_V1",
            signal_id="phase2c1-unsupported-action",
            strategy_version_id=version.id,
            payload_version="nova.v1",
            status="planned",
        )
        db.add(strategy_signal)
        db.flush()
        normalized = models.NormalizedOptionSignal(
            strategy_signal_id=strategy_signal.id,
            instance_id=instance.id,
            action="REVERSE",
            intent="BULLISH",
            symbol="NIFTY",
            instrument_type="OPTIDX",
            option_side="CE",
            strike_mode="MANUAL",
            resolved_strike=24500,
            expiry_mode="MANUAL",
            resolved_expiry=date(2026, 7, 9),
            qty_mode="LOTS",
            lots=1,
            quantity=None,
            order_type="MARKET",
            product_type="INTRADAY",
            raw_mapping_details={"source": "tradingview"},
        )
        db.add(normalized)
        db.flush()
        job = models.StrategyExecutionJob(
            strategy_signal_id=strategy_signal.id,
            instance_id=instance.id,
            strategy_version_id=version.id,
            normalized_signal_id=normalized.id,
            user_id=user.id,
            strategy_name="SUPERTREND_V1",
            signal_id="phase2c1-unsupported-action:v2",
            signal_payload={"payload_format": "NOVA_V2_EXECUTION_JOB"},
            lots=1,
            execution_mode=StrategyExecutionMode.SIGNAL_ONLY.value,
            status="v2_locked",
            locked_by="phase2c1-worker",
        )
        db.add(job)
        db.flush()

        result = process_v2_job_dry_run(db, job, "phase2c1-worker")

        assert result.ok is False
        assert result.status == V2_FAILED_DRY_RUN
        assert result.error_code == UNSUPPORTED_ACTION
        assert db.get(models.StrategyExecutionJob, job.id).status == V2_FAILED_DRY_RUN


def test_adapter_ignores_legacy_queued_jobs(mu_db):
    from app.services.strategy_worker_adapter_v2 import process_next_v2_job_dry_run

    user = make_user("phase2c1-legacy-ignore@example.com")

    with session_scope() as db:
        signal = models.StrategySignal(
            strategy_name="supertrend",
            signal_id="phase2c1-legacy",
            status="queued",
        )
        db.add(signal)
        db.flush()
        db.add(
            models.StrategyExecutionJob(
                strategy_signal_id=signal.id,
                user_id=user.id,
                strategy_name="supertrend",
                signal_id="phase2c1-legacy",
                signal_payload=_legacy_signal("phase2c1-legacy").model_dump(mode="json"),
                lots=1,
                execution_mode=StrategyExecutionMode.SIGNAL_ONLY.value,
                status="queued",
            )
        )

        assert process_next_v2_job_dry_run(db, "phase2c1-worker") is None


def test_adapter_does_not_write_order_jsonl_logs(mu_db, monkeypatch, tmp_path):
    from app.services import audit_logger, state_store
    from app.services.strategy_worker_adapter_v2 import process_next_v2_job_dry_run

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    monkeypatch.setattr("app.services.strategy_worker_adapter_v2._current_lot_size", lambda: 65)
    log_files = {
        "webhook": tmp_path / "webhook_events.jsonl",
        "order": tmp_path / "order_events.jsonl",
        "audit": tmp_path / "audit_events.jsonl",
        "error": tmp_path / "errors.jsonl",
        "paper_orders": tmp_path / "paper_orders.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    user = make_user("phase2c1-no-order-log@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=user.id, catalog=catalog, version=version)
        _create_v2_job(db, _raw_payload(signal_id="phase2c1-no-order-log"))

        result = process_next_v2_job_dry_run(db, "phase2c1-worker")

    assert result is not None
    assert result.ok is True
    order_path = Path(log_files["order"])
    assert not order_path.exists() or order_path.read_text(encoding="utf-8") == ""


def test_legacy_worker_behavior_still_processes_legacy_signal_only_job(mu_db, monkeypatch):
    from app.config import settings
    from app.workers.strategy_job_worker import process_queued_jobs_once

    monkeypatch.setattr(settings, "STRATEGY_JOB_WORKER_ENABLED", True, raising=False)
    user = make_user("phase2c1-legacy-worker@example.com")

    with session_scope() as db:
        signal = models.StrategySignal(
            strategy_name="supertrend",
            signal_id="phase2c1-legacy-worker",
            status="queued",
        )
        db.add(signal)
        db.flush()
        db.add(
            models.StrategyExecutionJob(
                strategy_signal_id=signal.id,
                user_id=user.id,
                strategy_name="supertrend",
                signal_id="phase2c1-legacy-worker",
                signal_payload=_legacy_signal("phase2c1-legacy-worker").model_dump(mode="json"),
                lots=1,
                execution_mode=StrategyExecutionMode.SIGNAL_ONLY.value,
                status="queued",
            )
        )

    assert process_queued_jobs_once(limit=1) == 1
    with session_scope() as db:
        job = db.scalar(select(models.StrategyExecutionJob))
        assert job.status == "completed"
        assert job.result_summary["status"] == "accepted"


def test_strategy_worker_adapter_v2_has_no_execution_or_broker_imports():
    import app.services.strategy_worker_adapter_v2 as adapter

    source = inspect.getsource(adapter)

    assert "route_signal" not in source
    assert "execution_router" not in source
    assert "dhan_client" not in source
    assert "RealDhanClient" not in source
    assert "get_broker_client" not in source
    assert "PaperBroker" not in source
    assert "paper_broker" not in source
    assert "state_store" not in source
    assert "set_open_position" not in source
    assert "get_open_position" not in source
