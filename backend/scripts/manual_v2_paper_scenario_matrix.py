#!/usr/bin/env python
"""Run an isolated v2 paper fanout scenario matrix.

Default mode creates a fresh temporary SQLite database and temporary runtime
state/log directories. It does not use or mutate the configured staging DB.

Run from backend/ with safe debug flags enabled:

    python -m scripts.manual_v2_paper_scenario_matrix
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.core.enums import (  # noqa: E402
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
    StrategySourceType,
)
from app.core.feature_flags import (  # noqa: E402
    MULTI_STRATEGY_FANOUT,
    V2_PAPER_RUNNER_DEBUG,
    feature_flag_states,
    is_feature_enabled,
)
from app.db import crud, models  # noqa: E402
from app.db.engine import init_db, reset_engine_for_tests, session_scope  # noqa: E402
from app.schemas.nova_signal_v1 import NovaSignalV1  # noqa: E402
from app.services import audit_logger, state_store  # noqa: E402
from app.services.strategy_execution_queue_v2 import V2_PENDING  # noqa: E402
from app.services.strategy_paper_fanout_inspector_v2 import REDACTED_KEYS as INSPECTOR_REDACTED_KEYS  # noqa: E402
from app.services.strategy_paper_fanout_runner_v2 import run_v2_paper_fanout_once  # noqa: E402
from scripts.backfill_multistrategy import run_backfill  # noqa: E402
from scripts.manual_v2_paper_fanout_check import (  # noqa: E402
    _install_simulated_paper_market_data,
    _restore_simulated_paper_market_data,
    build_analytics_pairing_preview,
)
from scripts.manual_v2_paper_fanout_inspector_check import (  # noqa: E402
    InspectorSmokeConfig,
    run_inspector_smoke_check,
)


SUPERTREND_CODE = "SUPERTREND_V1"
SUPERTREND_VERSION = "v1"
SCENARIO_WORKER_ID = "manual-v2-paper-scenario-matrix"

SENSITIVE_KEYS = {
    *INSPECTOR_REDACTED_KEYS,
    "raw_payload",
    "signal_payload",
    "payload",
}


class ScenarioMatrixError(RuntimeError):
    """Raised when the scenario matrix cannot run safely or a case fails."""


@dataclass(frozen=True)
class ScenarioMatrixConfig:
    allow_external_db: bool = False
    work_dir: Path | None = None
    keep_temp: bool = False


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class ScenarioContext:
    root: Path
    database_url: str
    runtime_state_dir: Path
    runtime_log_dir: Path
    isolated: bool


def build_entry_payload(
    *,
    signal_id: str,
    intent: str = "BULLISH",
    strike: float = 25000.0,
    expiry: str = "2026-07-09",
    lots: int = 1,
) -> dict[str, Any]:
    return _base_payload(
        signal_id=signal_id,
        action="ENTRY",
        intent=intent,
        option_side="AUTO",
        strike=strike,
        expiry=expiry,
        lots=lots,
    )


def build_exit_payload(
    *,
    signal_id: str,
    strike: float = 25000.0,
    expiry: str = "2026-07-09",
    lots: int = 1,
) -> dict[str, Any]:
    return _base_payload(
        signal_id=signal_id,
        action="EXIT",
        intent="FLAT",
        option_side="NONE",
        strike=strike,
        expiry=expiry,
        lots=lots,
    )


def run_scenario_matrix(
    config: ScenarioMatrixConfig,
    *,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    _require_safe_environment(config)
    context = _prepare_environment(config)
    shim = None
    results: list[ScenarioResult] = []
    try:
        shim = _install_simulated_paper_market_data()
        for name, runner in _scenario_functions():
            try:
                details = runner(context)
                results.append(ScenarioResult(name=name, passed=True, details=details))
            except Exception as exc:
                results.append(ScenarioResult(name=name, passed=False, error=_safe_error(exc)))
                break
    finally:
        if shim is not None:
            _restore_simulated_paper_market_data(shim)

    summary = _summary(context, results)
    _emit(out, "scenario_matrix_summary", summary)
    if not summary["overall_ok"]:
        raise ScenarioMatrixError("One or more v2 paper scenarios failed.")
    if context.isolated and not config.keep_temp:
        shutil.rmtree(context.root, ignore_errors=True)
    return _sanitize(summary)


def parse_args(argv: list[str] | None = None) -> ScenarioMatrixConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-external-db",
        action="store_true",
        help="Use the configured DATABASE_URL instead of a fresh temporary SQLite DB.",
    )
    parser.add_argument("--work-dir", type=Path, help="Optional temp root for isolated DB/runtime.")
    parser.add_argument("--keep-temp", action="store_true", help="Do not delete the isolated temp root after success.")
    args = parser.parse_args(argv)
    return ScenarioMatrixConfig(
        allow_external_db=bool(args.allow_external_db),
        work_dir=args.work_dir,
        keep_temp=bool(args.keep_temp),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        run_scenario_matrix(parse_args(argv))
    except ScenarioMatrixError as exc:
        print(json.dumps({"ok": False, "error": _safe_error(exc)}, sort_keys=True))
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "error": _safe_error(exc)}, sort_keys=True))
        return 1
    return 0


def _require_safe_environment(config: ScenarioMatrixConfig) -> None:
    errors = []
    if not settings.DEBUG_ENABLED:
        errors.append("DEBUG_ENABLED must be true.")
    if not is_feature_enabled(MULTI_STRATEGY_FANOUT):
        errors.append("MULTI_STRATEGY_FANOUT must be true.")
    if not is_feature_enabled(V2_PAPER_RUNNER_DEBUG):
        errors.append("V2_PAPER_RUNNER_DEBUG must be true.")
    if settings.ENABLE_LIVE_ORDERS:
        errors.append("ENABLE_LIVE_ORDERS must be false for the scenario matrix.")
    if config.allow_external_db and not str(settings.DATABASE_URL or "").strip():
        errors.append("DATABASE_URL must be configured when --allow-external-db is used.")
    if errors:
        raise ScenarioMatrixError(" ".join(errors))


def _prepare_environment(config: ScenarioMatrixConfig) -> ScenarioContext:
    if config.allow_external_db:
        root = config.work_dir or Path.cwd()
        context = ScenarioContext(
            root=root,
            database_url=str(settings.DATABASE_URL),
            runtime_state_dir=Path(settings.RUNTIME_STATE_DIR),
            runtime_log_dir=Path(settings.RUNTIME_LOG_DIR),
            isolated=False,
        )
    else:
        root = config.work_dir or Path(tempfile.mkdtemp(prefix="phase2e5-v2-paper-matrix-"))
        root.mkdir(parents=True, exist_ok=True)
        db_path = root / "scenario_matrix.sqlite3"
        database_url = f"sqlite:///{db_path.as_posix()}"
        settings.DATABASE_URL = database_url
        os.environ["DATABASE_URL"] = database_url
        context = ScenarioContext(
            root=root,
            database_url=database_url,
            runtime_state_dir=root / "runtime_state",
            runtime_log_dir=root / "runtime_logs",
            isolated=True,
        )
    _install_runtime_paths(context)
    reset_engine_for_tests()
    init_db()
    run_backfill()
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
    return context


def _install_runtime_paths(context: ScenarioContext) -> None:
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
        setattr(state_store, name, context.runtime_state_dir / filename)
    log_files = {
        "webhook": context.runtime_log_dir / "webhook_events.jsonl",
        "order": context.runtime_log_dir / "order_events.jsonl",
        "audit": context.runtime_log_dir / "audit_events.jsonl",
        "error": context.runtime_log_dir / "errors.jsonl",
        "paper_orders": context.runtime_log_dir / "paper_orders.jsonl",
    }
    state_store.LOG_FILES = log_files
    audit_logger.LOG_FILES = log_files
    paper_broker = sys.modules.get("app.services.paper_broker")
    if paper_broker is not None:
        paper_broker.LOG_FILES = log_files


def _scenario_functions() -> tuple[tuple[str, Callable[[ScenarioContext], dict[str, Any]]], ...]:
    return (
        ("bullish_entry_exit", _scenario_bullish_entry_exit),
        ("bearish_entry_exit", _scenario_bearish_entry_exit),
        ("duplicate_signal", _scenario_duplicate_signal),
        ("two_instance_isolation", _scenario_two_instance_isolation),
        ("real_orders_blocked", _scenario_real_orders_blocked),
        ("signal_only_not_routed", _scenario_signal_only_not_routed),
        ("max_jobs_respected", _scenario_max_jobs_respected),
        ("inspector_safe", _scenario_inspector_safe),
    )


def _scenario_bullish_entry_exit(_context: ScenarioContext) -> dict[str, Any]:
    return _single_instance_entry_exit(name="bullish", intent="BULLISH", expected_option_side="CE")


def _scenario_bearish_entry_exit(_context: ScenarioContext) -> dict[str, Any]:
    return _single_instance_entry_exit(name="bearish", intent="BEARISH", expected_option_side="PE")


def _single_instance_entry_exit(*, name: str, intent: str, expected_option_side: str) -> dict[str, Any]:
    family = _family(name)
    with session_scope() as db:
        _pause_all_instances(db)
        catalog, version = _ensure_supertrend_catalog_version(db)
        user = _ensure_user(db, f"{family}@example.invalid")
        instance = _add_instance(db, user_id=user.id, catalog=catalog, version=version)
        instance_id = str(instance.id)
        legacy_before = _set_legacy_position(f"NIFTY LEGACY {family}")
        entry = run_v2_paper_fanout_once(
            db,
            build_entry_payload(signal_id=f"{family}-entry", intent=intent),
            SUPERTREND_CODE,
            max_jobs=1,
        )
        position = state_store.get_open_position(instance_id) or {}
        _assert(entry.ok and entry.completed_count == 1, f"{name} entry did not complete.")
        _assert(position.get("has_open_position") is True, f"{name} position did not open.")
        _assert(position.get("option_side") == expected_option_side, f"{name} option side mismatch.")
        exit_result = run_v2_paper_fanout_once(
            db,
            build_exit_payload(signal_id=f"{family}-exit"),
            SUPERTREND_CODE,
            max_jobs=1,
        )
        _assert(exit_result.ok and exit_result.completed_count == 1, f"{name} exit did not complete.")
        _assert(state_store.get_open_position(instance_id) is None, f"{name} position did not clear.")
        _assert(state_store.get_paper_position() == legacy_before, f"{name} legacy paper position changed.")
    analytics = build_analytics_pairing_preview(instance_id=instance_id)
    _assert(analytics.get("paired_count") == 1, f"{name} analytics pairing failed.")
    return {
        "entry_position_opened": True,
        "exit_position_cleared": True,
        "legacy_position_untouched": True,
        "option_side": expected_option_side,
        "analytics_pairing": analytics,
    }


def _scenario_duplicate_signal(_context: ScenarioContext) -> dict[str, Any]:
    family = _family("duplicate")
    with session_scope() as db:
        _pause_all_instances(db)
        catalog, version = _ensure_supertrend_catalog_version(db)
        user = _ensure_user(db, f"{family}@example.invalid")
        instance = _add_instance(db, user_id=user.id, catalog=catalog, version=version)
        instance_id = str(instance.id)
        payload = build_entry_payload(signal_id=f"{family}-entry")
        first = run_v2_paper_fanout_once(db, payload, SUPERTREND_CODE, max_jobs=1)
        position_after_first = dict(state_store.get_open_position(instance_id) or {})
        orders_after_first = _paper_order_log_count()
        jobs_after_first = _job_count(db)
        analytics_after_first = build_analytics_pairing_preview(instance_id=instance_id)
        second = run_v2_paper_fanout_once(db, payload, SUPERTREND_CODE, max_jobs=1)
        _assert(first.ok and first.completed_count == 1, "duplicate scenario first run failed.")
        _assert(second.job_created_count == 0 and second.processed_count == 0, "duplicate created executable work.")
        _assert(_job_count(db) == jobs_after_first, "duplicate created an extra job.")
        _assert(_paper_order_log_count() == orders_after_first, "duplicate created an extra paper order.")
        _assert(state_store.get_open_position(instance_id) == position_after_first, "duplicate changed open position.")
    analytics = build_analytics_pairing_preview(instance_id=instance_id)
    _assert(analytics == analytics_after_first, "duplicate changed analytics pairing preview.")
    return {
        "first_completed": first.completed_count,
        "second_created_jobs": second.job_created_count,
        "job_count_unchanged": True,
        "position_unchanged": True,
        "paper_order_log_count_unchanged": True,
        "analytics_pairing_unchanged": True,
        "analytics_pairing": analytics,
    }


def _scenario_two_instance_isolation(_context: ScenarioContext) -> dict[str, Any]:
    family = _family("two-instance")
    with session_scope() as db:
        _pause_all_instances(db)
        catalog, version = _ensure_supertrend_catalog_version(db)
        first_user = _ensure_user(db, f"{family}-a@example.invalid")
        second_user = _ensure_user(db, f"{family}-b@example.invalid")
        first = _add_instance(db, user_id=first_user.id, catalog=catalog, version=version)
        second = _add_instance(db, user_id=second_user.id, catalog=catalog, version=version)
        first_id = str(first.id)
        second_id = str(second.id)
        legacy_before = _set_legacy_position("NIFTY LEGACY TWO")
        entry = run_v2_paper_fanout_once(
            db,
            build_entry_payload(signal_id=f"{family}-entry"),
            SUPERTREND_CODE,
            max_jobs=2,
        )
        _assert(entry.completed_count == 2, "two-instance entry did not process both jobs.")
        first_position = state_store.get_open_position(first_id)
        second_position = state_store.get_open_position(second_id)
        _assert((first_position or {}).get("has_open_position") is True, "first instance did not open.")
        _assert((second_position or {}).get("has_open_position") is True, "second instance did not open.")
        second.status = StrategyInstanceStatus.PAUSED.value
        db.flush()
        exit_first = run_v2_paper_fanout_once(
            db,
            build_exit_payload(signal_id=f"{family}-exit-a"),
            SUPERTREND_CODE,
            max_jobs=1,
        )
        _assert(exit_first.completed_count == 1, "first exit did not process one job.")
        _assert(state_store.get_open_position(first_id) is None, "first exit did not clear first instance.")
        _assert(state_store.get_open_position(second_id) is not None, "first exit cleared second instance.")
        first.status = StrategyInstanceStatus.PAUSED.value
        second.status = StrategyInstanceStatus.ACTIVE.value
        db.flush()
        exit_second = run_v2_paper_fanout_once(
            db,
            build_exit_payload(signal_id=f"{family}-exit-b"),
            SUPERTREND_CODE,
            max_jobs=1,
        )
        _assert(exit_second.completed_count == 1, "second exit did not process one job.")
        _assert(state_store.get_open_position(second_id) is None, "second exit did not clear second instance.")
        _assert(state_store.get_paper_position() == legacy_before, "two-instance legacy paper position changed.")
    return {
        "entry_processed": entry.completed_count,
        "first_exit_cleared_only_first": True,
        "second_exit_cleared_second": True,
        "legacy_position_untouched": True,
    }


def _scenario_real_orders_blocked(_context: ScenarioContext) -> dict[str, Any]:
    family = _family("real-orders")
    with _route_guard():
        with session_scope() as db:
            _pause_all_instances(db)
            catalog, version = _ensure_supertrend_catalog_version(db)
            user = _ensure_user(db, f"{family}@example.invalid")
            _add_instance(
                db,
                user_id=user.id,
                catalog=catalog,
                version=version,
                execution_mode=StrategyExecutionMode.REAL_ORDERS.value,
            )
            result = run_v2_paper_fanout_once(
                db,
                build_entry_payload(signal_id=f"{family}-entry"),
                SUPERTREND_CODE,
                max_jobs=1,
            )
            real_jobs = _job_count(db, execution_mode=StrategyExecutionMode.REAL_ORDERS.value)
            pending_real = _job_count(db, execution_mode=StrategyExecutionMode.REAL_ORDERS.value, status=V2_PENDING)
            _assert(result.job_created_count == 0, "real_orders created a v2 job.")
            _assert(real_jobs == 0 and pending_real == 0, "real_orders job rows were created.")
    return {
        "skipped_count": result.skipped_count,
        "real_order_jobs": real_jobs,
        "pending_real_order_jobs": pending_real,
    }


def _scenario_signal_only_not_routed(_context: ScenarioContext) -> dict[str, Any]:
    family = _family("signal-only")
    with _route_guard():
        with session_scope() as db:
            _pause_all_instances(db)
            catalog, version = _ensure_supertrend_catalog_version(db)
            user = _ensure_user(db, f"{family}@example.invalid")
            _add_instance(
                db,
                user_id=user.id,
                catalog=catalog,
                version=version,
                execution_mode=StrategyExecutionMode.SIGNAL_ONLY.value,
            )
            result = run_v2_paper_fanout_once(
                db,
                build_entry_payload(signal_id=f"{family}-entry"),
                SUPERTREND_CODE,
                max_jobs=1,
            )
            paper_positions = state_store.list_open_positions_by_instance()
            _assert(result.job_created_count == 0, "signal_only created a v2 paper job.")
            _assert(not paper_positions, "signal_only created a paper position.")
    return {
        "skipped_count": result.skipped_count,
        "job_created_count": result.job_created_count,
        "paper_positions_created": 0,
    }


def _scenario_max_jobs_respected(_context: ScenarioContext) -> dict[str, Any]:
    family = _family("max-jobs")
    with session_scope() as db:
        _pause_all_instances(db)
        catalog, version = _ensure_supertrend_catalog_version(db)
        for index in range(3):
            user = _ensure_user(db, f"{family}-{index}@example.invalid")
            _add_instance(db, user_id=user.id, catalog=catalog, version=version)
        result = run_v2_paper_fanout_once(
            db,
            build_entry_payload(signal_id=f"{family}-entry"),
            SUPERTREND_CODE,
            max_jobs=1,
        )
        pending = _job_count(db, status=V2_PENDING)
        open_positions = len(state_store.list_open_positions_by_instance())
        _assert(result.job_created_count == 3, "max_jobs scenario did not create all jobs.")
        _assert(result.processed_count == 1 and result.completed_count == 1, "max_jobs processed too many jobs.")
        _assert(pending == 2, "max_jobs did not leave remaining jobs pending.")
        _assert(open_positions == 1, "max_jobs opened more than one position.")
    return {
        "job_created_count": result.job_created_count,
        "processed_count": result.processed_count,
        "completed_count": result.completed_count,
        "remaining_pending_jobs": pending,
        "open_positions": open_positions,
    }


def _scenario_inspector_safe(_context: ScenarioContext) -> dict[str, Any]:
    output: list[str] = []
    summary = run_inspector_smoke_check(InspectorSmokeConfig(direct_service=True, limit=50), out=output.append)
    _assert(summary["readable"] == {"status": True, "jobs": True, "instances": True}, "inspector not readable.")
    _assert(summary["safety"]["sensitive_field_count"] == 0, "inspector exposed sensitive fields.")
    _assert(summary["safety"]["db_unchanged"] is True, "inspector mutated DB.")
    _assert(summary["safety"]["runtime_files_unchanged"] is True, "inspector mutated runtime files.")
    return {
        "readable": summary["readable"],
        "sensitive_field_count": summary["safety"]["sensitive_field_count"],
        "db_unchanged": summary["safety"]["db_unchanged"],
        "runtime_files_unchanged": summary["safety"]["runtime_files_unchanged"],
    }


def _ensure_supertrend_catalog_version(db: Any) -> tuple[models.StrategyCatalog, models.StrategyVersion]:
    catalog = db.scalar(select(models.StrategyCatalog).where(models.StrategyCatalog.code == SUPERTREND_CODE))
    if catalog is None:
        catalog = models.StrategyCatalog(
            code=SUPERTREND_CODE,
            name="Supertrend",
            status=StrategyCatalogStatus.ACTIVE.value,
            source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
            metadata_json={"aliases": ["supertrend", "TRADINGVIEW_NIFTY_V1"]},
        )
        db.add(catalog)
        db.flush()
    version = db.scalar(
        select(models.StrategyVersion).where(
            models.StrategyVersion.strategy_id == catalog.id,
            models.StrategyVersion.version == SUPERTREND_VERSION,
        )
    )
    if version is None:
        version = models.StrategyVersion(
            strategy_id=catalog.id,
            version=SUPERTREND_VERSION,
            status=StrategyCatalogStatus.ACTIVE.value,
            payload_version="nova.v1",
        )
        db.add(version)
        db.flush()
    return catalog, version


def _ensure_user(db: Any, email: str) -> models.User:
    return crud.upsert_google_user(
        db,
        google_sub=f"scenario-{email}",
        email=email,
        name="V2 Paper Scenario",
        picture_url=None,
        is_admin=False,
    )


def _add_instance(
    db: Any,
    *,
    user_id: uuid.UUID,
    catalog: models.StrategyCatalog,
    version: models.StrategyVersion,
    execution_mode: str = StrategyExecutionMode.PAPER_LIVE_DATA.value,
    status: str = StrategyInstanceStatus.ACTIVE.value,
) -> models.UserStrategyInstance:
    instance = models.UserStrategyInstance(
        user_id=user_id,
        strategy_id=catalog.id,
        strategy_version_id=version.id,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        status=status,
        execution_mode=execution_mode,
        lots=1,
        config_json={"created_by": "manual_v2_paper_scenario_matrix"},
    )
    db.add(instance)
    db.flush()
    return instance


def _pause_all_instances(db: Any) -> None:
    rows = db.scalars(
        select(models.UserStrategyInstance).where(models.UserStrategyInstance.status == StrategyInstanceStatus.ACTIVE.value)
    ).all()
    for row in rows:
        row.status = StrategyInstanceStatus.PAUSED.value
    db.flush()
    for instance_id in list(state_store.list_open_positions_by_instance()):
        state_store.clear_open_position(instance_id=instance_id)


def _job_count(db: Any, *, execution_mode: str | None = None, status: str | None = None) -> int:
    statement = select(func.count()).select_from(models.StrategyExecutionJob)
    if execution_mode is not None:
        statement = statement.where(models.StrategyExecutionJob.execution_mode == execution_mode)
    if status is not None:
        statement = statement.where(models.StrategyExecutionJob.status == status)
    return int(db.scalar(statement) or 0)


def _paper_order_log_count() -> int:
    path = state_store.LOG_FILES["paper_orders"]
    if not path.exists():
        return 0
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def _set_legacy_position(label: str) -> dict[str, Any]:
    position = {
        **state_store.default_open_position(),
        "has_open_position": True,
        "strategy_code": SUPERTREND_CODE,
        "symbol": "NIFTY",
        "trading_symbol": label,
        "option_side": "CE",
        "qty": 65,
        "entry_order_id": f"LEGACY-{uuid.uuid4().hex[:8]}",
        "entry_price": 100.0,
    }
    return state_store.set_open_position(position)


class _route_guard:
    def __enter__(self):
        from app.services import strategy_worker_adapter_v2

        self._module = strategy_worker_adapter_v2
        self._original = strategy_worker_adapter_v2._route_signal

        def fail_route(_signal):
            raise ScenarioMatrixError("route_signal was called unexpectedly.")

        strategy_worker_adapter_v2._route_signal = fail_route
        return self

    def __exit__(self, exc_type, exc, tb):
        self._module._route_signal = self._original
        return False


def _base_payload(
    *,
    signal_id: str,
    action: str,
    intent: str,
    option_side: str,
    strike: float,
    expiry: str,
    lots: int,
) -> dict[str, Any]:
    payload = {
        "version": "nova.v1",
        "secret": "manual-v2-paper-scenario-secret",
        "signal_id": signal_id,
        "strategy_code": SUPERTREND_CODE,
        "action": action,
        "intent": intent,
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "option_side": option_side,
        "strike_mode": "MANUAL",
        "strike": strike,
        "expiry_mode": "MANUAL",
        "expiry": expiry,
        "qty_mode": "LOTS",
        "lots": lots,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
        "source": "backend",
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": {"source": "manual_v2_paper_scenario_matrix"},
    }
    NovaSignalV1.model_validate(payload)
    return payload


def _family(name: str) -> str:
    return f"phase2e5-{name}-{uuid.uuid4().hex[:8]}"


def _summary(context: ScenarioContext, results: list[ScenarioResult]) -> dict[str, Any]:
    scenario_status = {result.name: "passed" if result.passed else "failed" for result in results}
    expected = [name for name, _runner in _scenario_functions()]
    overall_ok = len(results) == len(expected) and all(result.passed for result in results)
    return _sanitize(
        {
            "overall_ok": overall_ok,
            "fresh_isolated_db": context.isolated,
            "db_type": "sqlite_temp" if context.isolated else "configured_external",
            "runtime_isolated": context.isolated,
            "flags": feature_flag_states(),
            "scenarios": scenario_status,
            "results": [asdict(result) for result in results],
            "sensitive_field_count": _sensitive_field_count([asdict(result) for result in results]),
        }
    )


def _emit(out: Callable[[str], None], label: str, payload: Any) -> None:
    out(json.dumps({"event": label, "data": _sanitize(payload)}, sort_keys=True))


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioMatrixError(message)


def _safe_error(exc: Exception) -> str:
    if _sensitive_field_count({"error": str(exc)}):
        return "Scenario matrix failed."
    return str(exc)


def _sanitize(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return _sanitize(asdict(value))
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                continue
            sanitized[str(key)] = _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return value


def _sensitive_field_count(value: Any) -> int:
    if isinstance(value, dict):
        count = 0
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                count += 1
                continue
            count += _sensitive_field_count(item)
        return count
    if isinstance(value, list):
        return sum(_sensitive_field_count(item) for item in value)
    if isinstance(value, tuple):
        return sum(_sensitive_field_count(item) for item in value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
