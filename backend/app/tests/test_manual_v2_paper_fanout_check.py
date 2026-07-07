from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.core.enums import StrategyExecutionMode
from app.core.feature_flags import MULTI_STRATEGY_FANOUT, V2_PAPER_RUNNER_DEBUG
from app.db import models
from app.db.engine import session_scope
from app.schemas.nova_signal_v1 import NovaSignalV1
from app.services import state_store
from app.tests.conftest_multiuser import mu_db  # noqa: F401
from scripts import manual_v2_paper_fanout_check as script


class _FakeRunnerResult:
    def __init__(self, *, ok: bool = True, action: str = "ENTRY") -> None:
        self.ok = ok
        self.action = action

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "strategy_code": "SUPERTREND_V1",
            "signal_id": f"fake-{self.action.lower()}",
            "planned_count": 1,
            "job_created_count": 1,
            "processed_count": 1,
            "completed_count": 1 if self.ok else 0,
            "failed_count": 0 if self.ok else 1,
            "skipped_count": 0,
            "errors": [],
            "job_results": [
                {
                    "ok": self.ok,
                    "status": "v2_completed_paper",
                    "route_result_summary": {
                        "status": "PAPER_OK",
                        "access_token": "raw-token-secret",
                        "raw_response": {"secret": "broker-secret"},
                    },
                }
            ],
            "secret": "top-level-secret",
        }


def _set_safe_env(monkeypatch, *, fanout: bool = True, runner_debug: bool = True) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "DEBUG_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False, raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK", raising=False)
    monkeypatch.setenv(MULTI_STRATEGY_FANOUT, "true" if fanout else "false")
    monkeypatch.setenv(V2_PAPER_RUNNER_DEBUG, "true" if runner_debug else "false")


def _isolate_runtime(monkeypatch, tmp_path):
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
    monkeypatch.setattr(
        state_store,
        "LOG_FILES",
        {
            "webhook": log_root / "webhook_events.jsonl",
            "order": log_root / "order_events.jsonl",
            "audit": log_root / "audit_events.jsonl",
            "error": log_root / "errors.jsonl",
            "paper_orders": log_root / "paper_orders.jsonl",
        },
    )
    return state_root


def _position(**overrides: Any) -> dict[str, Any]:
    data = {
        **state_store.default_open_position(),
        "has_open_position": True,
        "strategy_code": "SUPERTREND_V1",
        "trading_symbol": "NIFTY LEGACY CE",
        "qty": 75,
        "entry_order_id": "PAPER-LEGACY",
        "entry_price": 100.0,
    }
    data.update(overrides)
    return data


def _install_fake_runner(monkeypatch):
    def fake_runner(db, raw_payload, strategy_code, *, max_jobs):
        assert strategy_code == "SUPERTREND_V1"
        assert max_jobs == 1
        instance = db.scalar(
            select(models.UserStrategyInstance).where(
                models.UserStrategyInstance.execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value
            )
        )
        assert instance is not None
        instance_id = str(instance.id)
        if raw_payload["action"] == "ENTRY":
            state_store.set_open_position(
                _position(
                    instance_id=instance_id,
                    trading_symbol=f"NIFTY INSTANCE {instance_id} CE",
                    entry_order_id="PAPER-V2-ENTRY",
                ),
                instance_id=instance_id,
            )
            return _FakeRunnerResult(action="ENTRY")
        state_store.clear_open_position(instance_id=instance_id)
        return _FakeRunnerResult(action="EXIT")

    monkeypatch.setattr(script, "run_v2_paper_fanout_once", fake_runner)


def test_script_refuses_when_required_flags_are_off(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch, fanout=False, runner_debug=True)

    with pytest.raises(script.ManualCheckError, match="MULTI_STRATEGY_FANOUT"):
        script.run_manual_check(script.ManualCheckConfig(dry_run=True), out=lambda _line: None)


def test_script_refuses_when_paper_confirmation_is_missing(mu_db, monkeypatch, tmp_path):
    state_root = _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch)

    with pytest.raises(script.ManualCheckError, match="confirm-paper-only"):
        script.run_manual_check(script.ManualCheckConfig(dry_run=False), out=lambda _line: None)

    assert list(state_root.rglob("*")) == []


def test_dry_run_does_not_mutate_db_or_state_files(mu_db, monkeypatch, tmp_path):
    state_root = _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch)
    lines: list[str] = []

    with session_scope() as db:
        assert db.query(models.User).count() == 0
        assert db.query(models.UserStrategyInstance).count() == 0

    result = script.run_manual_check(script.ManualCheckConfig(dry_run=True), out=lines.append)

    assert result["dry_run"] is True
    assert "would_run" in result
    with session_scope() as db:
        assert db.query(models.User).count() == 0
        assert db.query(models.UserStrategyInstance).count() == 0
    assert list(state_root.rglob("*")) == []


def test_script_builds_schema_valid_nova_v1_entry_payload():
    payload = script.build_entry_payload(
        signal_id="manual-entry-schema",
        strike=25000,
        expiry="2026-07-09",
        lots=1,
    )

    parsed = NovaSignalV1.model_validate(payload)

    assert parsed.action == "ENTRY"
    assert parsed.intent == "BULLISH"
    assert parsed.source == "backend"


def test_script_builds_schema_valid_nova_v1_exit_payload():
    payload = script.build_exit_payload(
        signal_id="manual-exit-schema",
        strike=25000,
        expiry="2026-07-09",
        lots=1,
    )

    parsed = NovaSignalV1.model_validate(payload)

    assert parsed.action == "EXIT"
    assert parsed.intent == "FLAT"
    assert parsed.option_side == "NONE"


def test_script_never_creates_real_orders_instance(mu_db, monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch)
    state_store.init_runtime_files()
    state_store.set_engine_mode("paper")
    _install_fake_runner(monkeypatch)

    result = script.run_manual_check(
        script.ManualCheckConfig(dry_run=False, confirm_paper_only=True),
        out=lambda _line: None,
    )

    assert result["entry_position_opened"] is True
    assert result["exit_position_cleared"] is True
    with session_scope() as db:
        modes = db.scalars(select(models.UserStrategyInstance.execution_mode)).all()
        assert StrategyExecutionMode.PAPER_LIVE_DATA.value in modes
        assert StrategyExecutionMode.REAL_ORDERS.value not in modes


def test_script_does_not_expose_secret_in_printed_summary(mu_db, monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    _set_safe_env(monkeypatch)
    state_store.init_runtime_files()
    state_store.set_engine_mode("paper")
    _install_fake_runner(monkeypatch)
    lines: list[str] = []

    script.run_manual_check(
        script.ManualCheckConfig(dry_run=False, confirm_paper_only=True),
        out=lines.append,
    )

    output = "\n".join(lines)
    assert "manual-v2-paper-check-secret" not in output
    assert "raw-token-secret" not in output
    assert "broker-secret" not in output
    assert '"secret"' not in output.lower()
    assert '"raw_response"' not in output.lower()


def test_cleanup_does_not_remove_legacy_position(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.init_runtime_files()
    state_store.set_engine_mode("paper")
    legacy = _position(trading_symbol="NIFTY LEGACY CE")
    state_store.set_open_position(legacy)
    state_store.set_open_position(_position(trading_symbol="NIFTY INSTANCE CE"), instance_id="instance-a")

    script.cleanup_instance_position("instance-a")

    assert state_store.get_open_position("instance-a") is None
    assert state_store.get_paper_position() == legacy
