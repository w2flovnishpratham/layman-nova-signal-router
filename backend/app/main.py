from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import session as chat_session
from app.api import ws as chat_ws
from app.auth.dependencies import get_execution_scoped_user
from app.config import settings
from app.routers import broker, control, dashboard, debug, engine, market, orders, positions, setup, webhook
from app.routers import admin as admin_router
from app.routers import live as live_router
from app.routers import user_credentials as user_credentials_router
from app.routers import user_webhook as user_webhook_router
from app.routers import strategies as strategies_router
from app.auth import google as google_auth
from app.db.engine import database_configured, init_db
from app.services.user_credential_vault import vault_ready as user_vault_ready
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
from app.workers.strategy_job_worker import (
    start_strategy_job_worker,
    stop_strategy_job_worker,
    strategy_job_worker_status,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nova_signal_router")


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


def validate_production_configuration() -> None:
    if settings.APP_ENV.lower() != "production":
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
    if settings.AUTH_REQUIRED:
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
    if settings.ENABLE_LIVE_ORDERS:
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
        from app.services.strategy_fanout import configured_egress_nodes

        if len(configured_egress_nodes()) < 2:
            raise RuntimeError(
                "The two-user live pilot requires at least two configured egress nodes."
            )


def _multi_user_mode() -> bool:
    return settings.AUTH_REQUIRED and database_configured()


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_configuration()
    validate_background_worker_runner_configuration()
    bind_chat_event_loop(asyncio.get_running_loop())
    init_runtime_files()
    sync_runtime_flags_from_env()
    vault = vault_status()
    if settings.APP_ENV.lower() == "production" and not vault["ready"]:
        raise RuntimeError(f"Encrypted credential vault unavailable: {vault['error']}")
    # Multi-user layer: ensure Neon schema exists (additive; paper mode unaffected).
    if database_configured():
        try:
            init_db()
            logger.info("Neon database schema ensured.")
        except Exception as exc:  # pragma: no cover
            if settings.is_production:
                raise
            logger.warning("Database init skipped (dev): %s", exc)
    elif settings.is_production:
        raise RuntimeError("DATABASE_URL must be configured in production.")
    if settings.is_production and not user_vault_ready():
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is missing or invalid; refusing to start in production.")
    start_instrument_cache_warmup()
    background_workers_enabled = bool(settings.BACKGROUND_WORKER_RUNNER_ENABLED)
    if background_workers_enabled and shared_market_data_configured():
        start_shared_token_worker()
        logger.info("Shared market-data token worker enabled (global paper-mode feed).")
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
    stop_shared_token_worker()
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
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

cors_origins = [settings.FRONTEND_ORIGIN, settings.FRONTEND_URL]
if settings.APP_ENV.lower() != "production":
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
app.include_router(orders.router, prefix="/api", tags=["Orders"], dependencies=authenticated)
app.include_router(market.router, prefix="/api", tags=["Market"], dependencies=authenticated)
app.include_router(positions.router, prefix="/api", tags=["Positions"], dependencies=authenticated)
app.include_router(control.router, prefix="/api/control", tags=["Control"], dependencies=authenticated)
app.include_router(broker.router, prefix="/api/broker", tags=["Broker"], dependencies=authenticated)
app.include_router(debug.router, prefix="/api/debug", tags=["Debug"], dependencies=authenticated)
app.include_router(webhook.router, prefix="/webhook", tags=["Webhook"])
app.include_router(webhook.router, prefix="/api/webhook", tags=["Webhook"])
app.include_router(chat_session.router)
app.include_router(chat_ws.router)

# Multi-user SaaS layer (additive — does not alter existing paper-mode routes).
app.include_router(google_auth.router)
app.include_router(user_credentials_router.router)
app.include_router(live_router.router)
app.include_router(user_webhook_router.router)
app.include_router(strategies_router.router)
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
