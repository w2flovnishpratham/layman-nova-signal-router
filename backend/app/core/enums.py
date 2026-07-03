from __future__ import annotations

from enum import Enum


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class StrategySourceType(_StringEnum):
    NOVA_OWNED_TRADINGVIEW = "nova_owned_tradingview"
    USER_OWNED_TRADINGVIEW = "user_owned_tradingview"
    USER_PASTED_PINE = "user_pasted_pine"
    USER_NO_WEBHOOK = "user_no_webhook"
    BACKEND_HOSTED = "backend_hosted"


class StrategyCatalogStatus(_StringEnum):
    ACTIVE = "active"
    BETA = "beta"
    COMING_SOON = "coming_soon"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"


class StrategyInstanceStatus(_StringEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    DISABLED = "disabled"


class StrategyExecutionMode(_StringEnum):
    SIGNAL_ONLY = "signal_only"
    PAPER_LIVE_DATA = "paper_live_data"
    REAL_ORDERS = "real_orders"


class OptionIntent(_StringEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    FLAT = "FLAT"


class StrikeMode(_StringEnum):
    ATM = "ATM"
    ITM1 = "ITM1"
    ITM2 = "ITM2"
    OTM1 = "OTM1"
    OTM2 = "OTM2"
    MANUAL = "MANUAL"


class ExpiryMode(_StringEnum):
    NEXT_WEEKLY = "NEXT_WEEKLY"
    SAME_DAY = "SAME_DAY"
    MANUAL = "MANUAL"
