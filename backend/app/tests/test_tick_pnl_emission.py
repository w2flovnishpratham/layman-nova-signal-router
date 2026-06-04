from __future__ import annotations

import asyncio
from typing import Any

from app.domain.state_machine import SetupState
from app.services import audit_logger, chat_event_publisher, option_position_monitor, state_store
from app.services.credential_vault import DhanCredentials
from app.services.dhan_marketfeed_ws import MarketFeedLtpResult
from app.store.redis_session import session_store


TICK_EVENT_KEYS = {"symbol", "securityId", "ltp", "pnl", "pnlPct", "mode"}


class _FakeClient:
    def poll_order_status(self, **_kwargs: Any) -> None:
        raise AssertionError("entry fill polling should not run when entry_price is already present")

    def get_ltp(self, **_kwargs: Any) -> None:
        raise AssertionError("REST LTP fallback should not run when the websocket quote succeeds")


def _isolate_runtime(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    log_files = {
        "webhook": log_dir / "webhook_events.jsonl",
        "order": log_dir / "order_events.jsonl",
        "paper_orders": log_dir / "paper_orders.jsonl",
        "audit": log_dir / "audit_events.jsonl",
        "error": log_dir / "errors.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)


def _run_monitor_tick(
    tmp_path,
    monkeypatch,
    *,
    mode: str,
    ltp: float,
    server_side_exit_enabled: bool = True,
) -> dict[str, Any]:
    _isolate_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode(mode)
    state_store.update_runtime_settings(
        server_side_exit_enabled=server_side_exit_enabled,
        marketfeed_ws_enabled=True,
        option_ltp_source="WEBSOCKET",
        option_rest_fallback_enabled=False,
        option_disable_sl=False,
        option_sl_percent=10.0,
        option_tp_percent=20.0,
    )
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TRADINGVIEW_NIFTY_V1",
            "symbol": "NIFTY",
            "instrument_type": "OPTIDX",
            "exchange_segment": "NSE_FNO",
            "security_id": "49081",
            "trading_symbol": "NIFTY TEST CE",
            "option_side": "CE",
            "strike": 22500.0,
            "expiry": "2026-06-25",
            "qty": 10,
            "entry_order_id": f"{mode.upper()}-ENTRY",
            "entry_price": 100.0,
            "order_type": "MARKET",
            "product_type": "INTRADAY",
            "opened_at": state_store.utc_now(),
        }
    )

    monkeypatch.setattr(option_position_monitor, "_client", lambda: _FakeClient())
    monkeypatch.setattr(option_position_monitor, "ensure_marketfeed_subscription", lambda **_kwargs: None)
    monkeypatch.setattr(
        option_position_monitor,
        "get_marketfeed_ltp",
        lambda **kwargs: MarketFeedLtpResult(
            success=True,
            message="ok",
            ltp=ltp,
            exchange_segment=kwargs["exchange_segment"],
            security_id=kwargs["security_id"],
            source="test_marketfeed",
        ),
    )
    monkeypatch.setattr(
        option_position_monitor,
        "get_dhan_credentials",
        lambda: DhanCredentials(client_id="1000000001", access_token="token"),
    )
    if not server_side_exit_enabled:
        monkeypatch.setattr(
            option_position_monitor,
            "_route_server_exit",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("server-side exit must remain disabled")
            ),
        )

    async def scenario() -> dict[str, Any]:
        session = await session_store.create(state=SetupState.LIVE)
        chat_event_publisher.bind_chat_event_loop(asyncio.get_running_loop())
        try:
            option_position_monitor.monitor_once()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            updated = await session_store.get(session.id)
            assert updated is not None
            ticks = [item for item in updated.events if item.type == "tick.pnl"]
            assert len(ticks) == 1
            return ticks[0].data
        finally:
            chat_event_publisher.clear_chat_event_loop()
            await session_store.update_state(session.id, SetupState.ENDED, {})

    return asyncio.run(scenario())


def test_paper_monitor_emits_tick_pnl_after_ltp_read(tmp_path, monkeypatch):
    tick = _run_monitor_tick(tmp_path, monkeypatch, mode="paper", ltp=105.0)

    assert set(tick) == TICK_EVENT_KEYS
    assert tick == {
        "symbol": "NIFTY TEST CE",
        "securityId": "49081",
        "ltp": 105.0,
        "pnl": 50.0,
        "pnlPct": 5.0,
        "mode": "paper",
    }


def test_live_monitor_emits_tick_pnl_after_ltp_read(tmp_path, monkeypatch):
    tick = _run_monitor_tick(tmp_path, monkeypatch, mode="live", ltp=97.5)

    assert set(tick) == TICK_EVENT_KEYS
    assert tick == {
        "symbol": "NIFTY TEST CE",
        "securityId": "49081",
        "ltp": 97.5,
        "pnl": -25.0,
        "pnlPct": -2.5,
        "mode": "live",
    }


def test_monitor_emits_tick_when_server_side_exits_are_disabled(tmp_path, monkeypatch):
    tick = _run_monitor_tick(
        tmp_path,
        monkeypatch,
        mode="paper",
        ltp=125.0,
        server_side_exit_enabled=False,
    )

    assert tick["ltp"] == 125.0
    assert tick["pnl"] == 250.0
    assert tick["mode"] == "paper"
