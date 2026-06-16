from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _current_user(model):
    from app.services.user_context import current_user_from_model

    return current_user_from_model(model)


def _signal(secret: str, signal_id: str = "fanout-1"):
    from app.config import DEFAULT_STRATEGY_CODE
    from app.schemas.signal import NormalizedSignal

    payload = {
        "secret": secret,
        "signal_id": signal_id,
        "strategy_code": DEFAULT_STRATEGY_CODE,
        "action": "ENTRY",
        "side": "BUY",
        "symbol": "NIFTY",
        "qty": 1,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
    }
    return NormalizedSignal(
        payload_format="NOVA",
        secret=secret,
        signal_id=signal_id,
        strategy_code=DEFAULT_STRATEGY_CODE,
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        qty=1,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload=payload,
    )


def test_context_credentials_are_isolated_per_user(mu_db):
    from app.services.credential_vault import get_dhan_credentials, save_dhan_credentials
    from app.services.execution_context import bind_execution_context

    alice = _current_user(make_user("fanout-alice@gmail.com"))
    bob = _current_user(make_user("fanout-bob@gmail.com"))

    with bind_execution_context(alice):
        save_dhan_credentials("ALICE-ID", "alice-token")
    with bind_execution_context(bob):
        save_dhan_credentials("BOB-ID", "bob-token")

    with bind_execution_context(alice):
        assert get_dhan_credentials().client_id == "ALICE-ID"
    with bind_execution_context(bob):
        assert get_dhan_credentials().client_id == "BOB-ID"


def test_runtime_state_is_isolated_per_user(mu_db, monkeypatch, tmp_path):
    from app.services import state_store, user_context
    from app.services.execution_context import bind_execution_context

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

    alice = _current_user(make_user("state-alice@gmail.com"))
    bob = _current_user(make_user("state-bob@gmail.com"))
    with bind_execution_context(alice):
        state_store.init_runtime_files()
        state_store.update_app_state(last_message="alice-state")
    with bind_execution_context(bob):
        state_store.init_runtime_files()
        state_store.update_app_state(last_message="bob-state")

    with bind_execution_context(alice):
        assert state_store.get_app_state()["last_message"] == "alice-state"
    with bind_execution_context(bob):
        assert state_store.get_app_state()["last_message"] == "bob-state"


def test_one_signal_routes_to_two_subscribers_with_their_lots(mu_db, monkeypatch):
    from app.services import strategy_fanout
    from app.services.execution_context import current_execution_user

    alice = make_user("route-alice@gmail.com")
    bob = make_user("route-bob@gmail.com")
    strategy_fanout.subscribe_user(
        alice.id,
        "supertrend",
        lots=1,
        execution_mode="paper_live_data",
    )
    strategy_fanout.subscribe_user(
        bob.id,
        "supertrend",
        lots=2,
        execution_mode="paper_live_data",
    )

    calls = []

    def fake_route(signal):
        calls.append((current_execution_user().email, signal.qty))
        return {"success": True, "status": "TRADED"}

    monkeypatch.setattr(strategy_fanout, "route_signal", fake_route)
    monkeypatch.setattr(strategy_fanout, "init_runtime_files", lambda: None)
    monkeypatch.setattr(
        strategy_fanout,
        "_quantity_for_subscription",
        lambda lots: lots * 75,
    )

    result = strategy_fanout.process_signal("supertrend", _signal("shared-secret"))

    assert result["subscriber_count"] == 2
    assert sorted(calls) == [
        ("route-alice@gmail.com", 75),
        ("route-bob@gmail.com", 150),
    ]


def test_live_broker_requires_and_uses_user_proxy(mu_db, monkeypatch):
    from app.config import settings
    from app.services import dhan_client
    from app.services.execution_context import bind_execution_context

    user = _current_user(make_user("proxy-user@gmail.com"))
    monkeypatch.setattr(
        settings,
        "EXECUTION_NODE_ROUTING_ENABLED",
        True,
        raising=False,
    )
    captured = {}

    def fake_http_client(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(dhan_client.httpx, "Client", fake_http_client)
    with bind_execution_context(
        user,
        proxy_url="http://proxy-user:secret@152.42.157.165:8888",
        egress_ip="152.42.157.165",
    ):
        client = dhan_client.get_broker_client("live")
        client._client(timeout=10)

    assert captured["proxy"].startswith("http://proxy-user:")
    assert "transport" not in captured


def test_authenticated_users_select_distinct_configured_egress_ips(
    mu_db,
    monkeypatch,
):
    from app.auth.dependencies import get_current_user
    from app.config import settings
    from app.routers.strategies import router
    from app.services import strategy_fanout

    alice = make_user("egress-alice@gmail.com")
    bob = make_user("egress-bob@gmail.com")
    nodes = [
        {
            "public_ip": "165.232.184.177",
            "proxy_url": "http://alice-node:secret-one@64.225.87.19:8888",
        },
        {
            "public_ip": "167.71.232.232",
            "proxy_url": "http://bob-node:secret-two@152.42.157.165:8888",
        },
    ]
    monkeypatch.setattr(
        settings,
        "EGRESS_NODES_JSON",
        json.dumps(nodes),
        raising=False,
    )
    monkeypatch.setattr(
        strategy_fanout,
        "verify_user_egress",
        lambda user_id: {
            "ok": True,
            "expected_ip": (
                "165.232.184.177"
                if user_id == alice.id
                else "167.71.232.232"
            ),
        },
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _current_user(alice)
    client = TestClient(app)

    options = client.get("/api/strategies/egress/options")
    assert options.status_code == 200
    assert [node["public_ip"] for node in options.json()["nodes"]] == [
        "165.232.184.177",
        "167.71.232.232",
    ]
    assert "secret-one" not in options.text
    assert "secret-two" not in options.text

    selected = client.post(
        "/api/strategies/egress/select",
        json={"public_ip": "165.232.184.177"},
    )
    assert selected.status_code == 200
    assert selected.json()["verification"]["ok"] is True
    assert "secret-one" not in selected.text

    app.dependency_overrides[get_current_user] = lambda: _current_user(bob)
    bob_options = client.get("/api/strategies/egress/options").json()
    assert bob_options["nodes"][0]["available"] is False
    assert bob_options["nodes"][1]["available"] is True

    conflict = client.post(
        "/api/strategies/egress/select",
        json={"public_ip": "165.232.184.177"},
    )
    assert conflict.status_code == 409

    bob_selected = client.post(
        "/api/strategies/egress/select",
        json={"public_ip": "167.71.232.232"},
    )
    assert bob_selected.status_code == 200


def test_context_free_live_router_is_blocked_when_egress_routing_is_enabled(
    monkeypatch,
):
    from app.config import settings
    from app.services.execution_router import _live_broker_client

    monkeypatch.setattr(
        settings,
        "EXECUTION_NODE_ROUTING_ENABLED",
        True,
        raising=False,
    )
    with pytest.raises(RuntimeError, match="Context-free live routing"):
        _live_broker_client()


def test_strategy_webhook_accepts_tradingview_json_and_blocks_duplicate(
    mu_db,
    monkeypatch,
):
    from app.config import DEFAULT_STRATEGY_CODE, settings
    from app.db import models
    from app.db.engine import session_scope
    from app.routers.strategies import router
    from app.services import strategy_fanout

    secret = "strategy-secret-1234567890-strong"
    monkeypatch.setattr(settings, "STRATEGY_WEBHOOK_SECRET", secret, raising=False)
    user = make_user("queued-user@gmail.com")
    strategy_fanout.subscribe_user(
        user.id,
        "supertrend",
        lots=1,
        execution_mode="signal_only",
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    body = {
        "secret": secret,
        "signal_id": "tv-shared-1",
        "strategy_code": DEFAULT_STRATEGY_CODE,
        "action": "ENTRY",
        "side": "BUY",
        "symbol": "NIFTY",
        "order_type": "MARKET",
        "product_type": "INTRADAY",
    }

    response = client.post("/api/webhook/strategy/supertrend", json=body)
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["subscriber_count"] == 1
    with session_scope() as db:
        jobs = db.query(models.StrategyExecutionJob).all()
        assert len(jobs) == 1
        assert jobs[0].user_id == user.id
        assert jobs[0].status == "queued"
        assert jobs[0].signal_payload["qty"] == 1
        assert jobs[0].signal_payload["secret"] == ""
        assert "secret" not in jobs[0].signal_payload["raw_payload"]

    duplicate = client.post("/api/webhook/strategy/supertrend", json=body)
    assert duplicate.status_code == 409


def test_durable_jobs_process_two_users_independently(mu_db, monkeypatch):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import strategy_fanout
    from app.services.execution_context import current_execution_user
    from app.workers.strategy_job_worker import process_queued_jobs_once

    alice = make_user("job-alice@gmail.com")
    bob = make_user("job-bob@gmail.com")
    strategy_fanout.subscribe_user(
        alice.id,
        "supertrend",
        lots=1,
        execution_mode="paper_live_data",
    )
    strategy_fanout.subscribe_user(
        bob.id,
        "supertrend",
        lots=2,
        execution_mode="paper_live_data",
    )
    calls = []

    def fake_route(signal):
        calls.append((current_execution_user().email, signal.qty))
        return {"success": True, "status": "TRADED"}

    monkeypatch.setattr(strategy_fanout, "route_signal", fake_route)
    monkeypatch.setattr(strategy_fanout, "init_runtime_files", lambda: None)
    monkeypatch.setattr(
        strategy_fanout,
        "_quantity_for_subscription",
        lambda lots: lots * 75,
    )

    queued = strategy_fanout.enqueue_strategy_signal(
        "supertrend",
        _signal("shared-secret", signal_id="durable-two-user-1"),
    )
    assert queued["subscriber_count"] == 2
    assert process_queued_jobs_once(limit=2) == 2
    assert sorted(calls) == [
        ("job-alice@gmail.com", 75),
        ("job-bob@gmail.com", 150),
    ]
    with session_scope() as db:
        jobs = db.query(models.StrategyExecutionJob).all()
        assert {job.status for job in jobs} == {"completed"}
        signal = db.query(models.StrategySignal).one()
        assert signal.status == "completed"


def test_new_session_replays_recent_strategy_job_result(mu_db, monkeypatch, tmp_path):
    from app.api.session import start_session
    from app.db import models
    from app.db.engine import session_scope
    from app.domain.state_machine import SetupState
    from app.services import state_store, user_context
    from app.store.redis_session import session_store

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
    state_store.init_runtime_files()
    state_store.set_engine_mode("live")
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)

    user = make_user("replay-user@gmail.com")
    signal = _signal("", signal_id="missed-eod")
    signal = signal.model_copy(update={"action": "EXIT", "source": "tradingview_eod"})
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        signal_row = models.StrategySignal(
            strategy_name="supertrend",
            signal_id=signal.signal_id,
            status="completed",
            result_summary={"subscriber_count": 1},
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
                execution_mode="real_orders",
                status="completed",
                locked_at=now,
                completed_at=now,
                result_summary={
                    "status": "blocked",
                    "execution_result": {
                        "blocked": True,
                        "status": "BLOCKED",
                        "reason": "Exit blocked: no open position exists.",
                    },
                },
            )
        )

    async def scenario():
        bootstrap = await start_session(_current_user(user))
        session = await session_store.get(str(bootstrap["sessionId"]))
        assert session is not None
        await session_store.update_state(session.id, SetupState.ENDED, {})
        return session

    session = asyncio.run(scenario())
    event_types = [item.type for item in session.events]
    signal_events = [item for item in session.events if item.type == "signal.received"]
    rejected_events = [item for item in session.events if item.type == "order.rejected"]

    assert "system.event" in event_types
    assert signal_events[-1].data["strategy"] == "Supertrend"
    assert signal_events[-1].data["action"] == "EXIT"
    assert rejected_events[-1].data["message"] == "Exit blocked: no open position exists."


def test_additive_schema_upgrades_pre_release_user_egress_table(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from scripts.init_db import ensure_additive_schema

    engine = create_engine(f"sqlite:///{tmp_path / 'old-schema.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE user_egress ("
                "id VARCHAR(36) PRIMARY KEY, "
                "user_id VARCHAR(36) NOT NULL, "
                "public_ip VARCHAR(64), "
                "proxy_url_encrypted TEXT, "
                "active BOOLEAN NOT NULL"
                ")"
            )
        )

    ensure_additive_schema(engine)
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("user_egress")
    }
    assert {
        "last_verified_at",
        "last_observed_ip",
        "verification_error",
    }.issubset(columns)
