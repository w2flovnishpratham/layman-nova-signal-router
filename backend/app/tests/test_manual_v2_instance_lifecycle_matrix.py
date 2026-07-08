from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.enums import StrategyExecutionMode
from app.db.engine import reset_engine_for_tests
from scripts import manual_v2_instance_lifecycle_matrix as script


def _protect_repo_runtime(monkeypatch, tmp_path) -> dict[str, Path]:
    repo_state = tmp_path / "repo-runtime-state"
    repo_logs = tmp_path / "repo-runtime-logs"
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
        monkeypatch.setattr(script.state_store, name, repo_state / filename, raising=False)
    log_files = {
        "webhook": repo_logs / "webhook_events.jsonl",
        "order": repo_logs / "order_events.jsonl",
        "audit": repo_logs / "audit_events.jsonl",
        "error": repo_logs / "errors.jsonl",
        "paper_orders": repo_logs / "paper_orders.jsonl",
    }
    monkeypatch.setattr(script.state_store, "LOG_FILES", log_files, raising=False)
    monkeypatch.setattr(script.audit_logger, "LOG_FILES", log_files, raising=False)
    return {"repo_state": repo_state, "repo_logs": repo_logs, "app_state_file": repo_state / "app_state.json"}


def _result_by_name(result: dict[str, Any], name: str) -> dict[str, Any]:
    return {item["name"]: item for item in result["results"]}[name]


def test_lifecycle_matrix_defaults_to_isolated_db_runtime_and_covers_scenarios(monkeypatch, tmp_path):
    from app.config import settings

    runtime = _protect_repo_runtime(monkeypatch, tmp_path)
    runtime["app_state_file"].parent.mkdir(parents=True, exist_ok=True)
    runtime["app_state_file"].write_text("repo-marker\n", encoding="utf-8")
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "postgresql://user:raw-db-password@shared-staging.example.invalid/prod",
        raising=False,
    )
    reset_engine_for_tests()
    lines: list[str] = []

    result = script.run_lifecycle_matrix(
        script.LifecycleMatrixConfig(work_dir=tmp_path / "matrix", keep_temp=True),
        out=lines.append,
    )

    assert result["overall_ok"] is True
    assert result["fresh_isolated_db"] is True
    assert result["runtime_isolated"] is True
    assert result["db_type"] == "sqlite_temp"
    assert (tmp_path / "matrix" / "lifecycle_matrix.sqlite3").exists()
    assert runtime["app_state_file"].read_text(encoding="utf-8") == "repo-marker\n"

    expected = {
        "create_born_paused",
        "paused_skipped_by_fanout",
        "resume_planning_eligible",
        "pause_skips_again",
        "real_orders_protected",
        "cross_user_isolation",
        "smuggled_fields_rejected",
        "frontend_static_safety",
    }
    assert set(result["scenarios"]) == expected
    assert all(status == "passed" for status in result["scenarios"].values())
    for name in expected:
        assert result[name] == "passed"

    created = _result_by_name(result, "create_born_paused")["details"]
    assert created["execution_mode"] == StrategyExecutionMode.PAPER_LIVE_DATA.value
    assert created["status"] == "paused"
    assert created["signals_created"] == 0
    assert created["jobs_created"] == 0
    assert created["webhook_credentials_created"] == 0
    assert created["runtime_position_files_unchanged"] is True

    paused = _result_by_name(result, "paused_skipped_by_fanout")["details"]
    assert paused["planned_count"] == 0
    assert paused["skip_reason"] == "PAUSED_INSTANCE"
    assert paused["v2_pending_jobs_created"] == 0

    resumed = _result_by_name(result, "resume_planning_eligible")["details"]
    assert resumed["resume_status"] == "active"
    assert resumed["planned_count"] == 1
    assert resumed["execution_mode"] == StrategyExecutionMode.PAPER_LIVE_DATA.value
    assert resumed["job_creation_invoked"] is False
    assert resumed["v2_pending_jobs_created"] == 0

    paused_again = _result_by_name(result, "pause_skips_again")["details"]
    assert paused_again["final_status"] == "paused"
    assert paused_again["planned_count"] == 0
    assert paused_again["skip_reason"] == "PAUSED_INSTANCE"
    assert paused_again["new_jobs_created"] == 0

    real_orders = _result_by_name(result, "real_orders_protected")["details"]
    assert real_orders["pause_status_code"] == 403
    assert real_orders["resume_status_code"] == 403
    assert real_orders["status_unchanged"] is True
    assert real_orders["planner_skip_reason"] == "REAL_ORDERS_NOT_SUPPORTED_YET"

    isolation = _result_by_name(result, "cross_user_isolation")["details"]
    assert isolation["pause_status_code"] == 404
    assert isolation["resume_status_code"] == 404
    assert isolation["status_unchanged"] is True
    assert isolation["existence_leak_blocked"] is True

    smuggled = _result_by_name(result, "smuggled_fields_rejected")["details"]
    assert smuggled["attempt_count"] == 10
    assert set(smuggled["status_codes"]) == {422}
    assert smuggled["db_writes"] == 0

    frontend = _result_by_name(result, "frontend_static_safety")["details"]
    assert frontend["mutation_flag_default_off"] is True
    assert frontend["catalog_gate_required"] is True
    assert frontend["runner_endpoints_referenced"] is False
    assert frontend["execution_controls_rendered"] is False

    assert result["execution_guard_calls"] == {
        "route_signal": 0,
        "worker_route_signal": 0,
        "dhan": 0,
        "paper_broker": 0,
    }
    assert result["settings"]["enable_live_orders"] is False
    assert result["sensitive_field_count"] == 0

    output = "\n".join(lines)
    assert "raw-db-password" not in output
    assert "shared-staging.example.invalid" not in output
    assert "phase2f2-lifecycle-matrix-secret" not in output
    assert '"raw_payload"' not in output
    assert '"signal_payload"' not in output


def test_lifecycle_matrix_refuses_external_db_without_configured_url(monkeypatch, tmp_path):
    from app.config import settings

    _protect_repo_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "DATABASE_URL", "", raising=False)
    reset_engine_for_tests()

    with pytest.raises(script.LifecycleMatrixError, match="DATABASE_URL"):
        script.run_lifecycle_matrix(
            script.LifecycleMatrixConfig(allow_external_db=True, work_dir=tmp_path / "external"),
            out=lambda _line: None,
        )


def test_lifecycle_matrix_summary_sanitizer_strips_sensitive_fields():
    dirty = {
        "safe": "kept",
        "secret": "hidden",
        "raw_payload": {"nested": "hidden"},
        "payload": {"nested": "hidden"},
        "webhook_credentials_created": 0,
        "details": {
            "signal_payload": {"nested": "hidden"},
            "access_token": "hidden",
            "safe_nested": {"value": "kept"},
        },
    }

    assert script._sanitize(dirty) == {
        "safe": "kept",
        "webhook_credentials_created": 0,
        "details": {"safe_nested": {"value": "kept"}},
    }
    assert script._sensitive_field_count(dirty) == 5


def test_lifecycle_matrix_exits_nonzero_on_scenario_failure(monkeypatch, tmp_path):
    _protect_repo_runtime(monkeypatch, tmp_path)

    def failing_scenario(_context):
        raise script.LifecycleMatrixError("forced scenario failure")

    monkeypatch.setattr(script, "_scenario_functions", lambda: (("forced_failure", failing_scenario),))

    code = script.main(["--work-dir", str(tmp_path / "failure"), "--keep-temp"])

    assert code == 2
