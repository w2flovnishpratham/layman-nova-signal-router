from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.services import audit_logger, paper_portfolio, runtime_reliability, state_store
from app.services.user_context import CurrentUser, dev_user


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "logs"
    paths = {
        "APP_STATE_FILE": state_dir / "app_state.json",
        "OPEN_POSITION_FILE": state_dir / "open_position.json",
        "PAPER_POSITION_FILE": state_dir / "paper_position.json",
        "PAPER_PORTFOLIO_FILE": state_dir / "paper_portfolio.json",
        "EXTERNAL_POSITIONS_FILE": state_dir / "external_positions.json",
        "SEEN_SIGNALS_FILE": state_dir / "seen_signals.json",
        "SETTINGS_FILE": state_dir / "settings.json",
        "PAPER_SETTINGS_FILE": state_dir / "paper_settings.json",
        "LIVE_SETTINGS_FILE": state_dir / "live_settings.json",
    }
    monkeypatch.setattr(state_store, "RUNTIME_STATE_DIR", state_dir)
    monkeypatch.setattr(state_store, "RUNTIME_LOG_DIR", log_dir)
    from app.services import user_context

    monkeypatch.setattr(user_context, "RUNTIME_STATE_DIR", state_dir)
    monkeypatch.setattr(user_context, "RUNTIME_LOG_DIR", log_dir)
    for name, value in paths.items():
        monkeypatch.setattr(state_store, name, value)
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", paths["PAPER_PORTFOLIO_FILE"])
    logs = {
        "webhook": log_dir / "webhook_events.jsonl",
        "order": log_dir / "order_events.jsonl",
        "paper_orders": log_dir / "paper_orders.jsonl",
        "audit": log_dir / "audit_events.jsonl",
        "error": log_dir / "errors.jsonl",
        "runtime_snapshots": log_dir / "runtime_snapshots.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", logs)
    monkeypatch.setattr(runtime_reliability, "LOG_FILES", logs)
    monkeypatch.setattr(audit_logger, "LOG_FILES", logs)
    monkeypatch.setattr(settings, "APP_ENV", "test", raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK", raising=False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False, raising=False)
    state_store.init_runtime_files()
    state_store.set_engine_mode("paper")
    return dev_user(), logs


def _open_position(**changes):
    value = {
        "has_open_position": True,
        "strategy_code": "TEST",
        "security_id": "123",
        "trading_symbol": "NIFTY TEST CE",
        "option_side": "CE",
        "qty": 65,
        "entry_price": 100.0,
        "opened_at": "2026-07-20T04:00:00+00:00",
    }
    value.update(changes)
    return value


def test_mode_settings_are_separate(runtime):
    user, _ = runtime
    runtime_reliability.configure_mode(
        user, mode="paper", lots=2, stop_loss_percent=8, target_profit_percent=16
    )
    runtime_reliability.switch_mode(user, "live")
    runtime_reliability.configure_mode(
        user, mode="live", lots=4, stop_loss_percent=12, target_profit_percent=24
    )
    status = runtime_reliability.runtime_status(user)
    assert status["config"]["paper"]["configured_lots"] == 2
    assert status["config"]["live"]["configured_lots"] == 4


def test_stop_preserves_open_position(runtime):
    user, _ = runtime
    state_store.set_paper_position(_open_position())
    runtime_reliability.mark_running()
    status = runtime_reliability.stop_engine(user)
    assert status["engine"]["state"] == "STOPPED"
    assert status["engine"]["accepting_signals"] is False
    assert status["position"]["has_open_position"] is True


def test_stopped_open_position_has_explicit_display(runtime):
    user, _ = runtime
    state_store.set_paper_position(_open_position())
    status = runtime_reliability.stop_engine(user)
    assert status["engine"]["display"] == "POSITION OPEN — ENGINE STOPPED"


def test_starting_and_running_transitions(runtime):
    user, _ = runtime
    runtime_reliability.mark_starting()
    assert runtime_reliability.runtime_status(user)["engine"]["state"] == "STARTING"
    runtime_reliability.mark_running()
    status = runtime_reliability.runtime_status(user)
    assert status["engine"]["state"] == "RUNNING"
    assert status["engine"]["accepting_signals"] is True


def test_start_failure_returns_stopped(runtime):
    user, _ = runtime
    runtime_reliability.mark_starting()
    runtime_reliability.mark_start_failed()
    assert runtime_reliability.runtime_status(user)["engine"]["state"] == "STOPPED"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("mode", "sim", "mode"),
        ("lots", 0, "lots"),
        ("lots", 21, "lots"),
        ("stop_loss_percent", -1, "stop_loss"),
        ("stop_loss_percent", 101, "stop_loss"),
        ("target_profit_percent", -1, "target_profit"),
        ("target_profit_percent", 1001, "target_profit"),
    ],
)
def test_configuration_validation(runtime, field, value, message):
    user, _ = runtime
    payload = {
        "mode": "paper",
        "lots": 1,
        "stop_loss_percent": 10,
        "target_profit_percent": 20,
    }
    payload[field] = value
    with pytest.raises(ValueError, match=message):
        runtime_reliability.configure_mode(user, **payload)


@pytest.mark.parametrize(
    ("lots", "sl", "tp"),
    [(1, 0, 0), (20, 100, 1000), (3, 5.5, 11.25), (7, 99.9, 20)],
)
def test_configuration_boundary_values(runtime, lots, sl, tp):
    user, _ = runtime
    status = runtime_reliability.configure_mode(
        user,
        mode="paper",
        lots=lots,
        stop_loss_percent=sl,
        target_profit_percent=tp,
    )
    assert status["config"]["paper"]["configured_lots"] == lots
    assert status["config"]["paper"]["option_sl_percent"] == sl
    assert status["config"]["paper"]["option_tp_percent"] == tp


def test_configuration_blocked_while_running(runtime):
    user, _ = runtime
    runtime_reliability.mark_running()
    with pytest.raises(ValueError, match="Stop the engine"):
        runtime_reliability.configure_mode(
            user, mode="paper", lots=2, stop_loss_percent=10, target_profit_percent=20
        )


def test_configuration_blocked_with_open_position(runtime):
    user, _ = runtime
    state_store.set_paper_position(_open_position())
    with pytest.raises(ValueError, match="tracked position"):
        runtime_reliability.configure_mode(
            user, mode="paper", lots=2, stop_loss_percent=10, target_profit_percent=20
        )


def test_mode_switch_blocked_while_running(runtime):
    user, _ = runtime
    runtime_reliability.mark_running()
    with pytest.raises(ValueError, match="Stop the engine"):
        runtime_reliability.switch_mode(user, "live")


def test_mode_switch_blocked_with_any_open_position(runtime):
    user, _ = runtime
    state_store.set_paper_position(_open_position())
    with pytest.raises(ValueError, match="position is open"):
        runtime_reliability.switch_mode(user, "live")


def test_mode_switch_does_not_restart(runtime):
    user, _ = runtime
    status = runtime_reliability.switch_mode(user, "live")
    assert status["engine"]["mode"] == "live"
    assert status["engine"]["running"] is False


def test_paper_reset_is_explicit_and_snapshotted(runtime):
    user, logs = runtime
    paper_portfolio.reset_paper_portfolio(125000)
    status = runtime_reliability.reset_paper_session(user, 150000)
    assert status["pnl"]["available_balance"] == 150000
    rows = [json.loads(row) for row in logs["runtime_snapshots"].read_text().splitlines()]
    assert rows[-1]["reason"] == "paper_reset"
    assert rows[-1]["final"] is True


def test_paper_reset_blocked_while_running(runtime):
    user, _ = runtime
    runtime_reliability.mark_running()
    with pytest.raises(ValueError, match="Stop the engine"):
        runtime_reliability.reset_paper_session(user)


def test_paper_reset_blocked_with_position(runtime):
    user, _ = runtime
    state_store.set_paper_position(_open_position())
    with pytest.raises(ValueError, match="Square off"):
        runtime_reliability.reset_paper_session(user)


def test_paper_reset_blocked_during_pending_exit(runtime):
    user, _ = runtime
    state_store.update_app_state(exit_state="EXIT_PENDING")
    with pytest.raises(ValueError, match="exit is pending"):
        runtime_reliability.reset_paper_session(user)


def test_square_off_already_flat_is_idempotent(runtime):
    user, _ = runtime
    first = runtime_reliability.square_off(user)
    second = runtime_reliability.square_off(user)
    assert first["exit"]["state"] == "CONFIRMED_FLAT"
    assert second["exit"]["state"] == "CONFIRMED_FLAT"


def test_square_off_submits_once_and_confirms_flat(runtime, monkeypatch):
    user, _ = runtime
    state_store.set_paper_position(_open_position())
    calls = []

    def fake_route(signal):
        calls.append(signal.signal_id)
        state_store.clear_paper_position()
        return {"success": True, "status": "ORDER_PLACED"}

    monkeypatch.setattr(runtime_reliability, "route_signal", fake_route)
    result = runtime_reliability.square_off(user)
    again = runtime_reliability.square_off(user)
    assert len(calls) == 1
    assert result["exit"]["state"] == "CONFIRMED_FLAT"
    assert again["position"]["has_open_position"] is False


def test_pending_square_off_retry_never_duplicates(runtime, monkeypatch):
    user, _ = runtime
    state_store.set_paper_position(_open_position())
    calls = []

    def pending(signal):
        calls.append(signal.signal_id)
        return {"success": True, "status": "PARTIAL_EXIT_FILLED"}

    monkeypatch.setattr(runtime_reliability, "route_signal", pending)
    first = runtime_reliability.square_off(user)
    second = runtime_reliability.square_off(user)
    assert len(calls) == 1
    assert first["exit"]["state"] == second["exit"]["state"] == "EXIT_PENDING"
    assert first["engine"]["state"] == second["engine"]["state"] == "STOPPING"


def test_failed_square_off_requires_reconciliation(runtime, monkeypatch):
    user, _ = runtime
    state_store.set_paper_position(_open_position())
    monkeypatch.setattr(
        runtime_reliability,
        "route_signal",
        lambda _signal: {"success": False, "status": "FAILED"},
    )
    status = runtime_reliability.square_off(user)
    assert status["exit"]["state"] == "RECONCILIATION_REQUIRED"
    assert status["engine"]["state"] == "STOPPING"


def test_reconciliation_retry_never_submits_a_second_exit(runtime, monkeypatch):
    user, _ = runtime
    state_store.set_paper_position(_open_position())
    calls = []

    def failed(signal):
        calls.append(signal.signal_id)
        return {"success": False, "status": "FAILED"}

    monkeypatch.setattr(runtime_reliability, "route_signal", failed)
    runtime_reliability.square_off(user)
    runtime_reliability.square_off(user)
    assert len(calls) == 1
    assert runtime_reliability.runtime_status(user)["engine"]["state"] == "STOPPING"


@pytest.mark.parametrize(
    ("age", "expected_status", "stale"),
    [(1, "ready", False), (16, "stale", True), (120, "stale", True)],
)
def test_ltp_freshness_metadata(runtime, age, expected_status, stale):
    user, _ = runtime
    checked = datetime.now(timezone.utc) - timedelta(seconds=age)
    state_store.set_paper_position(
        _open_position(
            live_pnl={
                "ltp": 105.5,
                "unrealized_pnl": 357.5,
                "status": "ready",
                "source": "test_feed",
                "last_checked_at": checked.isoformat(),
            }
        )
    )
    ltp = runtime_reliability.runtime_status(user)["position"]["ltp"]
    assert ltp["status"] == expected_status
    assert ltp["stale"] is stale
    assert ltp["received_at"] == checked.isoformat()


def test_unavailable_ltp_is_not_fabricated(runtime):
    user, _ = runtime
    state_store.set_paper_position(_open_position(live_pnl={"status": "ltp_error"}))
    ltp = runtime_reliability.runtime_status(user)["position"]["ltp"]
    assert ltp["value"] is None
    assert ltp["status"] == "ltp_error"


def test_status_includes_realized_unrealized_and_session_pnl(runtime):
    user, _ = runtime
    portfolio = paper_portfolio.get_paper_portfolio()
    portfolio.realized_pnl = 250
    paper_portfolio._write(portfolio)
    state_store.set_paper_position(_open_position(live_pnl={"unrealized_pnl": -50}))
    pnl = runtime_reliability.runtime_status(user)["pnl"]
    assert pnl == {
        "realized": 250,
        "unrealized": -50,
        "session": 200.0,
        "available_balance": 100000.0,
    }


def test_mock_account_refresh_is_controlled_and_does_not_stop_engine(runtime):
    user, _ = runtime
    runtime_reliability.mark_running()
    result = runtime_reliability.refresh_account(user)
    assert result["status"] == "UNAVAILABLE_IN_MOCK"
    assert result["engine"]["state"] == "RUNNING"


def test_status_never_exposes_plaintext_credentials(runtime, monkeypatch):
    user, _ = runtime
    monkeypatch.setattr(
        runtime_reliability.user_credential_vault,
        "user_credential_status",
        lambda _user_id: {
            "dhan_client_id_masked": "12••••78",
            "has_dhan_access_token": True,
            "dhan_token_saved_at": None,
        },
    )
    status = runtime_reliability.runtime_status(user)
    serialized = json.dumps(status)
    assert status["account"]["dhan_client_id_masked"] == "12••••78"
    assert "plaintext-token" not in serialized


@pytest.mark.parametrize(
    "reason",
    ["logout_keep_running", "manual_stop", "reconfigure", "square_off_requested"],
)
def test_snapshot_reasons_are_owner_scoped(runtime, reason):
    user, logs = runtime
    runtime_reliability.checkpoint(user, reason=reason)
    row = json.loads(logs["runtime_snapshots"].read_text().splitlines()[-1])
    assert row["owner_user_id"] == user.id_str
    assert row["reason"] == reason


def test_concurrent_stops_leave_deterministic_stopped_state(runtime):
    user, _ = runtime
    runtime_reliability.mark_running()
    threads = [threading.Thread(target=runtime_reliability.stop_engine, args=(user,)) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    status = runtime_reliability.runtime_status(user)
    assert status["engine"]["state"] == "STOPPED"
    assert status["engine"]["accepting_signals"] is False


def test_stop_snapshot_is_final(runtime):
    user, logs = runtime
    runtime_reliability.stop_engine(user)
    row = json.loads(logs["runtime_snapshots"].read_text().splitlines()[-1])
    assert row["final"] is True


def test_logout_checkpoint_is_non_final(runtime):
    user, logs = runtime
    runtime_reliability.checkpoint(user, reason="logout_keep_running")
    row = json.loads(logs["runtime_snapshots"].read_text().splitlines()[-1])
    assert row["final"] is False


def test_square_off_operation_id_is_server_generated(runtime, monkeypatch):
    user, _ = runtime
    state_store.set_paper_position(_open_position())
    observed = []

    def pending(signal):
        observed.append(signal.signal_id)
        return {"success": True, "status": "PARTIAL_EXIT_FILLED"}

    monkeypatch.setattr(runtime_reliability, "route_signal", pending)
    runtime_reliability.square_off(user)
    assert observed[0].startswith("RUNTIME_EXIT_")
    assert state_store.get_app_state()["exit_operation_id"] == observed[0]


def test_stop_does_not_change_engine_mode(runtime):
    user, _ = runtime
    runtime_reliability.stop_engine(user)
    assert runtime_reliability.runtime_status(user)["engine"]["mode"] == "paper"


def test_runtime_status_owner_matches_request_owner(runtime):
    user, _ = runtime
    assert runtime_reliability.runtime_status(user)["owner_user_id"] == user.id_str


def test_two_real_users_cannot_read_each_others_runtime(runtime):
    from app.services.execution_context import bind_user_execution_context

    first = CurrentUser(id=uuid.uuid4(), email="first@example.com")
    second = CurrentUser(id=uuid.uuid4(), email="second@example.com")
    with bind_user_execution_context(first):
        state_store.set_engine_mode("paper")
        state_store.set_paper_position(_open_position(trading_symbol="FIRST ONLY"))
    with bind_user_execution_context(second):
        state_store.set_engine_mode("paper")
        second_status = runtime_reliability.runtime_status(second)
    assert second_status["owner_user_id"] == second.id_str
    assert second_status["position"]["has_open_position"] is False
    assert second_status["position"]["trading_symbol"] is None


def _auth_client():
    from app.auth.google import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_logout_keep_running_writes_checkpoint_without_stopping(runtime, monkeypatch):
    _user, _ = runtime
    calls = {"checkpoint": 0, "stop": 0}
    monkeypatch.setattr(
        runtime_reliability,
        "checkpoint",
        lambda _user, *, reason: calls.__setitem__("checkpoint", calls["checkpoint"] + 1) or {},
    )
    monkeypatch.setattr(
        runtime_reliability,
        "stop_engine",
        lambda _user, *, reason: calls.__setitem__("stop", calls["stop"] + 1) or {},
    )
    response = _auth_client().post(
        "/api/auth/logout",
        json={"engine_action": "keep_running"},
    )
    assert response.status_code == 200
    assert response.json()["engine_action"] == "keep_running"
    assert calls == {"checkpoint": 1, "stop": 0}


def test_logout_stop_engine_uses_normal_stop(runtime, monkeypatch):
    _user, _ = runtime
    calls = []
    monkeypatch.setattr(
        runtime_reliability,
        "stop_engine",
        lambda _user, *, reason: calls.append(reason) or {},
    )
    response = _auth_client().post(
        "/api/auth/logout",
        json={"engine_action": "stop_engine"},
    )
    assert response.status_code == 200
    assert calls == ["logout"]


def test_logout_rejects_unknown_engine_action(runtime):
    response = _auth_client().post(
        "/api/auth/logout",
        json={"engine_action": "square_off"},
    )
    assert response.status_code == 400
