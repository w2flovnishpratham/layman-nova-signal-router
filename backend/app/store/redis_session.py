from __future__ import annotations

import asyncio
import secrets
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.domain.events import ServerEvent, event
from app.domain.state_machine import SetupState, next_prompt_for


@dataclass
class SessionDocument:
    id: str
    webhook_secret: str
    user_id: str | None = None
    state: SetupState = SetupState.IDLE
    config: dict[str, Any] = field(default_factory=dict)
    events: list[ServerEvent] = field(default_factory=list)
    active_trade: dict[str, Any] | None = None

    def public_dict(self, *, limit: int = 200) -> dict[str, Any]:
        return {
            "sessionId": self.id,
            "state": self.state.value,
            "config": deepcopy(self.config),
            "activeTrade": deepcopy(self.active_trade),
            "events": [item.model_dump(mode="json") for item in self.events[-limit:]],
        }


class InMemorySessionStore:
    """Temporary adapter matching the Redis session-store contract for local builds."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionDocument] = {}
        self._secret_index: dict[str, str] = {}
        self._subscribers: dict[str, set[asyncio.Queue[ServerEvent]]] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        webhook_secret: str | None = None,
        user_id: str | None = None,
        state: SetupState = SetupState.IDLE,
        config: dict[str, Any] | None = None,
    ) -> SessionDocument:
        session = SessionDocument(
            id=uuid4().hex[:12],
            webhook_secret=webhook_secret or secrets.token_urlsafe(24),
            user_id=user_id,
            state=state,
            config=deepcopy(config or {}),
        )
        session.events.extend(
            [
                event("bot.message", text="Welcome! I am NOVA, your conversational trading companion.", tone="neutral"),
                event("bot.message", text=next_prompt_for(state), tone="prompt", state=state.value),
            ]
        )
        async with self._lock:
            self._sessions[session.id] = session
            self._secret_index[session.webhook_secret] = session.id
        return session

    async def get(self, session_id: str) -> SessionDocument | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def active_session_ids(self, *, user_id: str | None = None) -> list[str]:
        async with self._lock:
            return [
                session_id
                for session_id, session in self._sessions.items()
                if session.state in {SetupState.LIVE, SetupState.PAUSED}
                and (user_id is None or session.user_id == user_id)
            ]

    async def find_by_webhook_secret(self, webhook_secret: str) -> SessionDocument | None:
        async with self._lock:
            session_id = self._secret_index.get(webhook_secret)
            return self._sessions.get(session_id) if session_id else None

    async def append_event(self, session_id: str, item: ServerEvent) -> None:
        async with self._lock:
            session = self._sessions[session_id]
            session.events.append(item)
            subscribers = list(self._subscribers.get(session_id, set()))

        for queue in subscribers:
            queue.put_nowait(item)

    async def update_state(self, session_id: str, state: SetupState, patch: dict[str, Any]) -> SessionDocument:
        async with self._lock:
            session = self._sessions[session_id]
            session.state = state
            _merge_config(session.config, patch)
            snapshot = session

        await self.append_event(session_id, event("setup.state", state=state.value, config=deepcopy(snapshot.config)))
        await self.append_event(session_id, event("bot.message", text=next_prompt_for(state), tone="prompt", state=state.value))
        return snapshot

    async def update_active_trade(self, session_id: str, active_trade: dict[str, Any] | None) -> None:
        async with self._lock:
            session = self._sessions[session_id]
            session.active_trade = deepcopy(active_trade)

    async def subscribe(self, session_id: str) -> asyncio.Queue[ServerEvent]:
        queue: asyncio.Queue[ServerEvent] = asyncio.Queue(maxsize=500)
        async with self._lock:
            self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue[ServerEvent]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(session_id)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(session_id, None)


def _merge_config(config: dict[str, Any], patch: dict[str, Any]) -> None:
    risk_patch = patch.pop("risk_patch", None)
    if risk_patch:
        config.setdefault("risk", {}).update(risk_patch)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value


session_store = InMemorySessionStore()
