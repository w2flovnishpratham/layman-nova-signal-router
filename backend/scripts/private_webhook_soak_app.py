"""Verification-only uvicorn app for the Phase 3A private-webhook soak.

Same pattern as position_typed_soak_app.py: patch the wall-clock market-hours
guards so the soak is time-of-day independent, then expose one authenticated
helper route that starts the calling user's paper engine (the chat/setup flow
equivalent, without driving the whole UI).
"""
from __future__ import annotations

from fastapi import Depends

from app.services import atm_ltp_service, execution_router, paper_broker, risk_manager
from app.services import state_store

execution_router._market_is_open = lambda: True
paper_broker._market_is_open = lambda: True
risk_manager._market_is_open = lambda: True
atm_ltp_service._market_is_open = lambda: True

from app.auth.dependencies import get_execution_scoped_user  # noqa: E402
from app.main import app  # noqa: E402


def start_paper_engine() -> dict:
    state_store.init_runtime_files()
    state_store.update_app_state(engine_mode="paper", engine_started=True, webhook_trading_enabled=True)
    state_store.update_runtime_settings(
        allow_entry=True,
        allow_exit=True,
        marketfeed_ws_enabled=False,
        option_ltp_source="REST",
        emergency_stop=False,
        global_kill_switch=False,
    )
    return {"ok": True, "engine_mode": "paper"}


def read_position() -> dict:
    return {"ok": True, "position": state_store.get_open_position()}


app.add_api_route(
    "/__soak__/engine", start_paper_engine, methods=["POST"],
    dependencies=[Depends(get_execution_scoped_user)], include_in_schema=False,
)
app.add_api_route(
    "/__soak__/position", read_position, methods=["GET"],
    dependencies=[Depends(get_execution_scoped_user)], include_in_schema=False,
)
