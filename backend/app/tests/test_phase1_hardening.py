# ruff: noqa: F811
from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _current_user(model):
    from app.services.user_context import current_user_from_model

    return current_user_from_model(model)


def _grant_real_order_entitlement(user) -> None:
    from app.db import models
    from app.db.engine import session_scope

    now = models.utcnow()
    with session_scope() as db:
        db.add(
            models.UserEntitlement(
                user_id=user.id,
                plan_code="nova_live",
                status="active",
                source="payment_provider",
                starts_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=30),
                live_orders_enabled=True,
                static_ip_enabled=False,
                strategy_access_enabled=True,
                metadata_json={"test": "phase1_egress_guard"},
                created_at=now,
                updated_at=now,
            )
        )


def _signal(signal_id: str = "phase1-signal"):
    from app.config import DEFAULT_STRATEGY_CODE
    from app.schemas.signal import NormalizedSignal

    payload = {
        "secret": "shared-secret",
        "signal_id": signal_id,
        "strategy_code": DEFAULT_STRATEGY_CODE,
        "action": "ENTRY",
        "side": "BUY",
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "exchange_segment": "NSE_FNO",
        "security_id": "57046",
        "trading_symbol": "NIFTY TEST CE",
        "option_side": "CE",
        "strike": 24000,
        "expiry": "2026-06-25",
        "qty": 1,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
    }
    return NormalizedSignal(
        payload_format="NOVA",
        secret=payload["secret"],
        signal_id=signal_id,
        strategy_code=DEFAULT_STRATEGY_CODE,
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment="NSE_FNO",
        security_id="57046",
        trading_symbol="NIFTY TEST CE",
        option_side="CE",
        strike=24000,
        expiry="2026-06-25",
        qty=1,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload=payload,
    )


def test_alembic_baseline_creates_current_schema(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from scripts.init_db import EXPECTED_TABLES
    from sqlalchemy import create_engine, inspect

    from app.config import settings
    from app.db import engine as db_engine

    db_path = tmp_path / "alembic-baseline.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    db_engine.reset_engine_for_tests()
    try:
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        command.upgrade(config, "head")
    finally:
        db_engine.reset_engine_for_tests()

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.issubset(tables)
    assert "portfolio_trades" in tables
    assert "portfolio_snapshots" in tables


def test_background_runner_rejects_multiple_web_workers(monkeypatch):
    from app.config import settings
    from app.main import validate_background_worker_runner_configuration

    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    monkeypatch.setattr(settings, "BACKGROUND_WORKER_RUNNER_ENABLED", True, raising=False)

    with pytest.raises(RuntimeError, match="Multiple web workers"):
        validate_background_worker_runner_configuration()


def test_background_runner_allows_api_only_extra_workers(monkeypatch):
    from app.config import settings
    from app.main import validate_background_worker_runner_configuration

    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    monkeypatch.setattr(settings, "BACKGROUND_WORKER_RUNNER_ENABLED", False, raising=False)

    validate_background_worker_runner_configuration()


def test_user_cannot_read_another_users_session(mu_db):
    from app.api import session as session_api
    from app.auth.dependencies import get_current_user
    from app.domain.state_machine import SetupState
    from app.store.redis_session import session_store

    alice = make_user("phase1-session-alice@gmail.com")
    bob = make_user("phase1-session-bob@gmail.com")

    async def create_session():
        return await session_store.create(user_id=str(alice.id), state=SetupState.LIVE)

    session = asyncio.run(create_session())

    app = FastAPI()
    app.include_router(session_api.router)
    app.dependency_overrides[get_current_user] = lambda: _current_user(bob)
    client = TestClient(app)
    assert client.get(f"/api/session/{session.id}").status_code == 404

    app.dependency_overrides[get_current_user] = lambda: _current_user(alice)
    allowed = client.get(f"/api/session/{session.id}")
    assert allowed.status_code == 200
    assert allowed.json()["sessionId"] == session.id

    async def end_session():
        await session_store.update_state(session.id, SetupState.ENDED, {})

    asyncio.run(end_session())


def test_duplicate_strategy_signal_creates_one_job_and_one_execution(mu_db, monkeypatch):
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.routers.strategies import router
    from app.services import private_webhook_execution, strategy_fanout
    from app.workers.strategy_job_worker import process_queued_jobs_once

    secret = "phase1-strategy-secret-1234567890"
    monkeypatch.setattr(settings, "STRATEGY_WEBHOOK_SECRET", secret, raising=False)
    user = make_user("phase1-duplicate-user@gmail.com")
    strategy_fanout.subscribe_user(user.id, "supertrend", lots=1, execution_mode="paper_live_data")
    from app.tests.test_strategy_fanout import _bind_paper_configuration

    _bind_paper_configuration(user, lots=1)

    calls = []
    monkeypatch.setattr(
        strategy_fanout,
        "route_signal",
        lambda signal, **_kwargs: calls.append(signal.signal_id)
        or {"success": True},
    )
    monkeypatch.setattr(strategy_fanout, "init_runtime_files", lambda: None)
    # The broadcast signal carries no contract (see build_broadcast_signal);
    # the worker's per-subscriber enricher resolves one before routing.
    monkeypatch.setattr(
        private_webhook_execution,
        "_resolve_entry_contract",
        lambda option_side, lots: {
            "security_id": "TEST-SECURITY-ID",
            "trading_symbol": "NIFTY TEST",
            "strike": 25000.0,
            "expiry": "2026-12-31",
        },
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    body = {
        "secret": secret,
        "signal_id": "phase1-duplicate-signal",
        "action": "BUY_CE",
        "signal_time": "2026-07-31T09:00:00Z",
    }

    assert client.post("/api/webhook/strategy/supertrend", json=body).status_code == 202
    assert client.post("/api/webhook/strategy/supertrend", json=body).status_code == 409
    assert process_queued_jobs_once(limit=10) == 1

    with session_scope() as db:
        assert db.query(models.StrategyExecutionJob).count() == 1
    assert calls == ["phase1-duplicate-signal"]


def test_paper_order_path_never_calls_real_dhan_order_api(tmp_path, monkeypatch):
    from app.services import (
        credential_vault,
        execution_router,
        paper_broker,
        paper_portfolio,
        state_store,
    )
    from app.services.credential_vault import DhanCredentials
    from app.services.dhan_client import DhanLtpResult
    from app.services.shared_market_data import shared_market_data_status

    state_dir = tmp_path / "runtime_state"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
    state_store.init_runtime_files()
    state_store.set_engine_mode("paper")
    state_store.update_runtime_settings(paper_starting_balance=100000, paper_slippage_percent=0.0)
    paper_portfolio.reset_paper_portfolio(100000)

    shared_creds = DhanCredentials("shared-client", "shared-token", "shared_market_data")

    class MarketDataOnlyDhanClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_ltp(self, **_kwargs):
            return DhanLtpResult(success=True, message="quote", ltp=100.0)

        def place_order(self, **_kwargs):
            raise AssertionError("paper mode must not call Dhan place_order")

        def place_super_order(self, **_kwargs):
            raise AssertionError("paper mode must not call Dhan place_super_order")

    monkeypatch.setattr(paper_broker, "RealDhanClient", MarketDataOnlyDhanClient)
    monkeypatch.setattr(
        paper_broker,
        "get_quote_snapshot",
        lambda **_kwargs: {"ltp": 100.0, "source": "DHAN_WEBSOCKET", "status": "FRESH", "stale": False},
    )
    monkeypatch.setattr("app.services.shared_market_data.get_shared_market_credentials", lambda: shared_creds)
    monkeypatch.setattr("app.services.shared_market_data.shared_market_data_configured", lambda: True)
    monkeypatch.setattr(
        "app.services.shared_market_data.shared_market_data_status",
        lambda: {**shared_market_data_status(), "configured": True},
    )

    result = execution_router._place_order(_signal("phase1-paper-order"), 10, "ENTRY")

    assert result["success"] is True
    assert result["order_id"].startswith("PAPER-")


def test_real_orders_fail_closed_without_verified_egress(mu_db, monkeypatch):
    from app.config import settings
    from app.services import strategy_fanout

    user = make_user("phase1-unverified-egress@gmail.com")
    _grant_real_order_entitlement(user)
    strategy_fanout.set_user_egress(
        user.id,
        public_ip="1.1.1.1",
        proxy_url="http://proxy.example.com:8888",
        active=True,
        allow_proxy_host_mismatch=True,
    )
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True, raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False, raising=False)
    monkeypatch.setattr(settings, "EXECUTION_NODE_ROUTING_ENABLED", True, raising=False)
    monkeypatch.setattr(
        strategy_fanout,
        "route_signal",
        lambda _signal: (_ for _ in ()).throw(AssertionError("unverified live egress must not route")),
    )

    result = strategy_fanout.dispatch_signal_job(
        user_id=user.id,
        strategy_name="supertrend",
        lots=1,
        execution_mode="real_orders",
        signal=_signal("phase1-egress-block"),
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "egress_not_verified"


def test_recovered_execution_job_fails_closed_instead_of_double_executing(mu_db, monkeypatch):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import strategy_fanout
    from app.workers.strategy_job_worker import process_queued_jobs_once

    user = make_user("phase1-retry-user@gmail.com")
    signal = _signal("phase1-worker-retry")
    with session_scope() as db:
        signal_row = models.StrategySignal(
            strategy_name="supertrend",
            signal_id=signal.signal_id,
            status="processing",
        )
        db.add(signal_row)
        db.flush()
        db.add(
            models.StrategyExecutionJob(
                strategy_signal_id=signal_row.id,
                user_id=user.id,
                strategy_name="supertrend",
                signal_id=signal.signal_id,
                signal_payload=signal.model_dump(mode="json"),
                lots=1,
                execution_mode="paper_live_data",
                status="queued",
                attempts=1,
            )
        )

    monkeypatch.setattr(
        strategy_fanout,
        "route_signal",
        lambda _signal: (_ for _ in ()).throw(AssertionError("recovered job must not execute again")),
    )

    assert process_queued_jobs_once(limit=1) == 1

    with session_scope() as db:
        job = db.query(models.StrategyExecutionJob).one()
        assert job.status == "failed"
        assert job.attempts == 2
        assert "not re-run" in (job.last_error or "")
