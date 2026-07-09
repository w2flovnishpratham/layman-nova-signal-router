#!/usr/bin/env python
"""Run an isolated Phase 2I-1 paper strategy clone safety matrix.

Default mode creates a fresh temporary SQLite database and temporary runtime
state/log directories. It does not use or mutate the configured staging DB.

Run from backend/:

    python -m scripts.manual_v2_instance_clone_matrix
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

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.dependencies import get_current_user  # noqa: E402
from app.config import settings  # noqa: E402
from app.core.enums import (  # noqa: E402
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
    StrategySourceType,
)
from app.core.feature_flags import (  # noqa: E402
    MULTI_STRATEGY_FANOUT,
    MULTI_STRATEGY_MODEL,
    STRATEGY_INSTANCE_MUTATION_DEBUG,
    V2_PAPER_RUNNER_DEBUG,
    feature_flag_states,
)
from app.db import crud, models  # noqa: E402
from app.db.engine import init_db, reset_engine_for_tests, session_scope  # noqa: E402
from app.services import audit_logger, state_store  # noqa: E402
from app.services.strategy_fanout_v2 import PAUSED_INSTANCE, plan_strategy_fanout_v2  # noqa: E402
from app.services.user_context import CurrentUser  # noqa: E402
from scripts.backfill_multistrategy import run_backfill  # noqa: E402


SUPERTREND_CODE = "SUPERTREND_V1"
SUPERTREND_VERSION = "v1"
MATRIX_SOURCE = "manual_v2_instance_clone_matrix"
CLONE_CAP = 10

SENSITIVE_KEYS = {
    "access_token",
    "client_id",
    "config",
    "config_json",
    "credential",
    "credential_hash",
    "credentials_hash",
    "headers",
    "message_secret_hash",
    "password",
    "payload",
    "pin",
    "proxy_url",
    "raw_payload",
    "raw_response",
    "request",
    "response",
    "response_json",
    "response_text",
    "risk_config",
    "secret",
    "signal_payload",
    "token",
    "webhook_key_hash",
}
SENSITIVE_KEY_FRAGMENTS = ("access_token", "headers", "proxy_url", "secret", "token")


class CloneMatrixError(RuntimeError):
    """Raised when the clone matrix cannot run safely or a case fails."""


@dataclass(frozen=True)
class CloneMatrixConfig:
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
class MatrixContext:
    root: Path
    database_url: str
    runtime_state_dir: Path
    runtime_log_dir: Path
    isolated: bool
    guard: "ExecutionGuard"
    audit_events: list[dict[str, Any]]


def run_clone_matrix(
    config: CloneMatrixConfig,
    *,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run all clone scenarios and return a sanitized summary."""
    _install_safe_process_flags()
    context = _prepare_environment(config)
    results: list[ScenarioResult] = []

    with ExecutionGuard() as guard, AuditCapture() as audit_events:
        context.guard = guard
        context.audit_events = audit_events
        for name, runner in _scenario_functions():
            try:
                details = runner(context)
                results.append(ScenarioResult(name=name, passed=True, details=details))
            except Exception as exc:
                results.append(ScenarioResult(name=name, passed=False, error=_safe_error(exc)))
                break

    summary = _summary(context, results)
    _emit(out, "clone_matrix_summary", summary)
    if not summary["overall_ok"]:
        raise CloneMatrixError("One or more clone matrix scenarios failed.")
    if context.isolated and not config.keep_temp:
        shutil.rmtree(context.root, ignore_errors=True)
    return _sanitize(summary)


def parse_args(argv: list[str] | None = None) -> CloneMatrixConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-external-db",
        action="store_true",
        help="Use the configured DATABASE_URL instead of a fresh temporary SQLite DB.",
    )
    parser.add_argument("--work-dir", type=Path, help="Optional temp root for isolated DB/runtime.")
    parser.add_argument("--keep-temp", action="store_true", help="Do not delete the isolated temp root after success.")
    args = parser.parse_args(argv)
    return CloneMatrixConfig(
        allow_external_db=bool(args.allow_external_db),
        work_dir=args.work_dir,
        keep_temp=bool(args.keep_temp),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        run_clone_matrix(parse_args(argv))
    except CloneMatrixError as exc:
        print(json.dumps({"ok": False, "error": _safe_error(exc)}, sort_keys=True))
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "error": _safe_error(exc)}, sort_keys=True))
        return 1
    return 0


def _install_safe_process_flags() -> None:
    settings.DEBUG_ENABLED = True
    settings.ENABLE_LIVE_ORDERS = False
    settings.DHAN_MODE = "MOCK"
    os.environ["ENABLE_LIVE_ORDERS"] = "false"
    os.environ[MULTI_STRATEGY_MODEL] = "true"
    os.environ[STRATEGY_INSTANCE_MUTATION_DEBUG] = "true"
    os.environ[MULTI_STRATEGY_FANOUT] = "true"
    os.environ.setdefault(V2_PAPER_RUNNER_DEBUG, "false")


def _prepare_environment(config: CloneMatrixConfig) -> MatrixContext:
    if config.allow_external_db:
        configured = str(settings.DATABASE_URL or "").strip()
        if not configured:
            raise CloneMatrixError("DATABASE_URL must be configured when --allow-external-db is used.")
        root = config.work_dir or Path.cwd()
        context = MatrixContext(
            root=root,
            database_url=configured,
            runtime_state_dir=root / "phase2i1-runtime-state",
            runtime_log_dir=root / "phase2i1-runtime-logs",
            isolated=False,
            guard=ExecutionGuard(),
            audit_events=[],
        )
    else:
        root = config.work_dir or Path(tempfile.mkdtemp(prefix="phase2i1-clone-matrix-"))
        root.mkdir(parents=True, exist_ok=True)
        db_path = root / "clone_matrix.sqlite3"
        database_url = f"sqlite:///{db_path.as_posix()}"
        settings.DATABASE_URL = database_url
        os.environ["DATABASE_URL"] = database_url
        context = MatrixContext(
            root=root,
            database_url=database_url,
            runtime_state_dir=root / "runtime_state",
            runtime_log_dir=root / "runtime_logs",
            isolated=True,
            guard=ExecutionGuard(),
            audit_events=[],
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


def _install_runtime_paths(context: MatrixContext) -> None:
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


def _scenario_functions() -> tuple[tuple[str, Callable[[MatrixContext], dict[str, Any]]], ...]:
    return (
        ("clone_paused_instance", _scenario_clone_paused_instance),
        ("clone_active_instance", _scenario_clone_active_instance),
        ("clone_archived_instance", _scenario_clone_archived_instance),
        ("clone_ineligible_blocked", _scenario_clone_ineligible_blocked),
        ("cross_user_isolation", _scenario_cross_user_isolation),
        ("label_numbering", _scenario_label_numbering),
        ("cap_enforced", _scenario_cap_enforced),
        ("planner_skips_until_resumed", _scenario_planner_skips_until_resumed),
        ("frontend_static_safety", _scenario_frontend_static_safety),
    )


def _scenario_clone_paused_instance(context: MatrixContext) -> dict[str, Any]:
    return _clone_from_status(context, "phase2i1-paused", StrategyInstanceStatus.PAUSED.value)


def _scenario_clone_active_instance(context: MatrixContext) -> dict[str, Any]:
    return _clone_from_status(context, "phase2i1-active", StrategyInstanceStatus.ACTIVE.value)


def _scenario_clone_archived_instance(context: MatrixContext) -> dict[str, Any]:
    return _clone_from_status(context, "phase2i1-archived", StrategyInstanceStatus.ARCHIVED.value)


def _clone_from_status(context: MatrixContext, email_prefix: str, source_status: str) -> dict[str, Any]:
    user = _ensure_current_user(f"{email_prefix}@example.invalid")
    source_id = _seed_instance(user, status=source_status, instance_label="Clone Source", lots=2, side_preference="CE")
    before_counts = _db_counts()
    before_audits = _clone_audit_count(context)

    response = _clone(user, source_id)
    _assert(response.status_code == 200, f"clone from {source_status} returned HTTP {response.status_code}")
    body = response.json()
    clone = body["instance"]
    _assert(clone["status"] == StrategyInstanceStatus.PAUSED.value, "clone was not born paused.")
    _assert(clone["execution_mode"] == StrategyExecutionMode.PAPER_LIVE_DATA.value, "clone was not paper_live_data.")
    _assert(clone["lots"] == 2, "clone did not copy lots.")
    _assert(clone["side_preference"] == "CE", "clone did not copy side_preference.")
    _assert(clone["instance_label"] == "Clone Source (Copy)", "clone label was not numbered.")
    _assert(clone["id"] != source_id, "clone reused the source id.")

    after_counts = _db_counts()
    _assert(_side_effect_counts(after_counts, before_counts) == {}, "clone wrote side-effect tables.")
    _assert(after_counts["user_strategy_instances"] == before_counts["user_strategy_instances"] + 1, "clone row count wrong.")
    _assert(_clone_audit_count(context) == before_audits + 1, "clone did not emit exactly one audit event.")

    with session_scope() as db:
        source_row = db.get(models.UserStrategyInstance, uuid.UUID(source_id))
        _assert(source_row.status == source_status, "cloning mutated the source instance status.")

    return {
        "source_status": source_status,
        "clone_id": clone["id"],
        "clone_status": clone["status"],
        "clone_execution_mode": clone["execution_mode"],
        "clone_label": clone["instance_label"],
        "side_effect_counts": _side_effect_counts(after_counts, before_counts),
    }


def _scenario_clone_ineligible_blocked(_context: MatrixContext) -> dict[str, Any]:
    user = _ensure_current_user("phase2i1-blocked@example.invalid")
    real_orders_id = _seed_real_orders_instance(user)
    disabled_id = _seed_instance(user, status=StrategyInstanceStatus.DISABLED.value, instance_label="Disabled Source")
    error_id = _seed_instance(user, status=StrategyInstanceStatus.ERROR.value, instance_label="Error Source")
    before_counts = _db_counts()

    real_response = _clone(user, real_orders_id)
    disabled_response = _clone(user, disabled_id)
    error_response = _clone(user, error_id)
    after_counts = _db_counts()

    _assert(real_response.status_code == 403, "real_orders clone was not blocked.")
    _assert(disabled_response.status_code == 409, "disabled clone was not blocked.")
    _assert(error_response.status_code == 409, "error clone was not blocked.")
    _assert(after_counts == before_counts, "blocked clone attempts wrote rows.")
    return {
        "real_orders_status_code": real_response.status_code,
        "disabled_status_code": disabled_response.status_code,
        "error_status_code": error_response.status_code,
        "db_unchanged": after_counts == before_counts,
    }


def _scenario_cross_user_isolation(_context: MatrixContext) -> dict[str, Any]:
    owner = _ensure_current_user("phase2i1-owner@example.invalid")
    intruder = _ensure_current_user("phase2i1-intruder@example.invalid")
    source_id = _seed_instance(owner, instance_label="Owner Source")
    before_counts = _db_counts()

    intruder_response = _clone(intruder, source_id)
    invalid_response = _clone(intruder, "not-a-uuid")
    after_counts = _db_counts()

    _assert(intruder_response.status_code == 404, "cross-user clone leaked instance existence.")
    _assert(invalid_response.status_code == 404, "invalid id did not return hidden 404.")
    _assert(after_counts == before_counts, "cross-user clone attempt wrote rows.")
    return {
        "intruder_status_code": intruder_response.status_code,
        "invalid_status_code": invalid_response.status_code,
        "db_unchanged": after_counts == before_counts,
    }


def _scenario_label_numbering(_context: MatrixContext) -> dict[str, Any]:
    user = _ensure_current_user("phase2i1-numbering@example.invalid")
    source_id = _seed_instance(user, instance_label="Momentum Strategy")

    labels = [_clone(user, source_id).json()["instance"]["instance_label"] for _ in range(3)]
    _assert(labels == ["Momentum Strategy (Copy)", "Momentum Strategy (Copy 2)", "Momentum Strategy (Copy 3)"], f"label numbering wrong: {labels}")
    return {"labels": labels}


def _scenario_cap_enforced(_context: MatrixContext) -> dict[str, Any]:
    user = _ensure_current_user("phase2i1-cap@example.invalid")
    source_id = _seed_instance(user, instance_label="Cap Source 0")
    for i in range(1, CLONE_CAP):
        _seed_instance(user, instance_label=f"Cap Source {i}")
    before_counts = _db_counts()

    response = _clone(user, source_id)
    after_counts = _db_counts()

    _assert(response.status_code == 409, "clone cap was not enforced.")
    _assert(after_counts == before_counts, "capped clone attempt wrote a row.")
    return {
        "cap": CLONE_CAP,
        "status_code": response.status_code,
        "db_unchanged": after_counts == before_counts,
    }


def _scenario_planner_skips_until_resumed(_context: MatrixContext) -> dict[str, Any]:
    # Earlier scenarios (e.g. clone_active_instance) leave ACTIVE instances
    # behind for other users on the same strategy code; pause them all first
    # so this scenario's planner calls only ever see the paused clone.
    _pause_all_instances()
    user = _ensure_current_user("phase2i1-planner@example.invalid")
    source_id = _seed_instance(user, status=StrategyInstanceStatus.PAUSED.value, instance_label="Planner Source")
    clone_id = _clone(user, source_id).json()["instance"]["id"]

    before_counts = _db_counts()
    with session_scope() as db:
        paused_plan = plan_strategy_fanout_v2(
            db, _nova_v1_payload("phase2i1-clone-paused"), SUPERTREND_CODE, dry_run=False
        )
    paused_skip = [
        skip for skip in paused_plan.skips if str(skip.instance_id) == clone_id and skip.reason == PAUSED_INSTANCE
    ]
    _assert(bool(paused_skip), "planner did not skip the paused clone.")
    after_counts = _db_counts()
    _assert(_side_effect_counts(after_counts, before_counts) == {}, "planner wrote side-effect tables for a paused clone.")

    resume = _debug_client(user).post(f"/api/debug/v2/instances/{clone_id}/resume", json={"confirm_paper_only": True})
    _assert(resume.status_code == 200, f"resume returned HTTP {resume.status_code}")
    with session_scope() as db:
        active_plan = plan_strategy_fanout_v2(db, _nova_v1_payload("phase2i1-clone-active"), SUPERTREND_CODE)
    clone_planned = [plan for plan in active_plan.plans if str(plan.instance_id) == clone_id]
    _assert(bool(clone_planned), "resumed clone was not planned.")

    return {
        "clone_id": clone_id,
        "paused_skip_reason": paused_skip[0].reason,
        "resumed_planned_count": len(clone_planned),
        "side_effect_counts_while_paused": _side_effect_counts(after_counts, before_counts),
    }


def _scenario_frontend_static_safety(_context: MatrixContext) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    catalog_panel = (repo_root / "frontend" / "src" / "debug" / "StrategyCatalogStatusPanel.tsx").read_text(
        encoding="utf-8"
    )
    paper_panel = (repo_root / "frontend" / "src" / "debug" / "V2PaperStatusPanel.tsx").read_text(encoding="utf-8")
    api_text = (repo_root / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")

    _assert("clonePaperStrategyInstance" in catalog_panel, "clone helper missing from catalog panel.")
    _assert('label="Clone"' in catalog_panel, "Clone control missing from catalog panel.")
    _assert("clonePaperStrategyInstance" not in paper_panel, "clone helper leaked into v2 paper panel.")
    _assert(
        "/api/debug/v2/instances/${encodeURIComponent(instanceId)}/clone" in api_text,
        "clone endpoint missing from api helpers.",
    )
    for forbidden in ("run-once", "process-ready", "paper-fanout", "runv2", "processready", "generate webhook", "go live"):
        _assert(forbidden not in catalog_panel.lower(), f"forbidden clone UI term found: {forbidden}")
    return {
        "clone_helper_present": True,
        "clone_control_present": True,
        "v2_paper_panel_read_only": True,
    }


def _debug_client(user: CurrentUser) -> TestClient:
    from app.routers import debug as debug_router

    app = FastAPI()
    app.include_router(debug_router.router, prefix="/api/debug")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=True)


def _ensure_current_user(email: str, *, is_admin: bool = True) -> CurrentUser:
    with session_scope() as db:
        user = crud.upsert_google_user(
            db,
            google_sub=f"phase2i1-{email}",
            email=email,
            name=email.split("@", 1)[0],
            picture_url=None,
            is_admin=is_admin,
        )
        return CurrentUser(
            id=user.id,
            email=user.email,
            name=user.name,
            picture_url=None,
            is_admin=is_admin,
            is_dev=False,
        )


def _seed_instance(
    user: CurrentUser,
    *,
    status: str = StrategyInstanceStatus.PAUSED.value,
    instance_label: str = "Clone Source",
    lots: int = 1,
    side_preference: str = "BOTH",
) -> str:
    with session_scope() as db:
        catalog = db.scalar(select(models.StrategyCatalog).where(models.StrategyCatalog.code == SUPERTREND_CODE))
        _assert(catalog is not None, "Supertrend catalog missing.")
        version = db.scalar(
            select(models.StrategyVersion).where(
                models.StrategyVersion.strategy_id == catalog.id,
                models.StrategyVersion.version == SUPERTREND_VERSION,
            )
        )
        _assert(version is not None, "Supertrend version missing.")
        row = models.UserStrategyInstance(
            user_id=user.id,
            strategy_id=catalog.id,
            strategy_version_id=version.id,
            instance_label=instance_label,
            source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
            status=status,
            execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
            lots=lots,
            side_preference=side_preference,
            config_json={"created_by": MATRIX_SOURCE},
        )
        db.add(row)
        db.flush()
        return str(row.id)


def _pause_all_instances() -> None:
    with session_scope() as db:
        rows = db.scalars(
            select(models.UserStrategyInstance).where(
                models.UserStrategyInstance.status == StrategyInstanceStatus.ACTIVE.value
            )
        ).all()
        for row in rows:
            row.status = StrategyInstanceStatus.PAUSED.value
            row.updated_at = models.utcnow()
        db.flush()


def _seed_real_orders_instance(user: CurrentUser) -> str:
    with session_scope() as db:
        catalog = db.scalar(select(models.StrategyCatalog).where(models.StrategyCatalog.code == SUPERTREND_CODE))
        version = db.scalar(
            select(models.StrategyVersion).where(
                models.StrategyVersion.strategy_id == catalog.id,
                models.StrategyVersion.version == SUPERTREND_VERSION,
            )
        )
        row = models.UserStrategyInstance(
            user_id=user.id,
            strategy_id=catalog.id,
            strategy_version_id=version.id,
            instance_label="Phase 2I-1 real_orders protected",
            source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
            status=StrategyInstanceStatus.PAUSED.value,
            execution_mode=StrategyExecutionMode.REAL_ORDERS.value,
            lots=1,
            side_preference="BOTH",
            config_json={"created_by": MATRIX_SOURCE},
        )
        db.add(row)
        db.flush()
        return str(row.id)


def _clone(user: CurrentUser, instance_id: str):
    return _debug_client(user).post(
        f"/api/debug/v2/instances/{instance_id}/clone", json={"confirm_paper_only": True}
    )


def _nova_v1_payload(signal_id: str, *, intent: str = "BULLISH", lots: int = 1) -> dict[str, Any]:
    return {
        "version": "nova.v1",
        "secret": "phase2i1-clone-matrix-secret",
        "signal_id": f"{signal_id}-{uuid.uuid4().hex[:8]}",
        "strategy_code": SUPERTREND_CODE,
        "action": "ENTRY",
        "intent": intent,
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "option_side": "AUTO",
        "strike_mode": "MANUAL",
        "strike": 25000,
        "expiry_mode": "MANUAL",
        "expiry": "2026-07-09",
        "qty_mode": "LOTS",
        "lots": lots,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
        "source": "backend",
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": {"source": MATRIX_SOURCE},
    }


def _db_counts() -> dict[str, int]:
    models_by_name = {
        "user_strategy_instances": models.UserStrategyInstance,
        "strategy_signals": models.StrategySignal,
        "normalized_option_signals": models.NormalizedOptionSignal,
        "strategy_execution_jobs": models.StrategyExecutionJob,
        "user_strategy_webhook_credentials": models.UserStrategyWebhookCredential,
    }
    with session_scope() as db:
        return {
            name: int(db.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in models_by_name.items()
        }


def _side_effect_counts(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    side_effect_tables = (
        "strategy_signals",
        "normalized_option_signals",
        "strategy_execution_jobs",
        "user_strategy_webhook_credentials",
    )
    return {
        table: after[table] - before[table]
        for table in side_effect_tables
        if after[table] != before[table]
    }


def _clone_audit_count(context: MatrixContext) -> int:
    return sum(1 for event in context.audit_events if event["event_type"] == "V2_INSTANCE_CLONED")


class AuditCapture:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._target: Any | None = None
        self._original: Any | None = None

    def __enter__(self) -> list[dict[str, Any]]:
        from app.routers import debug as debug_router

        self._target = debug_router
        self._original = debug_router.log_audit_event

        def capture(event_type, message, *, severity="INFO", metadata=None):
            self.events.append(
                {
                    "event_type": event_type,
                    "message": message,
                    "severity": severity,
                    "metadata": metadata or {},
                }
            )
            return {}

        debug_router.log_audit_event = capture
        return self.events

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._target is not None and self._original is not None:
            self._target.log_audit_event = self._original
        return False


class ExecutionGuard:
    def __init__(self) -> None:
        self.calls = {
            "route_signal": 0,
            "worker_route_signal": 0,
            "dhan": 0,
            "paper_broker": 0,
        }
        self._originals: list[tuple[Any, str, Any]] = []

    def __enter__(self) -> "ExecutionGuard":
        from app.services import dhan_client, execution_router, paper_broker, strategy_worker_adapter_v2

        def fail_route(*_args: Any, **_kwargs: Any) -> Any:
            self.calls["route_signal"] += 1
            raise CloneMatrixError("route_signal was called unexpectedly.")

        def fail_worker_route(*_args: Any, **_kwargs: Any) -> Any:
            self.calls["worker_route_signal"] += 1
            raise CloneMatrixError("worker route_signal was called unexpectedly.")

        def fail_dhan(*_args: Any, **_kwargs: Any) -> Any:
            self.calls["dhan"] += 1
            raise CloneMatrixError("Dhan client was called unexpectedly.")

        def fail_paper(*_args: Any, **_kwargs: Any) -> Any:
            self.calls["paper_broker"] += 1
            raise CloneMatrixError("PaperBroker was called unexpectedly.")

        self._patch(execution_router, "route_signal", fail_route)
        self._patch(strategy_worker_adapter_v2, "_route_signal", fail_worker_route)
        self._patch(dhan_client.RealDhanClient, "place_order", fail_dhan)
        self._patch(dhan_client.RealDhanClient, "place_super_order", fail_dhan)
        self._patch(paper_broker.PaperBroker, "place_order", fail_paper)
        self._patch(paper_broker.PaperBroker, "place_super_order", fail_paper)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        for target, name, original in reversed(self._originals):
            setattr(target, name, original)
        self._originals.clear()
        return False

    def _patch(self, target: Any, name: str, replacement: Any) -> None:
        self._originals.append((target, name, getattr(target, name)))
        setattr(target, name, replacement)


def _summary(context: MatrixContext, results: list[ScenarioResult]) -> dict[str, Any]:
    scenario_status = {result.name: "passed" if result.passed else "failed" for result in results}
    expected = [name for name, _runner in _scenario_functions()]
    overall_ok = len(results) == len(expected) and all(result.passed for result in results)
    data = {
        "overall_ok": overall_ok,
        "fresh_isolated_db": context.isolated,
        "db_type": "sqlite_temp" if context.isolated else "configured_external",
        "runtime_isolated": context.isolated,
        "flags": feature_flag_states(),
        "settings": {
            "debug_enabled": bool(settings.DEBUG_ENABLED),
            "enable_live_orders": bool(settings.ENABLE_LIVE_ORDERS),
            "dhan_mode": str(settings.DHAN_MODE).upper(),
        },
        "execution_guard_calls": dict(context.guard.calls),
        "scenarios": scenario_status,
        "results": [asdict(result) for result in results],
    }
    for name in expected:
        data[name] = scenario_status.get(name, "not_run")
    data["sensitive_field_count"] = _sensitive_field_count(data)
    return _sanitize(data)


def _emit(out: Callable[[str], None], label: str, payload: Any) -> None:
    out(json.dumps({"event": label, "data": _sanitize(payload)}, sort_keys=True))


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise CloneMatrixError(message)


def _safe_error(exc: Exception) -> str:
    if _sensitive_field_count({"error": str(exc)}):
        return "Clone matrix failed."
    return str(exc)


def _sanitize(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return _sanitize(asdict(value))
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
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
            if _is_sensitive_key(key):
                count += 1
                continue
            count += _sensitive_field_count(item)
        return count
    if isinstance(value, list):
        return sum(_sensitive_field_count(item) for item in value)
    if isinstance(value, tuple):
        return sum(_sensitive_field_count(item) for item in value)
    return 0


def _is_sensitive_key(key: Any) -> bool:
    lowered = str(key).lower()
    return lowered in SENSITIVE_KEYS or any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


if __name__ == "__main__":
    raise SystemExit(main())
