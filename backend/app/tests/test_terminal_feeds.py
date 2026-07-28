# ruff: noqa: F401, F811
from __future__ import annotations

import pytest

from app.db import models
from app.db.engine import session_scope
from app.services import (
    audit_logger,
    signals_feed,
    state_store,
    terminal_feeds,
    user_context,
)
from app.services.execution_context import bind_execution_context
from app.services.user_context import current_user_from_model
from app.tests.conftest_multiuser import make_user
from app.tests.conftest_multiuser import mu_db as mu_db_fixture


def _isolate_logs(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "RUNTIME_STATE_DIR", state_dir)
    monkeypatch.setattr(state_store, "RUNTIME_LOG_DIR", log_dir)
    monkeypatch.setattr(user_context, "RUNTIME_STATE_DIR", state_dir)
    monkeypatch.setattr(user_context, "RUNTIME_LOG_DIR", log_dir)
    logs = {
        "webhook": log_dir / "webhook_events.jsonl",
        "order": log_dir / "order_events.jsonl",
        "paper_orders": log_dir / "paper_orders.jsonl",
        "audit": log_dir / "audit_events.jsonl",
        "error": log_dir / "errors.jsonl",
        "runtime_snapshots": log_dir / "runtime_snapshots.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", logs)
    monkeypatch.setattr(audit_logger, "LOG_FILES", logs)
    monkeypatch.setattr(terminal_feeds, "read_jsonl", audit_logger.read_jsonl)
    monkeypatch.setattr(signals_feed, "list_signals", lambda *_args, **_kwargs: {"items": []})


def _order_event(order_id: str, *, mode: str, signal_id: str = "SIGNAL-1") -> dict:
    return {
        "event": "ORDER_FILLED",
        "signal_id": signal_id,
        "order_id": order_id,
        "operation_id": f"OP-{order_id}",
        "trading_symbol": "NIFTY TEST CE",
        "normalized_action": "ENTRY",
        "requested_qty": 75,
        "filled_qty": 50,
        "average_price": 101.5,
        "charges": 7.25,
        "slippagePercent": 0.1,
        "status": "TRADED",
        "mode": mode,
    }


def test_terminal_feeds_are_owner_mode_and_cursor_scoped(
    mu_db_fixture,
    tmp_path,
    monkeypatch,
):
    _isolate_logs(tmp_path, monkeypatch)
    alice_model = make_user("terminal-alice@example.com")
    bob_model = make_user("terminal-bob@example.com")
    alice = current_user_from_model(alice_model)
    bob = current_user_from_model(bob_model)

    with bind_execution_context(alice):
        audit_logger.log_order_event(_order_event("ALICE-1", mode="paper"))
        audit_logger.log_audit_event("ENGINE_STARTED", "Alice engine started.")
    with bind_execution_context(bob):
        audit_logger.log_order_event(_order_event("BOB-1", mode="paper"))
        audit_logger.log_audit_event("ENGINE_STARTED", "Bob engine started.")

    with bind_execution_context(alice):
        executions = terminal_feeds.executions(alice.id, mode="paper", limit=10)
        assert [item["order_id"] for item in executions["items"]] == ["ALICE-1"]
        assert executions["items"][0]["requested_qty"] == 75
        assert executions["items"][0]["filled_qty"] == 50
        assert executions["items"][0]["partial_fill"] is True
        assert executions["items"][0]["charges"] == 7.25
        assert terminal_feeds.executions(alice.id, mode="live", limit=10)["items"] == []
        with pytest.raises(terminal_feeds.TerminalFeedScopeError):
            terminal_feeds.executions(bob.id, limit=10)

        initial = terminal_feeds.engine_log(alice.id, limit=1)
        audit_logger.log_audit_event("ENGINE_STOPPED", "Alice engine stopped.")
        delta = terminal_feeds.engine_log(
            alice.id,
            cursor=initial["next_cursor"],
            limit=10,
        )
        assert [item["event_type"] for item in delta["items"]] == ["ENGINE_STOPPED"]
        assert delta["reconciliation_status"] == "CURRENT"
        reset = terminal_feeds.engine_log(alice.id, cursor="invalid", limit=10)
        assert reset["reconciliation_status"] == "CURSOR_RESET"


def test_activity_projects_correlated_lifecycle(mu_db_fixture, tmp_path, monkeypatch):
    _isolate_logs(tmp_path, monkeypatch)
    owner = current_user_from_model(make_user("terminal-lifecycle@example.com"))
    monkeypatch.setattr(
        signals_feed,
        "list_signals",
        lambda *_args, **_kwargs: {
            "items": [
                {
                    "id": "webhook-1",
                    "event_id": "SIGNAL-1",
                    "received_at": "2026-07-26T09:00:00+00:00",
                    "strategy_name": "supertrend",
                    "processed_status": "queued",
                    "error": None,
                    "summary": {
                        "action": "ENTRY",
                        "symbol": "NIFTY",
                        "qty": 75,
                        "mode": "paper",
                    },
                }
            ]
        },
    )
    with bind_execution_context(owner):
        audit_logger.log_order_event(_order_event("ORDER-1", mode="paper"))
        page = terminal_feeds.activity(owner.id, mode="paper", limit=10)

    signal_row = next(item for item in page["items"] if item["id"] == "signal:webhook-1")
    stages = [stage["stage"] for stage in signal_row["lifecycle"]]
    assert "SIGNAL_RECEIVED" in stages
    assert "ORDER_FILLED" in stages


def test_active_alert_cannot_be_acknowledged_but_historical_can(
    mu_db_fixture,
    tmp_path,
    monkeypatch,
):
    _isolate_logs(tmp_path, monkeypatch)
    owner = current_user_from_model(make_user("terminal-alert@example.com"))
    runtime = {
        "mode": "paper",
        "position": {
            "has_open_position": True,
            "ltp": {
                "value": None,
                "stale": True,
                "received_at": "2026-07-26T09:00:00+00:00",
                "message": "Live option quote unavailable.",
            },
        },
    }
    with bind_execution_context(owner):
        state_store.set_engine_mode("paper")
        audit_logger.log_error_event("RISK_BLOCK", "Daily loss cap reached.")
        page = terminal_feeds.alerts(owner.id, runtime=runtime, mode="paper")
        active_id = page["active_items"][0]["id"]
        historical_id = page["historical_items"][0]["id"]
        with pytest.raises(terminal_feeds.ActiveAlertAcknowledgementError):
            terminal_feeds.acknowledge(owner.id, [active_id], runtime=runtime)
        assert terminal_feeds.acknowledge(owner.id, [historical_id], runtime=runtime) == 1
        refreshed = terminal_feeds.alerts(owner.id, runtime=runtime, mode="paper")

    assert refreshed["active_items"][0]["acknowledged"] is False
    assert refreshed["historical_items"][0]["acknowledged"] is True


def test_executions_survive_without_runtime_json_logs(
    mu_db_fixture,
    tmp_path,
    monkeypatch,
):
    _isolate_logs(tmp_path, monkeypatch)
    owner = current_user_from_model(make_user("terminal-durable@example.com"))
    with session_scope() as db:
        signal = models.StrategySignal(
            strategy_name="private:test-instance",
            signal_id="TV-DURABLE-1",
            status="completed",
        )
        db.add(signal)
        db.flush()
        db.add(
            models.StrategyExecutionJob(
                strategy_signal_id=signal.id,
                user_id=owner.id,
                strategy_name=signal.strategy_name,
                signal_id=signal.signal_id,
                signal_payload={
                    "action": "ENTRY",
                    "option_side": "CE",
                    "trading_symbol": "NIFTY TEST CE",
                    "qty": 75,
                },
                lots=1,
                execution_mode="paper_live_data",
                status="completed",
                result_summary={
                    "status": "TRADED",
                    "order_id": "PAPER-AUTO-DURABLE",
                    "filled_qty": 75,
                    "avg_price": 101.25,
                },
            )
        )
        db.add(
            models.LiveOrderIntent(
                user_id=owner.id,
                scope="manual_paper_operation",
                idempotency_key="manual-durable-1",
                payload_hash="a" * 64,
                status="completed",
                action="EXIT",
                symbol="NIFTY TEST CE",
                result_summary={
                    "ok": True,
                    "status": "TRADED",
                    "order_id": "PAPER-MANUAL-DURABLE",
                    "filled_qty": 75,
                    "avg_price": 105.0,
                },
                intent_metadata={"mode": "paper", "manual_order": True},
            )
        )

    with bind_execution_context(owner):
        page = terminal_feeds.executions(owner.id, mode="paper", limit=10)
        activity = terminal_feeds.activity(owner.id, mode="paper", limit=10)

    assert {item["order_id"] for item in page["items"]} == {
        "PAPER-AUTO-DURABLE",
        "PAPER-MANUAL-DURABLE",
    }
    assert all(item["durable"] is True for item in page["items"])
    automated = next(
        item for item in page["items"] if item["order_id"] == "PAPER-AUTO-DURABLE"
    )
    assert automated["configuration_revision"] is None
    assert any(item.get("durable") is True for item in activity["items"])
