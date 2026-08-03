from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.services import quote_service
from app.services.credential_vault import DhanCredentials
from app.services.dhan_client import DhanLtpResult
from app.services.dhan_marketfeed_ws import MarketFeedLtpResult
from app.services.dhan_rate_limiter import dhan_quote_rate_limiter


def _waiting(**kwargs):
    return MarketFeedLtpResult(
        success=False,
        message="waiting",
        ltp=None,
        exchange_segment=kwargs["exchange_segment"],
        security_id=kwargs["security_id"],
        error="ws_waiting",
    )


def _prepare(monkeypatch):
    quote_service.reset_quote_state_for_tests()
    monkeypatch.setattr(quote_service, "_market_is_open", lambda: True)
    monkeypatch.setattr(quote_service, "ensure_marketfeed_subscription", lambda **_kwargs: None)
    monkeypatch.setattr(quote_service, "get_runtime_settings", lambda: {
        "marketfeed_ws_enabled": True,
        "option_ltp_source": "AUTO",
        "option_ltp_cache_seconds": 5,
    })
    monkeypatch.setattr(
        quote_service,
        "market_data_credentials",
        lambda: DhanCredentials("shared-client", "redacted", "shared_market_data"),
    )
    with dhan_quote_rate_limiter._lock:
        dhan_quote_rate_limiter._blocked_until = 0.0
        dhan_quote_rate_limiter._requests.clear()


def test_fresh_websocket_quote_never_calls_rest(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(
        quote_service,
        "get_marketfeed_ltp",
        lambda **kwargs: MarketFeedLtpResult(
            success=True,
            message="fresh",
            ltp=123.45,
            exchange_segment=kwargs["exchange_segment"],
            security_id=kwargs["security_id"],
            received_at="2026-07-20T09:30:00Z",
            age_seconds=0.1,
        ),
    )
    monkeypatch.setattr(
        quote_service,
        "RealDhanClient",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("REST must not be called")),
    )

    quote = quote_service.get_quote_snapshot(exchange_segment="NSE_FNO", security_id="123")

    assert quote["ltp"] == 123.45
    assert quote["source"] == "DHAN_WEBSOCKET"
    assert quote["stale"] is False


def test_concurrent_same_instrument_coalesces_to_one_rest_request(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(quote_service, "get_marketfeed_ltp", _waiting)
    calls = 0
    lock = threading.Lock()

    class Client:
        def __init__(self, **_kwargs):
            pass

        def get_ltp_batch(self, **kwargs):
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.1)
            key = tuple(kwargs["instruments"][0])
            return {key: DhanLtpResult(True, "fresh", 111.25)}

    monkeypatch.setattr(quote_service, "RealDhanClient", Client)

    with ThreadPoolExecutor(max_workers=8) as pool:
        quotes = list(pool.map(
            lambda _: quote_service.get_quote_snapshot(exchange_segment="NSE_FNO", security_id="456"),
            range(8),
        ))

    assert calls == 1
    assert {quote["ltp"] for quote in quotes} == {111.25}
    assert quote_service.quote_metrics()["singleflight_followers"] >= 1


def test_rest_batches_multiple_instruments(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(quote_service, "get_marketfeed_ltp", _waiting)
    batches = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        def get_ltp_batch(self, **kwargs):
            batches.append(list(kwargs["instruments"]))
            return {
                tuple(item): DhanLtpResult(True, "fresh", 100.0 + index)
                for index, item in enumerate(kwargs["instruments"])
            }

    monkeypatch.setattr(quote_service, "RealDhanClient", Client)
    requests = [
        quote_service.QuoteRequest("NSE_FNO", "CE1"),
        quote_service.QuoteRequest("NSE_FNO", "PE1"),
    ]

    quotes = quote_service.get_quote_snapshots(requests)

    assert batches == [[("NSE_FNO", "CE1"), ("NSE_FNO", "PE1")]]
    assert quotes[("NSE_FNO", "CE1")]["ltp"] == 100.0
    assert quotes[("NSE_FNO", "PE1")]["ltp"] == 101.0


def test_retry_after_cooldown_prevents_immediate_rest_storm(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(quote_service, "get_marketfeed_ltp", _waiting)
    calls = 0

    class Response:
        status_code = 429
        headers = {"Retry-After": "2"}

    dhan_quote_rate_limiter.observe_response(Response())

    class Client:
        def __init__(self, **_kwargs):
            pass

        def get_ltp_batch(self, **_kwargs):
            nonlocal calls
            calls += 1
            return {}

    monkeypatch.setattr(quote_service, "RealDhanClient", Client)
    first = quote_service.get_quote_snapshot(exchange_segment="NSE_FNO", security_id="789")
    second = quote_service.get_quote_snapshot(exchange_segment="NSE_FNO", security_id="789")

    assert calls == 0
    assert first["status"] == second["status"] == "RATE_LIMITED"
    assert first["retry_after_seconds"] > 0


def test_fresh_websocket_remains_usable_during_rest_cooldown(monkeypatch):
    _prepare(monkeypatch)

    class Response:
        status_code = 429
        headers = {"Retry-After": "2"}

    dhan_quote_rate_limiter.observe_response(Response())
    monkeypatch.setattr(
        quote_service,
        "get_marketfeed_ltp",
        lambda **kwargs: MarketFeedLtpResult(
            success=True,
            message="fresh",
            ltp=222.0,
            exchange_segment=kwargs["exchange_segment"],
            security_id=kwargs["security_id"],
            received_at="2026-07-20T09:30:00Z",
            age_seconds=0.1,
        ),
    )

    quote = quote_service.get_quote_snapshot(exchange_segment="NSE_FNO", security_id="999")

    assert quote["source"] == "DHAN_WEBSOCKET"
    assert quote["ltp"] == 222.0
