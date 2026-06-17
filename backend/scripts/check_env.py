#!/usr/bin/env python
"""Validate the backend environment / .env configuration.

Run on the VPS (with the venv active) from the backend/ directory:

    python -m scripts.check_env
    # or
    python scripts/check_env.py

Checks presence + validity of every required variable, tests the database
connection and the credential-encryption key, and prints a clear PASS/FAIL
report. Secret VALUES are never printed — only masked previews / booleans.
Exit code 0 = all good, 1 = at least one problem (CI-friendly).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

OK = "\033[92m[ OK ]\033[0m"
WARN = "\033[93m[WARN]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"

problems: list[str] = []
warnings: list[str] = []


def mask(value: str | None, show: int = 4) -> str:
    if not value:
        return "(empty)"
    v = str(value)
    if len(v) <= show * 2:
        return "*" * len(v)
    return f"{v[:show]}…{v[-show:]} (len {len(v)})"


def line(status: str, name: str, detail: str = "") -> None:
    print(f"  {status}  {name:<28} {detail}")


def require(name: str, value, detail: str = "") -> bool:
    if value:
        line(OK, name, detail)
        return True
    line(FAIL, name, "MISSING")
    problems.append(name)
    return False


print("=" * 64)
print(f"  Environment check — APP_ENV={settings.APP_ENV}  (production={settings.is_production})")
print("=" * 64)

# --- Core URLs ---
print("\nURLs / origins")
require("BACKEND_PUBLIC_BASE_URL", settings.BACKEND_PUBLIC_BASE_URL, settings.BACKEND_PUBLIC_BASE_URL)
require("FRONTEND_ORIGIN", settings.FRONTEND_ORIGIN, settings.FRONTEND_ORIGIN)
if not settings.FRONTEND_URL:
    line(WARN, "FRONTEND_URL", "empty — falls back to FRONTEND_ORIGIN")
    warnings.append("FRONTEND_URL")
else:
    line(OK, "FRONTEND_URL", settings.FRONTEND_URL)

# --- Secrets ---
print("\nSecrets")
if len(settings.app_secret) >= 32:
    line(OK, "APP_SECRET_KEY", mask(settings.app_secret))
else:
    line(FAIL, "APP_SECRET_KEY", f"too short (len {len(settings.app_secret)}, need >= 32)")
    problems.append("APP_SECRET_KEY")

# Credential encryption key must be a valid Fernet key.
from app.services.user_credential_vault import vault_ready  # noqa: E402

if not settings.credential_encryption_key:
    line(FAIL, "CREDENTIAL_ENCRYPTION_KEY", "MISSING")
    problems.append("CREDENTIAL_ENCRYPTION_KEY")
elif vault_ready():
    line(OK, "CREDENTIAL_ENCRYPTION_KEY", f"valid Fernet key {mask(settings.credential_encryption_key)}")
else:
    line(FAIL, "CREDENTIAL_ENCRYPTION_KEY", "set but NOT a valid Fernet key")
    problems.append("CREDENTIAL_ENCRYPTION_KEY")

# --- Auth / Google ---
print("\nAuth / Google OAuth")
line(OK, "AUTH_REQUIRED", str(settings.AUTH_REQUIRED))
if settings.AUTH_REQUIRED:
    require("GOOGLE_CLIENT_ID", settings.GOOGLE_CLIENT_ID, mask(settings.GOOGLE_CLIENT_ID, 6))
    require("GOOGLE_CLIENT_SECRET", settings.GOOGLE_CLIENT_SECRET, mask(settings.GOOGLE_CLIENT_SECRET))
    if require("GOOGLE_REDIRECT_URI", settings.GOOGLE_REDIRECT_URI, settings.GOOGLE_REDIRECT_URI):
        if not settings.GOOGLE_REDIRECT_URI.rstrip("/").endswith("/api/auth/google/callback"):
            line(WARN, "  redirect path", "should end with /api/auth/google/callback")
            warnings.append("GOOGLE_REDIRECT_URI path")
else:
    line(WARN, "AUTH_REQUIRED", "false — dev user fallback active (no real login)")
    warnings.append("AUTH_REQUIRED=false")
line(OK, "ADMIN_EMAILS", f"{len(settings.admin_emails)} configured")

# --- Database ---
print("\nDatabase (Neon / Postgres)")
from app.db.engine import database_configured, get_engine, normalize_database_url  # noqa: E402

if not database_configured():
    line(FAIL, "DATABASE_URL", "MISSING")
    problems.append("DATABASE_URL")
else:
    norm = normalize_database_url(settings.DATABASE_URL)
    driver = norm.split("://", 1)[0]
    line(OK, "DATABASE_URL", f"driver={driver}")
    try:
        from sqlalchemy import inspect, text

        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        tables = set(inspect(engine).get_table_names())
        expected = {"users", "user_sessions", "user_credential_vaults", "user_runs", "audit_logs"}
        missing = expected - tables
        line(OK, "  DB connection", f"host={engine.url.host} db={engine.url.database}")
        if missing:
            line(FAIL, "  auth tables", f"missing: {', '.join(sorted(missing))} — run scripts.init_db")
            problems.append("auth tables")
        else:
            line(OK, "  auth tables", "all 5 present")
    except Exception as exc:
        line(FAIL, "  DB connection", f"{type(exc).__name__}: {exc}")
        problems.append("DB connection")

# --- Live-mode safety flags ---
print("\nLive-mode safety flags (informational)")
line(OK, "DHAN_MODE", settings.DHAN_MODE.upper())
line(OK, "DHAN_READ_ONLY_REAL_DATA", str(settings.DHAN_READ_ONLY_REAL_DATA))
line(OK, "ENABLE_LIVE_ORDERS", str(settings.ENABLE_LIVE_ORDERS))
line(OK, "WEBHOOK_TRADING_ENABLED", str(settings.WEBHOOK_TRADING_ENABLED))
line(OK, "WEBHOOK_HMAC_REQUIRED", str(settings.WEBHOOK_HMAC_REQUIRED))
if settings.ENABLE_LIVE_ORDERS:
    line(WARN, "ENABLE_LIVE_ORDERS", "TRUE — real orders can be placed after per-user checks")
    warnings.append("ENABLE_LIVE_ORDERS=true")

# --- Summary ---
print("\n" + "=" * 64)
if problems:
    print(f"  RESULT: {FAIL}  {len(problems)} problem(s): {', '.join(problems)}")
    if warnings:
        print(f"          {len(warnings)} warning(s): {', '.join(warnings)}")
    raise SystemExit(1)
print(f"  RESULT: {OK}  All required configuration present and valid.")
if warnings:
    print(f"          {len(warnings)} warning(s): {', '.join(warnings)}")
raise SystemExit(0)
