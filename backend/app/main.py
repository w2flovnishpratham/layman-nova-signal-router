from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api import session as chat_session
from app.api import ws as chat_ws
from app.auth.db import init_database
from app.auth import router as auth_router
from app.auth.security import auth_enabled
from app.auth.service import admin_emails
from app.config import (
    ensure_runtime_directories,
    executor_shared_secrets,
    settings,
    validate_production_environment_file,
    validate_production_runtime_paths,
)
from app.middleware.user_scope import UserRuntimeScopeMiddleware
from app.routers import broker, connections, control, dashboard, debug, engine, live_pilot, orders, positions, relay, safety, setup, strategies, webhook
from app.services.audit_logger import log_audit_event
from app.services.chat_event_publisher import bind_chat_event_loop, clear_chat_event_loop
from app.services.credential_vault import vault_status
from app.services.instrument_resolver import start_instrument_cache_warmup
from app.services.option_position_monitor import start_option_position_monitor, stop_option_position_monitor
from app.services.readiness import readiness_payload
from app.services.state_store import get_app_state, get_engine_mode, get_runtime_settings, init_runtime_files, sync_runtime_flags_from_env
from app.services.strategy_catalog_service import seed_strategy_catalog
from app.services.startup_reconciler import reconcile_open_position_on_startup
from app.services.worker_policy import (
    configured_worker_role,
    validate_paper_worker_configuration,
    validate_live_pilot_worker_configuration,
    validate_worker_configuration,
)
from app.workers.eod_squareoff import start_eod_squareoff_worker, stop_eod_squareoff_worker
from app.workers.ghost_position_watcher import start_ghost_position_watcher, stop_ghost_position_watcher


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nova_signal_router")


def validate_production_configuration() -> None:
    role = configured_worker_role()
    validate_worker_configuration()
    if role == "paper-worker":
        validate_paper_worker_configuration()
    if role == "live-pilot-worker":
        validate_live_pilot_worker_configuration()
    if settings.APP_ENV.lower() != "production":
        return

    session_secret = settings.SESSION_TOKEN_SECRET.strip()
    if session_secret == "change-me-in-production" or len(session_secret) < 32:
        raise RuntimeError("SESSION_TOKEN_SECRET must be overridden with at least 32 random characters in production.")
    if not settings.AUTH_REQUIRED:
        raise RuntimeError("AUTH_REQUIRED must be true in production.")
    if not admin_emails():
        raise RuntimeError("ADMIN_EMAILS must contain at least one allowed login email in production.")
    database_url = settings.DATABASE_URL.strip() or settings.AUTH_DATABASE_URL.strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL or AUTH_DATABASE_URL is required in production.")
    if database_url.startswith("sqlite"):
        raise RuntimeError("PostgreSQL DATABASE_URL is required in production; SQLite is not allowed.")
    if not settings.WEBHOOK_HMAC_REQUIRED:
        raise RuntimeError("WEBHOOK_HMAC_REQUIRED must be true in production.")
    if settings.DHAN_MODE.upper() != "REAL":
        raise RuntimeError("Production requires DHAN_MODE=REAL; mock routing is only allowed for local development and tests.")
    if settings.DEBUG_ENABLED:
        raise RuntimeError("DEBUG_ENABLED must be false in production.")
    if settings.WEBHOOK_ALLOW_LEGACY_AUTH_LOCAL:
        raise RuntimeError("WEBHOOK_ALLOW_LEGACY_AUTH_LOCAL must be false in production.")
    if settings.PAPER_QUEUE_INLINE_LOCAL:
        raise RuntimeError("PAPER_QUEUE_INLINE_LOCAL must be false in production.")
    if not 5 <= settings.WORKER_JOB_LOCK_SECONDS <= 3600:
        raise RuntimeError("WORKER_JOB_LOCK_SECONDS must be between 5 and 3600.")
    if not 0.1 <= settings.WORKER_POLL_INTERVAL_SECONDS <= 60:
        raise RuntimeError("WORKER_POLL_INTERVAL_SECONDS must be between 0.1 and 60.")
    if not 1 <= settings.WORKER_HEARTBEAT_SECONDS <= 300:
        raise RuntimeError("WORKER_HEARTBEAT_SECONDS must be between 1 and 300.")
    if not 1 <= settings.WORKER_RETRY_BASE_SECONDS <= 300:
        raise RuntimeError("WORKER_RETRY_BASE_SECONDS must be between 1 and 300.")
    if not 1 <= settings.WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS <= 300:
        raise RuntimeError("WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS must be between 1 and 300 in production.")
    validate_production_runtime_paths()
    validate_production_environment_file()
    if settings.ENABLE_LIVE_ORDERS and settings.UNIQUE_EGRESS_PER_USER_REQUIRED and not settings.EXECUTION_NODE_ROUTING_ENABLED:
        raise RuntimeError(
            "Production live orders require verified execution-node routing for the user's assigned egress IP."
        )
    if settings.ENABLE_LIVE_ORDERS:
        if not settings.LIVE_PILOT_ENABLED:
            raise RuntimeError("Production live orders require LIVE_PILOT_ENABLED=true.")
        if settings.LIVE_ORDER_DRY_RUN_ONLY:
            raise RuntimeError("Production live orders require LIVE_ORDER_DRY_RUN_ONLY=false.")
        if not settings.RELAY_ENABLED or len(settings.RELAY_SHARED_SECRET.strip()) < 32:
            raise RuntimeError("Production live orders require the signing relay with a strong shared secret.")
        secrets = executor_shared_secrets()
        if not secrets or any(len(secret) < 32 for secret in secrets.values()):
            raise RuntimeError("Production live orders require strong executor-specific shared secrets.")
        if not settings.AUTO_RESOLVE_SECURITY_ID:
            raise RuntimeError("Production live orders require AUTO_RESOLVE_SECURITY_ID=true.")
        if settings.ALLOW_DEFAULT_SECURITY_ID:
            raise RuntimeError("Production live orders require ALLOW_DEFAULT_SECURITY_ID=false.")
        if not settings.REQUIRE_INSTRUMENT_MASTER_VALIDATION_LIVE:
            raise RuntimeError(
                "Production live orders require REQUIRE_INSTRUMENT_MASTER_VALIDATION_LIVE=true."
            )
        if not settings.REQUIRE_FRESH_EXPIRY_LIVE:
            raise RuntimeError("Production live orders require REQUIRE_FRESH_EXPIRY_LIVE=true.")
        if not settings.REQUIRE_SIGNAL_ID_LIVE:
            raise RuntimeError("Production live orders require REQUIRE_SIGNAL_ID_LIVE=true.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_configuration()
    init_database()
    seed_strategy_catalog()
    if settings.ENABLE_LIVE_ORDERS:
        from app.services.live_pilot_service import strategy_has_active_approval, validate_real_pilot_assignments

        if not strategy_has_active_approval("SUPERTREND_FLIP"):
            raise RuntimeError("Production live orders require at least one current SUPERTREND_FLIP pilot approval.")
        if not validate_real_pilot_assignments("SUPERTREND_FLIP"):
            raise RuntimeError("Every active live-pilot approval must have a verified one-user executor assignment.")
    bind_chat_event_loop(asyncio.get_running_loop())
    ensure_runtime_directories()
    worker_role = configured_worker_role()
    trading_workers_enabled = validate_worker_configuration()
    authenticated_runtime = auth_enabled()
    if authenticated_runtime:
        vault = vault_status()
        if settings.APP_ENV.lower() == "production" and not vault["ready"]:
            raise RuntimeError(f"Encrypted credential vault unavailable: {vault['error']}")
        start_instrument_cache_warmup()
        logger.info("NOVA Signal Router backend starting with authenticated per-user runtime isolation.")
        logger.info("APP_ENV=%s", settings.APP_ENV)
        logger.info("DHAN_MODE=%s", settings.DHAN_MODE.upper())
        logger.info("ENABLE_LIVE_ORDERS=%s", settings.ENABLE_LIVE_ORDERS)
        logger.info(
            "Trading workers disabled: role=%s enabled=%s; authenticated multi-user loops require DB-backed state.",
            worker_role,
            trading_workers_enabled,
        )
        try:
            yield
        finally:
            clear_chat_event_loop()
            logger.info("NOVA Signal Router shutting down.")
        return

    init_runtime_files()
    sync_runtime_flags_from_env()
    vault = vault_status()
    if settings.APP_ENV.lower() == "production" and not vault["ready"]:
        raise RuntimeError(f"Encrypted credential vault unavailable: {vault['error']}")
    start_instrument_cache_warmup()
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
    if trading_workers_enabled:
        logger.warning("Trading workers enabled for role=%s in single-tenant mode.", worker_role)
        reconcile_open_position_on_startup()
        start_option_position_monitor()
        start_eod_squareoff_worker()
        start_ghost_position_watcher()
    else:
        logger.info(
            "Trading workers disabled: role=%s ENABLE_TRADING_WORKERS=%s.",
            worker_role,
            settings.ENABLE_TRADING_WORKERS,
        )
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
    if trading_workers_enabled:
        stop_option_position_monitor()
        stop_eod_squareoff_worker()
        stop_ghost_position_watcher()
    clear_chat_event_loop()
    logger.info("NOVA Signal Router shutting down.")


app = FastAPI(
    title="NOVA Signal Router",
    description="TradingView webhook to Dhan signal router with user-scoped credentials and runtime state.",
    version="1.0.0-mvp",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

cors_origins = [settings.FRONTEND_ORIGIN]
if settings.APP_ENV.lower() != "production":
    cors_origins.extend(["http://localhost:5173", "http://127.0.0.1:5173"])
cors_origins = [origin for origin in dict.fromkeys(cors_origins) if origin]

app.add_middleware(UserRuntimeScopeMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(setup.router, prefix="/api", tags=["Setup"])
app.include_router(connections.router, prefix="/api", tags=["Connections"])
app.include_router(engine.router, prefix="/api", tags=["Engine"])
app.include_router(dashboard.router, prefix="/api", tags=["Dashboard"])
app.include_router(orders.router, prefix="/api", tags=["Orders"])
app.include_router(positions.router, prefix="/api", tags=["Positions"])
app.include_router(safety.router, prefix="/api", tags=["Safety"])
app.include_router(strategies.router, prefix="/api", tags=["Strategies"])
app.include_router(live_pilot.router, prefix="/api", tags=["Live Pilot"])
app.include_router(relay.router, tags=["TradingView Relay"])
app.include_router(control.router, prefix="/api/control", tags=["Control"])
app.include_router(broker.router, prefix="/api/broker", tags=["Broker"])
app.include_router(debug.router, prefix="/api/debug", tags=["Debug"])
app.include_router(webhook.router, prefix="/webhook", tags=["Webhook"])
app.include_router(webhook.router, prefix="/api/webhook", tags=["Webhook"])
app.include_router(chat_session.router)
app.include_router(chat_ws.router)


def _health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health", tags=["Health"])
def api_health() -> dict:
    return _health()


@app.get("/health", tags=["Health"])
def health() -> dict:
    return _health()


@app.get("/api/readiness", tags=["Health"])
def readiness() -> JSONResponse:
    payload, ready = readiness_payload()
    return JSONResponse(status_code=200 if ready else 503, content=payload)
