"""The TradingView webhook must never do its blocking work on the event loop.

tradingview_webhook is `async def`, so anything it calls without awaiting runs
ON the asyncio event loop thread. It used to call _route_payload (-> route_signal
-> dhan_client's SYNCHRONOUS httpx.Client with time.sleep() retry backoff)
directly, while also holding a threading.Lock across the call. For the length of
a broker round-trip that froze the whole engine process: WebSocket event
delivery, every other user's request, and the health endpoint.

The invariant pinned here: the blocking half runs in a DIFFERENT thread from the
async handler body. Asserting on thread identity rather than wall-clock timing
keeps this deterministic on a loaded CI box.
"""
from __future__ import annotations

import threading

import pytest

# Pytest resolves the imported fixture by name; reused intentionally.
from app.tests.test_webhook_formats import client  # noqa: F401
from app.tests.test_signal_parser import nova_payload


def _record_handler_thread(threads: dict[str, int]):
    """get_engine_mode is called inline in the async handler body, so whatever
    thread it observes is the event loop's own thread."""
    def recording(*_args, **_kwargs):
        threads.setdefault("handler", threading.get_ident())
        return "paper"
    return recording


@pytest.mark.usefixtures("ready_default_strategy")
def test_route_signal_runs_off_the_event_loop_thread(client, monkeypatch):
    from app.routers import webhook as webhook_router

    threads: dict[str, int] = {}

    def recording_route_signal(signal):
        threads["route_signal"] = threading.get_ident()
        return {
            "blocked": False,
            "success": True,
            "status": "ORDER_PLACED",
            "order_id": f"TEST-{signal.signal_id}",
        }

    monkeypatch.setattr(webhook_router, "get_engine_mode", _record_handler_thread(threads))
    monkeypatch.setattr(webhook_router, "route_signal", recording_route_signal)

    response = client.post(
        "/webhook/tradingview",
        json=nova_payload("ENTRY", "BUY", "loop-thread-1"),
    )

    assert response.status_code == 200, response.text
    assert "handler" in threads, "handler body never ran; scaffolding is wrong"
    assert "route_signal" in threads, "route_signal never ran; scaffolding is wrong"
    assert threads["route_signal"] != threads["handler"], (
        "route_signal executed on the event loop thread -- the asyncio.to_thread "
        "offload in tradingview_webhook was removed, so a slow broker call will "
        "again freeze WS delivery and every other request."
    )


@pytest.mark.usefixtures("ready_default_strategy")
def test_strategy_lock_is_acquired_off_the_event_loop_thread(client, monkeypatch):
    """The lock wait is the other half: parking on a contended threading.Lock
    from the event loop stalls the process just as hard as the I/O does."""
    from app.routers import webhook as webhook_router

    threads: dict[str, int] = {}
    real_get_lock = webhook_router._get_strategy_lock

    def recording_get_lock(strategy_code):
        threads["lock"] = threading.get_ident()
        return real_get_lock(strategy_code)

    monkeypatch.setattr(webhook_router, "get_engine_mode", _record_handler_thread(threads))
    monkeypatch.setattr(webhook_router, "_get_strategy_lock", recording_get_lock)
    monkeypatch.setattr(
        webhook_router,
        "route_signal",
        lambda signal: {"blocked": False, "success": True, "status": "ORDER_PLACED", "order_id": "TEST-1"},
    )

    response = client.post(
        "/webhook/tradingview",
        json=nova_payload("ENTRY", "BUY", "loop-thread-2"),
    )

    assert response.status_code == 200, response.text
    assert threads["lock"] != threads["handler"], (
        "the strategy lock was acquired on the event loop thread; a second "
        "concurrent alert for the same strategy would block the whole process"
    )
