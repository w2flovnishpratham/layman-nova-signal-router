#!/usr/bin/env python
"""Run an isolated Phase 2G-2 settings-edit safety matrix.

Default mode creates a fresh temporary SQLite database and temporary runtime
state/log directories. It does not use or mutate the configured staging DB.

Run from backend/:

    python -m scripts.manual_v2_instance_settings_matrix
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
MATRIX_SOURCE = "manual_v2_instance_settings_matrix"

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


class SettingsMatrixError(RuntimeError):
    """Raised when the settings matrix cannot run safely or a case fails."""


@dataclass(frozen=True)
class SettingsMatrixConfig:
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


def run_settings_matrix(
    config: SettingsMatrixConfig,
    *,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run all settings-edit scenarios and return a sanitized summary."""
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
    _emit(out, "settings_matrix_summary", summary)
    if not summary["overall_ok"]:
        raise SettingsMatrixError("One or more settings matrix scenarios failed.")
    if context.isolated and not config.keep_temp:
        shutil.rmtree(context.root, ignore_errors=True)
    return _sanitize(summary)


def parse_args(argv: list[str] | None = None) -> SettingsMatrixConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-external-db",
        action="store_true",
        help="Use the configured DATABASE_URL instead of a fresh temporary SQLite DB.",
    )
    parser.add_argument("--work-dir", type=Path, help="Optional temp root for isolated DB/runtime.")
    parser.add_argument("--keep-temp", action="store_true", help="Do not delete the isolated temp root after success.")
    args = parser.parse_args(argv)
    return SettingsMatrixConfig(
        allow_external_db=bool(args.allow_external_db),
        work_dir=args.work_dir,
        keep_temp=bool(args.keep_temp),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        run_settings_matrix(parse_args(argv))
    except SettingsMatrixError as exc:
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


def _prepare_environment(config: SettingsMatrixConfig) -> MatrixContext:
    if config.allow_external_db:
        configured = str(settings.DATABASE_URL or "").strip()
        if not configured:
            raise SettingsMatrixError("DATABASE_URL must be configured when --allow-external-db is used.")
        root = config.work_dir or Path.cwd()
        context = MatrixContext(
            root=root,
            database_url=configured,
            runtime_state_dir=root / "phase2g2-runtime-state",
            runtime_log_dir=root / "phase2g2-runtime-logs",
            isolated=False,
            guard=ExecutionGuard(),
            audit_events=[],
        )
    else:
        root = config.work_dir or Path(tempfile.mkdtemp(prefix="phase2g2-settings-matrix-"))
        root.mkdir(parents=True, exist_ok=True)
        db_path = root / "settings_matrix.sqlite3"
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
        ("paused_settings_edit", _scenario_paused_settings_edit),
        ("active_edit_blocked", _scenario_active_edit_blocked),
        ("pause_then_edit", _scenario_pause_then_edit),
        ("planner_observes_after_resume", _scenario_planner_observes_after_resume),
        ("real_orders_protected", _scenario_real_orders_protected),
        ("cross_user_isolation", _scenario_cross_user_isolation),
        ("smuggled_fields_rejected", _scenario_smuggled_fields_rejected),
        ("noop_edit_safe", _scenario_noop_edit_safe),
        ("frontend_static_safety", _scenario_frontend_static_safety),
    )


def _scenario_paused_settings_edit(context: MatrixContext) -> dict[str, Any]:
    _pause_all_instances()
    user = _ensure_current_user("phase2g2-paused-edit@example.invalid")
    created = _create_instance(user, instance_label="Phase 2G-2 Initial", lots=1, side_preference="BOTH")
    instance_id = created["id"]
    before = _instance_snapshot(instance_id)
    before_counts = _db_counts()
    before_audits = _settings_audit_count(context)

    response = _settings_edit(
        user,
        instance_id,
        instance_label="Phase 2G-2 Edited",
        lots=3,
        side_preference="PE",
    )
    _assert(response.status_code == 200, f"settings edit returned HTTP {response.status_code}")
    body = response.json()
    _assert(body["changed"] is True, "paused settings edit did not report a change.")

    after = _instance_snapshot(instance_id)
    after_counts = _db_counts()
    changed_keys = _changed_snapshot_keys(before, after)
    allowed = {"instance_label", "lots", "side_preference", "updated_at", "last_mutation_action"}
    _assert(set(changed_keys).issubset(allowed), f"unexpected instance fields changed: {changed_keys}")
    _assert(after["status"] == StrategyInstanceStatus.PAUSED.value, "settings edit changed paused status.")
    _assert(after["execution_mode"] == StrategyExecutionMode.PAPER_LIVE_DATA.value, "settings edit changed mode.")
    _assert(after["instance_label"] == "Phase 2G-2 Edited", "label was not updated.")
    _assert(after["lots"] == 3, "lots were not updated.")
    _assert(after["side_preference"] == "PE", "side preference was not updated.")
    _assert(_side_effect_counts(after_counts, before_counts) == {}, "settings edit wrote side-effect tables.")
    _assert(_settings_audit_count(context) == before_audits + 1, "changed edit did not emit one audit event.")

    return {
        "instance_id": instance_id,
        "changed": body["changed"],
        "changed_keys": changed_keys,
        "status": after["status"],
        "execution_mode": after["execution_mode"],
        "side_effect_counts": _side_effect_counts(after_counts, before_counts),
    }


def _scenario_active_edit_blocked(context: MatrixContext) -> dict[str, Any]:
    _pause_all_instances()
    user = _ensure_current_user("phase2g2-active-block@example.invalid")
    created = _create_instance(user, instance_label="Phase 2G-2 Active Block", lots=1, side_preference="BOTH")
    instance_id = created["id"]
    resume = _debug_client(user).post(f"/api/debug/v2/instances/{instance_id}/resume", json={"confirm_paper_only": True})
    _assert(resume.status_code == 200, f"resume returned HTTP {resume.status_code}")

    before = _instance_snapshot(instance_id)
    before_audits = _settings_audit_count(context)
    response = _settings_edit(user, instance_id, instance_label="Should Not Apply", lots=5, side_preference="CE")
    after = _instance_snapshot(instance_id)

    _assert(response.status_code == 409, "active settings edit was not blocked.")
    _assert(response.json().get("detail") == "Pause the instance before editing.", "active edit message changed.")
    _assert(after == before, "active blocked edit wrote instance state.")
    _assert(_settings_audit_count(context) == before_audits, "blocked active edit emitted settings audit.")
    return {
        "instance_id": instance_id,
        "status_code": response.status_code,
        "db_unchanged": after == before,
        "audit_unchanged": True,
    }


def _scenario_pause_then_edit(_context: MatrixContext) -> dict[str, Any]:
    _pause_all_instances()
    user = _ensure_current_user("phase2g2-pause-edit@example.invalid")
    created = _create_instance(user, instance_label="Phase 2G-2 Pause Then Edit", lots=1, side_preference="BOTH")
    instance_id = created["id"]
    client = _debug_client(user)
    resumed = client.post(f"/api/debug/v2/instances/{instance_id}/resume", json={"confirm_paper_only": True})
    _assert(resumed.status_code == 200, f"resume returned HTTP {resumed.status_code}")
    paused = client.post(f"/api/debug/v2/instances/{instance_id}/pause", json={"confirm_paper_only": True})
    _assert(paused.status_code == 200, f"pause returned HTTP {paused.status_code}")

    response = _settings_edit(user, instance_id, instance_label="Phase 2G-2 Edited Again", lots=2, side_preference="CE")
    _assert(response.status_code == 200, f"pause-then-edit returned HTTP {response.status_code}")
    body = response.json()
    after = _instance_snapshot(instance_id)
    _assert(body["changed"] is True, "pause-then-edit did not report changed.")
    _assert(after["status"] == StrategyInstanceStatus.PAUSED.value, "pause-then-edit changed paused status.")
    _assert(after["side_preference"] == "CE", "pause-then-edit did not update side preference.")
    return {
        "instance_id": instance_id,
        "changed": body["changed"],
        "final_status": after["status"],
        "side_preference": after["side_preference"],
    }


def _scenario_planner_observes_after_resume(_context: MatrixContext) -> dict[str, Any]:
    _pause_all_instances()
    user = _ensure_current_user("phase2g2-planner@example.invalid")
    created = _create_instance(user, instance_label="Phase 2G-2 Planner", lots=1, side_preference="BOTH")
    instance_id = created["id"]
    response = _settings_edit(user, instance_id, instance_label="Phase 2G-2 Planner Edited", lots=4, side_preference="CE")
    _assert(response.status_code == 200, f"planner settings edit returned HTTP {response.status_code}")

    before_counts = _db_counts()
    with session_scope() as db:
        paused_plan = plan_strategy_fanout_v2(db, _nova_v1_payload("phase2g2-paused-plan", lots=4), SUPERTREND_CODE)
    paused_skip = [
        skip
        for skip in paused_plan.skips
        if str(skip.instance_id) == instance_id and skip.reason == PAUSED_INSTANCE
    ]
    _assert(paused_plan.planned_count == 0, "paused edited instance was planned.")
    _assert(bool(paused_skip), "paused edited instance skip was not reported.")

    resume = _debug_client(user).post(f"/api/debug/v2/instances/{instance_id}/resume", json={"confirm_paper_only": True})
    _assert(resume.status_code == 200, f"resume returned HTTP {resume.status_code}")
    with session_scope() as db:
        bullish = plan_strategy_fanout_v2(
            db,
            _nova_v1_payload("phase2g2-resumed-bullish", intent="BULLISH", lots=4),
            SUPERTREND_CODE,
        )
        bearish = plan_strategy_fanout_v2(
            db,
            _nova_v1_payload("phase2g2-resumed-bearish", intent="BEARISH", lots=4),
            SUPERTREND_CODE,
        )
        row = db.get(models.UserStrategyInstance, uuid.UUID(instance_id))
        _assert(row is not None, "planner instance missing.")
        instance_lots = row.lots
        instance_side = row.side_preference
    after_counts = _db_counts()

    planned = [plan for plan in bullish.plans if str(plan.instance_id) == instance_id]
    _assert(bullish.planned_count == 1 and planned, "resumed edited instance was not planned.")
    _assert(planned[0].execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value, "resumed plan was not paper.")
    _assert(planned[0].normalized_draft.option_side == "CE", "side preference did not allow CE planning.")
    _assert(planned[0].normalized_draft.lots == 4, "planner draft did not carry expected lots.")
    _assert(bearish.planned_count == 0 and bearish.error_count == 1, "CE preference did not block bearish PE plan.")
    _assert(_side_effect_counts(after_counts, before_counts) == {}, "planner dry-run wrote side-effect tables.")

    return {
        "instance_id": instance_id,
        "paused_planned_count": paused_plan.planned_count,
        "paused_skip_reason": paused_skip[0].reason,
        "resumed_planned_count": bullish.planned_count,
        "execution_mode": planned[0].execution_mode,
        "instance_lots": instance_lots,
        "draft_lots": planned[0].normalized_draft.lots,
        "instance_side_preference": instance_side,
        "draft_option_side": planned[0].normalized_draft.option_side,
        "bearish_blocked": bearish.planned_count == 0 and bearish.error_count == 1,
        "side_effect_counts": _side_effect_counts(after_counts, before_counts),
    }


def _scenario_real_orders_protected(_context: MatrixContext) -> dict[str, Any]:
    user = _ensure_current_user("phase2g2-real@example.invalid")
    instance_id = _seed_real_orders_instance(user)
    before = _instance_snapshot(instance_id)
    response = _settings_edit(user, instance_id, instance_label="Should Not Apply", lots=3, side_preference="PE")
    after = _instance_snapshot(instance_id)

    _assert(response.status_code == 403, "real_orders settings edit was not blocked.")
    _assert(after == before, "real_orders settings edit changed instance state.")
    return {
        "instance_id": instance_id,
        "status_code": response.status_code,
        "status_unchanged": after == before,
    }


def _scenario_cross_user_isolation(_context: MatrixContext) -> dict[str, Any]:
    owner = _ensure_current_user("phase2g2-owner@example.invalid")
    intruder = _ensure_current_user("phase2g2-intruder@example.invalid")
    created = _create_instance(owner, instance_label="Phase 2G-2 Cross User", lots=1, side_preference="BOTH")
    instance_id = created["id"]
    before = _instance_snapshot(instance_id)

    response = _settings_edit(intruder, instance_id, instance_label="Intruder Edit", lots=5, side_preference="PE")
    after = _instance_snapshot(instance_id)
    invalid = _settings_edit(intruder, "not-a-uuid", instance_label="Intruder Edit", lots=5, side_preference="PE")

    _assert(response.status_code == 404, "cross-user settings edit leaked or mutated instance.")
    _assert(invalid.status_code == 404, "invalid id did not return hidden 404.")
    _assert(after == before, "cross-user settings edit changed owner instance.")
    return {
        "instance_id": instance_id,
        "edit_status_code": response.status_code,
        "invalid_status_code": invalid.status_code,
        "status_unchanged": after == before,
        "existence_leak_blocked": True,
    }


def _scenario_smuggled_fields_rejected(_context: MatrixContext) -> dict[str, Any]:
    user = _ensure_current_user("phase2g2-smuggle@example.invalid")
    created = _create_instance(user, instance_label="Phase 2G-2 Smuggle", lots=1, side_preference="BOTH")
    instance_id = created["id"]
    before = _instance_snapshot(instance_id)
    smuggled_cases = (
        {"status": "active"},
        {"execution_mode": "real_orders"},
        {"mode": "live"},
        {"user_id": str(uuid.uuid4())},
        {"live": True},
        {"real": True},
        {"real_orders": True},
        {"secret": "matrix-hidden"},
        {"access_token": "matrix-hidden"},
    )
    status_codes: list[int] = []
    for smuggled in smuggled_cases:
        response = _settings_edit(user, instance_id, instance_label="Should Not Apply", lots=2, side_preference="CE", **smuggled)
        status_codes.append(response.status_code)
        _assert(response.status_code == 422, f"smuggled settings field accepted: {sorted(smuggled)}")
    after = _instance_snapshot(instance_id)

    _assert(after == before, "smuggled settings attempts changed instance state.")
    return {
        "instance_id": instance_id,
        "attempt_count": len(smuggled_cases),
        "status_codes": status_codes,
        "db_unchanged": after == before,
    }


def _scenario_noop_edit_safe(context: MatrixContext) -> dict[str, Any]:
    _pause_all_instances()
    user = _ensure_current_user("phase2g2-noop@example.invalid")
    created = _create_instance(user, instance_label="Phase 2G-2 Noop", lots=2, side_preference="CE")
    instance_id = created["id"]
    before = _instance_snapshot(instance_id)
    before_audits = _settings_audit_count(context)

    response = _settings_edit(user, instance_id, instance_label="Phase 2G-2 Noop", lots=2, side_preference="CE")
    after = _instance_snapshot(instance_id)

    _assert(response.status_code == 200, f"noop edit returned HTTP {response.status_code}")
    _assert(response.json()["changed"] is False, "noop edit did not report changed=false.")
    _assert(after == before, "noop edit wrote instance state.")
    _assert(_settings_audit_count(context) == before_audits, "noop edit emitted settings audit.")
    return {
        "instance_id": instance_id,
        "changed": response.json()["changed"],
        "db_unchanged": after == before,
        "audit_unchanged": True,
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
    forbidden_catalog_terms = (
        "run-once",
        "process-ready",
        "paper-fanout",
        "runv2",
        "processready",
        "generate webhook",
        "rotate secret",
        "go live",
        "squareoff",
        "square off",
        "delete instance",
        "archive instance",
    )

    _assert("import.meta.env.VITE_ENABLE_STRATEGY_INSTANCE_MUTATION === 'true'" in app_text, "mutation UI flag missing.")
    _assert(
        "STRATEGY_INSTANCE_MUTATION_UI_ENABLED && canViewStrategyCatalogStatus" in app_text,
        "mutation UI is not combined with catalog visibility.",
    )
    _assert("enabled && mutationEnabled" in catalog_panel, "panel-side mutation gate missing.")
    _assert("updateStrategyInstanceSettings" in catalog_panel, "settings helper missing from catalog panel.")
    _assert("updateStrategyInstanceSettings" not in paper_panel, "settings helper leaked into v2 paper panel.")
    for term in forbidden_catalog_terms:
        _assert(term not in catalog_panel.lower(), f"forbidden settings UI term found: {term}")
    for forbidden in ("/api/debug/v2/paper-fanout/run-once", "/api/debug/v2/paper-fanout/process-ready"):
        _assert(forbidden not in lifecycle_section, f"settings helpers reference {forbidden}.")
    _assert("/settings" in lifecycle_section, "settings endpoint helper missing from API section.")
    _assert("confirm_paper_only: true" in lifecycle_section, "settings helpers do not force confirm_paper_only.")
    return {
        "mutation_flag_default_off": True,
        "catalog_gate_required": True,
        "settings_helper_scoped_to_catalog": True,
        "runner_endpoints_referenced": False,
        "execution_controls_rendered": False,
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
            google_sub=f"phase2g2-{email}",
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


def _create_instance(
    user: CurrentUser,
    *,
    instance_label: str,
    lots: int = 1,
    side_preference: str = "BOTH",
) -> dict[str, Any]:
    response = _debug_client(user).post(
        "/api/debug/v2/instances",
        json={
            "catalog_code": SUPERTREND_CODE,
            "instance_label": instance_label,
            "lots": lots,
            "side_preference": side_preference,
            "confirm_paper_only": True,
        },
    )
    _assert(response.status_code == 200, f"create returned HTTP {response.status_code}")
    return response.json()["instance"]


def _settings_edit(user: CurrentUser, instance_id: str, **overrides: Any):
    body: dict[str, Any] = {
        "instance_label": "Phase 2G-2 Settings",
        "lots": 2,
        "side_preference": "CE",
        "confirm_paper_only": True,
    }
    body.update(overrides)
    return _debug_client(user).post(f"/api/debug/v2/instances/{instance_id}/settings", json=body)


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
            instance_label="Phase 2G-2 real_orders protected",
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


def _instance_snapshot(instance_id: str) -> dict[str, Any]:
    with session_scope() as db:
        row = db.get(models.UserStrategyInstance, uuid.UUID(str(instance_id)))
        _assert(row is not None, "instance row not found.")
        config = row.config_json if isinstance(row.config_json, dict) else {}
        last_mutation = config.get("last_mutation") if isinstance(config.get("last_mutation"), dict) else {}
        return {
            "id": str(row.id),
            "user_id": str(row.user_id),
            "strategy_id": str(row.strategy_id),
            "strategy_version_id": str(row.strategy_version_id) if row.strategy_version_id else None,
            "instance_label": row.instance_label,
            "source_type": row.source_type,
            "status": row.status,
            "execution_mode": row.execution_mode,
            "lots": row.lots,
            "side_preference": row.side_preference,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "last_mutation_action": last_mutation.get("action"),
        }


def _changed_snapshot_keys(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(key for key in before if before.get(key) != after.get(key))


def _nova_v1_payload(
    signal_id: str,
    *,
    intent: str = "BULLISH",
    lots: int = 1,
) -> dict[str, Any]:
    return {
        "version": "nova.v1",
        "secret": "phase2g2-settings-matrix-secret",
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


def _settings_audit_count(context: MatrixContext) -> int:
    return sum(1 for event in context.audit_events if event["event_type"] == "V2_INSTANCE_SETTINGS_EDITED")


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
            raise SettingsMatrixError("route_signal was called unexpectedly.")

        def fail_worker_route(*_args: Any, **_kwargs: Any) -> Any:
            self.calls["worker_route_signal"] += 1
            raise SettingsMatrixError("worker route_signal was called unexpectedly.")

        def fail_dhan(*_args: Any, **_kwargs: Any) -> Any:
            self.calls["dhan"] += 1
            raise SettingsMatrixError("Dhan client was called unexpectedly.")

        def fail_paper(*_args: Any, **_kwargs: Any) -> Any:
            self.calls["paper_broker"] += 1
            raise SettingsMatrixError("PaperBroker was called unexpectedly.")

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
    start = api_text.find("Phase 2F/2G: paper strategy instance lifecycle/settings helpers")
    if start == -1:
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
        raise SettingsMatrixError(message)


def _safe_error(exc: Exception) -> str:
    if _sensitive_field_count({"error": str(exc)}):
        return "Settings matrix failed."
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
