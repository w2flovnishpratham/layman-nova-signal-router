from __future__ import annotations

from datetime import date, datetime, timedelta

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


def _candle(stamp: int, *, open_: float, high: float, low: float, close: float, volume: float = 1):
    return {
        "time": stamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def test_authoritative_provider_is_always_requested_at_one_minute(monkeypatch):
    trading_date = date(2026, 7, 24)
    start = int(charts.session_bounds_ist(trading_date)[0].timestamp())
    requested = []

    class FakeDhanClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_intraday_candles(self, **kwargs):
            requested.append(kwargs["interval"])
            return {
                "success": True,
                "candles": [_candle(start, open_=100, high=102, low=99, close=101)],
            }

    monkeypatch.setattr(charts, "RealDhanClient", FakeDhanClient)
    monkeypatch.setattr(
        charts,
        "market_data_credentials",
        lambda: DhanCredentials("client", "token", "shared_market_data"),
    )
    monkeypatch.setattr(charts, "_market_is_open", lambda: False)
    payload = charts._fetch_candles("15", "15m", trading_date)

    assert requested == ["1"]
    assert payload["status"] == "ready"
    assert payload["interval"] == "15m"


def test_five_and_fifteen_minute_bars_are_ist_aligned_from_native_one_minute():
    trading_date = date(2026, 7, 24)
    start = int(charts.session_bounds_ist(trading_date)[0].timestamp())
    source = [
        _candle(
            start + minute * 60,
            open_=100 + minute,
            high=102 + minute,
            low=99 + minute,
            close=101 + minute,
            volume=minute + 1,
        )
        for minute in range(16)
    ]

    five = charts.aggregate_one_minute_candles(
        source,
        interval_minutes=5,
        trading_date=trading_date,
    )
    fifteen = charts.aggregate_one_minute_candles(
        source,
        interval_minutes=15,
        trading_date=trading_date,
    )

    assert [bar["time"] for bar in five] == [start, start + 300, start + 600, start + 900]
    assert five[0] == {
        "time": start,
        "open": 100.0,
        "high": 106.0,
        "low": 99.0,
        "close": 105.0,
        "volume": 15.0,
    }
    assert [bar["time"] for bar in fifteen] == [start, start + 900]
    assert fifteen[0]["open"] == 100.0
    assert fifteen[0]["close"] == 115.0
    assert fifteen[0]["volume"] == 120.0


def test_aggregation_never_crosses_sessions_or_dates():
    trading_date = date(2026, 7, 24)
    start, end = charts.session_bounds_ist(trading_date)
    next_start = int(charts.session_bounds_ist(trading_date + timedelta(days=1))[0].timestamp())
    source = [
        _candle(int(start.timestamp()), open_=100, high=101, low=99, close=100),
        _candle(int(end.timestamp()), open_=200, high=201, low=199, close=200),
        _candle(next_start, open_=300, high=301, low=299, close=300),
    ]
    result = charts.aggregate_one_minute_candles(
        source,
        interval_minutes=15,
        trading_date=trading_date,
    )
    assert len(result) == 1
    assert result[0]["open"] == 100.0


def test_malformed_native_one_minute_data_returns_explicit_unavailable(monkeypatch):
    trading_date = date(2026, 7, 24)
    start = int(charts.session_bounds_ist(trading_date)[0].timestamp())

    class FakeDhanClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_intraday_candles(self, **kwargs):
            return {
                "success": True,
                "candles": [_candle(start + 30, open_=100, high=90, low=99, close=101)],
            }

    monkeypatch.setattr(charts, "RealDhanClient", FakeDhanClient)
    monkeypatch.setattr(
        charts,
        "market_data_credentials",
        lambda: DhanCredentials("client", "token", "shared_market_data"),
    )
    monkeypatch.setattr(charts, "_market_is_open", lambda: False)
    payload = charts._fetch_authoritative_one_minute(trading_date)

    assert payload["status"] == "unavailable"
    assert payload["reason"] == "authoritative_1m_malformed"


def test_stale_native_one_minute_data_returns_explicit_unavailable(monkeypatch):
    fixed_now = datetime(2026, 7, 24, 12, 0, tzinfo=charts._IST)
    trading_date = fixed_now.date()
    start = int(charts.session_bounds_ist(trading_date)[0].timestamp())

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    class FakeDhanClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_intraday_candles(self, **kwargs):
            return {
                "success": True,
                "candles": [_candle(start, open_=100, high=101, low=99, close=100)],
            }

    monkeypatch.setattr(charts, "RealDhanClient", FakeDhanClient)
    monkeypatch.setattr(charts, "datetime", FrozenDatetime)
    monkeypatch.setattr(
        charts,
        "market_data_credentials",
        lambda: DhanCredentials("client", "token", "shared_market_data"),
    )
    monkeypatch.setattr(charts, "_market_is_open", lambda: True)
    payload = charts._fetch_authoritative_one_minute(trading_date)

    assert payload["status"] == "unavailable"
    assert payload["reason"] == "authoritative_1m_stale"
