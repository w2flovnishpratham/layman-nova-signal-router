from datetime import datetime

from app.routers import market
from app.schemas.signal import NormalizedSignal
from app.services import audit_logger, execution_router, market_chart_service, state_store


def test_signal_log_fields_keep_nifty_trade_levels():
    signal = NormalizedSignal(
        payload_format="NOVA", secret="test", signal_id="signal-1", strategy_code="NOVA",
        action="ENTRY", side="BUY", symbol="NIFTY", option_side="CE", qty=75,
        raw_payload={"nifty_price": 22932.15, "sl_level": 22873.15, "tp_level": 23020},
    )
    fields = execution_router._normalized_log_fields(signal)
    assert fields["nifty_price"] == 22932.15
    assert fields["nifty_sl_level"] == 22873.15
    assert fields["nifty_target_level"] == 23020


def test_marker_feed_exposes_price_lines(monkeypatch):
    now = datetime.now(market_chart_service._IST)
    event = {
        "phase": "after_response", "success": True, "status": "TRADED",
        "timestamp": now.isoformat(), "engine_mode": "paper", "normalized_action": "ENTRY",
        "normalized_option_side": "CE", "order_id": "ORDER-1", "signal_id": "SIGNAL-1",
        "trading_symbol": "NIFTY TEST CE", "avg_price": 165.6,
        "nifty_price": 22932.15, "nifty_sl_level": 22873.15, "nifty_target_level": 23020,
    }
    monkeypatch.setattr(audit_logger, "read_jsonl", lambda *_args, **_kwargs: [event])
    monkeypatch.setattr(state_store, "get_engine_mode", lambda *_args, **_kwargs: "paper")
    result = market.nifty_markers(mode="paper", limit=200)
    assert result["markers"] == [{
        "id": "ORDER-1", "time": int(now.timestamp()), "side": "BUY", "option_side": "CE",
        "label": "BUY CE", "price": 22932.15, "stop_price": 22873.15,
        "target_price": 23020.0, "execution_price": 165.6, "contract": "NIFTY TEST CE",
        "pnl": None, "exit_kind": "EXIT", "approximate": False,
        "mode": "paper", "source": "paper_trade",
    }]
