from app.config import settings
from app.services import audit_logger, option_position_monitor, state_store
from app.services.credential_vault import DhanCredentials
from app.services.dhan_client import DhanListResult, DhanLtpResult, DhanOrderStatusResult
from app.services.dhan_marketfeed_ws import MarketFeedLtpResult


def setup_runtime(tmp_path, monkeypatch):
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
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


class FakeClient:
    def poll_order_status(self, **kwargs):
        raise AssertionError("entry fill polling should not run when entry_price is already present")

    def get_ltp(self, **kwargs):
        raise AssertionError("REST LTP fallback should be disabled by default")


class ConfirmingClient(FakeClient):
    def __init__(self, ltp: float):
        self.ltp = ltp

    def get_ltp(self, **kwargs):
        return DhanLtpResult(
            success=True,
            message="confirmed",
            ltp=self.ltp,
            exchange_segment=kwargs["exchange_segment"],
            security_id=kwargs["security_id"],
        )


def test_monitor_triggers_tp_exit_from_option_ltp(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
    monkeypatch.setattr(option_position_monitor, "_client", lambda: ConfirmingClient(121.5))
    monkeypatch.setattr(option_position_monitor, "ensure_marketfeed_subscription", lambda **kwargs: None)
    monkeypatch.setattr(
        option_position_monitor,
        "get_marketfeed_ltp",
        lambda **kwargs: MarketFeedLtpResult(
            success=True,
            message="ok",
            ltp=121.0,
            exchange_segment=kwargs["exchange_segment"],
            security_id=kwargs["security_id"],
            source="dhan_marketfeed_ws",
        ),
    )
    monkeypatch.setattr(
        option_position_monitor,
        "get_dhan_credentials",
        lambda: DhanCredentials(client_id="1000000001", access_token="token"),
    )
    monkeypatch.setattr(option_position_monitor, "get_webhook_secret", lambda: "test-secret")

    state_store.update_runtime_settings(
        server_side_exit_enabled=True,
        marketfeed_ws_enabled=True,
        option_ltp_source="WEBSOCKET",
        option_rest_fallback_enabled=False,
        option_sl_percent=10.0,
        option_tp_percent=20.0,
        option_ltp_poll_seconds=1.0,
        allow_exit=True,
    )
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TRADINGVIEW_NIFTY_V1",
            "symbol": "NIFTY",
            "instrument_type": "OPTIDX",
            "exchange_segment": "NSE_FNO",
            "security_id": "49081",
            "trading_symbol": "NIFTY 2026-05-28 22500 CE",
            "option_side": "CE",
            "strike": 22500.0,
            "expiry": "2026-05-28",
            "qty": 65,
            "entry_order_id": "ENTRY1",
            "entry_price": 100.0,
            "order_type": "MARKET",
            "product_type": "INTRADAY",
            "opened_at": state_store.utc_now(),
        }
    )

    routed = []

    def fake_route_signal(signal):
        routed.append(signal)
        return {"success": True, "status": "ORDER_PLACED", "order_id": "EXIT1"}

    monkeypatch.setattr("app.services.execution_router.route_signal", fake_route_signal)

    option_position_monitor.monitor_once()

    assert len(routed) == 1
    assert routed[0].action == "EXIT"
    assert routed[0].side == "SELL"
    assert routed[0].security_id == "49081"
    assert routed[0].raw_payload["exit_reason"] == "TP"
    position = state_store.get_open_position()
    assert position["live_pnl"]["ltp"] == 121.5
    assert position["live_pnl"]["source"] == "dhan_ltp_exit_confirmation"
    assert position["live_pnl"]["trigger_ltp"] == 121.0
    assert position["live_pnl"]["trigger_source"] == "dhan_marketfeed_ws"
    assert position["live_pnl"]["tp_price"] == 120.0
    assert position["server_exit"]["reason"] == "TP"


def test_monitor_rejects_unconfirmed_tp_spike(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
    monkeypatch.setattr(option_position_monitor, "_client", lambda: ConfirmingClient(105.0))
    monkeypatch.setattr(option_position_monitor, "ensure_marketfeed_subscription", lambda **kwargs: None)
    monkeypatch.setattr(
        option_position_monitor,
        "get_marketfeed_ltp",
        lambda **kwargs: MarketFeedLtpResult(
            success=True,
            message="bad spike",
            ltp=141.0,
            exchange_segment=kwargs["exchange_segment"],
            security_id=kwargs["security_id"],
            source="dhan_marketfeed_ws",
        ),
    )
    monkeypatch.setattr(
        option_position_monitor,
        "get_dhan_credentials",
        lambda: DhanCredentials(client_id="1000000001", access_token="token"),
    )
    monkeypatch.setattr(option_position_monitor, "get_webhook_secret", lambda: "test-secret")

    state_store.update_runtime_settings(
        server_side_exit_enabled=True,
        marketfeed_ws_enabled=True,
        option_ltp_source="WEBSOCKET",
        option_rest_fallback_enabled=False,
        option_sl_percent=10.0,
        option_tp_percent=20.0,
        option_ltp_poll_seconds=1.0,
        allow_exit=True,
    )
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TRADINGVIEW_NIFTY_V1",
            "symbol": "NIFTY",
            "instrument_type": "OPTIDX",
            "exchange_segment": "NSE_FNO",
            "security_id": "49081",
            "trading_symbol": "NIFTY 2026-05-28 22500 CE",
            "option_side": "CE",
            "strike": 22500.0,
            "expiry": "2026-05-28",
            "qty": 65,
            "entry_order_id": "ENTRY1",
            "entry_price": 100.0,
            "order_type": "MARKET",
            "product_type": "INTRADAY",
            "opened_at": state_store.utc_now(),
        }
    )

    routed = []
    monkeypatch.setattr("app.services.execution_router.route_signal", lambda signal: routed.append(signal))

    option_position_monitor.monitor_once()

    assert routed == []
    position = state_store.get_open_position()
    assert position["has_open_position"] is True
    assert position["live_pnl"]["ltp"] == 105.0
    assert position["live_pnl"]["status"] == "tracking"
    assert position["live_pnl"]["exit_reason"] is None
    assert position["live_pnl"]["rejected_exit_reason"] == "TP"
    assert position["live_pnl"]["trigger_ltp"] == 141.0
    assert "server_exit" not in position


def test_monitor_display_only_for_dhan_super_order_exit_levels(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
    monkeypatch.setattr(option_position_monitor, "_client", lambda: FakeClient())
    monkeypatch.setattr(option_position_monitor, "ensure_marketfeed_subscription", lambda **kwargs: None)
    monkeypatch.setattr(
        option_position_monitor,
        "get_marketfeed_ltp",
        lambda **kwargs: MarketFeedLtpResult(
            success=True,
            message="ok",
            ltp=121.0,
            exchange_segment=kwargs["exchange_segment"],
            security_id=kwargs["security_id"],
            source="dhan_marketfeed_ws",
        ),
    )
    monkeypatch.setattr(
        option_position_monitor,
        "get_dhan_credentials",
        lambda: DhanCredentials(client_id="1000000001", access_token="token"),
    )
    monkeypatch.setattr(
        "app.services.execution_router.route_signal",
        lambda signal: (_ for _ in ()).throw(AssertionError("Dhan Super Order exits must not route server exits")),
    )

    state_store.update_runtime_settings(
        server_side_exit_enabled=True,
        marketfeed_ws_enabled=True,
        option_ltp_source="WEBSOCKET",
        option_rest_fallback_enabled=False,
        option_sl_percent=10.0,
        option_tp_percent=20.0,
        option_ltp_poll_seconds=1.0,
        allow_exit=True,
    )
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TRADINGVIEW_NIFTY_V1",
            "symbol": "NIFTY",
            "instrument_type": "OPTIDX",
            "exchange_segment": "NSE_FNO",
            "security_id": "49081",
            "trading_symbol": "NIFTY 2026-05-28 22500 CE",
            "option_side": "CE",
            "strike": 22500.0,
            "expiry": "2026-05-28",
            "qty": 65,
            "entry_order_id": "SUPER1",
            "entry_price": 100.0,
            "exit_management": "DHAN_SUPER",
            "broker_sl_price": 90.0,
            "broker_tp_price": 120.0,
            "order_type": "MARKET",
            "product_type": "INTRADAY",
            "opened_at": state_store.utc_now(),
        }
    )

    option_position_monitor.monitor_once()

    position = state_store.get_open_position()
    assert position["live_pnl"]["ltp"] == 121.0
    assert position["live_pnl"]["status"] == "tp_hit"
    assert position["live_pnl"]["exit_management"] == "DHAN_SUPER"
    assert position["live_pnl"]["tp_price"] == 120.0
    assert "server_exit" not in position


def test_monitor_waits_for_websocket_tick_without_rest_fallback(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
    monkeypatch.setattr(option_position_monitor, "_client", lambda: FakeClient())
    monkeypatch.setattr(option_position_monitor, "ensure_marketfeed_subscription", lambda **kwargs: None)
    monkeypatch.setattr(option_position_monitor, "marketfeed_ws_status", lambda: {"connected": True})
    monkeypatch.setattr(
        option_position_monitor,
        "get_marketfeed_ltp",
        lambda **kwargs: MarketFeedLtpResult(
            success=False,
            message="waiting",
            ltp=None,
            exchange_segment=kwargs["exchange_segment"],
            security_id=kwargs["security_id"],
            error="ws_tick_missing",
        ),
    )
    monkeypatch.setattr(
        option_position_monitor,
        "get_dhan_credentials",
        lambda: DhanCredentials(client_id="1000000001", access_token="token"),
    )

    state_store.update_runtime_settings(
        server_side_exit_enabled=True,
        marketfeed_ws_enabled=True,
        option_ltp_source="WEBSOCKET",
        option_rest_fallback_enabled=False,
        allow_exit=True,
    )
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TRADINGVIEW_NIFTY_V1",
            "symbol": "NIFTY",
            "instrument_type": "OPTIDX",
            "exchange_segment": "NSE_FNO",
            "security_id": "49081",
            "trading_symbol": "NIFTY 2026-05-28 22500 CE",
            "qty": 65,
            "entry_order_id": "ENTRY1",
            "entry_price": 100.0,
            "opened_at": state_store.utc_now(),
        }
    )

    option_position_monitor.monitor_once()

    position = state_store.get_open_position()
    assert position["live_pnl"]["status"] == "ws_waiting"
    assert position["live_pnl"]["error"] == "ws_tick_missing"


def test_monitor_syncs_entry_price_from_positions_when_order_status_is_empty(tmp_path, monkeypatch):
    setup_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)

    class FillFallbackClient:
        def poll_order_status(self, **kwargs):
            return DhanOrderStatusResult(
                success=True,
                order_id=kwargs["order_id"],
                order_status="PENDING_CONFIRMATION",
                is_terminal=False,
                is_filled=False,
                avg_price=None,
                raw_response={"raw_response": []},
            )

        def get_order_book(self, **kwargs):
            return DhanListResult(success=True, message="ok", items=[])

        def get_positions_snapshot(self, **kwargs):
            return DhanListResult(
                success=True,
                message="ok",
                items=[{"securityId": "57048", "netQty": 65, "buyAvg": 100.0}],
            )

    monkeypatch.setattr(option_position_monitor, "_client", lambda: FillFallbackClient())
    monkeypatch.setattr(option_position_monitor, "ensure_marketfeed_subscription", lambda **kwargs: None)
    monkeypatch.setattr(
        option_position_monitor,
        "get_marketfeed_ltp",
        lambda **kwargs: MarketFeedLtpResult(
            success=True,
            message="ok",
            ltp=105.0,
            exchange_segment=kwargs["exchange_segment"],
            security_id=kwargs["security_id"],
            source="dhan_marketfeed_ws",
        ),
    )
    monkeypatch.setattr(
        option_position_monitor,
        "get_dhan_credentials",
        lambda: DhanCredentials(client_id="1000000001", access_token="token"),
    )
    published_active_trades = []
    monkeypatch.setattr(
        option_position_monitor,
        "publish_active_trade_from_sync",
        lambda position, mode: published_active_trades.append((position, mode)),
    )

    state_store.update_runtime_settings(
        server_side_exit_enabled=True,
        marketfeed_ws_enabled=True,
        option_ltp_source="AUTO",
        option_rest_fallback_enabled=True,
        option_sl_percent=10.0,
        option_tp_percent=20.0,
        allow_exit=True,
    )
    state_store.set_open_position(
        {
            "has_open_position": True,
            "strategy_code": "TRADINGVIEW_NIFTY_V1",
            "symbol": "NIFTY",
            "instrument_type": "OPTIDX",
            "exchange_segment": "NSE_FNO",
            "security_id": "57048",
            "trading_symbol": "NIFTY 02 JUN 23950 CALL",
            "option_side": "CE",
            "strike": 23950.0,
            "expiry": "2026-06-02",
            "qty": 65,
            "entry_order_id": "23226052743016",
            "entry_price": None,
            "order_type": "MARKET",
            "product_type": "INTRADAY",
            "opened_at": state_store.utc_now(),
        }
    )

    option_position_monitor.monitor_once()

    position = state_store.get_open_position()
    assert position["entry_price"] == 100.0
    assert position["entry_fill_source"] == "dhan_positions"
    assert position["live_pnl"]["ltp"] == 105.0
    assert position["live_pnl"]["status"] == "tracking"
    assert len(published_active_trades) == 1
    assert published_active_trades[0][0]["entry_price"] == 100.0
