from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.db.engine import reset_engine_for_tests
from scripts import manual_v2_instance_clone_matrix as script


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


def test_clone_matrix_defaults_to_isolated_db_runtime_and_covers_scenarios(monkeypatch, tmp_path):
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

    result = script.run_clone_matrix(
        script.CloneMatrixConfig(work_dir=tmp_path / "matrix", keep_temp=True),
        out=lines.append,
    )

    assert result["overall_ok"] is True
    assert result["fresh_isolated_db"] is True
    assert result["runtime_isolated"] is True
    assert result["db_type"] == "sqlite_temp"
    assert (tmp_path / "matrix" / "clone_matrix.sqlite3").exists()
    assert runtime["app_state_file"].read_text(encoding="utf-8") == "repo-marker\n"

    expected = {
        "clone_paused_instance",
        "clone_active_instance",
        "clone_archived_instance",
        "clone_ineligible_blocked",
        "cross_user_isolation",
        "label_numbering",
        "cap_enforced",
        "planner_skips_until_resumed",
        "frontend_static_safety",
    }
    assert set(result["scenarios"]) == expected
    assert all(status == "passed" for status in result["scenarios"].values())
    for name in expected:
        assert result[name] == "passed"

    for scenario_name in ("clone_paused_instance", "clone_active_instance", "clone_archived_instance"):
        details = _result_by_name(result, scenario_name)["details"]
        assert details["clone_status"] == "paused"
        assert details["clone_execution_mode"] == "paper_live_data"
        assert details["clone_label"] == "Clone Source (Copy)"
        assert details["side_effect_counts"] == {}

    blocked = _result_by_name(result, "clone_ineligible_blocked")["details"]
    assert blocked["real_orders_status_code"] == 403
    assert blocked["disabled_status_code"] == 409
    assert blocked["error_status_code"] == 409
    assert blocked["db_unchanged"] is True

    isolation = _result_by_name(result, "cross_user_isolation")["details"]
    assert isolation["intruder_status_code"] == 404
    assert isolation["invalid_status_code"] == 404
    assert isolation["db_unchanged"] is True

    numbering = _result_by_name(result, "label_numbering")["details"]
    assert numbering["labels"] == [
        "Momentum Strategy (Copy)",
        "Momentum Strategy (Copy 2)",
        "Momentum Strategy (Copy 3)",
    ]

    cap = _result_by_name(result, "cap_enforced")["details"]
    assert cap["cap"] == 10
    assert cap["status_code"] == 409
    assert cap["db_unchanged"] is True

    planner = _result_by_name(result, "planner_skips_until_resumed")["details"]
    assert planner["paused_skip_reason"] == "PAUSED_INSTANCE"
    assert planner["resumed_planned_count"] == 1
    assert planner["side_effect_counts_while_paused"] == {}

    frontend = _result_by_name(result, "frontend_static_safety")["details"]
    assert frontend["clone_helper_present"] is True
    assert frontend["clone_control_present"] is True
    assert frontend["v2_paper_panel_read_only"] is True

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
    assert "phase2i1-clone-matrix-secret" not in output
    assert '"raw_payload"' not in output
    assert '"signal_payload"' not in output


def test_clone_matrix_refuses_external_db_without_configured_url(monkeypatch, tmp_path):
    from app.config import settings

    _protect_repo_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "DATABASE_URL", "", raising=False)
    reset_engine_for_tests()

    with pytest.raises(script.CloneMatrixError, match="DATABASE_URL"):
        script.run_clone_matrix(
            script.CloneMatrixConfig(allow_external_db=True, work_dir=tmp_path / "external"),
            out=lambda _line: None,
        )


def test_clone_matrix_summary_sanitizer_strips_sensitive_fields():
    dirty = {
        "safe": "kept",
        "secret": "hidden",
        "raw_payload": {"nested": "hidden"},
        "payload": {"nested": "hidden"},
        "details": {
            "signal_payload": {"nested": "hidden"},
            "access_token": "hidden",
            "safe_nested": {"value": "kept"},
        },
    }

    assert script._sanitize(dirty) == {
        "safe": "kept",
        "details": {"safe_nested": {"value": "kept"}},
    }
    assert script._sensitive_field_count(dirty) == 5


def test_clone_matrix_exits_nonzero_on_scenario_failure(monkeypatch, tmp_path):
    _protect_repo_runtime(monkeypatch, tmp_path)

    def failing_scenario(_context):
        raise script.CloneMatrixError("forced scenario failure")

    monkeypatch.setattr(script, "_scenario_functions", lambda: (("forced_failure", failing_scenario),))

    code = script.main(["--work-dir", str(tmp_path / "failure"), "--keep-temp"])

    assert code == 2
