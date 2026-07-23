"""Real monitor-versus-exit race: exercises the actual option_position_monitor
iteration, not the state-store CAS primitives directly.

The exit is injected inside the monitor's own quote-resolution call — i.e. it
completes after the monitor has read the open position but before it writes its
snapshot back. The monitor's write must be rejected by compare-and-set so a
closed position is never resurrected.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.config import settings
from app.services import audit_logger, option_position_monitor, state_store
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


def test_real_monitor_iteration_cannot_resurrect_a_position_exited_mid_quote(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    _base_runtime(monkeypatch)
    opened = _open_identified_position()
    pid, ver = opened["position_id"], opened["position_version"]

    # The monitor has already read the open position (P/V). The exit completes
    # DURING quote resolution: clear the authoritative position to confirmed-flat
    # at a higher version, then hand the monitor a healthy quote so it proceeds to
    # its snapshot write with the now-stale in-memory position.
    def exit_then_quote(**_kwargs):
        closed, flat = state_store.close_open_position_cas(
            position_id=pid, position_version=ver
        )
        assert closed is True and flat["has_open_position"] is False
        return _quote(133.35)

    monkeypatch.setattr(option_position_monitor, "_authoritative_quote", exit_then_quote)

    option_position_monitor.monitor_once()

    # The real monitor iteration must NOT have resurrected the closed position.
    final = state_store.get_open_position()
    assert final["has_open_position"] is False
    assert final["last_closed_position_id"] == pid
    # And no stale live_pnl leaked back onto a flat position.
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
