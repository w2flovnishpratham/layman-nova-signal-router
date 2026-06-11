from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import session as chat_session
from app.api import ws as chat_ws
from app.auth.db import init_database
from app.auth import router as auth_router
from app.config import settings
from app.middleware.user_scope import UserRuntimeScopeMiddleware
from app.routers import broker, connections, control, dashboard, debug, engine, orders, positions, setup, webhook
from app.services.audit_logger import log_audit_event
from app.services.chat_event_publisher import bind_chat_event_loop, clear_chat_event_loop
from app.services.credential_vault import vault_status
from app.services.instrument_resolver import start_instrument_cache_warmup
from app.services.option_position_monitor import start_option_position_monitor, stop_option_position_monitor
from app.services.state_store import get_app_state, get_engine_mode, get_runtime_settings, init_runtime_files, sync_runtime_flags_from_env
from app.services.startup_reconciler import reconcile_open_position_on_startup
from app.workers.eod_squareoff import start_eod_squareoff_worker, stop_eod_squareoff_worker
from app.workers.ghost_position_watcher import start_ghost_position_watcher, stop_ghost_position_watcher


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nova_signal_router")


def validate_production_configuration() -> None:
    if settings.APP_ENV.lower() != "production":
        return

    session_secret = settings.SESSION_TOKEN_SECRET.strip()
    if session_secret == "change-me-in-production" or len(session_secret) < 32:
        raise RuntimeError("SESSION_TOKEN_SECRET must be overridden with at least 32 random characters in production.")
    if settings.DHAN_MODE.upper() != "REAL":
        raise RuntimeError("Production requires DHAN_MODE=REAL; mock routing is only allowed for local development and tests.")
    if settings.ENABLE_LIVE_ORDERS and settings.UNIQUE_EGRESS_PER_USER_REQUIRED and not settings.EXECUTION_NODE_ROUTING_ENABLED:
        raise RuntimeError(
            "Production live orders require EXECUTION_NODE_ROUTING_ENABLED=true when UNIQUE_EGRESS_PER_USER_REQUIRED=true."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_configuration()
    init_database()
    bind_chat_event_loop(asyncio.get_running_loop())
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
    reconcile_open_position_on_startup()
    start_option_position_monitor()
    # C1 — EOD square-off worker (15:15 IST). Runs as daemon; idempotent.
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
app.include_router(control.router, prefix="/api/control", tags=["Control"])
app.include_router(broker.router, prefix="/api/broker", tags=["Broker"])
app.include_router(debug.router, prefix="/api/debug", tags=["Debug"])
app.include_router(webhook.router, prefix="/webhook", tags=["Webhook"])
app.include_router(webhook.router, prefix="/api/webhook", tags=["Webhook"])
app.include_router(chat_session.router)
app.include_router(chat_ws.router)


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
    }


@app.get("/api/health", tags=["Health"])
def api_health() -> dict:
    return _health()


@app.get("/health", tags=["Health"])
def health() -> dict:
    return _health()
