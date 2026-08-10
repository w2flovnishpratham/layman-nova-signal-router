from __future__ import annotations

import asyncio
import queue
import threading

from app.services.dhan_marketfeed_ws import MarketFeedPacket
from app.services import chat_event_publisher
from app.store.redis_session import InMemorySessionStore
from app.workers import nifty_candle_pusher as pusher


def _packet(*, security_id: str = "13", ltp: float = 24001.5) -> MarketFeedPacket:
    return MarketFeedPacket(
        response_code=2,
        message_length=16,
        exchange_segment_code=0,
        exchange_segment="IDX_I",
        security_id=security_id,
        ltp=ltp,
        last_trade_time=1_800_000_000,
    )


def test_only_nifty_ticks_enter_the_candle_batch(monkeypatch):
    monkeypatch.setattr(pusher, "_TICKS", queue.SimpleQueue())
    monkeypatch.setattr(pusher, "_TICK_EVENT", threading.Event())

    pusher._queue_tick(_packet(security_id="99"))
    pusher._queue_tick(_packet())

    assert pusher._drain_ticks() == [(24001.5, 1_800_000_000)]


def test_tick_batch_publishes_only_returned_candle_deltas(monkeypatch):
    deltas = [{"interval": "1m"}, {"interval": "5m"}, {"interval": "15m"}]
    monkeypatch.setattr(pusher, "apply_nifty_ticks", lambda ticks: deltas)
    pushed = []
    monkeypatch.setattr(
        pusher,
        "publish_nifty_candle_upsert_from_sync",
        lambda *, delta: pushed.append(delta) or True,
    )

    pusher._publish_tick_batch([(24001.5, 1_800_000_000)])

    assert pushed == deltas


def test_candle_delta_reaches_idle_connected_session_without_bloating_history(monkeypatch):
    async def check() -> None:
        store = InMemorySessionStore()
        monkeypatch.setattr(chat_event_publisher, "session_store", store)
        session = await store.create()
        queue_ = await store.subscribe(session.id)
        assert await store.subscribed_session_ids() == [session.id]
        retained_before = len(session.events)
        delta = {"interval": "5m", "candle": {"time": 1}}

        await chat_event_publisher.publish_nifty_candle_upsert(delta=delta)

        pushed = await queue_.get()
        assert pushed.type == "market.candle.upsert"
        assert pushed.data["candle"] == {"time": 1}
        assert len(session.events) == retained_before

    asyncio.run(check())
