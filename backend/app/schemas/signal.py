from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class NormalizedSignal(BaseModel):
    payload_format: Literal["NOVA", "PINE_MULTI_LEG"]
    secret: str
    signal_id: str
    strategy_code: str
    action: Literal["ENTRY", "EXIT"]
    side: Literal["BUY", "SELL"]
    symbol: str
    instrument_type: str | None = None
    exchange_segment: str | None = None
    security_id: str | None = None
    trading_symbol: str | None = None
    option_side: Literal["CE", "PE"] | None = None
    strike: float | None = None
    expiry: str | None = None
    qty: int = Field(gt=0)
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    product_type: Literal["INTRADAY", "CNC", "MARGIN"] = "INTRADAY"
    source: str = "tradingview"
    raw_payload: dict[str, Any]

    @field_validator(
        "action",
        "side",
        "symbol",
        "exchange_segment",
        "order_type",
        "product_type",
        "option_side",
        mode="before",
    )
    @classmethod
    def uppercase_values(cls, value: str | None) -> str | None:
        return value.upper() if isinstance(value, str) else value

    @field_validator("signal_id", "strategy_code", "symbol")
    @classmethod
    def required_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("field must not be empty")
        return value.strip()


TradingViewWebhookPayload = NormalizedSignal


class WebhookResponse(BaseModel):
    accepted: bool
    signal_id: str | None = None
    action: str | None = None
    payload_format: str | None = None
    status: str
    message: str
    execution_result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
