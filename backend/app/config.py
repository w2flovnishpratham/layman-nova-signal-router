import os
import stat
import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
PRODUCTION_ENV_FILE = Path("/etc/layman/layman.env")
SETTINGS_ENV_FILE = Path(os.environ.get("LAYMAN_ENV_FILE") or BACKEND_DIR / ".env")

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
        env_file=str(SETTINGS_ENV_FILE),
        case_sensitive=True,
        extra="ignore",
    )

    APP_ENV: str = "local"

    BACKEND_HOST: str = "127.0.0.1"
    BACKEND_PORT: int = 8001
    FRONTEND_ORIGIN: str = "http://localhost:5173"
    BACKEND_PUBLIC_BASE_URL: str = "http://localhost:8001"
    SESSION_TOKEN_SECRET: str = "change-me-in-production"
    SESSION_TOKEN_TTL_SECONDS: int = 60 * 60 * 12

    AUTH_REQUIRED: bool = True
    DATABASE_URL: str = ""
    AUTH_DATABASE_URL: str = ""
    ADMIN_EMAILS: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""
    AUTH_COOKIE_NAME: str = "layman_auth"
    AUTH_COOKIE_TTL_SECONDS: int = 60 * 60 * 24 * 7
    CSRF_COOKIE_NAME: str = "layman_csrf"
    CSRF_HEADER_NAME: str = "X-CSRF-Token"
    OAUTH_STATE_COOKIE_NAME: str = "layman_oauth_state"
    OAUTH_STATE_TTL_SECONDS: int = 10 * 60

    WEBHOOK_TRADING_ENABLED: bool = False
    WEBHOOK_HMAC_REQUIRED: bool = True
    WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS: int = 60
    WEBHOOK_ALLOW_LEGACY_AUTH_LOCAL: bool = False
    REQUIRE_SIGNAL_ID_LIVE: bool = True
    WEBHOOK_RATE_LIMIT_PER_MINUTE: int = 120
    FREE_MAX_ACTIVE_PAPER_STRATEGIES: int = 1
    RELAY_SHARED_SECRET: str = ""
    RELAY_ALLOWED_STRATEGIES: str = "SUPERTREND_FLIP"
    RELAY_ENABLED: bool = False
    LIVE_PILOT_ENABLED: bool = False
    LIVE_PILOT_ALLOWED_STRATEGIES: str = "SUPERTREND_FLIP"
    LIVE_ORDER_DRY_RUN_ONLY: bool = True
    EXECUTOR_SHARED_SECRETS_JSON: str = "{}"
    EXECUTOR_REQUEST_TIMEOUT_SECONDS: float = 10.0
    EXECUTOR_TIMESTAMP_TOLERANCE_SECONDS: int = 60
    EXECUTOR_CODE: str = ""
    EXECUTOR_RESERVED_IP: str = ""
    EXECUTOR_SHARED_SECRET: str = ""
    EXECUTOR_EGRESS_CHECK_URL: str = "https://api.ipify.org?format=json"
    EXECUTOR_REAL_ORDERS_ENABLED: bool = False
    ENABLE_LIVE_PILOT_WORKERS: bool = False

    DHAN_MODE: str = "MOCK"
    DHAN_READ_ONLY_REAL_DATA: bool = True
    PAPER_MODE_ENABLED: bool = True
    ENABLE_LIVE_ORDERS: bool = False
    UNIQUE_EGRESS_PER_USER_REQUIRED: bool = True
    EXECUTION_NODE_ROUTING_ENABLED: bool = False
    WORKER_ROLE: str = "web"
    ENABLE_PAPER_WORKERS: bool = False
    PAPER_WORKER_CONCURRENCY: int = 1
    WORKER_JOB_LOCK_SECONDS: int = 60
    WORKER_POLL_INTERVAL_SECONDS: float = 2.0
    WORKER_HEARTBEAT_SECONDS: float = 10.0
    WORKER_RETRY_BASE_SECONDS: int = 2
    PAPER_QUEUE_INLINE_LOCAL: bool = False
    ENABLE_TRADING_WORKERS: bool = False
    TRADING_WORKER_DISTRIBUTED_LOCK_ENABLED: bool = False
    MARKET_CLOSED_DEBUG: bool = False
    FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED: bool = False
    TOKEN_ENCRYPTION_KEY: str = ""
    ALLOW_DEFAULT_SECURITY_ID: bool = False
    DEFAULT_SECURITY_ID: str = ""
    DHAN_SCRIP_MASTER_PATH: str = "data/dhan_scrip_master.csv"
    AUTO_RESOLVE_SECURITY_ID: bool = True
    REQUIRE_INSTRUMENT_MASTER_VALIDATION_LIVE: bool = True
    REQUIRE_FRESH_EXPIRY_LIVE: bool = True

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


def csv_setting(value: str) -> set[str]:
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def executor_shared_secrets() -> dict[str, str]:
    try:
        parsed = json.loads(settings.EXECUTOR_SHARED_SECRETS_JSON or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("EXECUTOR_SHARED_SECRETS_JSON must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("EXECUTOR_SHARED_SECRETS_JSON must be a JSON object.")
    return {
        str(code).strip().upper(): str(secret).strip()
        for code, secret in parsed.items()
        if str(code).strip() and str(secret).strip()
    }


def _resolve_backend_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return BACKEND_DIR / path


RUNTIME_STATE_DIR = _resolve_backend_path(settings.RUNTIME_STATE_DIR)
RUNTIME_LOG_DIR = _resolve_backend_path(settings.RUNTIME_LOG_DIR)


def validate_production_runtime_paths() -> None:
    if settings.APP_ENV.lower() != "production":
        return

    repo_root = REPO_ROOT.resolve()
    state_dir = RUNTIME_STATE_DIR.resolve()
    log_dir = RUNTIME_LOG_DIR.resolve()
    if state_dir == log_dir:
        raise RuntimeError("RUNTIME_STATE_DIR and RUNTIME_LOG_DIR must be different production directories.")

    for name, path in (("RUNTIME_STATE_DIR", state_dir), ("RUNTIME_LOG_DIR", log_dir)):
        if path == Path(path.anchor):
            raise RuntimeError(f"{name} cannot point to a filesystem root.")
        if path == repo_root or repo_root in path.parents:
            raise RuntimeError(f"{name} must point outside the repository in production.")


def validate_production_environment_file(env_file: Path | None = None) -> None:
    if settings.APP_ENV.lower() != "production":
        return

    configured_path = (env_file or SETTINGS_ENV_FILE).expanduser()
    if not configured_path.is_absolute():
        raise RuntimeError("Production environment file must use an absolute path outside the repository.")

    resolved_path = configured_path.resolve()
    repo_root = REPO_ROOT.resolve()
    if resolved_path == Path(resolved_path.anchor):
        raise RuntimeError("Production environment file cannot point to a filesystem root.")
    if resolved_path == repo_root or repo_root in resolved_path.parents:
        raise RuntimeError("Production environment file must be outside the repository.")
    if os.name != "nt" and resolved_path != PRODUCTION_ENV_FILE:
        raise RuntimeError(f"Production environment file must be {PRODUCTION_ENV_FILE}.")
    if not resolved_path.is_file():
        raise RuntimeError("Production environment file is missing.")
    if os.name != "nt":
        permissions = stat.S_IMODE(resolved_path.stat().st_mode)
        if permissions != 0o600:
            raise RuntimeError("Production environment file permissions must be 0600.")


def ensure_runtime_directories() -> None:
    for path in (RUNTIME_STATE_DIR, RUNTIME_LOG_DIR):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            path.chmod(0o700)
