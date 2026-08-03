from __future__ import annotations

import asyncio

from app.domain.state_machine import SetupState
from app.services import chat_event_publisher
from app.store.redis_session import session_store


def _runtime(state: str, *, accepting: bool, mode: str = "paper") -> dict:
    return {
        "engine": {
            "state": state,
            "accepting_signals": accepting,
            "mode": mode,
        }
    }


def test_idle_owner_session_is_promoted_and_receives_runtime_events() -> None:
    async def scenario() -> None:
        session = await session_store.create(user_id="owner-a", state=SetupState.IDLE)
        other = await session_store.create(user_id="owner-b", state=SetupState.IDLE)
        chat_event_publisher.bind_chat_event_loop(asyncio.get_running_loop())
        try:
            chat_event_publisher.synchronize_runtime_sessions_from_sync(
                _runtime("RUNNING", accepting=True), user_id="owner-a"
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            promoted = await session_store.get(session.id)
            untouched = await session_store.get(other.id)
            assert promoted is not None and promoted.state is SetupState.LIVE
            assert untouched is not None and untouched.state is SetupState.IDLE

            await chat_event_publisher.publish_tick_pnl(
                symbol="NIFTY TEST CE",
                security_id="123",
                ltp=101.5,
                pnl=97.5,
                pnl_pct=1.5,
                mode="paper",
                user_id="owner-a",
            )
            await chat_event_publisher.publish_market_snapshot(
                {"niftySpot": 24100.0}, user_id="owner-a"
            )
            refreshed = await session_store.get(session.id)
            assert refreshed is not None
            assert [item.type for item in refreshed.events].count("tick.pnl") == 1
            assert [item.type for item in refreshed.events].count("market.snapshot") == 1
        finally:
            chat_event_publisher.clear_chat_event_loop()
            await session_store.update_state(session.id, SetupState.ENDED, {})
            await session_store.update_state(other.id, SetupState.ENDED, {})

    asyncio.run(scenario())


def test_runtime_stop_and_pause_synchronize_owner_session() -> None:
    async def scenario() -> None:
        session = await session_store.create(user_id="owner-c", state=SetupState.IDLE)
        await chat_event_publisher.synchronize_runtime_sessions(
            _runtime("RUNNING", accepting=False), user_id="owner-c"
        )
        paused = await session_store.get(session.id)
        assert paused is not None and paused.state is SetupState.PAUSED

        await chat_event_publisher.synchronize_runtime_sessions(
            _runtime("STOPPED", accepting=False), user_id="owner-c"
        )
        stopped = await session_store.get(session.id)
        assert stopped is not None and stopped.state is SetupState.IDLE
        await session_store.update_state(session.id, SetupState.ENDED, {})

    asyncio.run(scenario())
