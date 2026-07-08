#!/usr/bin/env python
"""Safely check DATABASE_URL connectivity and required schema.

Run from backend/:

    python -m scripts.check_db_connectivity

The script never prints the full DATABASE_URL, password, token, or secret.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.db.engine import database_configured, get_engine, normalize_database_url  # noqa: E402


REQUIRED_TABLES = (
    "users",
    "strategy_catalog",
    "strategy_versions",
    "user_strategy_instances",
    "strategy_signals",
    "normalized_option_signals",
    "strategy_execution_jobs",
)


def main() -> int:
    summary: dict[str, Any] = {
        "ok": False,
        "database_url_exists": database_configured(),
        "target": _safe_target_summary(),
        "connection": {"ok": False},
        "select_1": {"ok": False},
        "required_tables": {"ok": False, "missing": list(REQUIRED_TABLES), "present": []},
        "alembic": {"version_table_present": False, "versions": []},
    }
    if not database_configured():
        summary["error"] = "DATABASE_URL is not configured."
        _print(summary)
        return 2

    try:
        engine = get_engine()
        with engine.connect() as connection:
            summary["connection"] = {
                "ok": True,
                "dialect": engine.dialect.name,
                "driver": engine.driver,
            }
            selected = connection.execute(text("SELECT 1")).scalar()
            summary["select_1"] = {"ok": selected == 1, "value": int(selected or 0)}

            inspector = inspect(connection)
            table_names = set(inspector.get_table_names())
            present = sorted(name for name in REQUIRED_TABLES if name in table_names)
            missing = sorted(name for name in REQUIRED_TABLES if name not in table_names)
            summary["required_tables"] = {
                "ok": not missing,
                "present": present,
                "missing": missing,
            }
            summary["alembic"] = _alembic_version(connection, table_names)
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        summary["error"] = _safe_db_error_message(exc)
        _print(summary)
        return 2

    summary["ok"] = bool(summary["connection"]["ok"] and summary["select_1"]["ok"] and summary["required_tables"]["ok"])
    _print(summary)
    return 0 if summary["ok"] else 3


def _safe_target_summary() -> dict[str, Any]:
    if not database_configured():
        return {
            "driver": None,
            "host_masked": None,
            "database": None,
            "sslmode": None,
        }
    try:
        url = make_url(normalize_database_url(settings.DATABASE_URL))
    except Exception:
        return {
            "driver": "unparseable",
            "host_masked": None,
            "database": None,
            "sslmode": None,
        }
    return {
        "driver": url.drivername,
        "host_masked": _mask_host(url.host),
        "database": _safe_database_name(url.database),
        "sslmode": url.query.get("sslmode"),
    }


def _alembic_version(connection: Any, table_names: set[str]) -> dict[str, Any]:
    if "alembic_version" not in table_names:
        return {"version_table_present": False, "versions": []}
    try:
        rows = connection.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars().all()
    except Exception:
        return {"version_table_present": True, "versions": [], "readable": False}
    return {"version_table_present": True, "versions": [str(row) for row in rows], "readable": True}


def _mask_host(host: str | None) -> str | None:
    if not host:
        return host
    value = str(host)
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}***{value[-3:]}"


def _safe_database_name(database: str | None) -> str | None:
    if not database:
        return database
    value = str(database).replace("\\", "/").rstrip("/")
    return value.rsplit("/", 1)[-1] or None


def _safe_db_error_message(exc: Exception) -> str:
    text = str(exc).lower()
    if "password authentication failed" in text or "authentication failed" in text:
        return "Database authentication failed; verify DATABASE_URL credentials."
    if "ssl" in text:
        return "Database SSL connection failed; verify whether sslmode=require is needed."
    if "could not translate host name" in text or "name or service not known" in text:
        return "Database host could not be resolved; verify DATABASE_URL host."
    if "connection refused" in text:
        return "Database connection was refused; verify host, port, and network access."
    if "timed out" in text or "timeout" in text:
        return "Database connection timed out; verify host, port, and network access."
    if type(exc).__module__.split(".", 1)[0] in {"sqlalchemy", "psycopg", "psycopg2"}:
        return "Database operation failed; verify DATABASE_URL credentials and connectivity."
    return "Database connectivity check failed."


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
