from __future__ import annotations

from starlette.requests import Request

from app.routers import orders
from app.services.execution_context import bind_execution_context
from app.services.user_context import current_user_from_model
from app.tests.conftest_multiuser import make_user, mu_db as mu_db_fixture  # noqa: F401


def test_accepted_request_without_persisted_position_is_not_success(monkeypatch):
    monkeypatch.setattr(orders, "get_open_position", lambda: {"has_open_position": False})
    monkeypatch.setattr(orders, "get_engine_mode", lambda **_kwargs: "live")

    response = orders._manual_response({"success": True, "status": "ORDER_PLACED"}, operation="ENTRY")

    assert response["ok"] is False
    assert response["operationState"] == "RECONCILIATION_REQUIRED"


def test_confirmed_fill_and_persisted_position_returns_position_open(monkeypatch):
    position = {
        "has_open_position": True,
        "entry_order_id": "PAPER-1",
        "entry_price": 101.25,
        "qty": 65,
        "security_id": "123",
        "trading_symbol": "NIFTY CE",
    }
    monkeypatch.setattr(orders, "get_open_position", lambda: position)
    monkeypatch.setattr(orders, "get_engine_mode", lambda **_kwargs: "live")

    response = orders._manual_response({"success": True, "status": "ORDER_PLACED"}, operation="ENTRY")

    assert response["ok"] is True
    assert response["operationState"] == "POSITION_OPEN"
    assert response["position"]["entry_order_id"] == "PAPER-1"


def test_duplicate_manual_operation_replays_one_durable_server_result(mu_db_fixture):  # noqa: F811
    calls = 0
    user = make_user("paper-idempotency@example.com")
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/orders/manual-entry",
        "headers": [(b"idempotency-key", b"same-operation")],
    })

    def execute():
        nonlocal calls
        calls += 1
        return {"ok": True, "operationState": "POSITION_OPEN", "message": "Position opened."}

    with bind_execution_context(current_user_from_model(user)):
        first = orders._run_manual_operation(
            request=request,
            operation="ENTRY",
            payload={"side": "CE"},
            execute=execute,
        )
        second = orders._run_manual_operation(
            request=request,
            operation="ENTRY",
            payload={"side": "CE"},
            execute=execute,
        )

    assert calls == 1
    assert first["operationState"] == "POSITION_OPEN"
    assert second["idempotentReplay"] is True
