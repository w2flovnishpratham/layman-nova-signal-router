#!/usr/bin/env python
"""Run an isolated Phase 2F-2 paper instance lifecycle safety matrix.

Default mode creates a fresh temporary SQLite database and temporary runtime
state/log directories. It does not use or mutate the configured staging DB.

Run from backend/:

    python -m scripts.manual_v2_instance_lifecycle_matrix
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
from app.services.strategy_fanout_v2 import (  # noqa: E402
    PAUSED_INSTANCE,
    REAL_ORDERS_NOT_SUPPORTED_YET,
    plan_strategy_fanout_v2,
)
from app.services.user_context import CurrentUser  # noqa: E402
from scripts.backfill_multistrategy import run_backfill  # noqa: E402


SUPERTREND_CODE = "SUPERTREND_V1"
SUPERTREND_VERSION = "v1"
MATRIX_SOURCE = "manual_v2_instance_lifecycle_matrix"

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

POSITION_STATE_FILE_ATTRS = (
    "OPEN_POSITION_FILE",
    "PAPER_POSITION_FILE",
    "OPEN_POSITIONS_BY_INSTANCE_FILE",
)


class LifecycleMatrixError(RuntimeError):
    """Raised when the lifecycle matrix cannot run safely or a case fails."""


@dataclass(frozen=True)
class LifecycleMatrixConfig:
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


def run_lifecycle_matrix(
    config: LifecycleMatrixConfig,
    *,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run all lifecycle-only scenarios and return a sanitized summary."""
    _install_safe_process_flags()
    context = _prepare_environment(config)
    results: list[ScenarioResult] = []

    with ExecutionGuard() as guard:
        context.guard = guard
        for name, runner in _scenario_functions():
            try:
                details = runner(context)
                results.append(ScenarioResult(name=name, passed=True, details=details))
            except Exception as exc:
                results.append(ScenarioResult(name=name, passed=False, error=_safe_error(exc)))
                break

    summary = _summary(context, results)
    _emit(out, "lifecycle_matrix_summary", summary)
    if not summary["overall_ok"]:
        raise LifecycleMatrixError("One or more lifecycle matrix scenarios failed.")
    if context.isolated and not config.keep_temp:
        shutil.rmtree(context.root, ignore_errors=True)
    return _sanitize(summary)


def parse_args(argv: list[str] | None = None) -> LifecycleMatrixConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-external-db",
        action="store_true",
        help="Use the configured DATABASE_URL instead of a fresh temporary SQLite DB.",
    )
    parser.add_argument("--work-dir", type=Path, help="Optional temp root for isolated DB/runtime.")
    parser.add_argument("--keep-temp", action="store_true", help="Do not delete the isolated temp root after success.")
    args = parser.parse_args(argv)
    return LifecycleMatrixConfig(
        allow_external_db=bool(args.allow_external_db),
        work_dir=args.work_dir,
        keep_temp=bool(args.keep_temp),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        run_lifecycle_matrix(parse_args(argv))
    except LifecycleMatrixError as exc:
        print(json.dumps({"ok": False, "error": _safe_error(exc)}, sort_keys=True))
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "error": _safe_error(exc)}, sort_keys=True))
        return 1
    return 0


def _install_safe_process_flags() -> None:
    """Configure this isolated matrix process for debug lifecycle checks only."""
    settings.DEBUG_ENABLED = True
    settings.ENABLE_LIVE_ORDERS = False
    settings.DHAN_MODE = "MOCK"
    os.environ["ENABLE_LIVE_ORDERS"] = "false"
    os.environ[MULTI_STRATEGY_MODEL] = "true"
    os.environ[STRATEGY_INSTANCE_MUTATION_DEBUG] = "true"
    os.environ[MULTI_STRATEGY_FANOUT] = "true"
    os.environ.setdefault(V2_PAPER_RUNNER_DEBUG, "false")


def _prepare_environment(config: LifecycleMatrixConfig) -> MatrixContext:
    if config.allow_external_db:
        configured = str(settings.DATABASE_URL or "").strip()
        if not configured:
            raise LifecycleMatrixError("DATABASE_URL must be configured when --allow-external-db is used.")
        context = MatrixContext(
            root=config.work_dir or Path.cwd(),
            database_url=configured,
            runtime_state_dir=(config.work_dir or Path.cwd()) / "phase2f2-runtime-state",
            runtime_log_dir=(config.work_dir or Path.cwd()) / "phase2f2-runtime-logs",
            isolated=False,
            guard=ExecutionGuard(),
        )
    else:
        root = config.work_dir or Path(tempfile.mkdtemp(prefix="phase2f2-lifecycle-matrix-"))
        root.mkdir(parents=True, exist_ok=True)
        db_path = root / "lifecycle_matrix.sqlite3"
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
        ("create_born_paused", _scenario_create_born_paused),
        ("paused_skipped_by_fanout", _scenario_paused_skipped_by_fanout),
        ("resume_planning_eligible", _scenario_resume_planning_eligible),
        ("pause_skips_again", _scenario_pause_skips_again),
        ("real_orders_protected", _scenario_real_orders_protected),
        ("cross_user_isolation", _scenario_cross_user_isolation),
        ("smuggled_fields_rejected", _scenario_smuggled_fields_rejected),
        ("frontend_static_safety", _scenario_frontend_static_safety),
    )


def _scenario_create_born_paused(_context: MatrixContext) -> dict[str, Any]:
    user = _ensure_current_user("phase2f2-create@example.invalid")
    client = _debug_client(user)
    before_counts = _db_counts()
    before_positions = _position_file_snapshot()

    response = client.post("/api/debug/v2/instances", json=_create_body(instance_label="Phase 2F-2 Born Paused"))
    _assert(response.status_code == 200, f"create returned HTTP {response.status_code}")
    body = response.json()
    instance = body.get("instance") or {}
    instance_id = str(instance.get("id"))
    _assert(instance.get("execution_mode") == StrategyExecutionMode.PAPER_LIVE_DATA.value, "created mode was not paper.")
    _assert(instance.get("status") == StrategyInstanceStatus.PAUSED.value, "created instance was not born paused.")

    with session_scope() as db:
        row = db.get(models.UserStrategyInstance, uuid.UUID(instance_id))
        _assert(row is not None, "created instance row not found.")
        _assert(row.user_id == user.id, "created instance owner was not the authenticated user.")
        _assert(row.execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value, "server did not hardcode paper mode.")
        _assert(row.status == StrategyInstanceStatus.PAUSED.value, "server did not hardcode paused status.")

    after_counts = _db_counts()
    after_positions = _position_file_snapshot()
    _assert(after_counts["strategy_signals"] == before_counts["strategy_signals"], "create wrote strategy_signals.")
    _assert(after_counts["normalized_option_signals"] == before_counts["normalized_option_signals"], "create wrote normalized signals.")
    _assert(after_counts["strategy_execution_jobs"] == before_counts["strategy_execution_jobs"], "create wrote execution jobs.")
    _assert(after_counts["user_strategy_webhook_credentials"] == before_counts["user_strategy_webhook_credentials"], "create wrote webhook credentials.")
    _assert(after_positions == before_positions, "create changed runtime position state files.")

    return {
        "instance_id": instance_id,
        "execution_mode": instance.get("execution_mode"),
        "status": instance.get("status"),
        "signals_created": after_counts["strategy_signals"] - before_counts["strategy_signals"],
        "jobs_created": after_counts["strategy_execution_jobs"] - before_counts["strategy_execution_jobs"],
        "webhook_credentials_created": (
            after_counts["user_strategy_webhook_credentials"] - before_counts["user_strategy_webhook_credentials"]
        ),
        "runtime_position_files_unchanged": after_positions == before_positions,
    }


def _scenario_paused_skipped_by_fanout(_context: MatrixContext) -> dict[str, Any]:
    _pause_all_instances()
    user = _ensure_current_user("phase2f2-paused@example.invalid")
    created = _create_instance(user, instance_label="Phase 2F-2 Paused Skip")
    instance_id = created["id"]
    before_jobs = _db_counts()["strategy_execution_jobs"]

    with session_scope() as db:
        plan = plan_strategy_fanout_v2(db, _nova_v1_payload("phase2f2-paused"), SUPERTREND_CODE)
    paused_skip = [
        skip
        for skip in plan.skips
        if str(skip.instance_id) == instance_id and skip.reason == PAUSED_INSTANCE
    ]
    after_jobs = _db_counts()["strategy_execution_jobs"]

    _assert(plan.planned_count == 0, "paused instance was planned.")
    _assert(bool(paused_skip), "paused instance skip was not reported.")
    _assert(after_jobs == before_jobs, "paused planner created a job.")
    return {
        "instance_id": instance_id,
        "planned_count": plan.planned_count,
        "skipped_count": plan.skipped_count,
        "skip_reason": paused_skip[0].reason,
        "v2_pending_jobs_created": after_jobs - before_jobs,
        "route_signal_calls": _context.guard.calls["route_signal"],
    }


def _scenario_resume_planning_eligible(_context: MatrixContext) -> dict[str, Any]:
    _pause_all_instances()
    user = _ensure_current_user("phase2f2-resume@example.invalid")
    created = _create_instance(user, instance_label="Phase 2F-2 Resume Eligible")
    instance_id = created["id"]
    client = _debug_client(user)

    response = client.post(f"/api/debug/v2/instances/{instance_id}/resume", json={"confirm_paper_only": True})
    _assert(response.status_code == 200, f"resume returned HTTP {response.status_code}")
    before_counts = _db_counts()
    with session_scope() as db:
        plan = plan_strategy_fanout_v2(db, _nova_v1_payload("phase2f2-resume"), SUPERTREND_CODE)
    after_counts = _db_counts()

    planned = [plan_row for plan_row in plan.plans if str(plan_row.instance_id) == instance_id]
    _assert(plan.planned_count == 1 and planned, "resumed instance was not planned exactly once.")
    _assert(planned[0].execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value, "resumed plan was not paper.")
    _assert(after_counts["strategy_execution_jobs"] == before_counts["strategy_execution_jobs"], "dry-run planner created a job.")
    _assert(after_counts["strategy_signals"] == before_counts["strategy_signals"], "dry-run planner created a strategy signal.")

    return {
        "instance_id": instance_id,
        "resume_status": response.json()["instance"]["status"],
        "planned_count": plan.planned_count,
        "execution_mode": planned[0].execution_mode,
        "job_creation_invoked": False,
        "v2_pending_jobs_created": 0,
        "route_signal_calls": _context.guard.calls["route_signal"],
    }


def _scenario_pause_skips_again(_context: MatrixContext) -> dict[str, Any]:
    _pause_all_instances()
    user = _ensure_current_user("phase2f2-pause-again@example.invalid")
    created = _create_instance(user, instance_label="Phase 2F-2 Pause Again")
    instance_id = created["id"]
    client = _debug_client(user)

    resumed = client.post(f"/api/debug/v2/instances/{instance_id}/resume", json={"confirm_paper_only": True})
    _assert(resumed.status_code == 200, f"resume returned HTTP {resumed.status_code}")
    paused = client.post(f"/api/debug/v2/instances/{instance_id}/pause", json={"confirm_paper_only": True})
    _assert(paused.status_code == 200, f"pause returned HTTP {paused.status_code}")

    before_jobs = _db_counts()["strategy_execution_jobs"]
    with session_scope() as db:
        plan = plan_strategy_fanout_v2(db, _nova_v1_payload("phase2f2-pause-again"), SUPERTREND_CODE)
    after_jobs = _db_counts()["strategy_execution_jobs"]
    paused_skip = [
        skip
        for skip in plan.skips
        if str(skip.instance_id) == instance_id and skip.reason == PAUSED_INSTANCE
    ]

    _assert(plan.planned_count == 0, "paused-after-resume instance was planned.")
    _assert(bool(paused_skip), "paused-after-resume skip was not reported.")
    _assert(after_jobs == before_jobs, "pause-again planner created a job.")
    return {
        "instance_id": instance_id,
        "final_status": paused.json()["instance"]["status"],
        "planned_count": plan.planned_count,
        "skip_reason": paused_skip[0].reason,
        "new_jobs_created": after_jobs - before_jobs,
    }


def _scenario_real_orders_protected(_context: MatrixContext) -> dict[str, Any]:
    user = _ensure_current_user("phase2f2-real@example.invalid")
    instance_id = _seed_real_orders_instance(user)
    client = _debug_client(user)
    before = _instance_status(instance_id)

    pause = client.post(f"/api/debug/v2/instances/{instance_id}/pause", json={"confirm_paper_only": True})
    resume = client.post(f"/api/debug/v2/instances/{instance_id}/resume", json={"confirm_paper_only": True})
    after = _instance_status(instance_id)
    with session_scope() as db:
        plan = plan_strategy_fanout_v2(db, _nova_v1_payload("phase2f2-real-orders"), SUPERTREND_CODE)
    real_skip = [
        skip
        for skip in plan.skips
        if str(skip.instance_id) == instance_id and skip.reason == REAL_ORDERS_NOT_SUPPORTED_YET
    ]

    _assert(pause.status_code == 403, "real_orders pause was not blocked.")
    _assert(resume.status_code == 403, "real_orders resume was not blocked.")
    _assert(before == after, "real_orders instance changed.")
    _assert(bool(real_skip), "real_orders planner skip was not reported.")
    return {
        "instance_id": instance_id,
        "pause_status_code": pause.status_code,
        "resume_status_code": resume.status_code,
        "status_unchanged": before == after,
        "planner_skip_reason": real_skip[0].reason,
    }


def _scenario_cross_user_isolation(_context: MatrixContext) -> dict[str, Any]:
    owner = _ensure_current_user("phase2f2-owner@example.invalid")
    intruder = _ensure_current_user("phase2f2-intruder@example.invalid")
    created = _create_instance(owner, instance_label="Phase 2F-2 Cross User")
    instance_id = created["id"]
    before = _instance_status(instance_id)

    intruder_client = _debug_client(intruder)
    pause = intruder_client.post(f"/api/debug/v2/instances/{instance_id}/pause", json={"confirm_paper_only": True})
    resume = intruder_client.post(f"/api/debug/v2/instances/{instance_id}/resume", json={"confirm_paper_only": True})
    after = _instance_status(instance_id)

    _assert(pause.status_code == 404, "cross-user pause leaked or mutated instance.")
    _assert(resume.status_code == 404, "cross-user resume leaked or mutated instance.")
    _assert(before == after, "cross-user attempt changed owner instance.")
    return {
        "instance_id": instance_id,
        "pause_status_code": pause.status_code,
        "resume_status_code": resume.status_code,
        "status_unchanged": before == after,
        "existence_leak_blocked": True,
    }


def _scenario_smuggled_fields_rejected(_context: MatrixContext) -> dict[str, Any]:
    user = _ensure_current_user("phase2f2-smuggle@example.invalid")
    client = _debug_client(user)
    before_instances = _db_counts()["user_strategy_instances"]
    smuggled_cases = (
        {"execution_mode": "real_orders"},
        {"mode": "live"},
        {"status": "active"},
        {"user_id": str(uuid.uuid4())},
        {"live": True},
        {"real": True},
        {"real_orders": True},
        {"webhook_secret": "matrix-hidden"},
        {"secret": "matrix-hidden"},
        {"credential": "matrix-hidden"},
    )
    status_codes: list[int] = []
    for smuggled in smuggled_cases:
        response = client.post("/api/debug/v2/instances", json=_create_body(**smuggled))
        status_codes.append(response.status_code)
        _assert(response.status_code == 422, f"smuggled field accepted: {sorted(smuggled)}")
    after_instances = _db_counts()["user_strategy_instances"]

    _assert(after_instances == before_instances, "smuggled create wrote an instance row.")
    return {
        "attempt_count": len(smuggled_cases),
        "status_codes": status_codes,
        "db_writes": after_instances - before_instances,
    }


def _scenario_frontend_static_safety(_context: MatrixContext) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    app_text = (repo_root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    catalog_panel = (repo_root / "frontend" / "src" / "debug" / "StrategyCatalogStatusPanel.tsx").read_text(
        encoding="utf-8"
    )
    paper_panel = (repo_root / "frontend" / "src" / "debug" / "V2PaperStatusPanel.tsx").read_text(encoding="utf-8")
    api_text = (repo_root / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")
    lifecycle_section = _lifecycle_api_section(api_text)

    approved_helpers = (
        "createPaperStrategyInstance",
        "pausePaperStrategyInstance",
        "resumePaperStrategyInstance",
    )
    forbidden_catalog_terms = (
        "run-once",
        "process-ready",
        "paper-fanout",
        "generate webhook",
        "rotate secret",
        "go live",
        "squareoff",
        "square off",
        "delete instance",
    )

    _assert("import.meta.env.VITE_ENABLE_STRATEGY_INSTANCE_MUTATION === 'true'" in app_text, "mutation UI flag missing.")
    _assert(
        "STRATEGY_INSTANCE_MUTATION_UI_ENABLED && canViewStrategyCatalogStatus" in app_text,
        "mutation UI is not combined with catalog visibility.",
    )
    _assert("enabled && mutationEnabled" in catalog_panel, "panel-side mutation gate missing.")
    _assert("Paper only. No orders will be placed." in catalog_panel, "paper-only confirmation copy missing.")
    for helper in approved_helpers:
        _assert(helper in catalog_panel, f"{helper} missing from catalog panel.")
        _assert(helper not in paper_panel, f"{helper} leaked into v2 paper status panel.")
    for term in forbidden_catalog_terms:
        _assert(term not in catalog_panel.lower(), f"forbidden lifecycle UI term found: {term}")
    _assert("method: 'POST'" in lifecycle_section, "lifecycle helpers are not explicit POST calls.")
    _assert("confirm_paper_only: true" in lifecycle_section, "lifecycle helpers do not force confirm_paper_only.")
    for forbidden in ("/api/debug/v2/paper-fanout/run-once", "/api/debug/v2/paper-fanout/process-ready"):
        _assert(forbidden not in lifecycle_section, f"lifecycle helpers reference {forbidden}.")
    return {
        "mutation_flag_default_off": True,
        "catalog_gate_required": True,
        "approved_helpers": list(approved_helpers),
        "runner_endpoints_referenced": False,
        "execution_controls_rendered": False,
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
            google_sub=f"phase2f2-{email}",
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


def _create_instance(user: CurrentUser, *, instance_label: str) -> dict[str, Any]:
    response = _debug_client(user).post(
        "/api/debug/v2/instances",
        json=_create_body(instance_label=instance_label),
    )
    _assert(response.status_code == 200, f"create returned HTTP {response.status_code}")
    return response.json()["instance"]


def _create_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "catalog_code": SUPERTREND_CODE,
        "instance_label": "Phase 2F-2 Paper",
        "lots": 1,
        "side_preference": "BOTH",
        "confirm_paper_only": True,
    }
    body.update(overrides)
    return body


def _seed_real_orders_instance(user: CurrentUser) -> str:
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
            instance_label="Phase 2F-2 real_orders protected",
            source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
            status=StrategyInstanceStatus.ACTIVE.value,
            execution_mode=StrategyExecutionMode.REAL_ORDERS.value,
            lots=1,
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


def _instance_status(instance_id: str) -> tuple[str, str]:
    with session_scope() as db:
        row = db.get(models.UserStrategyInstance, uuid.UUID(str(instance_id)))
        _assert(row is not None, "instance row not found.")
        return str(row.status), str(row.execution_mode)


def _nova_v1_payload(signal_id: str) -> dict[str, Any]:
    return {
        "version": "nova.v1",
        "secret": "phase2f2-lifecycle-matrix-secret",
        "signal_id": f"{signal_id}-{uuid.uuid4().hex[:8]}",
        "strategy_code": SUPERTREND_CODE,
        "action": "ENTRY",
        "intent": "BULLISH",
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "option_side": "AUTO",
        "strike_mode": "MANUAL",
        "strike": 25000,
        "expiry_mode": "MANUAL",
        "expiry": "2026-07-09",
        "qty_mode": "LOTS",
        "lots": 1,
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


def _position_file_snapshot() -> dict[str, dict[str, Any]]:
    return {
        attr: _file_signature(state_store.scoped_runtime_path(getattr(state_store, attr)))
        for attr in POSITION_STATE_FILE_ATTRS
    }


def _file_signature(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {"exists": False, "size": 0, "sha256": None}
    except OSError:
        return {"exists": False, "size": None, "sha256": "unreadable"}
    import hashlib

    return {"exists": True, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


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
            raise LifecycleMatrixError("route_signal was called unexpectedly.")

        def fail_worker_route(*_args: Any, **_kwargs: Any) -> Any:
            self.calls["worker_route_signal"] += 1
            raise LifecycleMatrixError("worker route_signal was called unexpectedly.")

        def fail_dhan(*_args: Any, **_kwargs: Any) -> Any:
            self.calls["dhan"] += 1
            raise LifecycleMatrixError("Dhan client was called unexpectedly.")

        def fail_paper(*_args: Any, **_kwargs: Any) -> Any:
            self.calls["paper_broker"] += 1
            raise LifecycleMatrixError("PaperBroker was called unexpectedly.")

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


def _lifecycle_api_section(api_text: str) -> str:
    start = api_text.find("Phase 2F-1: paper strategy instance lifecycle")
    end = api_text.find("async function apiFetch", start)
    if start == -1 or end == -1:
        return ""
    return api_text[start:end]


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
        raise LifecycleMatrixError(message)


def _safe_error(exc: Exception) -> str:
    if _sensitive_field_count({"error": str(exc)}):
        return "Lifecycle matrix failed."
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
