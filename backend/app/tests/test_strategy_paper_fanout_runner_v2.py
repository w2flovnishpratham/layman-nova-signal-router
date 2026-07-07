from __future__ import annotations

from pathlib import Path
from typing import Any

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
        "signal_id": "phase2e0-signal-001",
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
    execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
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


def _isolate_runtime(monkeypatch, tmp_path):
    from app.services import audit_logger, state_store

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
    state_store.init_runtime_files()
    state_store.set_engine_mode("paper")
    state_store.update_app_state(webhook_trading_enabled=True, engine_started=True)
    state_store.update_runtime_settings(
        allow_entry=True,
        allow_exit=True,
        emergency_stop=False,
        global_kill_switch=False,
        max_trades_per_day=0,
        max_daily_loss=0,
        allowed_option_side="BOTH",
        option_exit_mode="SERVER",
    )
    return state_root


def _position(instance_id: str | None = None, **overrides: Any) -> dict[str, Any]:
    from app.services import state_store

    position = {
        **state_store.default_open_position(),
        "has_open_position": True,
        "strategy_code": "SUPERTREND_V1",
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "exchange_segment": "NSE_FNO",
        "security_id": "57046",
        "trading_symbol": "NIFTY RUNNER CE",
        "option_side": "CE",
        "strike": 24500.0,
        "expiry": "2026-07-09",
        "qty": 75,
        "entry_order_id": "PAPER-ENTRY",
        "entry_price": 100.0,
        "exit_management": "SERVER",
        "order_type": "MARKET",
        "product_type": "INTRADAY",
    }
    if instance_id is not None:
        position["instance_id"] = instance_id
        position["trading_symbol"] = f"NIFTY INSTANCE {instance_id} CE"
    position.update(overrides)
    return position


def _install_fake_route(monkeypatch, calls: list[Any]):
    from app.services import state_store

    def fake_route(signal):
        assert state_store.get_engine_mode(legacy_fallback=False) == "paper"
        calls.append(signal)
        instance_id = str(signal.raw_payload["instance_id"])
        if signal.action == "EXIT":
            state_store.clear_open_position(instance_id=instance_id)
            return {
                "success": True,
                "status": "PAPER_EXIT_OK",
                "order_id": f"paper-exit-{instance_id}",
                "access_token": "should-not-leak",
                "raw_response": {"secret": "should-not-leak"},
            }
        state_store.set_open_position(
            _position(
                instance_id,
                qty=signal.qty,
                entry_order_id=f"PAPER-ENTRY-{signal.signal_id}",
                execution_mode=signal.raw_payload["execution_mode"],
                strategy_version_id=signal.raw_payload["strategy_version_id"],
                v2_job_id=signal.raw_payload["v2_job_id"],
                source_signal_id=signal.raw_payload["source_signal_id"],
            ),
            instance_id=instance_id,
        )
        return {
            "success": True,
            "status": "PAPER_ENTRY_OK",
            "order_id": f"paper-entry-{instance_id}",
            "access_token": "should-not-leak",
            "raw_response": {"secret": "should-not-leak"},
            "nested": {"proxy_url": "should-not-leak", "keep": "safe"},
        }

    monkeypatch.setattr("app.services.strategy_worker_adapter_v2._route_signal", fake_route)


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


def test_feature_flag_off_returns_disabled_without_side_effects(mu_db, monkeypatch):
    from app.services.strategy_fanout_v2 import FEATURE_FLAG_DISABLED
    from app.services.strategy_paper_fanout_runner_v2 import run_v2_paper_fanout_once

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "0")

    with session_scope() as db:
        result = run_v2_paper_fanout_once(
            db,
            _raw_payload(signal_id="phase2e0-flag-off"),
            "SUPERTREND_V1",
        )

        assert result.ok is False
        assert result.errors[0]["reason"] == FEATURE_FLAG_DISABLED
        assert db.query(models.StrategySignal).count() == 0
        assert db.query(models.StrategyExecutionJob).count() == 0


def test_runner_creates_pending_paper_job_when_processing_disabled(mu_db, monkeypatch):
    from app.services.strategy_execution_queue_v2 import V2_PENDING
    from app.services.strategy_paper_fanout_runner_v2 import run_v2_paper_fanout_once

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    user = make_user("phase2e0-pending@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        instance = _add_instance(db, user_id=user.id, catalog=catalog, version=version)

        result = run_v2_paper_fanout_once(
            db,
            _raw_payload(signal_id="phase2e0-pending"),
            "SUPERTREND_V1",
            max_jobs=0,
        )

        assert result.ok is True
        assert result.planned_count == 1
        assert result.job_created_count == 1
        assert result.processed_count == 0
        job = db.scalar(select(models.StrategyExecutionJob))
        assert job is not None
        assert job.status == V2_PENDING
        assert job.instance_id == instance.id
        assert job.execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value


def test_runner_processes_paper_job_with_internal_marker_and_sanitized_result(
    mu_db,
    monkeypatch,
    tmp_path,
):
    from app.services import state_store
    from app.services.dhan_client import RealDhanClient
    from app.services.strategy_paper_fanout_runner_v2 import run_v2_paper_fanout_once
    from app.services.strategy_worker_adapter_v2 import V2_COMPLETED_PAPER

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    monkeypatch.setattr("app.services.strategy_worker_adapter_v2._current_lot_size", lambda: 75)
    dhan_calls = []
    monkeypatch.setattr(
        RealDhanClient,
        "place_order",
        lambda self, **kwargs: dhan_calls.append(kwargs) or {"success": False},
    )
    _isolate_runtime(monkeypatch, tmp_path)
    route_calls = []
    _install_fake_route(monkeypatch, route_calls)
    user = make_user("phase2e0-process@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        instance = _add_instance(db, user_id=user.id, catalog=catalog, version=version)

        result = run_v2_paper_fanout_once(
            db,
            _raw_payload(signal_id="phase2e0-process"),
            "SUPERTREND_V1",
        )

        assert result.ok is True
        assert result.completed_count == 1
        assert result.failed_count == 0
        assert len(route_calls) == 1
        signal = route_calls[0]
        assert signal.secret == ""
        assert signal.raw_payload["v2_internal"] is True
        assert signal.raw_payload["instance_id"] == str(instance.id)
        assert signal.raw_payload["strategy_version_id"] == str(version.id)
        assert signal.raw_payload["execution_mode"] == StrategyExecutionMode.PAPER_LIVE_DATA.value
        assert result.job_results[0]["status"] == V2_COMPLETED_PAPER
        assert result.job_results[0]["route_result_summary"]["nested"] == {"keep": "safe"}
        assert _contains_key(
            result.as_dict(),
            {"access_token", "raw_payload", "raw_response", "proxy_url", "secret"},
        ) is False
        assert state_store.get_open_position(str(instance.id))["has_open_position"] is True
        assert state_store.get_open_position()["has_open_position"] is False
        assert dhan_calls == []


def test_runner_filters_signal_only_and_real_orders_before_route(mu_db, monkeypatch, tmp_path):
    from app.services.strategy_paper_fanout_runner_v2 import run_v2_paper_fanout_once

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    monkeypatch.setattr("app.services.strategy_worker_adapter_v2._current_lot_size", lambda: 75)
    _isolate_runtime(monkeypatch, tmp_path)
    route_calls = []
    _install_fake_route(monkeypatch, route_calls)
    paper_user = make_user("phase2e0-paper@example.com")
    signal_user = make_user("phase2e0-signal-only@example.com")
    real_user = make_user("phase2e0-real@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        paper_instance = _add_instance(db, user_id=paper_user.id, catalog=catalog, version=version)
        _add_instance(
            db,
            user_id=signal_user.id,
            catalog=catalog,
            version=version,
            execution_mode=StrategyExecutionMode.SIGNAL_ONLY.value,
        )
        _add_instance(
            db,
            user_id=real_user.id,
            catalog=catalog,
            version=version,
            execution_mode=StrategyExecutionMode.REAL_ORDERS.value,
        )

        result = run_v2_paper_fanout_once(
            db,
            _raw_payload(signal_id="phase2e0-paper-only"),
            "SUPERTREND_V1",
        )

        jobs = db.scalars(select(models.StrategyExecutionJob)).all()
        assert result.ok is True
        assert result.planned_count == 1
        assert result.job_created_count == 1
        assert result.skipped_count == 2
        assert len(route_calls) == 1
        assert len(jobs) == 1
        assert jobs[0].instance_id == paper_instance.id
        assert jobs[0].execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value


def test_spoofed_external_metadata_is_replaced_by_internal_instance(mu_db, monkeypatch, tmp_path):
    from app.services import state_store
    from app.services.strategy_paper_fanout_runner_v2 import run_v2_paper_fanout_once

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    monkeypatch.setattr("app.services.strategy_worker_adapter_v2._current_lot_size", lambda: 75)
    _isolate_runtime(monkeypatch, tmp_path)
    route_calls = []
    _install_fake_route(monkeypatch, route_calls)
    user = make_user("phase2e0-spoof@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        instance = _add_instance(db, user_id=user.id, catalog=catalog, version=version)

        result = run_v2_paper_fanout_once(
            db,
            _raw_payload(
                signal_id="phase2e0-spoof",
                metadata={
                    "instance_id": "evil",
                    "v2_internal": True,
                    "source": "strategy_worker_adapter_v2",
                },
            ),
            "SUPERTREND_V1",
        )

        assert result.ok is True
        assert len(route_calls) == 1
        assert route_calls[0].raw_payload["instance_id"] == str(instance.id)
        assert route_calls[0].raw_payload["v2_internal"] is True
        assert state_store.get_open_position(str(instance.id))["has_open_position"] is True
        assert state_store.get_open_position("evil") is None


def test_two_paper_instances_receive_separate_positions_and_legacy_stays_untouched(
    mu_db,
    monkeypatch,
    tmp_path,
):
    from app.services import state_store
    from app.services.strategy_paper_fanout_runner_v2 import run_v2_paper_fanout_once

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    monkeypatch.setattr("app.services.strategy_worker_adapter_v2._current_lot_size", lambda: 75)
    _isolate_runtime(monkeypatch, tmp_path)
    route_calls = []
    _install_fake_route(monkeypatch, route_calls)
    state_store.set_open_position(_position(trading_symbol="NIFTY LEGACY CE"))
    first_user = make_user("phase2e0-two-a@example.com")
    second_user = make_user("phase2e0-two-b@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        first = _add_instance(db, user_id=first_user.id, catalog=catalog, version=version)
        second = _add_instance(db, user_id=second_user.id, catalog=catalog, version=version)

        result = run_v2_paper_fanout_once(
            db,
            _raw_payload(signal_id="phase2e0-two-paper"),
            "SUPERTREND_V1",
        )

        assert result.ok is True
        assert result.completed_count == 2
        assert len(route_calls) == 2
        assert state_store.get_open_position(str(first.id))["has_open_position"] is True
        assert state_store.get_open_position(str(second.id))["has_open_position"] is True
        assert state_store.get_paper_position()["trading_symbol"] == "NIFTY LEGACY CE"


def test_runner_exit_clears_only_matching_instance(mu_db, monkeypatch, tmp_path):
    from app.services import state_store
    from app.services.strategy_paper_fanout_runner_v2 import run_v2_paper_fanout_once

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    monkeypatch.setattr("app.services.strategy_worker_adapter_v2._current_lot_size", lambda: 75)
    _isolate_runtime(monkeypatch, tmp_path)
    route_calls = []
    _install_fake_route(monkeypatch, route_calls)
    user = make_user("phase2e0-exit@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        instance = _add_instance(db, user_id=user.id, catalog=catalog, version=version)
        instance_id = str(instance.id)
        state_store.set_open_position(_position(instance_id), instance_id=instance_id)
        state_store.set_open_position(_position("other-instance"), instance_id="other-instance")

        result = run_v2_paper_fanout_once(
            db,
            _raw_payload(
                signal_id="phase2e0-exit",
                action="EXIT",
                intent="FLAT",
                option_side="NONE",
            ),
            "SUPERTREND_V1",
        )

        assert result.ok is True
        assert result.completed_count == 1
        assert len(route_calls) == 1
        assert route_calls[0].action == "EXIT"
        assert state_store.get_open_position(instance_id) is None
        assert state_store.get_open_position("other-instance")["has_open_position"] is True


def test_legacy_worker_cannot_claim_runner_created_v2_jobs(mu_db, monkeypatch):
    from app.config import settings
    from app.services.strategy_execution_queue_v2 import V2_PENDING
    from app.services.strategy_paper_fanout_runner_v2 import run_v2_paper_fanout_once
    from app.workers import strategy_job_worker

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    monkeypatch.setattr(settings, "STRATEGY_JOB_WORKER_ENABLED", True, raising=False)
    route_calls = []
    monkeypatch.setattr(
        strategy_job_worker.strategy_fanout,
        "dispatch_signal_job",
        lambda **kwargs: route_calls.append(kwargs) or {"status": "unexpected"},
    )
    user = make_user("phase2e0-legacy-worker@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=user.id, catalog=catalog, version=version)
        result = run_v2_paper_fanout_once(
            db,
            _raw_payload(signal_id="phase2e0-legacy-worker"),
            "SUPERTREND_V1",
            max_jobs=0,
        )
        job_id = db.scalar(select(models.StrategyExecutionJob.id))

    assert result.job_created_count == 1
    assert strategy_job_worker.process_queued_jobs_once(limit=1) == 0
    assert route_calls == []
    with session_scope() as db:
        job = db.get(models.StrategyExecutionJob, job_id)
        assert job is not None
        assert job.status == V2_PENDING


def test_process_ready_paper_jobs_respects_max_jobs(mu_db, monkeypatch, tmp_path):
    from app.services.strategy_execution_queue_v2 import V2_PENDING
    from app.services.strategy_paper_fanout_runner_v2 import (
        process_ready_v2_paper_jobs_once,
        run_v2_paper_fanout_once,
    )
    from app.services.strategy_worker_adapter_v2 import V2_COMPLETED_PAPER

    monkeypatch.setenv("MULTI_STRATEGY_FANOUT", "1")
    monkeypatch.setattr("app.services.strategy_worker_adapter_v2._current_lot_size", lambda: 75)
    _isolate_runtime(monkeypatch, tmp_path)
    route_calls = []
    _install_fake_route(monkeypatch, route_calls)
    first_user = make_user("phase2e0-ready-a@example.com")
    second_user = make_user("phase2e0-ready-b@example.com")

    with session_scope() as db:
        catalog, version = _seed_supertrend(db)
        _add_instance(db, user_id=first_user.id, catalog=catalog, version=version)
        _add_instance(db, user_id=second_user.id, catalog=catalog, version=version)
        queued = run_v2_paper_fanout_once(
            db,
            _raw_payload(signal_id="phase2e0-ready"),
            "SUPERTREND_V1",
            max_jobs=0,
        )

        processed = process_ready_v2_paper_jobs_once(db, max_jobs=1)
        statuses = db.scalars(select(models.StrategyExecutionJob.status)).all()

        assert queued.job_created_count == 2
        assert processed.processed_count == 1
        assert processed.completed_count == 1
        assert len(route_calls) == 1
        assert statuses.count(V2_COMPLETED_PAPER) == 1
        assert statuses.count(V2_PENDING) == 1


def test_paper_fanout_runner_is_not_registered_in_startup_routes_or_workers():
    app_root = Path(__file__).resolve().parents[1]
    checked_paths = [
        app_root / "main.py",
        app_root / "workers" / "strategy_job_worker.py",
        app_root / "routers" / "strategies.py",
        app_root / "routers" / "webhooks.py",
        app_root / "services" / "strategy_fanout_v2.py",
    ]

    for path in checked_paths:
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        assert "run_v2_paper_fanout_once" not in source
        assert "process_ready_v2_paper_jobs_once" not in source
