from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from sqlalchemy import select

from app.core.enums import (
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
    StrategySourceType,
)
from app.core.feature_flags import MULTI_STRATEGY_FANOUT, V2_PAPER_RUNNER_DEBUG
from app.db import models
from app.db.engine import session_scope
from app.services import state_store
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from scripts import manual_v2_paper_fanout_inspector_check as script


def _set_safe_env(monkeypatch, *, fanout: bool = True, runner_debug: bool = True) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "DEBUG_ENABLED", True, raising=False)
    monkeypatch.setenv(MULTI_STRATEGY_FANOUT, "true" if fanout else "false")
    monkeypatch.setenv(V2_PAPER_RUNNER_DEBUG, "true" if runner_debug else "false")


def _isolate_runtime(monkeypatch, tmp_path) -> dict[str, Any]:
    from app.services import audit_logger

    state_root = tmp_path / "state"
    log_root = tmp_path / "logs"
    for name, filename in {
        "APP_STATE_FILE": "app_state.json",
        "OPEN_POSITION_FILE": "open_position.json",
        "PAPER_POSITION_FILE": "paper_position.json",
        "OPEN_POSITIONS_BY_INSTANCE_FILE": "open_positions_by_instance.json",
        "PAPER_PORTFOLIO_FILE": "paper_portfolio.json",
        "EXTERNAL_POSITIONS_FILE": "external_positions.json",
        "SEEN_SIGNALS_FILE": "seen_signals.json",
        "SETTINGS_FILE": "settings.json",
    }.items():
        monkeypatch.setattr(state_store, name, state_root / filename)
    log_files = {
        "webhook": log_root / "webhook_events.jsonl",
        "order": log_root / "order_events.jsonl",
        "audit": log_root / "audit_events.jsonl",
        "error": log_root / "errors.jsonl",
        "paper_orders": log_root / "paper_orders.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    return {"state_root": state_root, "log_root": log_root, "log_files": log_files}


def _safe_config() -> script.InspectorSmokeConfig:
    return script.InspectorSmokeConfig(direct_service=True, limit=5)


def _safe_status() -> dict[str, Any]:
    return {
        "ok": True,
        "counts": {
            "paper_instances": 0,
            "v2_pending_jobs": 0,
            "v2_completed_paper_jobs": 0,
        },
        "jobs_by_status": {},
    }


def _safe_jobs() -> dict[str, Any]:
    return {"ok": True, "limit": 5, "count": 0, "jobs": []}


def _safe_instances() -> dict[str, Any]:
    return {"ok": True, "limit": 5, "count": 0, "instances": []}


def _patch_fake_inspector(monkeypatch, *, status=None, jobs=None, instances=None, calls=None) -> None:
    calls = calls if calls is not None else []

    def fake_status(db, *, limit):
        calls.append(("status", limit))
        return _safe_status() if status is None else status

    def fake_jobs(db, *, limit):
        calls.append(("jobs", limit))
        return _safe_jobs() if jobs is None else jobs

    def fake_instances(db, *, limit):
        calls.append(("instances", limit))
        return _safe_instances() if instances is None else instances

    monkeypatch.setattr(script, "get_v2_paper_fanout_status", fake_status)
    monkeypatch.setattr(script, "list_v2_paper_fanout_jobs", fake_jobs)
    monkeypatch.setattr(script, "list_v2_paper_strategy_instances", fake_instances)


def _fail_if_called(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("execution path should not be called")


def _seed_post_paper_state() -> dict[str, str]:
    user = make_user("phase2e4-inspector@example.com")
    with session_scope() as db:
        catalog = models.StrategyCatalog(
            code="SUPERTREND_V1",
            name="Supertrend",
            status=StrategyCatalogStatus.ACTIVE.value,
            source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
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
        instance = models.UserStrategyInstance(
            user_id=user.id,
            strategy_id=catalog.id,
            strategy_version_id=version.id,
            source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
            status=StrategyInstanceStatus.ACTIVE.value,
            execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
            lots=1,
            config_json={"secret": "do-not-return"},
        )
        db.add(instance)
        db.flush()
        signal = models.StrategySignal(
            strategy_name="SUPERTREND_V1",
            signal_id="phase2e4-entry",
            instance_id=instance.id,
            strategy_version_id=version.id,
            payload_version="nova.v1",
            status="accepted",
            result_summary={"raw_response": {"secret": "do-not-return"}},
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
            resolved_expiry=date(2026, 7, 9),
            qty_mode="LOTS",
            lots=1,
            quantity=75,
            order_type="MARKET",
            product_type="INTRADAY",
            raw_mapping_details={"raw_payload": {"secret": "do-not-return"}},
        )
        db.add(normalized)
        db.flush()
        job = models.StrategyExecutionJob(
            strategy_signal_id=signal.id,
            instance_id=instance.id,
            strategy_version_id=version.id,
            normalized_signal_id=normalized.id,
            user_id=user.id,
            strategy_name="SUPERTREND_V1",
            signal_id=f"phase2e4-entry:{instance.id}",
            signal_payload={"secret": "do-not-return"},
            lots=1,
            execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
            status="v2_completed_paper",
            attempts=1,
            max_attempts=1,
            locked_by="manual-v2-paper-runner",
            result_summary={"status": "v2_completed_paper", "raw_response": {"secret": "do-not-return"}},
        )
        db.add(job)
        db.flush()
        return {"instance_id": str(instance.id), "job_id": str(job.id)}


def _write_open_positions(path, instance_id: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                instance_id: {
                    "has_open_position": True,
                    "symbol": "NIFTY",
                    "option_side": "CE",
                    "strike": 25000,
                    "expiry": "2026-07-09",
                    "qty": 75,
                    "entry_order_id": "hidden",
                    "raw_response": {"secret": "hidden"},
                }
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path.read_text(encoding="utf-8")


def _db_snapshot() -> dict[str, Any]:
    with session_scope() as db:
        jobs = db.scalars(select(models.StrategyExecutionJob).order_by(models.StrategyExecutionJob.id)).all()
        instances = db.scalars(select(models.UserStrategyInstance).order_by(models.UserStrategyInstance.id)).all()
        signals = db.scalars(select(models.StrategySignal).order_by(models.StrategySignal.id)).all()
        return {
            "jobs": [(str(job.id), job.status, job.attempts, job.locked_by, job.updated_at.isoformat()) for job in jobs],
            "instances": [
                (str(instance.id), instance.status, instance.execution_mode, instance.updated_at.isoformat())
                for instance in instances
            ],
            "signals": [(str(signal.id), signal.status, signal.updated_at.isoformat()) for signal in signals],
        }


def test_script_refuses_when_debug_enabled_false(mu_db, monkeypatch, tmp_path):
    from app.config import settings

    _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch)
    monkeypatch.setattr(settings, "DEBUG_ENABLED", False, raising=False)

    with pytest.raises(script.InspectorSmokeError, match="DEBUG_ENABLED"):
        script.run_inspector_smoke_check(_safe_config(), out=lambda _line: None)


def test_script_refuses_when_multi_strategy_fanout_false(mu_db, monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch, fanout=False)

    with pytest.raises(script.InspectorSmokeError, match="MULTI_STRATEGY_FANOUT"):
        script.run_inspector_smoke_check(_safe_config(), out=lambda _line: None)


def test_script_refuses_when_v2_paper_runner_debug_false(mu_db, monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch, runner_debug=False)

    with pytest.raises(script.InspectorSmokeError, match="V2_PAPER_RUNNER_DEBUG"):
        script.run_inspector_smoke_check(_safe_config(), out=lambda _line: None)


def test_script_refuses_without_database_url(monkeypatch, tmp_path):
    from app.config import settings

    _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch)
    monkeypatch.setattr(settings, "DATABASE_URL", "", raising=False)

    with pytest.raises(script.InspectorSmokeError, match="DATABASE_URL"):
        script.run_inspector_smoke_check(_safe_config(), out=lambda _line: None)


def test_direct_service_mode_calls_inspector_only_not_runner(mu_db, monkeypatch, tmp_path):
    from app.services import strategy_paper_fanout_runner_v2

    _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch)
    calls: list[tuple[str, int]] = []
    _patch_fake_inspector(monkeypatch, calls=calls)
    monkeypatch.setattr(strategy_paper_fanout_runner_v2, "run_v2_paper_fanout_once", _fail_if_called)
    monkeypatch.setattr(strategy_paper_fanout_runner_v2, "process_ready_v2_paper_jobs_once", _fail_if_called)

    result = script.run_inspector_smoke_check(_safe_config(), out=lambda _line: None)

    assert result["ok"] is True
    assert calls == [("status", 5), ("jobs", 5), ("instances", 5)]


def test_script_does_not_call_route_signal_or_brokers(mu_db, monkeypatch, tmp_path):
    from app.services import dhan_client, execution_router, paper_broker

    _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch)
    _patch_fake_inspector(monkeypatch)
    monkeypatch.setattr(execution_router, "route_signal", _fail_if_called)
    monkeypatch.setattr(dhan_client.RealDhanClient, "place_order", _fail_if_called)
    monkeypatch.setattr(dhan_client.RealDhanClient, "place_super_order", _fail_if_called)
    monkeypatch.setattr(paper_broker.PaperBroker, "place_order", _fail_if_called)
    monkeypatch.setattr(paper_broker.PaperBroker, "place_super_order", _fail_if_called)

    result = script.run_inspector_smoke_check(_safe_config(), out=lambda _line: None)

    assert result["ok"] is True


def test_script_summary_strips_sensitive_fields(mu_db, monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch)
    _patch_fake_inspector(
        monkeypatch,
        status={"ok": True, "counts": {}, "secret": "raw-secret"},
        jobs={
            "ok": True,
            "limit": 5,
            "count": 1,
            "jobs": [{"id": "job-1", "status": "v2_pending", "signal_payload": {"access_token": "raw-token"}}],
        },
        instances={
            "ok": True,
            "limit": 5,
            "count": 1,
            "instances": [
                {
                    "id": "instance-1",
                    "execution_mode": "paper_live_data",
                    "open_position_summary": {"has_open_position": False, "headers": {"secret": "hidden"}},
                }
            ],
        },
    )
    lines: list[str] = []

    with pytest.raises(script.InspectorSmokeError, match="forbidden sensitive fields"):
        script.run_inspector_smoke_check(_safe_config(), out=lines.append)

    output = "\n".join(lines)
    assert "raw-secret" not in output
    assert "raw-token" not in output
    assert "signal_payload" not in output
    assert "access_token" not in output
    assert "headers" not in output
    assert '"secret"' not in output.lower()


def test_script_does_not_mutate_db_rows_or_state_files(mu_db, monkeypatch, tmp_path):
    runtime = _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch)
    ids = _seed_post_paper_state()
    state_file = state_store.OPEN_POSITIONS_BY_INSTANCE_FILE
    state_before = _write_open_positions(state_file, ids["instance_id"])
    db_before = _db_snapshot()

    result = script.run_inspector_smoke_check(_safe_config(), out=lambda _line: None)

    assert result["safety"]["db_unchanged"] is True
    assert result["safety"]["runtime_files_unchanged"] is True
    assert _db_snapshot() == db_before
    assert state_file.read_text(encoding="utf-8") == state_before
    assert list(runtime["log_root"].rglob("*")) == []


def test_script_can_report_empty_state_safely(mu_db, monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch)

    result = script.run_inspector_smoke_check(_safe_config(), out=lambda _line: None)

    assert result["ok"] is True
    assert result["readable"] == {"status": True, "jobs": True, "instances": True}
    assert result["counts"]["status_counts"]["paper_instances"] == 0
    assert result["counts"]["jobs_count"] == 0
    assert result["counts"]["instances_count"] == 0
    assert result["safety"]["sensitive_field_count"] == 0


def test_script_can_report_post_paper_run_state_safely(mu_db, monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch)
    ids = _seed_post_paper_state()
    _write_open_positions(state_store.OPEN_POSITIONS_BY_INSTANCE_FILE, ids["instance_id"])

    result = script.run_inspector_smoke_check(_safe_config(), out=lambda _line: None)

    assert result["ok"] is True
    assert result["counts"]["status_counts"]["paper_instances"] == 1
    assert result["counts"]["status_counts"]["v2_completed_paper_jobs"] == 1
    assert result["counts"]["jobs_count"] == 1
    assert result["counts"]["instances_count"] == 1
    assert result["safety"]["sensitive_field_count"] == 0
    assert result["safety"]["jobs_safe_fields_only"] is True
    assert result["safety"]["instances_safe_fields_only"] is True
