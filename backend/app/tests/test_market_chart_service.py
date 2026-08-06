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


def test_failed_fetch_retries_sooner_than_a_successful_closed_market_cache(monkeypatch):
    trading_date = date(2026, 7, 24)
    calls = {"count": 0}
    clock = {"t": 0.0}

    class FakeDhanClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_intraday_candles(self, **kwargs):
            calls["count"] += 1
            return {"success": True, "candles": []}

    monkeypatch.setattr(charts, "RealDhanClient", FakeDhanClient)
    monkeypatch.setattr(charts, "market_data_credentials", lambda: DhanCredentials("client", "token", "shared_market_data"))
    monkeypatch.setattr(charts, "_market_is_open", lambda: False)
    monkeypatch.setattr(charts, "chart_trading_date_ist", lambda: trading_date)
    monkeypatch.setattr(charts.time, "monotonic", lambda: clock["t"])
    charts._CACHE.clear()

    first = charts.get_nifty_candles(interval="5m")
    assert first["status"] == "unavailable"
    # Counted as a delta rather than an absolute: with the provider returning
    # nothing for every date, one attempt now also walks back through
    # MAX_TRADING_DAY_LOOKBACK earlier sessions looking for candles. This test
    # is about cache TTL, not about how many dates a single attempt probes.
    after_first = calls["count"]
    assert after_first >= 1

    clock["t"] += charts.CACHE_TTL_FAILURE_SECONDS + 1
    second = charts.get_nifty_candles(interval="5m")
    assert second["status"] == "unavailable"
    assert calls["count"] > after_first, (
        "a failed fetch must retry after the short failure TTL, "
        "not sit cached for the full 5-minute closed-market TTL"
    )


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


def _one_minute_candles(trading_date: date, count: int = 5) -> list[dict]:
    start, _ = charts.session_bounds_ist(trading_date)
    base = int(start.timestamp())
    return [
        {"time": base + i * 60, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10}
        for i in range(count)
    ]


def test_closed_market_walks_back_to_the_last_session_that_has_candles(monkeypatch):
    """Dhan does not always serve the just-closed session: measured at 02:25 IST,
    Aug 6 returned 0 candles while Aug 5/4/3 each returned 383. Without a
    walk-back the chart shows an error every night and every pre-market morning.
    chart_trading_date_ist() cannot fix this -- it only reasons about the
    calendar, and cannot know a date came back empty."""
    empty_date = date(2026, 8, 6)   # Thursday, resolved but empty
    good_date = date(2026, 8, 5)    # Wednesday, has data
    asked: list[str] = []

    class FakeDhanClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_intraday_candles(self, **kwargs):
            asked.append(kwargs["from_date"][:10])
            if kwargs["from_date"].startswith(good_date.isoformat()):
                return {"success": True, "candles": _one_minute_candles(good_date)}
            return {"success": True, "candles": []}

    monkeypatch.setattr(charts, "RealDhanClient", FakeDhanClient)
    monkeypatch.setattr(charts, "market_data_credentials", lambda: DhanCredentials("client", "token", "shared_market_data"))
    monkeypatch.setattr(charts, "_market_is_open", lambda: False)
    monkeypatch.setattr(charts, "chart_trading_date_ist", lambda: empty_date)
    charts._CACHE.clear()

    payload = charts.get_nifty_candles(interval="5m")

    assert payload["status"] == "ready"
    assert payload["candles"], "expected the fallback session's candles"
    assert payload["trading_date"] == good_date.isoformat(), (
        "the response must report the session actually served, not the empty one"
    )
    assert asked[0] == empty_date.isoformat()
    assert good_date.isoformat() in asked


def test_open_market_never_substitutes_a_previous_session(monkeypatch):
    """While the market is OPEN an empty result means the session has just begun
    and candles are still arriving. Silently showing a previous day's prices on a
    live trading chart would be dangerous, so the walk-back must not run."""
    today = date(2026, 8, 6)
    asked: list[str] = []

    class FakeDhanClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_intraday_candles(self, **kwargs):
            asked.append(kwargs["from_date"][:10])
            return {"success": True, "candles": []}

    monkeypatch.setattr(charts, "RealDhanClient", FakeDhanClient)
    monkeypatch.setattr(charts, "market_data_credentials", lambda: DhanCredentials("client", "token", "shared_market_data"))
    monkeypatch.setattr(charts, "_market_is_open", lambda: True)
    monkeypatch.setattr(charts, "chart_trading_date_ist", lambda: today)
    charts._CACHE.clear()

    payload = charts.get_nifty_candles(interval="5m")

    assert payload["status"] == "unavailable"
    assert asked == [today.isoformat()], "must not walk back into a prior session while live"


def test_walk_back_is_bounded(monkeypatch):
    """Each step is a Dhan round trip, so a permanently empty provider must not
    spin -- it stops after MAX_TRADING_DAY_LOOKBACK attempts."""
    start_date = date(2026, 8, 6)
    calls = {"count": 0}

    class FakeDhanClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_intraday_candles(self, **kwargs):
            calls["count"] += 1
            return {"success": True, "candles": []}

    monkeypatch.setattr(charts, "RealDhanClient", FakeDhanClient)
    monkeypatch.setattr(charts, "market_data_credentials", lambda: DhanCredentials("client", "token", "shared_market_data"))
    monkeypatch.setattr(charts, "_market_is_open", lambda: False)
    monkeypatch.setattr(charts, "chart_trading_date_ist", lambda: start_date)
    charts._CACHE.clear()

    payload = charts.get_nifty_candles(interval="5m")

    assert payload["status"] == "unavailable"
    assert calls["count"] == 1 + charts.MAX_TRADING_DAY_LOOKBACK
