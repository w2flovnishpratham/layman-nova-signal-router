"""The candle push worker must only broadcast when the cached candle payload
actually changed -- a cache hit between Dhan fetches is a no-op, not a
redundant WS push to every open session."""
from __future__ import annotations

from app.workers import nifty_candle_pusher as pusher


def _payload(updated_at: str) -> dict:
    return {"symbol": "NIFTY", "interval": "5m", "status": "ready", "updated_at": updated_at, "candles": []}


def test_pushes_once_then_skips_an_unchanged_payload(monkeypatch):
    monkeypatch.setattr(pusher, "_LAST_PUSHED_AT", {})
    monkeypatch.setattr(pusher, "get_nifty_candles", lambda interval: _payload("t1"))
    pushes = []
    monkeypatch.setattr(
        pusher,
        "publish_nifty_candles_from_sync",
        lambda *, interval, series: pushes.append((interval, series["updated_at"])) or True,
    )

    pusher._push_if_changed("5m")
    pusher._push_if_changed("5m")

    assert pushes == [("5m", "t1")]


def test_pushes_again_once_updated_at_changes(monkeypatch):
    monkeypatch.setattr(pusher, "_LAST_PUSHED_AT", {})
    current = {"updated_at": "t1"}
    monkeypatch.setattr(pusher, "get_nifty_candles", lambda interval: _payload(current["updated_at"]))
    pushes = []
    monkeypatch.setattr(
        pusher,
        "publish_nifty_candles_from_sync",
        lambda *, interval, series: pushes.append(series["updated_at"]) or True,
    )

    pusher._push_if_changed("5m")
    current["updated_at"] = "t2"
    pusher._push_if_changed("5m")

    assert pushes == ["t1", "t2"]


def test_intervals_are_tracked_independently(monkeypatch):
    monkeypatch.setattr(pusher, "_LAST_PUSHED_AT", {})
    monkeypatch.setattr(pusher, "get_nifty_candles", lambda interval: _payload("same"))
    pushes = []
    monkeypatch.setattr(
        pusher,
        "publish_nifty_candles_from_sync",
        lambda *, interval, series: pushes.append(interval) or True,
    )

    pusher._push_if_changed("5m")
    pusher._push_if_changed("15m")
    pusher._push_if_changed("5m")

    assert pushes == ["5m", "15m"]


def test_does_not_record_a_push_that_failed_to_publish(monkeypatch):
    # publish_*_from_sync returns False when there's no bound event loop yet
    # (e.g. during startup) -- a payload that never actually reached anyone
    # must not be remembered as pushed, or a real later push gets skipped.
    monkeypatch.setattr(pusher, "_LAST_PUSHED_AT", {})
    monkeypatch.setattr(pusher, "get_nifty_candles", lambda interval: _payload("t1"))
    monkeypatch.setattr(pusher, "publish_nifty_candles_from_sync", lambda *, interval, series: False)

    pusher._push_if_changed("5m")

    assert pusher._LAST_PUSHED_AT.get("5m") is None
