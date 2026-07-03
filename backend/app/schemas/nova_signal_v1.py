from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NovaSignalV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["nova.v1"]
    secret: str = Field(min_length=1)
    signal_id: str = Field(min_length=1)
    strategy_code: str = Field(min_length=1)
    strategy_instance_id: str | None = None
    action: Literal["ENTRY", "EXIT", "REVERSE", "MODIFY", "HEARTBEAT"]
    intent: Literal["BULLISH", "BEARISH", "FLAT"]
    symbol: Literal["NIFTY", "BANKNIFTY"]
    instrument_type: Literal["OPTIDX"]
    option_side: Literal["CE", "PE", "AUTO", "NONE"]
    strike_mode: Literal["ATM", "ITM1", "ITM2", "OTM1", "OTM2", "MANUAL"]
    strike: float | None = None
    expiry_mode: Literal["NEXT_WEEKLY", "SAME_DAY", "MANUAL"]
    expiry: date | None = None
    qty_mode: Literal["LOTS", "FIXED_QTY", "RISK_BASED"]
    lots: int = Field(ge=1)
    order_type: Literal["MARKET", "LIMIT"]
    product_type: Literal["INTRADAY"]
    source: Literal["tradingview", "backend"]
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_manual_values(self) -> "NovaSignalV1":
        if self.strike_mode == "MANUAL" and self.strike is None:
            raise ValueError("strike is required when strike_mode is MANUAL")
        if self.expiry_mode == "MANUAL" and self.expiry is None:
            raise ValueError("expiry is required when expiry_mode is MANUAL")
        return self
