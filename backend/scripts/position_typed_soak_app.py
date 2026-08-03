"""Verification-only uvicorn app with deterministic mock fills."""
from __future__ import annotations

import time
import os
from types import SimpleNamespace

from fastapi import Depends

from app.auth.dependencies import get_execution_scoped_user
from app.services import position_operations as ops, position_store, position_read_shadow, state_store
from app.services import execution_router, paper_broker, risk_manager
from app.services.execution_context import current_execution_user
execution_router._market_is_open = lambda: True
paper_broker._market_is_open = lambda: True
risk_manager._market_is_open = lambda: True
from app.main import app


def _position(side="CE", qty=75, order="SOAK-E1"):
    return {
        "has_open_position": True, "strategy_code": "SOAK", "symbol": "NIFTY",
        "security_id": "11001" if side == "CE" else "11002",
        "trading_symbol": f"NIFTY SOAK {side}", "option_side": side,
        "strike": 25000, "expiry": "2026-07-30", "qty": qty,
        "requested_qty": 75, "filled_qty": 75, "entry_order_id": order,
        "entry_price": 100.0, "partial_fill": False,
    }


def transition(body: dict):
    kind = body["kind"]
    signal = SimpleNamespace(signal_id=body.get("signal_id", f"SOAK-{kind}"), source="tradingview", raw_payload={})
    started = time.perf_counter()
    result = None
    ctx = ops._context()
    if kind == "setup":
        state_store.update_app_state(engine_mode="paper", engine_started=True, webhook_trading_enabled=True)
        state_store.update_runtime_settings(allow_entry=True, allow_exit=True, emergency_stop=False, global_kill_switch=False)
    elif kind == "entry":
        position = _position(body.get("side", "CE"), order=body.get("order_id", "SOAK-E1"))
        state_store.set_open_position(position)
        result = ops.record_entry(ctx, source=body.get("source", ops.STRATEGY_ENTRY), signal_id=signal.signal_id,
            order_id=position["entry_order_id"], requested_qty=75, filled_qty=75, fill_price=100.0,
            partial_fill=False, position_snapshot=position)
    elif kind == "monitor":
        position = dict(state_store.get_open_position())
        position["live_pnl"] = {"ltp": body.get("ltp", 101), "status": "tracking"}
        state_store.set_open_position(position)
    elif kind == "partial":
        position = dict(state_store.get_open_position()); position["qty"] = body.get("remaining_qty", 50)
        position["partial_exit"] = {"order_id": body.get("order_id", "SOAK-P1")}
        state_store.set_open_position(position)
        result = ops.apply_partial_exit_fill(ctx, source=body.get("source", ops.STRATEGY_EXIT),
            signal_id=signal.signal_id, exit_order_id=body.get("order_id", "SOAK-P1"),
            filled_qty=body.get("filled_qty", 25), remaining_qty=position["qty"], position_snapshot=position)
    elif kind == "close":
        state_store.clear_open_position()
        result = ops.close_position(ctx, source=body.get("source", ops.STRATEGY_EXIT), signal_id=signal.signal_id,
            exit_order_id=body.get("order_id", f"SOAK-X-{signal.signal_id}"), reason=body.get("reason"))
    elif kind == "reversal":
        current = state_store.get_open_position()
        result = ops.mark_reversal_requested(ctx, signal_id=signal.signal_id,
            from_option_side=current.get("option_side"), to_option_side=body.get("side", "PE"))
    elif kind == "reconcile":
        result = ops.reconcile_position(ctx, source=body.get("source", ops.STARTUP_RECONCILIATION),
            status=body.get("status", "verified"), checked_at=body.get("checked_at", signal.signal_id),
            position_snapshot=state_store.get_open_position())
    elif kind == "outage_entry":
        from app.config import settings
        from app.db import engine as db_engine
        real_url = os.environ["DATABASE_URL"]
        position = _position(body.get("side", "CE"), order=body.get("order_id", "SOAK-OUTAGE"))
        state_store.set_open_position(position)
        settings.DATABASE_URL = body["bad_url"]
        db_engine.reset_engine_for_tests()
        result = ops.record_entry(ctx, source=ops.STRATEGY_ENTRY, signal_id=signal.signal_id,
            order_id=position["entry_order_id"], requested_qty=75, filled_qty=75, fill_price=100.0,
            partial_fill=False, position_snapshot=position)
        settings.DATABASE_URL = real_url
        db_engine.reset_engine_for_tests()
    elif kind == "outage_read":
        from app.config import settings
        from app.db import engine as db_engine
        real_url = os.environ["DATABASE_URL"]
        settings.DATABASE_URL = body["bad_url"]
        db_engine.reset_engine_for_tests()
        result = state_store.get_open_position()
        settings.DATABASE_URL = real_url
        db_engine.reset_engine_for_tests()
    else:
        raise ValueError(kind)
    return {"ok": True, "operation_result": result,
            "typed_ms": round((time.perf_counter() - started) * 1000, 3),
            "json": state_store.get_open_position(), "health": position_store.shadow_health(),
            "read_health": position_read_shadow.health(),
            "user": str(current_execution_user().id)}


app.add_api_route("/__verify__/transition", transition, methods=["POST"],
                  dependencies=[Depends(get_execution_scoped_user)], include_in_schema=False)
