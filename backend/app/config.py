from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]

SUPPORT_NOVA_PAYLOAD = True
SUPPORT_PINE_MULTI_LEG_PAYLOAD = True

DEFAULT_STRATEGY_CODE = "TRADINGVIEW_NIFTY_V1"
DEFAULT_PRODUCT_TYPE = "INTRADAY"
DEFAULT_ORDER_TYPE = "MARKET"
DEFAULT_EXCHANGE_SEGMENT = "NSE_FNO"
DEFAULT_INSTRUMENT_TYPE = "OPTIDX"
QTY_MODE = "ABSOLUTE"

PINE_PRODUCT_I_MAPS_TO = "INTRADAY"
PINE_ORDER_MKT_MAPS_TO = "MARKET"
PINE_EXCHANGE_NSE_OPT_MAPS_TO = "NSE_FNO"

ALLOW_ONLY_NIFTY = True
ALLOW_ONLY_INTRADAY = True
BLOCK_DUPLICATE_SIGNALS = True
GLOBAL_KILL_SWITCH_BLOCKS_EXITS = False
DASHBOARD_POLL_SECONDS = 2
DEFAULT_NIFTY_LOT_SIZE = 65

DEFAULT_RUNTIME_SETTINGS = {
    # H8 — Optimistic-locking counter; incremented on every settings write.
    # Clients pass the version they last read; mismatched writes return 409.
    "_version": 0,
    "max_qty_per_order": DEFAULT_NIFTY_LOT_SIZE,
    "allowed_option_side": "BOTH",
    "max_trades_per_day": 0,
    "max_daily_loss": 0.0,
    # SL disable — when True, _broker_exit_levels sets the Super Order SL
    # leg to a floor price (~₹0.10 or 1% of entry) so it effectively never
    # fires. Position is exited by opposite Supertrend reversal, TP, or EOD.
    "option_disable_sl": True,
    "server_side_exit_enabled": True,
    "marketfeed_ws_enabled": True,
    "option_ltp_source": "AUTO",
    "option_exit_mode": "DHAN_SUPER",
    "option_ws_stale_seconds": 5.0,
    "option_rest_fallback_enabled": True,
    "option_rest_fallback_cooldown_seconds": 15.0,
    "option_sl_percent": 10.0,
    "option_tp_percent": 20.0,
    "option_ltp_poll_seconds": 1.0,
    "eod_squareoff_enabled": True,
    "allow_entry": True,
    "allow_exit": True,
    "emergency_stop": False,
    "global_kill_switch": False,
    "paper_starting_balance": 100000.0,
    "paper_slippage_percent": 0.10,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        case_sensitive=True,
        extra="ignore",
    )

    APP_ENV: str = "local"

    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8001
    FRONTEND_ORIGIN: str = "http://localhost:5173"
    BACKEND_PUBLIC_BASE_URL: str = "http://localhost:8001"
    SESSION_TOKEN_SECRET: str = "change-me-in-production"
    SESSION_TOKEN_TTL_SECONDS: int = 60 * 60 * 12

    WEBHOOK_TRADING_ENABLED: bool = False
    WEBHOOK_HMAC_REQUIRED: bool = False
    WEBHOOK_RATE_LIMIT_PER_MINUTE: int = 120

    DHAN_MODE: str = "MOCK"
    DHAN_READ_ONLY_REAL_DATA: bool = True
    PAPER_MODE_ENABLED: bool = True
    ENABLE_LIVE_ORDERS: bool = False
    MARKET_CLOSED_DEBUG: bool = False
    FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED: bool = False
    TOKEN_ENCRYPTION_KEY: str = ""
    ALLOW_DEFAULT_SECURITY_ID: bool = False
    DEFAULT_SECURITY_ID: str = ""
    DHAN_SCRIP_MASTER_PATH: str = "data/dhan_scrip_master.csv"
    AUTO_RESOLVE_SECURITY_ID: bool = True

    REQUIRE_MARKET_HOURS: bool = False

    # Token lifetime - Dhan access tokens are short-lived (~24 hours).
    # Warn at TOKEN_WARN_AGE_HOURS; hard-block engine start at TOKEN_MAX_AGE_HOURS.
    TOKEN_MAX_AGE_HOURS: int = 24
    TOKEN_WARN_AGE_HOURS: int = 23

    # Market quote validation before live order placement (phase 2).
    # Set True once POST /marketfeed/ltp is implemented in dhan_client.py.
    QUOTE_REQUIRED_BEFORE_ORDER: bool = False

    # Dhan header: client-id
    # Dhan v2 official docs show only `access-token` as the auth header.
    # The `client-id` header is not documented by Dhan but is sent for compatibility.
    # Set to False to omit it (safe -- dhanClientId is always in the order body).
    DHAN_SEND_CLIENT_ID_HEADER: bool = True
    DHAN_API_MAX_REQUESTS_PER_SECOND: float = 4.0
    DHAN_API_BURST: int = 4
    DHAN_API_MAX_RETRY_AFTER_SECONDS: int = 60

    RUNTIME_STATE_DIR: str = "runtime_state"
    RUNTIME_LOG_DIR: str = "runtime_logs"

    DEBUG_ENABLED: bool = False


settings = Settings()


def _resolve_backend_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return BACKEND_DIR / path


RUNTIME_STATE_DIR = _resolve_backend_path(settings.RUNTIME_STATE_DIR)
RUNTIME_LOG_DIR = _resolve_backend_path(settings.RUNTIME_LOG_DIR)
