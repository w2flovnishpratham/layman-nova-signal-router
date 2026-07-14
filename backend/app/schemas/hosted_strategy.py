"""Strict, non-executable NOVA Strategy IR v1 contract."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

IR_VERSION = 1
VALIDATOR_VERSION = "1.0"
MAX_INDICATORS = 50
MAX_CONDITIONS = 100
MAX_EXPRESSION_DEPTH = 20
MAX_ACTIONS = 20


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


PriceField = Literal["open", "high", "low", "close", "volume"]
PositionState = Literal["FLAT", "LONG_CE", "LONG_PE"]


class Indicator(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    type: Literal["SMA", "EMA", "RSI", "ATR", "VWAP", "SUPERTREND", "HIGHEST", "LOWEST", "ROC"]
    source: PriceField = "close"
    period: int | None = None
    atr_period: int | None = None
    multiplier: float | None = None

    @model_validator(mode="after")
    def validate_parameters(self) -> "Indicator":
        limits = {"SMA": (2, 500), "EMA": (2, 500), "RSI": (2, 100), "ATR": (2, 100),
                  "HIGHEST": (2, 500), "LOWEST": (2, 500), "ROC": (2, 500)}
        if self.type in limits:
            lo, hi = limits[self.type]
            if self.period is None or not lo <= self.period <= hi:
                raise ValueError(f"{self.type} period must be between {lo} and {hi}")
            if self.atr_period is not None or self.multiplier is not None:
                raise ValueError(f"{self.type} does not accept Supertrend parameters")
        elif self.type == "SUPERTREND":
            if self.atr_period is None or not 2 <= self.atr_period <= 100:
                raise ValueError("SUPERTREND atr_period must be between 2 and 100")
            if self.multiplier is None or not math.isfinite(self.multiplier) or not 0.1 <= self.multiplier <= 20:
                raise ValueError("SUPERTREND multiplier must be between 0.1 and 20")
            if self.period is not None:
                raise ValueError("SUPERTREND does not accept period")
        else:  # VWAP
            if any(value is not None for value in (self.period, self.atr_period, self.multiplier)):
                raise ValueError("VWAP does not accept period parameters")
        return self


class Expression(StrictModel):
    op: Literal[
        "CANDLE_FIELD", "INDICATOR_VALUE", "PARAMETER_VALUE", "CONSTANT", "POSITION_STATE",
        "PREVIOUS_VALUE", "GT", "GTE", "LT", "LTE", "EQ", "NE", "AND", "OR", "NOT",
        "CROSSES_ABOVE", "CROSSES_BELOW", "ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "ABS", "MIN", "MAX",
    ]
    field: PriceField | None = None
    indicator: str | None = None
    parameter: str | None = None
    constant: float | bool | str | None = None
    state: PositionState | None = None
    bars: int | None = Field(default=None, ge=1, le=500)
    left: "Expression | None" = None
    right: "Expression | None" = None
    arg: "Expression | None" = None
    args: list["Expression"] | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "Expression":
        supplied = {name for name in ("field", "indicator", "parameter", "constant", "state", "bars", "left", "right", "arg", "args") if getattr(self, name) is not None}
        expected: set[str]
        if self.op == "CANDLE_FIELD": expected = {"field"}
        elif self.op == "INDICATOR_VALUE": expected = {"indicator"}
        elif self.op == "PARAMETER_VALUE": expected = {"parameter"}
        elif self.op == "CONSTANT": expected = {"constant"}
        elif self.op == "POSITION_STATE": expected = {"state"}
        elif self.op == "PREVIOUS_VALUE": expected = {"arg", "bars"}
        elif self.op in {"NOT", "ABS"}: expected = {"arg"}
        elif self.op in {"AND", "OR", "MIN", "MAX"}:
            expected = {"args"}
            if self.args is None or len(self.args) < 2 or len(self.args) > 20:
                raise ValueError(f"{self.op} requires 2 to 20 args")
        else: expected = {"left", "right"}
        if supplied != expected:
            raise ValueError(f"{self.op} requires exactly {sorted(expected)}")
        if isinstance(self.constant, float) and not math.isfinite(self.constant):
            raise ValueError("constants must be finite")
        return self


class ActionRule(StrictModel):
    action: Literal["BUY_CE", "BUY_PE", "EXIT", "HOLD"]
    when: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    priority: int = Field(ge=0, le=1000)
    cooldown_bars: int = Field(default=0, ge=0, le=500)
    bar_close_confirmation: bool = True
    audit_label: str | None = Field(default=None, max_length=80)
    position_states: list[PositionState] | None = Field(default=None, max_length=3)


class SessionRules(StrictModel):
    timezone: Literal["Asia/Kolkata"] = "Asia/Kolkata"
    entry_start: str = Field(default="09:20", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    entry_end: str = Field(default="14:45", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    force_exit_time: str = Field(default="15:15", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    skip_expiry_day: bool = False

    @model_validator(mode="after")
    def ordered(self) -> "SessionRules":
        if not self.entry_start < self.entry_end < self.force_exit_time <= "15:30":
            raise ValueError("session times must be ordered and finish by 15:30")
        return self


class StrategyIR(StrictModel):
    ir_version: Literal[1]
    strategy_name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    underlying: Literal["NIFTY"]
    timeframe: Literal["1m"]
    evaluation: Literal["BAR_CLOSE"]
    warmup_bars: int = Field(ge=1, le=2000)
    parameters: dict[str, float | int | bool] = Field(default_factory=dict, max_length=50)
    indicators: list[Indicator] = Field(max_length=MAX_INDICATORS)
    conditions: dict[str, Expression] = Field(max_length=MAX_CONDITIONS)
    actions: list[ActionRule] = Field(min_length=1, max_length=MAX_ACTIONS)
    session: SessionRules = Field(default_factory=SessionRules)
    risk_metadata: dict[str, str | float | int | bool] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def finite_parameters(self) -> "StrategyIR":
        if any(isinstance(value, float) and not math.isfinite(value) for value in self.parameters.values()):
            raise ValueError("parameters must be finite")
        return self


class Candle(StrictModel):
    instrument: Literal["NIFTY"] = "NIFTY"
    timeframe: Literal["1m"] = "1m"
    close_timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    finalized: Literal[True] = True

    @model_validator(mode="after")
    def valid_bar(self) -> "Candle":
        values = (self.open, self.high, self.low, self.close, self.volume)
        if self.close_timestamp.tzinfo is None or not all(math.isfinite(v) for v in values):
            raise ValueError("candle timestamp must be timezone-aware and values finite")
        if self.high < max(self.open, self.low, self.close) or self.low > min(self.open, self.high, self.close):
            raise ValueError("invalid OHLC range")
        if self.close_timestamp.second or self.close_timestamp.microsecond:
            raise ValueError("one-minute close timestamp must align to a minute")
        return self


class ReplayRequest(StrictModel):
    candles: list[Candle] = Field(min_length=1, max_length=5000)
    starting_position: PositionState = "FLAT"


class HostedLinkRequest(StrictModel):
    ir_version_id: str


class CreateIRRequest(StrictModel):
    strategy_id: str
    creation_source: Literal["MANUAL_TEST_FIXTURE", "NOVA_OWNED"]
    ir: StrategyIR
