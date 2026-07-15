from __future__ import annotations

from datetime import datetime, timedelta

from app.services import market_chart_service as charts
from app.services import shared_market_data as smd
from app.services.credential_vault import DhanCredentials


def test_chart_date_uses_today_after_market_close():
    now = datetime(2026, 7, 10, 16, 20, tzinfo=charts._IST)

    assert charts.chart_trading_date_ist(now) == now.date()


def test_chart_date_uses_previous_weekday_before_open_or_weekend():
    monday_preopen = datetime(2026, 7, 13, 8, 30, tzinfo=charts._IST)
    sunday = datetime(2026, 7, 12, 12, 0, tzinfo=charts._IST)

    assert charts.chart_trading_date_ist(monday_preopen).isoformat() == "2026-07-10"
    assert charts.chart_trading_date_ist(sunday).isoformat() == "2026-07-10"


def test_chart_auth_failure_does_not_refresh_shared_market_token(monkeypatch):
    calls = {"refresh": 0}

    class FakeDhanClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_intraday_candles(self, **kwargs):
            return {"success": False, "status_code": 401, "error": "Unauthorized"}

    def fake_refresh(**kwargs):
        calls["refresh"] += 1
        raise AssertionError("chart fetch must not refresh the shared token")

    monkeypatch.setattr(charts, "RealDhanClient", FakeDhanClient)
    monkeypatch.setattr(charts, "market_data_credentials", lambda: DhanCredentials("client", "token", "shared_market_data"))
    monkeypatch.setattr(charts, "shared_market_data_configured", lambda: True)
    monkeypatch.setattr(smd, "refresh_shared_token_after_auth_failure", fake_refresh)

    payload = charts._fetch_candles("5", "5m", datetime.now(charts._IST).date() - timedelta(days=1))

    assert payload["status"] == "unavailable"
    assert payload["reason"] == "market_data_chart_auth_failed"
    assert calls["refresh"] == 0


def test_session_filter_keeps_only_valid_aligned_five_minute_candles():
    trading_date = datetime(2026, 7, 15, tzinfo=charts._IST).date()
    start, end = charts.session_bounds_ist(trading_date)

    def candle(at: datetime, **overrides):
        return {"time": int(at.timestamp()), "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, **overrides}

    valid = candle(start)
    rows = charts.filter_today_session([
        candle(end),
        candle(start + timedelta(minutes=1)),
        candle(start + timedelta(minutes=5), high=100.0),
        {**valid, "open": float("nan")},
        valid,
        valid,
    ], trading_date)

    assert rows == [valid]
