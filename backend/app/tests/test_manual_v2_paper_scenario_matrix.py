from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.feature_flags import MULTI_STRATEGY_FANOUT, V2_PAPER_RUNNER_DEBUG
from app.db.engine import reset_engine_for_tests
from app.schemas.nova_signal_v1 import NovaSignalV1
from scripts import manual_v2_paper_scenario_matrix as script


def _set_safe_env(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "DEBUG_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False, raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK", raising=False)
    monkeypatch.setenv(MULTI_STRATEGY_FANOUT, "true")
    monkeypatch.setenv(V2_PAPER_RUNNER_DEBUG, "true")
    reset_engine_for_tests()


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


def test_matrix_defaults_to_isolated_db_and_covers_required_scenarios(monkeypatch, tmp_path):
    from app.config import settings

    _set_safe_env(monkeypatch)
    runtime = _protect_repo_runtime(monkeypatch, tmp_path)
    runtime["app_state_file"].parent.mkdir(parents=True, exist_ok=True)
    runtime["app_state_file"].write_text("repo-marker\n", encoding="utf-8")
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "postgresql://user:raw-db-password@shared-staging.example.invalid/prod",
        raising=False,
    )
    lines: list[str] = []

    result = script.run_scenario_matrix(
        script.ScenarioMatrixConfig(work_dir=tmp_path / "matrix", keep_temp=True),
        out=lines.append,
    )

    assert result["overall_ok"] is True
    assert result["fresh_isolated_db"] is True
    assert result["runtime_isolated"] is True
    assert result["db_type"] == "sqlite_temp"
    assert (tmp_path / "matrix" / "scenario_matrix.sqlite3").exists()
    assert runtime["app_state_file"].read_text(encoding="utf-8") == "repo-marker\n"

    expected = {
        "bullish_entry_exit",
        "bearish_entry_exit",
        "duplicate_signal",
        "two_instance_isolation",
        "real_orders_blocked",
        "signal_only_not_routed",
        "max_jobs_respected",
        "inspector_safe",
    }
    assert set(result["scenarios"]) == expected
    assert all(status == "passed" for status in result["scenarios"].values())

    bullish = _result_by_name(result, "bullish_entry_exit")["details"]
    assert bullish["entry_position_opened"] is True
    assert bullish["exit_position_cleared"] is True
    assert bullish["legacy_position_untouched"] is True
    assert bullish["option_side"] == "CE"
    assert bullish["analytics_pairing"]["paired_count"] == 1

    bearish = _result_by_name(result, "bearish_entry_exit")["details"]
    assert bearish["entry_position_opened"] is True
    assert bearish["exit_position_cleared"] is True
    assert bearish["legacy_position_untouched"] is True
    assert bearish["option_side"] == "PE"
    assert bearish["analytics_pairing"]["paired_count"] == 1

    duplicate = _result_by_name(result, "duplicate_signal")["details"]
    assert duplicate["second_created_jobs"] == 0
    assert duplicate["job_count_unchanged"] is True
    assert duplicate["position_unchanged"] is True
    assert duplicate["paper_order_log_count_unchanged"] is True
    assert duplicate["analytics_pairing_unchanged"] is True

    isolation = _result_by_name(result, "two_instance_isolation")["details"]
    assert isolation["entry_processed"] == 2
    assert isolation["first_exit_cleared_only_first"] is True
    assert isolation["second_exit_cleared_second"] is True
    assert isolation["legacy_position_untouched"] is True

    real_orders = _result_by_name(result, "real_orders_blocked")["details"]
    assert real_orders["real_order_jobs"] == 0
    assert real_orders["pending_real_order_jobs"] == 0

    signal_only = _result_by_name(result, "signal_only_not_routed")["details"]
    assert signal_only["job_created_count"] == 0
    assert signal_only["paper_positions_created"] == 0

    max_jobs = _result_by_name(result, "max_jobs_respected")["details"]
    assert max_jobs["job_created_count"] == 3
    assert max_jobs["processed_count"] == 1
    assert max_jobs["completed_count"] == 1
    assert max_jobs["remaining_pending_jobs"] == 2
    assert max_jobs["open_positions"] == 1

    inspector = _result_by_name(result, "inspector_safe")["details"]
    assert inspector["readable"] == {"status": True, "jobs": True, "instances": True}
    assert inspector["sensitive_field_count"] == 0
    assert inspector["db_unchanged"] is True
    assert inspector["runtime_files_unchanged"] is True

    output = "\n".join(lines)
    assert "raw-db-password" not in output
    assert "shared-staging.example.invalid" not in output
    assert "manual-v2-paper-scenario-secret" not in output
    assert '"raw_payload"' not in output
    assert '"signal_payload"' not in output
    assert result["sensitive_field_count"] == 0


def test_matrix_builds_schema_valid_bullish_and_bearish_payloads():
    bullish = NovaSignalV1.model_validate(
        script.build_entry_payload(signal_id="schema-bullish", intent="BULLISH")
    )
    bearish = NovaSignalV1.model_validate(
        script.build_entry_payload(signal_id="schema-bearish", intent="BEARISH")
    )

    assert bullish.action == "ENTRY"
    assert bullish.intent == "BULLISH"
    assert bullish.option_side == "AUTO"
    assert bearish.action == "ENTRY"
    assert bearish.intent == "BEARISH"
    assert bearish.option_side == "AUTO"


def test_matrix_summary_sanitizer_strips_sensitive_fields():
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

    assert script._sanitize(dirty) == {"safe": "kept", "details": {"safe_nested": {"value": "kept"}}}
    assert script._sensitive_field_count(dirty) == 5


def test_matrix_refuses_live_orders_flag(monkeypatch, tmp_path):
    from app.config import settings

    _set_safe_env(monkeypatch)
    _protect_repo_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True, raising=False)

    with pytest.raises(script.ScenarioMatrixError, match="ENABLE_LIVE_ORDERS"):
        script.run_scenario_matrix(
            script.ScenarioMatrixConfig(work_dir=tmp_path / "blocked", keep_temp=True),
            out=lambda _line: None,
        )


def test_matrix_exits_nonzero_on_scenario_failure(monkeypatch, tmp_path):
    _set_safe_env(monkeypatch)
    _protect_repo_runtime(monkeypatch, tmp_path)

    def failing_scenario(_context):
        raise script.ScenarioMatrixError("forced scenario failure")

    monkeypatch.setattr(script, "_scenario_functions", lambda: (("forced_failure", failing_scenario),))

    code = script.main(["--work-dir", str(tmp_path / "failure"), "--keep-temp"])

    assert code == 2
