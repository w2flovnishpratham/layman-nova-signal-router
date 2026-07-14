"""Phase 3A private webhook worker execution: engine resolution, lot
resolution, contract resolution, no-op semantics, reversal routing, and
worker-time instance revalidation."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.schemas.signal import NormalizedSignal
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_private_webhook import (  # noqa: F401
    _issue_token,
    _make_instance,
    _payload,
    _post,
    client,
    webhook_enabled,
)


@pytest.fixture
def per_user_runtime(mu_db, monkeypatch, tmp_path):
    """Per-user isolated runtime state dirs (mirrors test_strategy_fanout)."""
    from app.services import audit_logger, paper_broker, paper_portfolio, state_store, user_context

    state_root = tmp_path / "state"
    log_root = tmp_path / "logs"
    monkeypatch.setattr(state_store, "RUNTIME_STATE_DIR", state_root)
    monkeypatch.setattr(state_store, "RUNTIME_LOG_DIR", log_root)
    monkeypatch.setattr(user_context, "RUNTIME_STATE_DIR", state_root)
    monkeypatch.setattr(user_context, "RUNTIME_LOG_DIR", log_root)
    for name, filename in {
        "APP_STATE_FILE": "app_state.json",
        "OPEN_POSITION_FILE": "open_position.json",
        "PAPER_POSITION_FILE": "paper_position.json",
        "PAPER_PORTFOLIO_FILE": "paper_portfolio.json",
        "EXTERNAL_POSITIONS_FILE": "external_positions.json",
        "SEEN_SIGNALS_FILE": "seen_signals.json",
        "SETTINGS_FILE": "settings.json",
    }.items():
        monkeypatch.setattr(state_store, name, state_root / filename)
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", state_root / "paper_portfolio.json")
    log_files = {
        "webhook": log_root / "webhook_events.jsonl",
        "order": log_root / "order_events.jsonl",
        "audit": log_root / "audit_events.jsonl",
        "error": log_root / "errors.jsonl",
        "paper_orders": log_root / "paper_orders.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(paper_broker, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    monkeypatch.setattr(paper_broker, "_market_is_open", lambda: True)


def _start_paper_engine(user, *, allow_entry: bool = True, allowed_option_side: str = "BOTH"):
    from app.services import state_store
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    current = current_user_from_model(user)
    with bind_user_execution_context(current):
        state_store.init_runtime_files()
        state_store.update_app_state(
            engine_mode="paper", engine_started=True, webhook_trading_enabled=True
        )
        state_store.update_runtime_settings(
            allow_entry=allow_entry,
            allow_exit=True,
            allowed_option_side=allowed_option_side,
            emergency_stop=False,
            global_kill_switch=False,
        )
    return current


def _fake_contract(option_side: str = "CE"):
    return {
        "security_id": "57046" if option_side == "CE" else "57047",
        "trading_symbol": f"NIFTY TEST {option_side}",
        "strike": 25000.0,
        "expiry": "2026-07-16",
    }


@pytest.fixture
def deterministic_paper_market(monkeypatch):
    """Deterministic ATM contract + paper LTP so PaperBroker fills offline."""
    from app.services import paper_broker, private_webhook_execution, shared_market_data
    from app.services.credential_vault import DhanCredentials
    from app.services.dhan_client import DhanLtpResult

    monkeypatch.setattr(
        private_webhook_execution,
        "_resolve_entry_contract",
        lambda option_side, lots: _fake_contract(option_side),
    )

    class _QuoteClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get_ltp(self, **_kwargs) -> DhanLtpResult:
            return DhanLtpResult(success=True, message="quote", ltp=100.0)

    monkeypatch.setattr(paper_broker, "RealDhanClient", _QuoteClient)
    fake_creds = DhanCredentials("shared-client", "shared-token", "shared_market_data")
    monkeypatch.setattr(paper_broker, "get_shared_market_credentials", lambda: fake_creds)
    monkeypatch.setattr(shared_market_data, "get_shared_market_credentials", lambda: fake_creds)
    monkeypatch.setattr(shared_market_data, "shared_market_data_configured", lambda: True)


def _ingest(client, token, **overrides) -> str:
    body = _payload(**overrides)
    response = _post(client, token, body)
    assert response.status_code == 202, response.json()
    return body["signal_id"]


def _run_worker(limit: int = 5) -> int:
    from app.workers.strategy_job_worker import process_queued_jobs_once

    return process_queued_jobs_once(limit=limit)


def _job_rows(instance_id: str):
    from sqlalchemy import select

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        rows = db.scalars(
            select(models.StrategyExecutionJob)
            .where(models.StrategyExecutionJob.strategy_name == f"instance:{instance_id}")
            .order_by(models.StrategyExecutionJob.created_at.asc())
        ).all()
        return [
            {"status": row.status, "signal_id": row.signal_id, "result": row.result_summary}
            for row in rows
        ]


def _position_for(user):
    from app.services import state_store
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    with bind_user_execution_context(current_user_from_model(user)):
        return state_store.get_open_position()


@pytest.fixture
def worker_enabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "STRATEGY_JOB_WORKER_ENABLED", True, raising=False)


# ---------------------------------------------------------------------------
# Trading flow (paper, through the real execution router + PaperBroker)
# ---------------------------------------------------------------------------

def test_flat_buy_ce_places_paper_entry(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    user = make_user("trade-entry@gmail.com")
    instance_id = _make_instance(user, lots=2)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    _ingest(client, token, action="BUY_CE")
    assert _run_worker() == 1

    jobs = _job_rows(instance_id)
    assert jobs[0]["status"] == "completed"
    result = jobs[0]["result"]
    assert result["status"] == "completed", result
    execution = result["execution_result"]
    assert execution["success"] is True
    assert execution.get("order_id", "").startswith("PAPER-")
    position = _position_for(user)
    assert position["has_open_position"] is True
    assert position["option_side"] == "CE"
    # Server lots x lot size — payload never carries quantity.
    from app.routers.setup import current_nifty_lot_size

    assert position["qty"] == 2 * current_nifty_lot_size()


def test_duplicate_same_side_entry_is_no_op(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    user = make_user("trade-noop@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    _ingest(client, token, action="BUY_CE")
    assert _run_worker() == 1
    _ingest(client, token, action="BUY_CE")
    assert _run_worker() == 1

    jobs = _job_rows(instance_id)
    # The worker's pre-dispatch idempotency check short-circuits: no risk
    # reservation, no run row, no broker call.
    assert jobs[1]["result"]["no_op"] is True
    assert jobs[1]["result"]["reason"] == "ALREADY_IN_CE"
    # Still exactly one open position, no scale-in.
    position = _position_for(user)
    from app.routers.setup import current_nifty_lot_size

    assert position["qty"] == current_nifty_lot_size()


def test_opposite_signal_exits_before_reversing(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    user = make_user("trade-reversal@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    _ingest(client, token, action="BUY_CE")
    assert _run_worker() == 1
    _ingest(client, token, action="BUY_PE")
    assert _run_worker() == 1

    jobs = _job_rows(instance_id)
    execution = jobs[1]["result"]["execution_result"]
    assert execution["status"] == "REVERSAL_ORDER_PLACED", execution
    reversal = execution["reversal"]
    assert reversal["from_option_side"] == "CE"
    assert reversal["to_option_side"] == "PE"
    position = _position_for(user)
    assert position["option_side"] == "PE"


def test_exit_closes_position_and_exit_while_flat_is_no_op(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    user = make_user("trade-exit@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    _ingest(client, token, action="BUY_CE")
    assert _run_worker() == 1
    _ingest(client, token, action="EXIT")
    assert _run_worker() == 1
    position = _position_for(user)
    assert position["has_open_position"] is False

    _ingest(client, token, action="EXIT")
    assert _run_worker() == 1
    jobs = _job_rows(instance_id)
    assert jobs[2]["result"]["reason"] == "ALREADY_FLAT"
    assert jobs[2]["result"]["no_op"] is True


def test_hold_never_reaches_worker_or_broker(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    user = make_user("trade-hold@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    _ingest(client, token, action="HOLD")
    assert _run_worker() == 0
    assert _job_rows(instance_id) == []
    assert _position_for(user)["has_open_position"] is False


def test_contract_resolution_failure_fails_job_without_order(client, per_user_runtime, worker_enabled, monkeypatch):
    from app.services import private_webhook_execution

    monkeypatch.setattr(private_webhook_execution, "_resolve_entry_contract", lambda side, lots: None)
    user = make_user("trade-contract-fail@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    _ingest(client, token, action="BUY_CE")
    assert _run_worker() == 1
    jobs = _job_rows(instance_id)
    execution = jobs[0]["result"]["execution_result"]
    assert execution["block_code"] == "CONTRACT_RESOLUTION_FAILED"
    assert execution["success"] is False
    assert _position_for(user)["has_open_position"] is False


def test_lots_change_while_flat_applies_to_next_entry(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    from app.services import strategy_instance_service

    user = make_user("trade-lots@gmail.com")
    instance_id = _make_instance(user, lots=1)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    # Queue with lots=1, then update lots BEFORE the worker claims the job:
    # the worker must use the fresh value (revalidation at processing time).
    _ingest(client, token, action="BUY_CE")
    strategy_instance_service.update_lots(user.id, instance_id, 4)
    assert _run_worker() == 1
    from app.routers.setup import current_nifty_lot_size

    assert _position_for(user)["qty"] == 4 * current_nifty_lot_size()


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------

def test_no_active_engine_blocks_entry_without_order(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    from app.services import state_store
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    user = make_user("engine-none@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    # Engine mode selected but engine NOT started.
    with bind_user_execution_context(current_user_from_model(user)):
        state_store.init_runtime_files()
        state_store.update_app_state(engine_mode="paper", engine_started=False, webhook_trading_enabled=False)

    _ingest(client, token, action="BUY_CE")
    assert _run_worker() == 1
    jobs = _job_rows(instance_id)
    execution = jobs[0]["result"]["execution_result"]
    assert execution["blocked"] is True
    assert "WEBHOOK_TRADING_ENABLED" in execution["reason"]
    assert _position_for(user)["has_open_position"] is False


def test_entries_paused_by_side_filter(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    user = make_user("engine-paused-side@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user, allowed_option_side="PE")

    _ingest(client, token, action="BUY_CE")
    assert _run_worker() == 1
    execution = _job_rows(instance_id)[0]["result"]["execution_result"]
    assert execution["blocked"] is True
    assert "side filter" in execution["reason"]


def test_entries_paused_by_allow_entry_flag(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    user = make_user("engine-paused-entry@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user, allow_entry=False)

    _ingest(client, token, action="BUY_CE")
    assert _run_worker() == 1
    execution = _job_rows(instance_id)[0]["result"]["execution_result"]
    assert execution["blocked"] is True


def test_instance_stopped_after_queue_is_rejected_at_claim(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    from app.services import strategy_instance_service

    user = make_user("engine-stopped@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    _ingest(client, token, action="BUY_CE")
    strategy_instance_service.stop_instance(user.id, instance_id)
    assert _run_worker() == 1
    result = _job_rows(instance_id)[0]["result"]
    assert result["status"] == "rejected"
    assert result["reason"] == "INACTIVE_INSTANCE"
    assert _position_for(user)["has_open_position"] is False


@pytest.mark.parametrize(
    ("open_side", "blocked_action"),
    [("CE", "BUY_PE"), ("PE", "BUY_CE")],
)
def test_paused_blocks_reversal_but_allows_exit(
    client,
    per_user_runtime,
    deterministic_paper_market,
    worker_enabled,
    open_side,
    blocked_action,
):
    from app.services import strategy_instance_service

    user = make_user(f"paused-{open_side.lower()}@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    _ingest(client, token, action=f"BUY_{open_side}")
    assert _run_worker() == 1
    strategy_instance_service.pause_instance(user.id, instance_id)

    _ingest(client, token, action=blocked_action)
    assert _run_worker() == 1
    blocked = _job_rows(instance_id)[1]["result"]
    assert blocked["status"] == "NO_OP"
    assert blocked["reason"] == "INSTANCE_PAUSED_ENTRIES_BLOCKED"
    assert _position_for(user)["option_side"] == open_side

    _ingest(client, token, action="EXIT")
    assert _run_worker() == 1
    assert _position_for(user)["has_open_position"] is False


def test_pause_after_queue_blocks_entry_and_hold_remains_audit_only(
    client, per_user_runtime, deterministic_paper_market, worker_enabled
):
    from app.services import strategy_instance_service

    user = make_user("paused-before-claim@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    _ingest(client, token, action="BUY_CE")
    strategy_instance_service.pause_instance(user.id, instance_id)
    assert _run_worker() == 1
    assert _job_rows(instance_id)[0]["result"]["reason"] == "INSTANCE_PAUSED_ENTRIES_BLOCKED"
    assert _position_for(user)["has_open_position"] is False

    _ingest(client, token, action="HOLD")
    assert _run_worker() == 0
    assert len(_job_rows(instance_id)) == 1


def test_stop_rejects_open_position_then_succeeds_after_paused_exit(
    client, per_user_runtime, deterministic_paper_market, worker_enabled
):
    from app.services import strategy_instance_service

    user = make_user("stop-flat-only@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)
    _ingest(client, token, action="BUY_CE")
    assert _run_worker() == 1

    with pytest.raises(strategy_instance_service.InstanceError) as caught:
        strategy_instance_service.stop_instance(user.id, instance_id)
    assert caught.value.status_code == 409
    assert caught.value.code == "POSITION_OPEN"

    strategy_instance_service.pause_instance(user.id, instance_id)
    _ingest(client, token, action="EXIT")
    assert _run_worker() == 1
    assert _position_for(user)["has_open_position"] is False
    assert strategy_instance_service.stop_instance(user.id, instance_id)["status"] == "stopped"


def test_invalid_lots_rejected_at_claim(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    from sqlalchemy import update

    from app.db import models
    from app.db.engine import session_scope

    user = make_user("engine-lots@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    _ingest(client, token, action="BUY_CE")
    with session_scope() as db:  # corrupt lots below the service-level floor
        db.execute(
            update(models.StrategyInstance)
            .where(models.StrategyInstance.id == uuid.UUID(instance_id))
            .values(current_lots=0)
        )
    assert _run_worker() == 1
    result = _job_rows(instance_id)[0]["result"]
    assert result["status"] == "rejected"
    assert result["reason"] == "INVALID_LOTS"


def test_live_instance_blocked_while_private_live_flag_disabled(client, per_user_runtime, worker_enabled, monkeypatch):
    from sqlalchemy import update

    from app.db import models
    from app.db.engine import session_scope

    user = make_user("engine-live@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)
    _ingest(client, token, action="BUY_CE")
    # Flip the instance to real_orders after ingestion (bypasses entitlement
    # gates deliberately): the worker must still refuse under the phase flag.
    with session_scope() as db:
        db.execute(
            update(models.StrategyInstance)
            .where(models.StrategyInstance.id == uuid.UUID(instance_id))
            .values(execution_mode="real_orders")
        )
    assert _run_worker() == 1
    result = _job_rows(instance_id)[0]["result"]
    assert result["status"] == "blocked"
    assert result["reason"] == "PRIVATE_WEBHOOK_LIVE_EXECUTION_DISABLED"
    assert _position_for(user)["has_open_position"] is False


def test_worker_retry_of_money_mode_job_fails_safe(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    from sqlalchemy import update

    from app.db import models
    from app.db.engine import session_scope
    from app.workers import strategy_job_worker

    user = make_user("engine-retry@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)
    _ingest(client, token, action="BUY_CE")
    # Simulate a recovered job whose first attempt may have reached the broker.
    with session_scope() as db:
        db.execute(
            update(models.StrategyExecutionJob)
            .where(models.StrategyExecutionJob.strategy_name == f"instance:{instance_id}")
            .values(attempts=1)
        )
    assert strategy_job_worker.process_queued_jobs_once(limit=5) == 1
    rows = _job_rows(instance_id)
    assert rows[0]["status"] == "failed"
    assert _position_for(user)["has_open_position"] is False


# ---------------------------------------------------------------------------
# Tenant isolation through execution
# ---------------------------------------------------------------------------

def test_two_users_execute_with_their_own_lots_and_state(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    user_a = make_user("exec-tenant-a@gmail.com")
    user_b = make_user("exec-tenant-b@gmail.com")
    instance_a = _make_instance(user_a, lots=1)
    instance_b = _make_instance(user_b, lots=3)
    token_a = _issue_token(user_a, instance_a)
    token_b = _issue_token(user_b, instance_b)
    _start_paper_engine(user_a)
    _start_paper_engine(user_b)

    _ingest(client, token_a, action="BUY_CE")
    _ingest(client, token_b, action="BUY_PE")
    assert _run_worker() == 2

    from app.routers.setup import current_nifty_lot_size

    position_a = _position_for(user_a)
    position_b = _position_for(user_b)
    assert position_a["option_side"] == "CE" and position_a["qty"] == 1 * current_nifty_lot_size()
    assert position_b["option_side"] == "PE" and position_b["qty"] == 3 * current_nifty_lot_size()


def test_json_remains_position_read_authority(client, per_user_runtime, deterministic_paper_market, worker_enabled, monkeypatch):
    """PostgreSQL shadow rows never control execution decisions."""
    from app.config import settings

    # Shadow writes stay off; execution still works purely on JSON authority.
    monkeypatch.setattr(settings, "POSITION_DB_TYPED_WRITES_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "POSITION_DB_READ_SHADOW_ENABLED", False, raising=False)

    user = make_user("authority-json@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)
    _ingest(client, token, action="BUY_CE")
    assert _run_worker() == 1
    assert _position_for(user)["has_open_position"] is True

    from sqlalchemy import select

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        shadow_rows = db.scalars(select(models.StrategyInstancePosition)).all()
        assert shadow_rows == []  # execution never depended on the PG ledger
