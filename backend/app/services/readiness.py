from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.auth.db import engine
from app.config import BACKEND_DIR, RUNTIME_LOG_DIR, RUNTIME_STATE_DIR, settings
from app.services.credential_vault import vault_ready
from app.services.worker_policy import configured_worker_role


logger = logging.getLogger("nova_signal_router.readiness")


def database_ready() -> bool:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True


def migrations_ready() -> bool:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    script = ScriptDirectory.from_config(config)
    expected_heads = set(script.get_heads())
    with engine.connect() as connection:
        current_heads = set(MigrationContext.configure(connection).get_current_heads())
    return bool(expected_heads) and current_heads == expected_heads


def runtime_directory_ready(path: Path) -> bool:
    return path.is_dir() and os.access(path, os.R_OK | os.W_OK | os.X_OK)


def _safe_check(name: str, check: Callable[[], bool]) -> bool:
    try:
        return bool(check())
    except Exception:
        logger.exception("Readiness check failed: %s", name)
        return False


def readiness_payload() -> tuple[dict[str, str], bool]:
    production = settings.APP_ENV.lower() == "production"
    database_ok = _safe_check("database", database_ready)
    migrations_ok = database_ok and _safe_check("migrations", migrations_ready)
    vault_ok = _safe_check("vault", vault_ready)
    state_ok = _safe_check("runtime_state", lambda: runtime_directory_ready(RUNTIME_STATE_DIR))
    logs_ok = _safe_check("runtime_logs", lambda: runtime_directory_ready(RUNTIME_LOG_DIR))
    auth_ok = not production or settings.AUTH_REQUIRED
    live_ok = not settings.ENABLE_LIVE_ORDERS
    worker_ok = _safe_check(
        "worker_policy",
        lambda: configured_worker_role() == "web" and not settings.ENABLE_TRADING_WORKERS,
    )
    debug_ok = not production or not settings.DEBUG_ENABLED

    checks = {
        "database": "ok" if database_ok else "unavailable",
        "migrations": "ok" if migrations_ok else "out_of_date",
        "vault": "ok" if vault_ok else "unavailable",
        "runtime_state": "ok" if state_ok else "unavailable",
        "runtime_logs": "ok" if logs_ok else "unavailable",
        "auth_policy": "ok" if auth_ok else "unsafe",
        "live_policy": "disabled" if live_ok else "unsafe_enabled",
        "worker_policy": "ok" if worker_ok else "unsafe",
        "debug": "disabled" if debug_ok else "unsafe_enabled",
    }
    ready = all(
        (
            database_ok,
            migrations_ok,
            vault_ok,
            state_ok,
            logs_ok,
            auth_ok,
            live_ok,
            worker_ok,
            debug_ok,
        )
    )
    return {"status": "ready" if ready else "not_ready", **checks}, ready
