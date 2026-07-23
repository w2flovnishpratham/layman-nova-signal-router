"""Real monitor-versus-exit race: exercises the actual option_position_monitor
iteration, not the state-store CAS primitives directly.

The exit is injected inside the monitor's own quote-resolution call — i.e. it
completes after the monitor has read the open position but before it writes its
snapshot back. The monitor's write must be rejected by compare-and-set so a
closed position is never resurrected.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

from app.config import settings
from app.schemas.signal import NormalizedSignal
from app.services import audit_logger, execution_router, option_position_monitor, state_store
from app.services.credential_vault import DhanCredentials


def setup_runtime(tmp_path, monkeypatch):
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    log_files = {
        "webhook": log_dir / "webhook_events.jsonl",
        "order": log_dir / "order_events.jsonl",
        "audit": log_dir / "audit_events.jsonl",
        "error": log_dir / "errors.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    state_store.init_runtime_files()


def _quote(ltp, *, source="DHAN_WEBSOCKET"):
    return SimpleNamespace(
        success=ltp is not None, message="ok", ltp=ltp, source=source,
        status="FRESH", error=None, received_at=None, age_seconds=0.1,
    )


def _open_identified_position():
    return state_store.set_open_position(
        state_store.stamp_new_open_position({
            "has_open_position": True,
            "strategy_code": "TRADINGVIEW_NIFTY_V1",
            "symbol": "NIFTY",
            "instrument_type": "OPTIDX",
            "exchange_segment": "NSE_FNO",
            "security_id": "49081",
            "trading_symbol": "NIFTY 21 JUL 24250 CE",
            "option_side": "CE",
            "strike": 24250.0,
            "expiry": "2026-07-21",
            "qty": 65,
            "entry_order_id": "PAPER-FCB6BB1C1701",
            "entry_price": 135.0,
            "order_type": "MARKET",
            "product_type": "INTRADAY",
            "opened_at": state_store.utc_now(),
        })
    )


def _base_runtime(monkeypatch):
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
    monkeypatch.setattr(option_position_monitor, "_client", lambda: SimpleNamespace())
    monkeypatch.setattr(option_position_monitor, "_market_is_open", lambda: True)
    monkeypatch.setattr(option_position_monitor, "_publish_market_snapshot_from_monitor", lambda: False)
    monkeypatch.setattr(
        option_position_monitor,
        "get_dhan_credentials",
        lambda: DhanCredentials(client_id="1000000001", access_token="token"),
    )
    monkeypatch.setattr(option_position_monitor, "get_webhook_secret", lambda: "test-secret")
    # SL/TP far away so a healthy in-band quote never routes the monitor's own exit.
    state_store.update_runtime_settings(
        server_side_exit_enabled=False,
        marketfeed_ws_enabled=True,
        option_ltp_source="WEBSOCKET",
        option_rest_fallback_enabled=False,
        option_sl_percent=90.0,
        option_tp_percent=90.0,
        option_ltp_poll_seconds=1.0,
        allow_exit=True,
    )


def _exit_signal(signal_id: str = "MANUAL-EXIT") -> NormalizedSignal:
    return NormalizedSignal(
        payload_format="NOVA",
        secret="test-secret",
        signal_id=signal_id,
        strategy_code="TRADINGVIEW_NIFTY_V1",
        action="EXIT",
        side="SELL",
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment="NSE_FNO",
        security_id="49081",
        trading_symbol="NIFTY 21 JUL 24250 CE",
        option_side="CE",
        strike=24250.0,
        expiry="2026-07-21",
        qty=65,
        order_type="MARKET",
        product_type="INTRADAY",
        source="manual_exit",
        raw_payload={},
    )


def test_real_monitor_thread_cannot_resurrect_position_closed_by_exit_router(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    _base_runtime(monkeypatch)
    state_store.update_app_state(engine_mode="paper")
    opened = _open_identified_position()
    quote_started = threading.Event()
    exit_finished = threading.Event()
    results: list[dict] = []

    def blocked_quote(**_kwargs):
        quote_started.set()
        assert exit_finished.wait(timeout=5), "exit thread did not finish"
        return _quote(133.35)

    monkeypatch.setattr(option_position_monitor, "_authoritative_quote", blocked_quote)
    monkeypatch.setattr(
        execution_router,
        "_place_order",
        lambda *_args, **_kwargs: {
            "success": True,
            "status": "TRADED",
            "order_id": "PAPER-EXIT-1",
            "avg_price": 133.35,
        },
    )
    monkeypatch.setattr(execution_router, "_cancel_super_order_exit_legs", lambda _p: [])
    monkeypatch.setattr(execution_router, "refresh_wallet_snapshot", lambda **_k: {})
    monkeypatch.setattr(execution_router.position_operations, "on_position_closed", lambda *_a, **_k: None)
    monkeypatch.setattr(settings, "REQUIRE_MARKET_HOURS", False)

    monitor_thread = threading.Thread(target=option_position_monitor.monitor_once)

    def exit_position() -> None:
        assert quote_started.wait(timeout=5), "monitor never entered quote resolution"
        try:
            results.append(execution_router.route_exit_signal(_exit_signal()))
        finally:
            exit_finished.set()

    exit_thread = threading.Thread(target=exit_position)
    monitor_thread.start()
    exit_thread.start()
    monitor_thread.join(timeout=10)
    exit_thread.join(timeout=10)

    assert not monitor_thread.is_alive()
    assert not exit_thread.is_alive()
    assert results[0]["success"] is True
    final = state_store.get_open_position()
    assert final["has_open_position"] is False
    assert final["last_closed_position_id"] == opened["position_id"]
    assert final["last_exit_operation"]["status"] == "COMPLETED"
    assert final.get("live_pnl") in (None, {})


def test_real_monitor_iteration_patches_when_position_unchanged(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    _base_runtime(monkeypatch)
    opened = _open_identified_position()

    # No exit during quote resolution: the healthy quote must update live_pnl and
    # bump the position version via compare-and-set.
    monkeypatch.setattr(
        option_position_monitor, "_authoritative_quote", lambda **_k: _quote(140.0)
    )
    option_position_monitor.monitor_once()

    final = state_store.get_open_position()
    assert final["has_open_position"] is True
    assert final["position_id"] == opened["position_id"]
    assert final["position_version"] == opened["position_version"] + 1
    assert final["live_pnl"]["ltp"] == 140.0


def test_monitor_retains_last_valid_ltp_across_quote_error_and_market_close(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    _base_runtime(monkeypatch)
    _open_identified_position()

    market_open = {"value": True}
    monkeypatch.setattr(option_position_monitor, "_market_is_open", lambda: market_open["value"])
    quotes = iter(
        [
            _quote(140.0),
            SimpleNamespace(
                success=False,
                message="temporary feed gap",
                ltp=None,
                source="DHAN_WEBSOCKET",
                status="STALE",
                error="stale_quote",
                received_at=None,
                age_seconds=4.0,
            ),
        ]
    )
    monkeypatch.setattr(option_position_monitor, "_authoritative_quote", lambda **_k: next(quotes))

    option_position_monitor.monitor_once()
    valid = state_store.get_open_position()["live_pnl"]
    assert valid["ltp"] == 140.0

    option_position_monitor.monitor_once()
    errored = state_store.get_open_position()["live_pnl"]
    assert errored["status"] == "ltp_error"
    assert errored["ltp"] == 140.0
    assert errored["unrealized_pnl"] == valid["unrealized_pnl"]

    market_open["value"] = False
    option_position_monitor.monitor_once()
    closed_market = state_store.get_open_position()["live_pnl"]
    assert closed_market["status"] == "market_closed"
    assert closed_market["ltp"] == 140.0
    assert closed_market["unrealized_pnl"] == valid["unrealized_pnl"]
