from __future__ import annotations

import json

from scripts import manual_v2_paper_repeated_smoke as script


def _ok_summary(**overrides):
    data = {
        "overall_ok": True,
        "fresh_isolated_db": True,
        "runtime_isolated": True,
        "scenarios": {
            "bullish_entry_exit": "passed",
            "bearish_entry_exit": "passed",
            "duplicate_signal": "passed",
            "two_instance_isolation": "passed",
            "real_orders_blocked": "passed",
            "signal_only_not_routed": "passed",
            "max_jobs_respected": "passed",
            "inspector_safe": "passed",
        },
    }
    data.update(overrides)
    return data


def test_repeated_smoke_runs_multiple_isolated_iterations(monkeypatch, tmp_path):
    calls = []

    def fake_matrix(config, *, out):
        calls.append(config)
        return _ok_summary()

    monkeypatch.setattr(script, "run_scenario_matrix", fake_matrix)
    lines: list[str] = []

    result = script.run_repeated_smoke(
        script.RepeatedSmokeConfig(iterations=3, work_dir=tmp_path / "repeat"),
        out=lines.append,
    )

    assert result["ok"] is True
    assert result["iterations_requested"] == 3
    assert result["successful_iterations"] == 3
    assert result["fresh_isolated_db_all"] is True
    assert result["runtime_isolated_all"] is True
    assert [call.allow_external_db for call in calls] == [False, False, False]
    assert [call.work_dir.name for call in calls] == ["iteration-1", "iteration-2", "iteration-3"]
    assert all(str(call.work_dir).startswith(str(tmp_path / "repeat")) for call in calls)

    event = json.loads(lines[0])
    assert event["event"] == "repeated_v2_paper_smoke_summary"
    assert event["data"]["ok"] is True


def test_repeated_smoke_summary_is_sanitized(monkeypatch, tmp_path):
    def fake_matrix(config, *, out):
        return _ok_summary(
            secret="hidden-secret",
            raw_payload={"access_token": "hidden-token"},
            signal_payload={"headers": {"secret": "hidden"}},
        )

    monkeypatch.setattr(script, "run_scenario_matrix", fake_matrix)
    lines: list[str] = []

    result = script.run_repeated_smoke(
        script.RepeatedSmokeConfig(iterations=1, work_dir=tmp_path / "repeat"),
        out=lines.append,
    )

    output = "\n".join(lines)
    assert result["ok"] is True
    assert result["results"][0]["sensitive_field_count"] == 3
    assert "hidden-secret" not in output
    assert "hidden-token" not in output
    assert "raw_payload" not in output
    assert "signal_payload" not in output
    assert "access_token" not in output
    assert '"secret"' not in output.lower()


def test_repeated_smoke_main_exits_nonzero_on_failure(monkeypatch, tmp_path):
    def fake_matrix(config, *, out):
        raise script.RepeatedSmokeError("forced failure")

    monkeypatch.setattr(script, "run_scenario_matrix", fake_matrix)

    code = script.main(["--iterations", "2", "--work-dir", str(tmp_path / "repeat")])

    assert code == 2
