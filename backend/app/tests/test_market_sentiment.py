"""Market sentiment proxy: cache, stale-on-failure, honest unavailable."""
from __future__ import annotations

from app.routers import market


def _reset():
    market._sentiment_cache["data"] = None
    market._sentiment_cache["at"] = 0.0


def test_fetches_then_caches_within_ttl(monkeypatch):
    _reset()
    monkeypatch.setattr(market, "_fetch_sentiment",
                        lambda url=market._SENTIMENT_URL: {"available": True, "bullish_percent": 79,
                                                           "bearish_percent": 21, "updated_at": "t"})
    first = market.market_sentiment()
    assert (first["bullish_percent"], first["cached"]) == (79, False)
    # A second call inside the TTL must not hit upstream again.
    monkeypatch.setattr(market, "_fetch_sentiment",
                        lambda url=market._SENTIMENT_URL: (_ for _ in ()).throw(AssertionError("should be cached")))
    second = market.market_sentiment()
    assert second["cached"] is True and second["bullish_percent"] == 79


def test_serves_last_known_when_upstream_fails(monkeypatch):
    _reset()
    monkeypatch.setattr(market, "_fetch_sentiment",
                        lambda url=market._SENTIMENT_URL: {"available": True, "bullish_percent": 60,
                                                           "bearish_percent": 40, "updated_at": "t"})
    market.market_sentiment()
    market._sentiment_cache["at"] = 0.0  # expire

    def boom(url=market._SENTIMENT_URL):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(market, "_fetch_sentiment", boom)
    stale = market.market_sentiment()
    assert stale["bullish_percent"] == 60 and stale.get("stale") is True


def test_reports_unavailable_when_no_cache_and_upstream_fails(monkeypatch):
    _reset()
    monkeypatch.setattr(market, "_fetch_sentiment",
                        lambda url=market._SENTIMENT_URL: (_ for _ in ()).throw(RuntimeError("down")))
    result = market.market_sentiment()
    assert result["available"] is False and result["bullish_percent"] is None
