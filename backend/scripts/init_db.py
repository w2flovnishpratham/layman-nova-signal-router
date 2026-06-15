#!/usr/bin/env python
"""Initialize the Neon PostgreSQL schema for the multi-user layer.

Usage (from the backend/ directory, with the venv active and DATABASE_URL set):

    python -m scripts.init_db
    # or
    python scripts/init_db.py

Creates the users, user_sessions, user_credential_vaults, user_runs, and
audit_logs tables if they do not already exist. Safe to run repeatedly.
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text

# Allow running as a plain script: ensure the backend root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.engine import database_configured, get_engine, init_db, normalize_database_url  # noqa: E402


EXPECTED_TABLES = {
    "users",
    "user_sessions",
    "user_credential_vaults",
    "user_runs",
    "audit_logs",
}


def main() -> int:
    if not database_configured():
        print("ERROR: DATABASE_URL is not set. Export your Neon connection string first.")
        return 1
    engine = get_engine()
    print(f"Connecting to: {normalize_database_url('postgresql://***')[:24]}... (driver normalized)")
    print(f"Host: {engine.url.host}  Database: {engine.url.database}")
    init_db()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    tables = set(inspect(engine).get_table_names())
    missing = EXPECTED_TABLES - tables
    if missing:
        print(f"ERROR: schema verification failed; missing tables: {', '.join(sorted(missing))}")
        return 1
    print("OK: database connection and all auth tables verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
