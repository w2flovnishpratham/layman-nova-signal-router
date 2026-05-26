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

DEFAULT_RUNTIME_SETTINGS = {
    "max_qty_per_order": 1,
    "max_trades_per_day": 1,
    "daily_loss_limit": 500,
    "server_side_exit_enabled": True,
    "option_sl_percent": 10.0,
    "option_tp_percent": 20.0,
    "option_ltp_poll_seconds": 1.0,
    "allow_entry": True,
    "allow_exit": True,
    "emergency_stop": False,
    "global_kill_switch": False,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        case_sensitive=True,
        extra="ignore",
    )

    APP_ENV: str = "local"

    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000
    FRONTEND_ORIGIN: str = "http://localhost:5173"
    BACKEND_PUBLIC_BASE_URL: str = "http://localhost:8000"

    WEBHOOK_TRADING_ENABLED: bool = False

    DHAN_MODE: str = "MOCK"
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
