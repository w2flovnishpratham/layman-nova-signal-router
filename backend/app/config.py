import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.trading_constants import (
    DEFAULT_EXCHANGE_SEGMENT,  # noqa: F401 - compatibility re-export
)

BACKEND_DIR = Path(__file__).resolve().parents[1]

SUPPORT_NOVA_PAYLOAD = True
SUPPORT_PINE_MULTI_LEG_PAYLOAD = True

DEFAULT_STRATEGY_CODE = "TRADINGVIEW_NIFTY_V1"
DEFAULT_PRODUCT_TYPE = "INTRADAY"
DEFAULT_ORDER_TYPE = "MARKET"
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
VALID_PAYMENT_PROVIDERS = {"none", "razorpay"}

DEFAULT_RUNTIME_SETTINGS = {
    # H8 — Optimistic-locking counter; incremented on every settings write.
    # Clients pass the version they last read; mismatched writes return 409.
    "_version": 0,
    # Revision of the last committed strategy+risk configuration. Both halves
    # carry the same number, so a torn save is detectable before the engine starts.
    "configuration_revision": 0,
    "allowed_option_side": "BOTH",
    "configured_lots": 1,
    "max_trades_per_day": 0,
    "max_daily_loss": 0.0,
    # No new entries after this IST time ("HH:MM"). Empty disables the cutoff.
    # It never blocks exits, so an open position can always be closed.
    "entry_cutoff_ist": "",
    # SL disable — when True, _broker_exit_levels sets the Super Order SL
    # leg to a floor price (~Rs.0.10 or 0.1% of entry) so it effectively never
    # fires. Position is exited by opposite Supertrend reversal, TP, or EOD.
    "option_disable_sl": True,
    "server_side_exit_enabled": True,
    "marketfeed_ws_enabled": True,
    "option_ltp_source": "AUTO",
    "option_exit_mode": "DHAN_SUPER",
    "option_ws_stale_seconds": 2.0,
    "option_rest_fallback_enabled": True,
    "option_rest_fallback_cooldown_seconds": 3.0,
    "option_ltp_cache_seconds": 1.0,
    "option_sl_percent": DISABLED_OPTION_SL_PERCENT,
    "option_tp_percent": 20.0,
    # Must satisfy risk_settings_valid()'s floor (>= 1) and the setup API schemas
    # (ge=1.0). A sub-1 default silently fails engine-start readiness for any user
    # who configures via the chat flow (which never writes this field).
    "option_ltp_poll_seconds": 1.0,
    "eod_squareoff_enabled": True,
    "allow_entry": True,
    "allow_exit": True,
    "emergency_stop": False,
    "global_kill_switch": False,
    "paper_starting_balance": 1000000.0,
    "paper_slippage_percent": 0.10,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("LAYMAN_ENV_FILE", str(BACKEND_DIR / ".env")),
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
    # Phase 2A: shadow dual-write of JSON position state into PostgreSQL.
    # JSON remains the execution read authority while this is rolled out.
    POSITION_DB_SHADOW_WRITE_ENABLED: bool = False
    # Hard budget for one shadow write (statement_timeout) and its lock waits
    # (lock_timeout) on PostgreSQL; writes exceeding the budget count as
    # failures and feed the circuit breaker.
    POSITION_DB_SHADOW_WRITE_TIMEOUT_MS: int = 1500
    POSITION_DB_SHADOW_LOCK_TIMEOUT_MS: int = 500
    # Consecutive failures that open the breaker, and how long it stays open
    # before a half-open recovery probe.
    # Phase 2B1: explicit typed position operations write the PG ledger from
    # the business transition points. PRECEDENCE: when this is on, the generic
    # diff-based shadow hook is fully inert (POSITION_DB_SHADOW_WRITE_ENABLED
    # then only labels the legacy generic mechanism). JSON remains the
    # execution read authority either way.
    POSITION_DB_TYPED_WRITES_ENABLED: bool = False
    POSITION_DB_READ_SHADOW_ENABLED: bool = False
    POSITION_DB_READ_SHADOW_TIMEOUT_MS: int = 250
    POSITION_DB_READ_SHADOW_LOCK_TIMEOUT_MS: int = 100
    POSITION_DB_READ_SHADOW_FAILURE_THRESHOLD: int = 5
    POSITION_DB_READ_SHADOW_CIRCUIT_OPEN_SECONDS: int = 30
    POSITION_DB_READ_SHADOW_SAMPLE_RATE: float = 1.0
    POSITION_DB_READ_SHADOW_GRACE_MS: int = 500
    ENABLE_TEST_MARKET_DATA_PROVIDER: bool = False
    POSITION_DB_SHADOW_FAILURE_THRESHOLD: int = 5
    POSITION_DB_SHADOW_CIRCUIT_OPEN_SECONDS: int = 60
    WEBHOOK_HMAC_REQUIRED: bool = False
    WEBHOOK_RATE_LIMIT_PER_MINUTE: int = 120
    WEBHOOK_REPLAY_RETENTION_SECONDS: int = 60 * 60 * 24 * 3

    # Phase 3A: private strategy-instance webhook execution. Master rollout
    # gate — when false the private webhook endpoint accepts nothing and
    # creates no signals/jobs. Live private-webhook execution stays behind its
    # own additional flag and remains disabled for the whole phase.
    PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED: bool = False
    PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED: bool = False
    CANONICAL_SIGNAL_SHADOW: bool = False
    # R1B persistence flags. Server-side only (never API/webhook/frontend
    # controllable); missing or invalid values resolve to False via the
    # fail-safe validator below. R1B-2B connects decision evidence only to
    # freshly committed HOLD signals; trading actions, outcomes and rejections
    # remain disconnected.
    R1B_CANONICAL_DECISION_PERSISTENCE: bool = False
    R1B_CANONICAL_OUTCOME_PERSISTENCE: bool = False
    R1B_SIGNAL_REJECTION_PERSISTENCE: bool = False
    R1B_PINE_ANALYSIS_PERSISTENCE: bool = False
    PRIVATE_WEBHOOK_MAX_AGE_SECONDS: int = 300
    PRIVATE_WEBHOOK_MAX_FUTURE_SKEW_SECONDS: int = 30
    PRIVATE_WEBHOOK_MAX_BODY_BYTES: int = 4096
    PRIVATE_WEBHOOK_RATE_LIMIT_PER_MINUTE: int = 60
    PERSONAL_PINE_MAX_SOURCE_BYTES: int = 262144
    PINE_CONVERSION_MANUAL_PACKAGE_ENABLED: bool = True
    PINE_CONVERSION_AI_ENABLED: bool = False
    PINE_CONVERSION_PROVIDER: str = ""
    PINE_CONVERSION_PROVIDER_URL: str = ""
    PINE_CONVERSION_PROVIDER_API_KEY: str = ""
    PINE_CONVERSION_MODEL: str = ""
    PINE_CONVERSION_TIMEOUT_SECONDS: int = 120
    PINE_CONVERSION_MAX_RETRIES: int = 1
    PINE_CONVERSION_MAX_DAILY_REQUESTS_PER_USER: int = 10
    PINE_CONVERSION_MAX_CONCURRENT_PER_USER: int = 1
    PINE_CONVERSION_GLOBAL_CONCURRENCY: int = 4
    PINE_CONVERSION_MAX_SOURCE_BYTES: int = 262144
    PINE_CONVERSION_PROMPT_VERSION: str = "v2"
    PINE_CONVERSION_QUALIFICATION_PROMPT_VERSION: str = "v3.1"
    PINE_CONVERSION_QUALIFICATION_PACKAGE_ENABLED: bool = False
    PINE_CONVERSION_WORKER_POLL_SECONDS: float = 1.0
    PINE_CONVERSION_STALE_SECONDS: int = 300
    # Admin-only C1 Claude conversion. This is independent from the existing
    # owner opt-in queue and remains disabled unless explicitly configured.
    CLAUDE_CONVERSION_ENABLED: bool = False
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_CONVERSION_MODEL: str = ""
    CLAUDE_CONVERSION_TIMEOUT_SECONDS: int = 1800
    CLAUDE_CONVERSION_MAX_REPAIRS: int = 5
    CLAUDE_CONVERSION_MAX_INPUT_TOKENS: int = 120000
    CLAUDE_CONVERSION_MAX_OUTPUT_TOKENS: int = 64000
    CLAUDE_CONVERSION_DAILY_ADMIN_LIMIT: int = 10
    CLAUDE_CONVERSION_DAILY_GLOBAL_LIMIT: int = 50
    # C2 product installation controls. Disabled by default; read-only C1 and
    # the pre-existing private webhook behavior remain available independently.
    C2_TRADINGVIEW_INSTALLATION_ENABLED: bool = False

    @field_validator(
        "CANONICAL_SIGNAL_SHADOW",
        "R1B_CANONICAL_DECISION_PERSISTENCE",
        "R1B_CANONICAL_OUTCOME_PERSISTENCE",
        "R1B_SIGNAL_REJECTION_PERSISTENCE",
        "R1B_PINE_ANALYSIS_PERSISTENCE",
        mode="before",
    )
    @classmethod
    def _safe_shadow_flag(cls, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off", ""}:
                return False
        return False

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

    # ------------------------------------------------------------------
    # Shared market-data account (global LTP/feed for all paper users).
    # A dedicated, data-only Dhan account whose access token is refreshed
    # automatically from a TOTP secret. Supplied via environment ONLY and
    # never logged. When enabled, paper mode needs no per-user Dhan token.
    # ------------------------------------------------------------------
    DHAN_SHARED_DATA_ENABLED: bool = False
    DHAN_SHARED_CLIENT_ID: str = ""
    DHAN_SHARED_PIN: str = ""
    DHAN_SHARED_TOTP_SECRET: str = ""

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
    # Dhan's Market Quote / LTP endpoint (/marketfeed/ltp) is throttled to
    # 1 request/second — far stricter than the general API budget above. A
    # dedicated limiter serializes these calls so they QUEUE (wait) instead of
    # being rejected with HTTP 429 (which previously blocked SL/TP exits).
    DHAN_QUOTE_MAX_REQUESTS_PER_SECOND: float = 1.0
    DHAN_QUOTE_BURST: int = 1

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

    # Durable fan-out worker. The webhook commits one job per subscribed user
    # before returning 202, then this worker executes those jobs independently.
    STRATEGY_JOB_WORKER_ENABLED: bool = True
    STRATEGY_JOB_WORKER_POLL_SECONDS: float = 0.5
    STRATEGY_JOB_WORKER_CONCURRENCY: int = 4
    STRATEGY_JOB_STALE_SECONDS: int = 120

    # Singleton background components (Dhan WS, monitor, EOD, ghost watcher,
    # shared-token refresh, durable job worker) must run in exactly one process.
    # Set false on any extra API-only web workers.
    BACKGROUND_WORKER_RUNNER_ENABLED: bool = True

    # Server-only JSON list of selectable egress nodes. Proxy URLs may contain
    # credentials and are never returned to the frontend. public_ip is the
    # outbound address Dhan sees; the proxy URL may use a different Reserved IP.
    # Example:
    # [{"public_ip":"203.0.113.10","proxy_url":"http://user:pass@198.51.100.20:8888"}]
    EGRESS_NODES_JSON: str = "[]"

    # AWS static-IP proxy slots. Password values are server-only secrets used
    # only to build backend HTTP proxy URLs; never return them to clients.
    AWS_PROXY_SLOTS_ENABLED: bool = False
    AWS_PROXY_HOST: str = "13.203.58.220"
    AWS_PROXY_SHARED_PASSWORD: str = ""
    AWS_PROXY_SLOT_1_PASSWORD: str = ""
    AWS_PROXY_SLOT_2_PASSWORD: str = ""
    AWS_PROXY_SLOT_3_PASSWORD: str = ""
    AWS_PROXY_SLOT_4_PASSWORD: str = ""
    AWS_PROXY_SLOT_5_PASSWORD: str = ""

    # Payment provider configuration. Razorpay is the selected provider for
    # Nova subscriptions, but local/dev can leave secrets empty while live
    # orders are disabled. Secrets are backend-only and must never reach clients.
    PAYMENT_PROVIDER: str = "none"
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""
    RAZORPAY_PLAN_PREMIUM_MONTHLY: str = ""
    # One-time purchase gating paper-mode engine start (independent of the
    # live/static-IP/strategy-access bundle above).
    RAZORPAY_PLAN_PAPER_PREMIUM: str = ""
    # Deprecated split-plan IDs retained only for legacy webhook compatibility.
    # New checkout creation uses RAZORPAY_PLAN_PREMIUM_MONTHLY.
    RAZORPAY_PLAN_LIVE_MONTHLY: str = ""
    RAZORPAY_PLAN_STATIC_IP_MONTHLY: str = ""
    RAZORPAY_PLAN_STRATEGY_MONTHLY: str = ""
    PAYMENT_SIMULATION_ENABLED: bool = False

    # Server-side defaults for per-user strategy fan-out risk gates. A value of
    # 0 disables the cap until a user or user+strategy override is saved.
    STRATEGY_MAX_LOTS_PER_ORDER: int = 0
    STRATEGY_MAX_NOTIONAL_PER_TRADE_PAISE: int = 0
    STRATEGY_MAX_ORDERS_PER_DAY: int = 0
    STRATEGY_MAX_LOSS_PER_DAY_PAISE: int = 0

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
        return self.APP_ENV.strip().lower() in {"production", "prod"}

    @property
    def payment_provider_normalized(self) -> str:
        return (self.PAYMENT_PROVIDER or "none").strip().lower()


settings = Settings()


def _resolve_backend_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return BACKEND_DIR / path


RUNTIME_STATE_DIR = _resolve_backend_path(settings.RUNTIME_STATE_DIR)
RUNTIME_LOG_DIR = _resolve_backend_path(settings.RUNTIME_LOG_DIR)
