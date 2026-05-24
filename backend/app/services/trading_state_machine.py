from enum import Enum


class TradeState(str, Enum):
    WAITING_ENTRY = "WAITING_ENTRY"
    ENTRY_SIGNAL_RECEIVED = "ENTRY_SIGNAL_RECEIVED"
    ENTRY_RISK_CHECK = "ENTRY_RISK_CHECK"
    ENTRY_ORDER_SENDING = "ENTRY_ORDER_SENDING"
    WAITING_EXIT = "WAITING_EXIT"
    EXIT_SIGNAL_RECEIVED = "EXIT_SIGNAL_RECEIVED"
    EXIT_ORDER_SENDING = "EXIT_ORDER_SENDING"
    WAITING_ENTRY_AFTER_EXIT = "WAITING_ENTRY"
    BLOCKED = "BLOCKED"
    DUPLICATE_SIGNAL = "DUPLICATE_SIGNAL"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"
    GLOBAL_KILL_SWITCH_ACTIVE = "GLOBAL_KILL_SWITCH_ACTIVE"
    ERROR = "ERROR"


STATE_MESSAGES = {
    TradeState.WAITING_ENTRY: "Waiting for TradingView entry alert",
    TradeState.ENTRY_SIGNAL_RECEIVED: "Entry alert received",
    TradeState.ENTRY_RISK_CHECK: "Running entry risk checks",
    TradeState.ENTRY_ORDER_SENDING: "Sending entry order to Dhan",
    TradeState.WAITING_EXIT: "Entry placed. Waiting for TradingView exit alert",
    TradeState.EXIT_SIGNAL_RECEIVED: "Exit alert received",
    TradeState.EXIT_ORDER_SENDING: "Sending exit order to Dhan",
    TradeState.BLOCKED: "Trade blocked",
    TradeState.DUPLICATE_SIGNAL: "Duplicate signal ignored",
    TradeState.EMERGENCY_STOPPED: "Emergency stop active",
    TradeState.GLOBAL_KILL_SWITCH_ACTIVE: "Global kill switch active",
    TradeState.ERROR: "Execution error",
}


def get_state_message(state: str, extra: str = "") -> str:
    try:
        message = STATE_MESSAGES[TradeState(state)]
    except ValueError:
        message = state
    return f"{message}. {extra}" if extra else message
