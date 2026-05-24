from typing import Any

from pydantic import BaseModel


class OrderEvent(BaseModel):
    id: int
    created_at: str | None
    phase: str | None
    signal_id: str | None
    payload_format: str | None = None
    action: str | None
    side: str | None = None
    normalized_action: str | None = None
    normalized_side: str | None = None
    normalized_qty: int | None = None
    normalized_symbol: str | None = None
    normalized_strike: float | None = None
    normalized_expiry: str | None = None
    normalized_option_side: str | None = None
    dhan_mode: str | None
    live_orders_enabled: bool | None = None
    order_id: str | None = None
    status: str | None = None
    success: bool | None = None
    blocked: bool | None = None
    reason: str | None = None
    security_id: str | None = None
    trading_symbol: str | None = None
    qty: int | None = None
    order_type: str | None = None
    product_type: str | None = None
    avg_price: float | None = None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
