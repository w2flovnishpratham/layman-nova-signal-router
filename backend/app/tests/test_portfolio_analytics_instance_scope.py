from __future__ import annotations

from datetime import date
from typing import Any

from app.schemas.signal import NormalizedSignal
from app.services.credential_vault import DhanCredentials
from app.services import audit_logger, execution_router, portfolio_analytics, state_store
from app.services.dhan_client import DhanOrderResult


def _event(
    *,
    order_id: str,
    action: str,
    timestamp: str,
    avg_price: float,
    instance_id: str | None = None,
    qty: int = 75,
    option_side: str = "CE",
    trading_symbol: str = "NIFTY TEST CE",
    **metadata: Any,
) -> dict[str, Any]:
    event = {
        "timestamp": timestamp,
        "mode": "live",
        "phase": "after_response",
        "success": True,
        "status": "TRADED",
        "order_id": order_id,
        "avg_price": avg_price,
        "normalized_action": action,
        "normalized_qty": qty,
        "normalized_option_side": option_side,
        "trading_symbol": trading_symbol,
    }
    if instance_id is not None:
        event["instance_id"] = instance_id
    event.update(metadata)
    return event


def _signal(raw_payload: dict[str, Any]) -> NormalizedSignal:
    return NormalizedSignal(
        payload_format="NOVA",
        secret="do-not-log",
        signal_id="metadata-signal",
        strategy_code="SUPERTREND_V1",
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment="NSE_FNO",
        security_id="57046",
        trading_symbol="NIFTY TEST CE",
        option_side="CE",
        strike=25000.0,
        expiry="2026-07-09",
        qty=75,
        order_type="MARKET",
        product_type="INTRADAY",
        source="strategy_worker_adapter_v2",
        raw_payload=raw_payload,
    )


def _isolate_runtime(monkeypatch, tmp_path):
    state_root = tmp_path / "state"
    log_root = tmp_path / "logs"
    for name, filename in {
        "APP_STATE_FILE": "app_state.json",
        "OPEN_POSITION_FILE": "open_position.json",
        "PAPER_POSITION_FILE": "paper_position.json",
        "OPEN_POSITIONS_BY_INSTANCE_FILE": "open_positions_by_instance.json",
        "PAPER_PORTFOLIO_FILE": "paper_portfolio.json",
        "EXTERNAL_POSITIONS_FILE": "external_positions.json",
        "SEEN_SIGNALS_FILE": "seen_signals.json",
        "SETTINGS_FILE": "settings.json",
    }.items():
        monkeypatch.setattr(state_store, name, state_root / filename)
    log_files = {
        "webhook": log_root / "webhook_events.jsonl",
        "order": log_root / "order_events.jsonl",
        "audit": log_root / "audit_events.jsonl",
        "error": log_root / "errors.jsonl",
        "paper_orders": log_root / "paper_orders.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    state_store.init_runtime_files()
    return log_files


def test_legacy_order_logs_without_instance_id_pair_as_before():
    events = [
        _event(order_id="LEGACY-ENTRY", action="ENTRY", timestamp="2026-07-03T10:00:00+00:00", avg_price=100.0),
        _event(order_id="LEGACY-EXIT", action="EXIT", timestamp="2026-07-03T10:20:00+00:00", avg_price=112.0),
    ]

    trades, open_entry = portfolio_analytics._pair_round_trips(events)

    assert open_entry is None
    assert len(trades) == 1
    assert trades[0]["entry_order_id"] == "LEGACY-ENTRY"
    assert trades[0]["exit_order_id"] == "LEGACY-EXIT"
    assert trades[0]["realized_pnl"] == 900.0
    assert "instance_id" not in trades[0]


def test_v2_instances_pair_independently_and_preserve_metadata():
    events = [
        _event(
            order_id="A-ENTRY",
            action="ENTRY",
            timestamp="2026-07-03T10:00:00+00:00",
            avg_price=100.0,
            instance_id="A",
            strategy_code="SUPERTREND_V1",
            strategy_version_id="version-a",
            execution_mode="paper_live_data",
            v2_job_id="job-a",
            source_signal_id="source-a",
        ),
        _event(
            order_id="B-ENTRY",
            action="ENTRY",
            timestamp="2026-07-03T10:01:00+00:00",
            avg_price=200.0,
            instance_id="B",
            strategy_code="SUPERTREND_V1",
            strategy_version_id="version-b",
            execution_mode="paper_live_data",
            v2_job_id="job-b",
            source_signal_id="source-b",
        ),
        _event(order_id="A-EXIT", action="EXIT", timestamp="2026-07-03T10:10:00+00:00", avg_price=110.0, instance_id="A"),
        _event(order_id="B-EXIT", action="EXIT", timestamp="2026-07-03T10:20:00+00:00", avg_price=190.0, instance_id="B"),
    ]

    trades, open_entry = portfolio_analytics._pair_round_trips(events)

    assert open_entry is None
    assert [trade["entry_order_id"] for trade in trades] == ["A-ENTRY", "B-ENTRY"]
    assert [trade["exit_order_id"] for trade in trades] == ["A-EXIT", "B-EXIT"]
    assert trades[0]["realized_pnl"] == 750.0
    assert trades[1]["realized_pnl"] == -750.0
    assert trades[0]["instance_id"] == "A"
    assert trades[0]["strategy_code"] == "SUPERTREND_V1"
    assert trades[0]["strategy_version_id"] == "version-a"
    assert trades[0]["execution_mode"] == "paper_live_data"
    assert trades[0]["v2_job_id"] == "job-a"
    assert trades[0]["source_signal_id"] == "source-a"


def test_legacy_exit_does_not_close_v2_instance_entry():
    events = [
        _event(order_id="A-ENTRY", action="ENTRY", timestamp="2026-07-03T10:00:00+00:00", avg_price=100.0, instance_id="A"),
        _event(order_id="LEGACY-EXIT", action="EXIT", timestamp="2026-07-03T10:10:00+00:00", avg_price=110.0),
    ]

    trades, open_entry = portfolio_analytics._pair_round_trips(events)

    assert trades == []
    assert open_entry is not None
    assert open_entry["order_id"] == "A-ENTRY"


def test_v2_exit_does_not_close_legacy_entry():
    events = [
        _event(order_id="LEGACY-ENTRY", action="ENTRY", timestamp="2026-07-03T10:00:00+00:00", avg_price=100.0),
        _event(order_id="A-EXIT", action="EXIT", timestamp="2026-07-03T10:10:00+00:00", avg_price=110.0, instance_id="A"),
    ]

    trades, open_entry = portfolio_analytics._pair_round_trips(events)

    assert trades == []
    assert open_entry is not None
    assert open_entry["order_id"] == "LEGACY-ENTRY"


def test_build_portfolio_analytics_adds_metadata_to_open_position(monkeypatch):
    monkeypatch.setattr(
        portfolio_analytics,
        "read_jsonl",
        lambda _name, limit=5000: [
            _event(
                order_id="A-ENTRY",
                action="ENTRY",
                timestamp="2026-07-03T10:00:00+00:00",
                avg_price=100.0,
                instance_id="A",
                strategy_code="SUPERTREND_V1",
                strategy_version_id="version-a",
                execution_mode="paper_live_data",
                v2_job_id="job-a",
                source_signal_id="source-a",
            )
        ],
    )
    monkeypatch.setattr(portfolio_analytics, "_live_wallet_snapshot", lambda: {})

    payload = portfolio_analytics.build_portfolio_analytics(persist=False)

    assert payload["trades"] == []
    assert payload["open_position"]["entry_order_id"] == "A-ENTRY"
    assert payload["open_position"]["instance_id"] == "A"
    assert payload["open_position"]["strategy_version_id"] == "version-a"


def test_strategy_metadata_from_signal_does_not_leak_secret_or_raw_payload():
    signal = _signal(
        {
            "secret": "raw-secret",
            "instance_id": "A",
            "strategy_version_id": "version-a",
            "execution_mode": "paper_live_data",
            "v2_job_id": "job-a",
            "source_signal_id": "source-a",
        }
    )

    metadata = execution_router.strategy_metadata_from_signal(signal)

    assert metadata == {
        "instance_id": "A",
        "strategy_code": "SUPERTREND_V1",
        "strategy_version_id": "version-a",
        "execution_mode": "paper_live_data",
        "v2_job_id": "job-a",
        "source_signal_id": "source-a",
    }
    assert "secret" not in metadata
    assert "raw_payload" not in metadata


def test_execution_router_order_jsonl_gets_instance_metadata(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("paper")
    state_store.update_runtime_settings(option_exit_mode="SERVER")
    monkeypatch.setattr(execution_router, "_market_is_open", lambda: True)
    monkeypatch.setattr("app.services.shared_market_data.shared_market_data_configured", lambda: True)
    monkeypatch.setattr("app.services.shared_market_data.shared_market_data_status", lambda: {"configured": True})
    monkeypatch.setattr(
        "app.services.shared_market_data.get_shared_market_credentials",
        lambda: DhanCredentials("shared-client", "shared-token", "shared_market_data"),
    )

    class FakeBroker:
        def place_order(self, *, client_id, access_token, payload):
            return DhanOrderResult(
                success=True,
                order_id="PAPER-META-1",
                status="TRADED",
                avg_price=100.0,
                raw_response={"orderStatus": "TRADED", "avgPrice": 100.0},
            )

    monkeypatch.setattr(execution_router, "get_broker_client", lambda _mode: FakeBroker())

    execution_router._place_order(
        _signal(
            {
                "instance_id": "A",
                "strategy_version_id": "version-a",
                "execution_mode": "paper_live_data",
                "v2_job_id": "job-a",
                "source_signal_id": "source-a",
            }
        ),
        75,
        "ENTRY",
        runtime=state_store.get_runtime_settings(),
    )

    events = audit_logger.read_jsonl("order", limit=10)
    before_request = next(event for event in events if event.get("phase") == "before_request")
    after_response = next(event for event in events if event.get("phase") == "after_response")

    for event in (before_request, after_response):
        assert event["instance_id"] == "A"
        assert event["strategy_code"] == "SUPERTREND_V1"
        assert event["strategy_version_id"] == "version-a"
        assert event["execution_mode"] == "paper_live_data"
        assert event["v2_job_id"] == "job-a"
        assert event["source_signal_id"] == "source-a"
        assert "raw_payload" not in event
        assert event.get("secret") is None


def test_chart_markers_tolerate_old_and_instance_metadata(monkeypatch):
    from app.routers import market

    events = [
        _event(order_id="LEGACY-ENTRY", action="ENTRY", timestamp="2026-07-07T04:00:00+00:00", avg_price=100.0),
        _event(
            order_id="A-ENTRY",
            action="ENTRY",
            timestamp="2026-07-07T04:05:00+00:00",
            avg_price=101.0,
            instance_id="A",
            strategy_code="SUPERTREND_V1",
            strategy_version_id="version-a",
            execution_mode="paper_live_data",
            v2_job_id="job-a",
            source_signal_id="source-a",
        ),
    ]
    monkeypatch.setattr(audit_logger, "read_jsonl", lambda _name, limit=1000: events)
    monkeypatch.setattr("app.services.market_chart_service.trading_date_ist", lambda: date(2026, 7, 7))

    payload = market.nifty_markers(mode="live", limit=200)

    assert payload["mode"] == "live"
    assert len(payload["markers"]) == 2
    assert [marker["side"] for marker in payload["markers"]] == ["BUY", "BUY"]
