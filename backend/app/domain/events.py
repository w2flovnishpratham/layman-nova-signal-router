from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field


IST = ZoneInfo("Asia/Kolkata")

EventType = Literal[
    "bot.message",
    "system.event",
    "setup.state",
    "setup.info",
    "mode.update",
    "signal.received",
    "strategy.job",
    "order.placed",
    "order.filled",
    "order.rejected",
    "tick.pnl",
    "position.update",
    "funds.update",
    "trade.exit",
    "session.error",
    "session.eod",
]


def now_ist() -> datetime:
    return datetime.now(tz=IST)


class ServerEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    type: EventType
    ts: datetime = Field(default_factory=now_ist)
    data: dict[str, Any] = Field(default_factory=dict)


def event(event_type: EventType, **data: Any) -> ServerEvent:
    from app.services.state_store import get_engine_mode

    data.setdefault("mode", get_engine_mode(legacy_fallback=False))
    return ServerEvent(type=event_type, data=data)
