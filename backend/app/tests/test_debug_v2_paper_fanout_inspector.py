from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
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
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


V2_PENDING = "v2_pending"
V2_COMPLETED_PAPER = "v2_completed_paper"

FORBIDDEN_KEYS = {
    "access_token",
    "credential_hash",
    "credentials_hash",
    "headers",
    "message_secret_hash",
    "raw_payload",
    "raw_response",
    "request",
    "response",
    "secret",
    "signal_payload",
    "webhook_key_hash",
}


def _client(monkeypatch, *, debug_enabled: bool = True) -> TestClient:
    from app.config import settings
    from app.routers import debug

    monkeypatch.setattr(settings, "DEBUG_ENABLED", debug_enabled, raising=False)
    app = FastAPI()
    app.include_router(debug.router, prefix="/api/debug")
    return TestClient(app)


def _set_flags(monkeypatch, *, fanout: bool = True, runner_debug: bool = True) -> None:
    monkeypatch.setenv(MULTI_STRATEGY_FANOUT, "true" if fanout else "false")
    monkeypatch.setenv(V2_PAPER_RUNNER_DEBUG, "true" if runner_debug else "false")


def _fail_if_called(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("execution path should not be called")


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


def _seed_strategy(db):
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
    return catalog, version


def _add_instance(
    db,
    *,
    user_id,
    catalog,
    version,
    execution_mode: str = StrategyExecutionMode.PAPER_LIVE_DATA.value,
    config_json: dict[str, Any] | None = None,
):
    instance = models.UserStrategyInstance(
        user_id=user_id,
        strategy_id=catalog.id,
        strategy_version_id=version.id,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status=StrategyInstanceStatus.ACTIVE.value,
        execution_mode=execution_mode,
        lots=1,
        side_preference="CE",
        config_json=config_json,
    )
    db.add(instance)
    db.flush()
    return instance


def _add_job(
    db,
    *,
    user_id,
    instance,
    version,
    signal_id: str,
    status: str = V2_COMPLETED_PAPER,
    execution_mode: str = StrategyExecutionMode.PAPER_LIVE_DATA.value,
):
    signal = models.StrategySignal(
        strategy_name="SUPERTREND_V1",
        signal_id=signal_id,
        instance_id=instance.id,
        strategy_version_id=version.id,
        payload_version="nova.v1",
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
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
        user_id=user_id,
        strategy_name="SUPERTREND_V1",
        signal_id=f"{signal_id}:{instance.id}",
        signal_payload={
            "signal_id": signal_id,
            "secret": "do-not-return",
            "raw_payload": {"access_token": "do-not-return"},
        },
        lots=1,
        execution_mode=execution_mode,
        status=status,
        attempts=1,
        max_attempts=1,
        locked_by="manual-v2-paper-runner" if status == V2_COMPLETED_PAPER else None,
        result_summary={
            "status": status,
            "error_code": None,
            "access_token": "do-not-return",
            "raw_response": {"headers": {"secret": "do-not-return"}},
        },
    )
    db.add(job)
    db.flush()
    return signal, normalized, job


def _seed_inspector_rows() -> dict[str, str]:
    paper_user = make_user("phase2e3-paper@example.com")
    real_user = make_user("phase2e3-real@example.com")
    with session_scope() as db:
        catalog, version = _seed_strategy(db)
        paper_instance = _add_instance(
            db,
            user_id=paper_user.id,
            catalog=catalog,
            version=version,
            config_json={
                "secret": "do-not-return",
                "webhook_key_hash": "hash-do-not-return",
                "message_secret_hash": "hash-do-not-return",
            },
        )
        real_instance = _add_instance(
            db,
            user_id=real_user.id,
            catalog=catalog,
            version=version,
            execution_mode=StrategyExecutionMode.REAL_ORDERS.value,
        )
        _add_job(
            db,
            user_id=paper_user.id,
            instance=paper_instance,
            version=version,
            signal_id="phase2e3-pending",
            status=V2_PENDING,
        )
        completed_signal, _, completed_job = _add_job(
            db,
            user_id=paper_user.id,
            instance=paper_instance,
            version=version,
            signal_id="phase2e3-completed",
            status=V2_COMPLETED_PAPER,
        )
        _add_job(
            db,
            user_id=real_user.id,
            instance=real_instance,
            version=version,
            signal_id="phase2e3-real",
            status=V2_PENDING,
            execution_mode=StrategyExecutionMode.REAL_ORDERS.value,
        )
        return {
            "paper_instance_id": str(paper_instance.id),
            "completed_signal_id": str(completed_signal.id),
            "completed_job_id": str(completed_job.id),
        }


def _patch_state_file(monkeypatch, tmp_path, positions: dict[str, Any]):
    from app.services import state_store

    path = tmp_path / "state" / "open_positions_by_instance.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(positions, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(state_store, "OPEN_POSITIONS_BY_INSTANCE_FILE", path)
    return path


def _patch_order_log(monkeypatch, tmp_path, rows: list[dict[str, Any]]):
    from app.services import audit_logger

    path = tmp_path / "logs" / "order_events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr(audit_logger, "LOG_FILES", {**audit_logger.LOG_FILES, "order": path})
    return path


def _db_snapshot() -> dict[str, Any]:
    with session_scope() as db:
        jobs = db.scalars(select(models.StrategyExecutionJob).order_by(models.StrategyExecutionJob.id)).all()
        signals = db.scalars(select(models.StrategySignal).order_by(models.StrategySignal.id)).all()
        instances = db.scalars(select(models.UserStrategyInstance).order_by(models.UserStrategyInstance.id)).all()
        return {
            "jobs": [
                (
                    str(job.id),
                    job.status,
                    job.attempts,
                    job.locked_by,
                    job.completed_at.isoformat() if job.completed_at else None,
                    job.last_error,
                    job.dead_letter_reason,
                    json.dumps(job.signal_payload, sort_keys=True),
                    json.dumps(job.result_summary, sort_keys=True),
                )
                for job in jobs
            ],
            "signals": [
                (
                    str(signal.id),
                    signal.status,
                    json.dumps(signal.result_summary, sort_keys=True),
                    signal.updated_at.isoformat(),
                )
                for signal in signals
            ],
            "instances": [
                (
                    str(instance.id),
                    instance.status,
                    instance.execution_mode,
                    instance.lots,
                    instance.side_preference,
                    json.dumps(instance.config_json, sort_keys=True),
                    instance.updated_at.isoformat(),
                )
                for instance in instances
            ],
        }


def test_status_endpoint_blocked_when_debug_disabled(monkeypatch):
    from app.routers import debug

    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    monkeypatch.setattr(debug, "get_v2_paper_fanout_status", _fail_if_called)

    response = _client(monkeypatch, debug_enabled=False).get("/api/debug/v2/paper-fanout/status")

    assert response.status_code == 404


def test_status_endpoint_blocked_when_v2_paper_runner_debug_false(monkeypatch):
    from app.routers import debug

    _set_flags(monkeypatch, fanout=True, runner_debug=False)
    monkeypatch.setattr(debug, "get_v2_paper_fanout_status", _fail_if_called)

    response = _client(monkeypatch).get("/api/debug/v2/paper-fanout/status")

    assert response.status_code == 403
    assert "disabled" in response.json()["detail"]


def test_jobs_and_instances_blocked_when_debug_flag_off(monkeypatch):
    from app.routers import debug

    _set_flags(monkeypatch, fanout=True, runner_debug=False)
    monkeypatch.setattr(debug, "list_v2_paper_fanout_jobs", _fail_if_called)
    monkeypatch.setattr(debug, "list_v2_paper_strategy_instances", _fail_if_called)
    client = _client(monkeypatch)

    jobs_response = client.get("/api/debug/v2/paper-fanout/jobs")
    instances_response = client.get("/api/debug/v2/paper-fanout/instances")

    assert jobs_response.status_code == 403
    assert instances_response.status_code == 403


def test_status_returns_safe_counts(mu_db, monkeypatch):
    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    _seed_inspector_rows()

    response = _client(monkeypatch).get("/api/debug/v2/paper-fanout/status?limit=5")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["flags"]["debug_enabled"] is True
    assert body["flags"]["multi_strategy_fanout"] is True
    assert body["flags"]["v2_paper_runner_debug"] is True
    assert body["counts"]["paper_instances"] == 1
    assert body["counts"]["active_paper_instances"] == 1
    assert body["counts"]["real_order_instances"] == 1
    assert body["counts"]["v2_pending_jobs"] == 1
    assert body["counts"]["v2_completed_paper_jobs"] == 1
    assert body["counts"]["v2_non_paper_jobs"] == 1
    assert body["safety"]["read_only"] is True
    assert body["safety"]["real_orders_supported"] is False
    assert body["recent_strategy_signals"]
    assert body["recent_normalized_option_signals"]
    assert _contains_key(body, FORBIDDEN_KEYS) is False


def test_jobs_response_excludes_sensitive_payload_fields(mu_db, monkeypatch):
    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    _seed_inspector_rows()

    response = _client(monkeypatch).get("/api/debug/v2/paper-fanout/jobs?limit=10")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert {job["execution_mode"] for job in body["jobs"]} == {StrategyExecutionMode.PAPER_LIVE_DATA.value}
    assert {job["status"] for job in body["jobs"]} == {V2_PENDING, V2_COMPLETED_PAPER}
    assert _contains_key(body, FORBIDDEN_KEYS) is False


def test_instances_response_excludes_credential_hashes_and_secrets(mu_db, monkeypatch, tmp_path):
    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    ids = _seed_inspector_rows()
    _patch_state_file(monkeypatch, tmp_path, {ids["paper_instance_id"]: {"has_open_position": False}})

    response = _client(monkeypatch).get("/api/debug/v2/paper-fanout/instances")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert "config_json" not in body["instances"][0]
    assert _contains_key(body, FORBIDDEN_KEYS) is False


def test_open_position_summary_includes_only_safe_fields(mu_db, monkeypatch, tmp_path):
    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    ids = _seed_inspector_rows()
    _patch_state_file(
        monkeypatch,
        tmp_path,
        {
            ids["paper_instance_id"]: {
                "has_open_position": True,
                "strategy_code": "SUPERTREND_V1",
                "symbol": "NIFTY",
                "instrument_type": "OPTIDX",
                "exchange_segment": "NSE_FNO",
                "trading_symbol": "NIFTY TEST CE",
                "option_side": "CE",
                "strike": 25000,
                "expiry": "2026-07-09",
                "qty": 75,
                "entry_price": 101.25,
                "opened_at": "2026-07-08T09:15:00+05:30",
                "execution_mode": "paper_live_data",
                "source_signal_id": "phase2e3-completed",
                "v2_job_id": ids["completed_job_id"],
                "entry_order_id": "paper-entry-hidden",
                "access_token": "do-not-return",
                "headers": {"secret": "do-not-return"},
                "raw_response": {"secret": "do-not-return"},
            }
        },
    )

    response = _client(monkeypatch).get("/api/debug/v2/paper-fanout/instances")

    assert response.status_code == 200
    summary = response.json()["instances"][0]["open_position_summary"]
    assert summary["has_open_position"] is True
    assert summary["symbol"] == "NIFTY"
    assert summary["option_side"] == "CE"
    assert summary["qty"] == 75
    assert set(summary) <= {
        "has_open_position",
        "strategy_code",
        "symbol",
        "instrument_type",
        "exchange_segment",
        "trading_symbol",
        "option_side",
        "strike",
        "expiry",
        "qty",
        "entry_price",
        "opened_at",
        "execution_mode",
        "source_signal_id",
        "v2_job_id",
    }
    assert _contains_key(summary, FORBIDDEN_KEYS) is False


def test_inspector_endpoints_do_not_call_execution_paths(mu_db, monkeypatch, tmp_path):
    from app.routers import debug
    from app.services import dhan_client, execution_router, paper_broker, strategy_worker_adapter_v2

    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    ids = _seed_inspector_rows()
    _patch_state_file(monkeypatch, tmp_path, {ids["paper_instance_id"]: {"has_open_position": False}})
    monkeypatch.setattr(debug, "run_v2_paper_fanout_once", _fail_if_called)
    monkeypatch.setattr(debug, "process_ready_v2_paper_jobs_once", _fail_if_called)
    monkeypatch.setattr(strategy_worker_adapter_v2, "process_next_v2_job_paper_once", _fail_if_called)
    monkeypatch.setattr(strategy_worker_adapter_v2, "process_v2_job_paper_once", _fail_if_called)
    monkeypatch.setattr(execution_router, "route_signal", _fail_if_called)
    monkeypatch.setattr(dhan_client.RealDhanClient, "place_order", _fail_if_called)
    monkeypatch.setattr(dhan_client.RealDhanClient, "place_super_order", _fail_if_called)
    monkeypatch.setattr(paper_broker.PaperBroker, "place_order", _fail_if_called)
    monkeypatch.setattr(paper_broker.PaperBroker, "place_super_order", _fail_if_called)
    client = _client(monkeypatch)

    assert client.get("/api/debug/v2/paper-fanout/status").status_code == 200
    assert client.get("/api/debug/v2/paper-fanout/jobs").status_code == 200
    assert client.get("/api/debug/v2/paper-fanout/instances").status_code == 200


def test_inspector_does_not_mutate_db_rows_or_state_files(mu_db, monkeypatch, tmp_path):
    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    ids = _seed_inspector_rows()
    state_path = _patch_state_file(
        monkeypatch,
        tmp_path,
        {ids["paper_instance_id"]: {"has_open_position": True, "symbol": "NIFTY", "qty": 75}},
    )
    log_path = _patch_order_log(
        monkeypatch,
        tmp_path,
        [{"event": "PAPER_ORDER_FILLED", "instance_id": ids["paper_instance_id"], "order_id": "PAPER-1"}],
    )
    before_db = _db_snapshot()
    before_state = state_path.read_text(encoding="utf-8")
    before_log = log_path.read_text(encoding="utf-8")
    client = _client(monkeypatch)

    assert client.get("/api/debug/v2/paper-fanout/status").status_code == 200
    assert client.get("/api/debug/v2/paper-fanout/jobs").status_code == 200
    assert client.get("/api/debug/v2/paper-fanout/instances").status_code == 200

    assert _db_snapshot() == before_db
    assert state_path.read_text(encoding="utf-8") == before_state
    assert log_path.read_text(encoding="utf-8") == before_log


def test_legacy_debug_feature_flags_endpoint_still_works(monkeypatch):
    _set_flags(monkeypatch, fanout=True, runner_debug=True)

    response = _client(monkeypatch).get("/api/debug/feature-flags")

    assert response.status_code == 200
    body = response.json()
    assert body[MULTI_STRATEGY_FANOUT] is True
    assert body[V2_PAPER_RUNNER_DEBUG] is True
