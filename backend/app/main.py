from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import session as chat_session
from app.api import ws as chat_ws
from app.auth.dependencies import get_execution_scoped_user
from app.config import VALID_PAYMENT_PROVIDERS, settings
from app.routers import broker, control, dashboard, debug, engine, market, orders, payments, positions, setup, signals, webhook
from app.routers import admin as admin_router
from app.routers import live as live_router
from app.routers import user_credentials as user_credentials_router
from app.routers import user_webhook as user_webhook_router
from app.routers import private_webhook as private_webhook_router
from app.routers import c2_tradingview as c2_tradingview_router
from app.routers import strategies as strategies_router
from app.routers import strategy_instances as strategy_instances_router
from app.routers import personal_pine as personal_pine_router
from app.routers import pine_conversion as pine_conversion_router
from app.routers import tradingview_setup as tradingview_setup_router
from app.auth import google as google_auth
from app.db.engine import database_configured, get_engine, init_db
from app.services.user_credential_vault import vault_ready as user_vault_ready
from app.services.pine_conversion_provider import validate_provider_configuration
from app.workers.pine_conversion_worker import start_pine_conversion_worker, stop_pine_conversion_worker
from app.services.audit_logger import log_audit_event
from app.services.chat_event_publisher import bind_chat_event_loop, clear_chat_event_loop
from app.services.credential_vault import vault_status
from app.services.instrument_resolver import start_instrument_cache_warmup
from app.services.shared_market_data import (
    shared_market_data_configured,
    start_shared_token_worker,
    stop_shared_token_worker,
)
from app.services.option_position_monitor import start_option_position_monitor, stop_option_position_monitor
from app.services.state_store import get_app_state, get_engine_mode, get_runtime_settings, init_runtime_files, sync_runtime_flags_from_env
from app.services.startup_reconciler import reconcile_open_position_on_startup
from app.workers.eod_squareoff import start_eod_squareoff_worker, stop_eod_squareoff_worker
from app.workers.ghost_position_watcher import start_ghost_position_watcher, stop_ghost_position_watcher
from app.workers.nifty_candle_pusher import start_nifty_candle_pusher, stop_nifty_candle_pusher
from app.workers.strategy_job_worker import (
    start_strategy_job_worker,
    stop_strategy_job_worker,
    strategy_job_worker_status,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nova_signal_router")


def _current_alembic_heads() -> set[str]:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Alembic migration metadata is unavailable; run `python -m alembic upgrade head` "
            "before starting production."
        ) from exc

    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    heads = set(ScriptDirectory.from_config(config).get_heads())
    if not heads:
        raise RuntimeError("Alembic migration metadata has no head revision.")
    return heads


def validate_production_database_migration_state() -> None:
    """Fail closed if production is not already migrated by Alembic."""
    if not settings.is_production:
        return
    if not database_configured():
        raise RuntimeError("DATABASE_URL must be configured in production.")

    expected_heads = _current_alembic_heads()
    try:
        from sqlalchemy import inspect, text

        engine = get_engine()
        with engine.connect() as connection:
            table_names = set(inspect(connection).get_table_names())
            if "alembic_version" not in table_names:
                raise RuntimeError(
                    "Database schema is not Alembic-managed. Run `python -m alembic upgrade head` "
                    "before starting production."
                )
            versions = {
                str(row[0])
                for row in connection.execute(text("SELECT version_num FROM alembic_version")).all()
                if row[0]
            }
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "Database migration validation failed. Run `python -m alembic upgrade head` "
            "before starting production."
        ) from exc

    if versions != expected_heads:
        raise RuntimeError(
            "Database schema is not at the current Alembic head. Run `python -m alembic upgrade head` "
            "before starting production."
        )
    logger.info("Database Alembic migration state verified.")


def ensure_database_schema_ready_for_startup() -> None:
    if settings.is_production:
        validate_production_database_migration_state()
        return
    if database_configured():
        try:
            init_db()
            logger.info("Development database schema ensured via SQLAlchemy create_all.")
        except Exception as exc:  # pragma: no cover
            logger.warning("Database init skipped (dev): %s", exc)


def _configured_web_worker_count() -> int:
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        raw = os.environ.get(name)
        if raw in (None, ""):
            continue
        try:
            return max(int(raw), 1)
        except ValueError:
            logger.warning("Ignoring invalid %s=%r.", name, raw)
    return 1


def validate_background_worker_runner_configuration() -> None:
    worker_count = _configured_web_worker_count()
    if settings.BACKGROUND_WORKER_RUNNER_ENABLED and worker_count > 1:
        raise RuntimeError(
            "Multiple web workers were requested while "
            "BACKGROUND_WORKER_RUNNER_ENABLED=true. Singleton workers such as "
            "Dhan WS, option monitor, shared-token refresh, EOD, ghost watcher, "
            "and strategy jobs must run in exactly one process. Use one web "
            "worker, or set BACKGROUND_WORKER_RUNNER_ENABLED=false on API-only "
            "workers and run a dedicated worker process."
        )


def _validate_live_aws_proxy_slots() -> None:
    if not settings.AWS_PROXY_SLOTS_ENABLED:
        return

    from app.services.aws_proxy_slots import AWSProxySlotConfigError, build_aws_proxy_slots

    try:
        slots = build_aws_proxy_slots(settings)
    except AWSProxySlotConfigError as exc:
        raise RuntimeError(f"AWS proxy slot configuration invalid: {exc}") from exc
    if not slots:
        raise RuntimeError("AWS proxy slots are enabled but no AWS proxy slots were built.")


def _validate_live_payment_provider_configuration() -> None:
    provider = settings.payment_provider_normalized
    if provider != "razorpay":
        raise RuntimeError("ENABLE_LIVE_ORDERS=true requires PAYMENT_PROVIDER=razorpay.")
    for name, value in (
        ("RAZORPAY_KEY_ID", settings.RAZORPAY_KEY_ID),
        ("RAZORPAY_KEY_SECRET", settings.RAZORPAY_KEY_SECRET),
        ("RAZORPAY_WEBHOOK_SECRET", settings.RAZORPAY_WEBHOOK_SECRET),
        ("RAZORPAY_PLAN_PREMIUM_MONTHLY", settings.RAZORPAY_PLAN_PREMIUM_MONTHLY),
    ):
        if not (value or "").strip():
            raise RuntimeError(f"ENABLE_LIVE_ORDERS=true requires {name}.")


def validate_production_configuration() -> None:
    if settings.ENABLE_TEST_MARKET_DATA_PROVIDER and settings.APP_ENV.strip().lower() not in {"test", "isolated_staging"}:
        raise RuntimeError("ENABLE_TEST_MARKET_DATA_PROVIDER is allowed only in test or isolated_staging.")
    if settings.ENABLE_TEST_MARKET_DATA_PROVIDER and (settings.ENABLE_LIVE_ORDERS or settings.DHAN_MODE.upper() != "MOCK"):
        raise RuntimeError("ENABLE_TEST_MARKET_DATA_PROVIDER requires ENABLE_LIVE_ORDERS=false and DHAN_MODE=MOCK.")
    if not settings.is_production:
        return

    session_secret = settings.SESSION_TOKEN_SECRET.strip()
    if session_secret == "change-me-in-production" or len(session_secret) < 32:
        raise RuntimeError("SESSION_TOKEN_SECRET must be overridden with at least 32 random characters in production.")
    if settings.DHAN_MODE.upper() != "REAL":
        raise RuntimeError("Production requires DHAN_MODE=REAL; mock routing is only allowed for local development and tests.")
    if settings.DEBUG_ENABLED:
        raise RuntimeError("DEBUG_ENABLED must be false in production.")
    if settings.ALLOW_DEFAULT_SECURITY_ID or (settings.DEFAULT_SECURITY_ID or "").strip():
        raise RuntimeError("DEFAULT_SECURITY_ID overrides are not allowed in production.")

    # Multi-user SaaS layer requirements in production.
    if len(settings.app_secret) < 32:
        raise RuntimeError("APP_SECRET_KEY must be set to at least 32 random characters in production.")
    if not settings.credential_encryption_key:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY (or TOKEN_ENCRYPTION_KEY) must be set in production; refusing to start."
        )
    if not database_configured():
        raise RuntimeError("DATABASE_URL must be set in production (Neon PostgreSQL).")
    if not settings.AUTH_REQUIRED:
        raise RuntimeError("AUTH_REQUIRED must be true in production.")
    for name, value in (
        ("GOOGLE_CLIENT_ID", settings.GOOGLE_CLIENT_ID),
        ("GOOGLE_CLIENT_SECRET", settings.GOOGLE_CLIENT_SECRET),
        ("GOOGLE_REDIRECT_URI", settings.GOOGLE_REDIRECT_URI),
    ):
        if not (value or "").strip():
            raise RuntimeError(f"AUTH_REQUIRED=true but {name} is not configured.")
    if len((settings.STRATEGY_WEBHOOK_SECRET or "").strip()) < 24:
        raise RuntimeError(
            "STRATEGY_WEBHOOK_SECRET must be set to at least 24 random characters in production."
        )
    if not settings.WEBHOOK_HMAC_REQUIRED:
        raise RuntimeError("WEBHOOK_HMAC_REQUIRED must be true in production.")
    if settings.payment_provider_normalized not in VALID_PAYMENT_PROVIDERS:
        raise RuntimeError("PAYMENT_PROVIDER must be one of: none, razorpay.")
    if settings.PAYMENT_SIMULATION_ENABLED:
        raise RuntimeError("PAYMENT_SIMULATION_ENABLED must be false in production.")
    if settings.ENABLE_LIVE_ORDERS:
        _validate_live_payment_provider_configuration()
        if settings.DHAN_READ_ONLY_REAL_DATA:
            raise RuntimeError(
                "ENABLE_LIVE_ORDERS=true requires DHAN_READ_ONLY_REAL_DATA=false."
            )
        if not settings.EXECUTION_NODE_ROUTING_ENABLED:
            raise RuntimeError(
                "ENABLE_LIVE_ORDERS=true requires EXECUTION_NODE_ROUTING_ENABLED=true."
            )
        if not settings.STRATEGY_JOB_WORKER_ENABLED:
            raise RuntimeError(
                "ENABLE_LIVE_ORDERS=true requires STRATEGY_JOB_WORKER_ENABLED=true."
            )
        if settings.EXECUTION_NODE_ROUTING_ENABLED and settings.AWS_PROXY_SLOTS_ENABLED:
            _validate_live_aws_proxy_slots()
        from app.services.strategy_fanout import configured_egress_nodes

        if len(configured_egress_nodes()) < 2:
            raise RuntimeError(
                "The two-user live pilot requires at least two configured egress nodes."
            )


def _multi_user_mode() -> bool:
    return settings.AUTH_REQUIRED and database_configured()


def _api_documentation_urls() -> dict[str, str | None]:
    if settings.is_production:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {
        "docs_url": "/api/docs",
        "redoc_url": "/api/redoc",
        "openapi_url": "/openapi.json",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_provider_configuration()
    validate_production_configuration()
    validate_background_worker_runner_configuration()
    bind_chat_event_loop(asyncio.get_running_loop())
    init_runtime_files()
    sync_runtime_flags_from_env()
    vault = vault_status()
    if settings.is_production and not vault["ready"]:
        raise RuntimeError(f"Encrypted credential vault unavailable: {vault['error']}")
    ensure_database_schema_ready_for_startup()
    if settings.is_production and not user_vault_ready():
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is missing or invalid; refusing to start in production.")
    start_instrument_cache_warmup()
    background_workers_enabled = bool(settings.BACKGROUND_WORKER_RUNNER_ENABLED)
    if background_workers_enabled and shared_market_data_configured():
        start_shared_token_worker()
        logger.info("Shared market-data token worker enabled (global paper-mode feed).")
    if background_workers_enabled:
        # Global market data, one push worker per engine process regardless
        # of single- vs multi-user mode -- unlike the per-user option monitor
        # below, this isn't scoped to any particular user's positions.
        start_nifty_candle_pusher()
    runtime_settings = get_runtime_settings()
    logger.info("NOVA Signal Router backend starting.")
    logger.info("APP_ENV=%s", settings.APP_ENV)
    logger.info("DHAN_MODE=%s", settings.DHAN_MODE.upper())
    logger.info("ENGINE_MODE=%s", get_engine_mode(legacy_fallback=False))
    logger.info("ENABLE_LIVE_ORDERS=%s", settings.ENABLE_LIVE_ORDERS)
    logger.info("MARKET_CLOSED_DEBUG=%s", settings.MARKET_CLOSED_DEBUG)
    logger.info("FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED=%s", settings.FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED)
    logger.info("WEBHOOK_TRADING_ENABLED=%s", get_app_state().get("webhook_trading_enabled"))
    logger.info("EMERGENCY_STOP=%s", runtime_settings.get("emergency_stop"))
    logger.info("GLOBAL_KILL_SWITCH=%s", runtime_settings.get("global_kill_switch"))
    if settings.DHAN_MODE.upper() == "REAL":
        logger.warning("REAL DHAN MODE CONFIGURED.")
    if settings.ENABLE_LIVE_ORDERS:
        logger.warning("LIVE ORDERS ENABLED. REAL MONEY ORDERS MAY BE SENT AFTER RISK CHECKS.")
    if not background_workers_enabled:
        logger.warning("Singleton background workers are disabled for this process.")
    if background_workers_enabled:
        start_strategy_job_worker()
        start_pine_conversion_worker()
    if background_workers_enabled and _multi_user_mode():
        logger.info(
            "Multi-user reconcile, position monitor, EOD, and ghost workers enabled."
        )
        from app.services.execution_context import bind_user_execution_context
        from app.services.strategy_fanout import (
            active_routing_user_ids,
            load_user_context,
        )

        for user_id in active_routing_user_ids(real_orders_only=True):
            user = load_user_context(user_id)
            if user is None:
                continue
            try:
                with bind_user_execution_context(user):
                    reconcile_open_position_on_startup()
            except Exception:
                logger.exception("Per-user startup reconciliation failed for %s", user.id_str)
        start_option_position_monitor()
        start_eod_squareoff_worker()
        start_ghost_position_watcher()
    elif background_workers_enabled:
        reconcile_open_position_on_startup()
        start_option_position_monitor()
        # EOD square-off worker (15:15 IST). Runs as daemon; idempotent.
        start_eod_squareoff_worker()
        start_ghost_position_watcher()
    log_audit_event(
        "APP_START",
        "NOVA Signal Router backend started.",
        metadata={
            "app_env": settings.APP_ENV,
            "dhan_mode": settings.DHAN_MODE.upper(),
            "engine_mode": get_engine_mode(legacy_fallback=False),
            "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
            "debug_enabled": settings.DEBUG_ENABLED,
        },
    )
    yield
    stop_strategy_job_worker()
    stop_pine_conversion_worker()
    stop_shared_token_worker()
    stop_nifty_candle_pusher()
    stop_option_position_monitor()
    stop_eod_squareoff_worker()
    stop_ghost_position_watcher()
    clear_chat_event_loop()
    logger.info("NOVA Signal Router shutting down.")


app = FastAPI(
    title="NOVA Signal Router",
    description="TradingView webhook to Dhan signal router with server-side credentials and file-backed MVP runtime state.",
    version="1.0.0-mvp",
    lifespan=lifespan,
    **_api_documentation_urls(),
)

cors_origins = [settings.FRONTEND_ORIGIN, settings.FRONTEND_URL]
if not settings.is_production:
    cors_origins.extend(["http://localhost:5173", "http://127.0.0.1:5173"])
cors_origins = [origin.rstrip("/") for origin in dict.fromkeys(cors_origins) if origin]

# allow_credentials=True is required so the auth session cookie is sent on
# cross-origin API calls. Origins must therefore be explicit (never "*").
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

authenticated = [Depends(get_execution_scoped_user)]

app.include_router(setup.router, prefix="/api", tags=["Setup"], dependencies=authenticated)
app.include_router(engine.router, prefix="/api", tags=["Engine"], dependencies=authenticated)
app.include_router(dashboard.router, prefix="/api", tags=["Dashboard"], dependencies=authenticated)
app.include_router(signals.router, prefix="/api", tags=["Signals"], dependencies=authenticated)
app.include_router(orders.router, prefix="/api", tags=["Orders"], dependencies=authenticated)
app.include_router(market.router, prefix="/api", tags=["Market"], dependencies=authenticated)
app.include_router(positions.router, prefix="/api", tags=["Positions"], dependencies=authenticated)
app.include_router(control.router, prefix="/api/control", tags=["Control"], dependencies=authenticated)
app.include_router(broker.router, prefix="/api/broker", tags=["Broker"], dependencies=authenticated)
app.include_router(debug.router, prefix="/api/debug", tags=["Debug"], dependencies=authenticated)
app.include_router(webhook.router, prefix="/webhook", tags=["Webhook"])
app.include_router(webhook.router, prefix="/api/webhook", tags=["Webhook"])
app.include_router(payments.router)
app.include_router(chat_session.router)
app.include_router(chat_ws.router)

# Multi-user SaaS layer (additive — does not alter existing paper-mode routes).
app.include_router(google_auth.router)
app.include_router(user_credentials_router.router)
app.include_router(live_router.router)
app.include_router(user_webhook_router.router)
app.include_router(strategies_router.router)
app.include_router(private_webhook_router.router)
app.include_router(c2_tradingview_router.router)
app.include_router(c2_tradingview_router.admin_router)
app.include_router(strategy_instances_router.router)
app.include_router(strategy_instances_router.admin_router)
app.include_router(strategy_instances_router.engine_router)
app.include_router(personal_pine_router.router)
app.include_router(personal_pine_router.link_router)
app.include_router(personal_pine_router.admin_router)
app.include_router(pine_conversion_router.router)
app.include_router(pine_conversion_router.admin_router)
app.include_router(tradingview_setup_router.router)
app.include_router(tradingview_setup_router.admin_router)
app.include_router(admin_router.router)


def _health() -> dict:
    runtime_settings = get_runtime_settings()
    app_state = get_app_state()
    return {
        "status": "ok",
        "app_env": settings.APP_ENV,
        "dhan_mode": settings.DHAN_MODE.upper(),
        "engine_mode": get_engine_mode(legacy_fallback=False),
        "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
        "private_webhook_execution_enabled": settings.PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED,
        "private_webhook_live_execution_enabled": settings.PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED,
        "market_closed_debug": settings.MARKET_CLOSED_DEBUG,
        "force_allow_order_when_market_closed": settings.FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED,
        "webhook_trading_enabled": bool(app_state.get("webhook_trading_enabled")),
        "emergency_stop": runtime_settings.get("emergency_stop"),
        "global_kill_switch": runtime_settings.get("global_kill_switch"),
        "execution_node_routing_enabled": settings.EXECUTION_NODE_ROUTING_ENABLED,
        "multi_user_mode": _multi_user_mode(),
        "legacy_single_user_workers_enabled": not _multi_user_mode(),
        "strategy_job_worker": strategy_job_worker_status(),
    }


@app.get("/api/health", tags=["Health"])
def api_health() -> dict:
    return _health()


@app.get("/health", tags=["Health"])
def health() -> dict:
    return _health()
