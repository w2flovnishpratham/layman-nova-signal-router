from typing import Any

from pydantic import BaseModel


class OpenPosition(BaseModel):
    has_open_position: bool
    strategy_code: str | None = None
    security_id: str | None = None
    trading_symbol: str | None = None
    qty: int = 0
    entry_order_id: str | None = None
    entry_price: float | None = None
    opened_at: str | None = None
    live_pnl: dict[str, Any] | None = None
    extra: dict[str, Any] | None = None
