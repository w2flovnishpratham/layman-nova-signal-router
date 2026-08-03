# ruff: noqa: F401, F811
from __future__ import annotations

import threading
from types import SimpleNamespace

from starlette.requests import Request

from app.routers import orders
from app.services import audit_logger, execution_router, paper_portfolio, state_store
from app.services.execution_context import bind_execution_context
from app.services.user_context import current_user_from_model
from app.tests.conftest_multiuser import make_user
from app.tests.conftest_multiuser import mu_db as mu_db_fixture


def _isolate_position_runtime(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    log_files = {
        "webhook": log_dir / "webhook_events.jsonl",
        "order": log_dir / "order_events.jsonl",
        "paper_orders": log_dir / "paper_orders.jsonl",
        "audit": log_dir / "audit_events.jsonl",
        "error": log_dir / "errors.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    state_store.init_runtime_files()


def _seed_position(monkeypatch) -> dict:
    monkeypatch.setattr(orders, "current_nifty_lot_size", lambda: 65)
    state_store.set_engine_mode("paper")
    state_store.update_runtime_settings(max_qty_per_order=130)
    paper_portfolio.reset_paper_portfolio(100_000)
    portfolio = paper_portfolio.get_paper_portfolio()
    portfolio.available_balance = 93_500.0
    portfolio.utilized_amount = 6_500.0
    portfolio.open_trade = {
        "symbol": "NIFTY TEST CE",
        "qty": 65,
        "entry_price": 100.0,
        "entry_value": 6_500.0,
        "entry_order_id": "PAPER-ENTRY",
    }
    paper_portfolio._write(portfolio)
    state_store.set_open_position(
        {
            "has_open_position": True,
            "symbol": "NIFTY",
            "trading_symbol": "NIFTY TEST CE",
            "security_id": "12345",
            "option_side": "CE",
            "qty": 65,
            "entry_price": 100.0,
            "exit_management": "SERVER",
            "active_exit_levels": {
                "source": "manual",
                "stopLossPrice": 90.0,
                "targetPrice": 120.0,
            },
            "stop_loss_percent": 10.0,
            "take_profit_percent": 20.0,
            "live_pnl": {"entry_price": 100.0, "ltp": 110.0, "qty": 65},
        }
    )
    return state_store.ensure_open_position_identity()


def _confirmed_fill(**kwargs):
    return {
        "success": True,
        "status": "TRADED",
        "order_id": f"FILL-{kwargs['operation_id']}",
        "fill_price": 110.0,
        "filled_qty": kwargs["qty"],
        "charges": 0.0,
    }


def test_exit_level_units_normalize_against_confirmed_entry():
    assert orders._normalize_exit_levels(
        unit="PERCENTAGE",
        stop_value=10,
        target_value=20,
        reference_price=100,
    ) == (90.0, 120.0)
    assert orders._normalize_exit_levels(
        unit="POINTS",
        stop_value=8,
        target_value=12,
        reference_price=100,
    ) == (92.0, 112.0)
    assert orders._normalize_exit_levels(
        unit="ABSOLUTE_TRIGGER",
        stop_value=95,
        target_value=125,
        reference_price=100,
    ) == (95.0, 125.0)


def test_add_lots_requires_a_confirmed_fresh_fill(tmp_path, monkeypatch):
    _isolate_position_runtime(tmp_path, monkeypatch)
    opened = _seed_position(monkeypatch)
    monkeypatch.setattr(
        orders,
        "confirm_position_adjustment_fill",
        lambda **_kwargs: {
            "success": False,
            "status": "REJECTED",
            "error": "Authoritative option LTP is unavailable.",
        },
    )

    result = orders._apply_paper_position_adjustment(
        operation="add",
        lots=1,
        expected_version=opened["position_version"],
        operation_id="NO-FILL",
        protection_mode="KEEP_EXISTING",
        custom_stop_loss=None,
        custom_target=None,
    )

    assert result["ok"] is False
    assert result["operationState"] == "REJECTED_NO_FRESH_QUOTE"
    assert state_store.get_open_position()["qty"] == 65
    assert paper_portfolio.get_paper_portfolio().open_trade["qty"] == 65


def test_add_lots_protection_choices_and_weighted_average(tmp_path, monkeypatch):
    _isolate_position_runtime(tmp_path, monkeypatch)
    opened = _seed_position(monkeypatch)
    monkeypatch.setattr(orders, "confirm_position_adjustment_fill", _confirmed_fill)

    result = orders._apply_paper_position_adjustment(
        operation="add",
        lots=1,
        expected_version=opened["position_version"],
        operation_id="RECALCULATE",
        protection_mode="RECALCULATE_FROM_AVERAGE",
        custom_stop_loss=None,
        custom_target=None,
    )

    assert result["ok"] is True
    assert result["entryPrice"] == 105.0
    assert result["activeExitLevels"]["stopLossPrice"] == 94.5
    assert result["activeExitLevels"]["targetPrice"] == 126.0
    assert paper_portfolio.get_paper_portfolio().open_trade["entry_price"] == 105.0
    custom = orders._add_lots_protection(
        position=state_store.get_open_position(),
        live_pnl={},
        mode="CUSTOM",
        new_average=105.0,
        confirmed_fill=110.0,
        custom_stop=92.0,
        custom_target=130.0,
    )
    assert isinstance(custom, dict)
    assert custom["stopLossPrice"] == 92.0
    assert custom["targetPrice"] == 130.0


def test_concurrent_add_lots_applies_exactly_one_cas_and_ledger_change(tmp_path, monkeypatch):
    _isolate_position_runtime(tmp_path, monkeypatch)
    opened = _seed_position(monkeypatch)
    barrier = threading.Barrier(2)

    def concurrent_fill(**kwargs):
        barrier.wait(timeout=5)
        return _confirmed_fill(**kwargs)

    monkeypatch.setattr(orders, "confirm_position_adjustment_fill", concurrent_fill)
    results: list[dict] = []
    lock = threading.Lock()

    def add(operation_id: str) -> None:
        result = orders._apply_paper_position_adjustment(
            operation="add",
            lots=1,
            expected_version=opened["position_version"],
            operation_id=operation_id,
            protection_mode="KEEP_EXISTING",
            custom_stop_loss=None,
            custom_target=None,
        )
        with lock:
            results.append(result)

    threads = [threading.Thread(target=add, args=(f"ADD-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sum(result["ok"] for result in results) == 1
    assert sum(result.get("operationState") == "RECONCILIATION_REQUIRED" for result in results) == 1
    assert state_store.get_open_position()["qty"] == 130
    assert paper_portfolio.get_paper_portfolio().open_trade["qty"] == 130


def test_duplicate_add_lots_key_replays_one_server_operation(mu_db_fixture):
    user = make_user("position-add-idempotency@example.com")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/orders/active-position/add-lots",
            "headers": [(b"idempotency-key", b"same-add")],
        }
    )
    calls = 0

    def execute():
        nonlocal calls
        calls += 1
        return {"ok": True, "operationState": "TRADED", "qty": 130}

    with bind_execution_context(current_user_from_model(user)):
        first = orders._run_manual_operation(
            request=request,
            operation="ADD_LOTS",
            payload={"lots": 1, "expectedPositionVersion": 1},
            execute=execute,
        )
        replay = orders._run_manual_operation(
            request=request,
            operation="ADD_LOTS",
            payload={"lots": 1, "expectedPositionVersion": 1},
            execute=execute,
        )

    assert calls == 1
    assert first["qty"] == 130
    assert replay["idempotentReplay"] is True


def test_live_partial_protection_failure_rolls_back_confirmed_leg(monkeypatch):
    calls: list[dict] = []
    outcomes = [
        SimpleNamespace(success=True, order_id="SUPER-1", status="MODIFIED", error=None),
        SimpleNamespace(success=False, order_id="SUPER-1", status="REJECTED", error="stop rejected"),
        SimpleNamespace(success=True, order_id="SUPER-1", status="MODIFIED", error=None),
    ]

    class Broker:
        def modify_super_order(self, **kwargs):
            calls.append(kwargs["payload"])
            return outcomes.pop(0)

    monkeypatch.setattr(execution_router, "get_engine_mode", lambda: "live")
    monkeypatch.setattr(
        execution_router,
        "get_dhan_credentials",
        lambda: SimpleNamespace(client_id="client", access_token="token"),
    )
    monkeypatch.setattr(execution_router, "require_verified_live_egress", lambda: None)
    monkeypatch.setattr(execution_router, "_live_broker_client", Broker)
    monkeypatch.setattr(execution_router, "log_order_event", lambda _event: None)

    result = execution_router.apply_manual_super_order_exit_levels(
        {
            "exit_management": "DHAN_SUPER",
            "entry_order_id": "SUPER-1",
            "active_exit_levels": {"stopLossPrice": 90.0, "targetPrice": 120.0},
        },
        stop_loss_price=95.0,
        target_price=125.0,
    )

    assert result["ok"] is False
    assert result["rollback_confirmed"] is True
    assert result["reconciliation_required"] is False
    assert calls[-1]["legName"] == "TARGET_LEG"
    assert calls[-1]["targetPrice"] == 120.0
