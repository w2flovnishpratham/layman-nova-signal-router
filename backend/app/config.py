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
DISABLED_OPTION_SL_PERCENT = 99.9
DISABLED_OPTION_SL_PRICE_FRACTION = (100.0 - DISABLED_OPTION_SL_PERCENT) / 100.0

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
    # leg to a floor price (~Rs.0.10 or 0.1% of entry) so it effectively never
    # fires. Position is exited by opposite Supertrend reversal, TP, or EOD.
    "option_disable_sl": True,
    "server_side_exit_enabled": True,
    "marketfeed_ws_enabled": True,
    "option_ltp_source": "AUTO",
    "option_exit_mode": "DHAN_SUPER",
    "option_ws_stale_seconds": 5.0,
    "option_rest_fallback_enabled": True,
    "option_rest_fallback_cooldown_seconds": 15.0,
    "option_sl_percent": DISABLED_OPTION_SL_PERCENT,
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

    # ------------------------------------------------------------------
    # Multi-user / SaaS layer (additive — paper mode keeps working as-is)
    # ------------------------------------------------------------------
    # Neon PostgreSQL connection string. Supplied via environment ONLY.
    # Example: postgresql://user:pass@host/db?sslmode=require&channel_binding=require
    # The DB layer normalizes postgresql:// -> postgresql+psycopg:// at runtime.
    DATABASE_URL: str = ""

    # Auth. When AUTH_REQUIRED=false (local dev) a deterministic anonymous
    # "dev user" is used so the existing single-user paper flow keeps working.
    AUTH_REQUIRED: bool = False

    # App-wide signing secret for the session cookie. In production this MUST be
    # overridden with a long random value (falls back to SESSION_TOKEN_SECRET).
    APP_SECRET_KEY: str = ""

    # Per-user credential encryption key (Fernet). Falls back to the legacy
    # TOKEN_ENCRYPTION_KEY when unset so existing deployments keep working.
    CREDENTIAL_ENCRYPTION_KEY: str = ""

    # Where the encrypted credential vault lives: "db" (per-user, Neon) or
    # "file" (legacy single global vault). Defaults to db for the SaaS build.
    CREDENTIAL_VAULT_BACKEND: str = "db"

    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""
    FRONTEND_URL: str = ""

    # Comma-separated admin emails. Only gates /api/admin/* — never blocks login.
    ADMIN_EMAILS: str = ""

    # Shared secret for the single strategy fan-out webhook (one TradingView
    # account fires one alert per strategy; the backend fans it out to all
    # active subscribers of that strategy). TradingView sends it in the JSON
    # body because TradingView webhooks cannot generate dynamic HMAC headers.
    STRATEGY_WEBHOOK_SECRET: str = ""

    # Master gate for routing real orders through per-user egress nodes.
    EXECUTION_NODE_ROUTING_ENABLED: bool = False

    # Cookie configuration
    SESSION_COOKIE_NAME: str = "nova_session"
    SESSION_COOKIE_SECURE: bool = True
    SESSION_COOKIE_SAMESITE: str = "lax"
    SESSION_COOKIE_DOMAIN: str = ""

    @property
    def app_secret(self) -> str:
        return (self.APP_SECRET_KEY or self.SESSION_TOKEN_SECRET or "").strip()

    @property
    def credential_encryption_key(self) -> str:
        return (self.CREDENTIAL_ENCRYPTION_KEY or self.TOKEN_ENCRYPTION_KEY or "").strip()

    @property
    def admin_emails(self) -> set[str]:
        return {
            item.strip().lower()
            for item in (self.ADMIN_EMAILS or "").split(",")
            if item.strip()
        }

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() == "production"


settings = Settings()


def _resolve_backend_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return BACKEND_DIR / path


RUNTIME_STATE_DIR = _resolve_backend_path(settings.RUNTIME_STATE_DIR)
RUNTIME_LOG_DIR = _resolve_backend_path(settings.RUNTIME_LOG_DIR)
