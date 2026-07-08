#!/usr/bin/env python
"""Prepare a local/staging DB for single-instance v2 paper verification.

Default mode is read-only:

    python -m scripts.prepare_single_paper_instance_check

Confirmed cleanup mode pauses extra active paper_live_data instances:

    python -m scripts.prepare_single_paper_instance_check \
        --keep-instance-id <INSTANCE_ID_TO_KEEP> \
        --confirm-staging-cleanup

The script never prints DATABASE_URL, passwords, tokens, or secrets. It never
deletes rows and never modifies real_orders instances.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.core.enums import StrategyExecutionMode, StrategyInstanceStatus  # noqa: E402
from app.db import models  # noqa: E402
from app.db.engine import database_configured, session_scope, normalize_database_url  # noqa: E402


PAPER_MODE = StrategyExecutionMode.PAPER_LIVE_DATA.value
REAL_ORDERS_MODE = StrategyExecutionMode.REAL_ORDERS.value
ACTIVE_STATUS = StrategyInstanceStatus.ACTIVE.value
PAUSED_STATUS = StrategyInstanceStatus.PAUSED.value


class PrepareSinglePaperInstanceError(RuntimeError):
    """Raised when the DB cannot be prepared safely."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = {
        "ok": False,
        "database_url_exists": database_configured(),
        "target": _safe_target_summary(),
        "mode": "confirmed_cleanup" if args.confirm_staging_cleanup else "read_only",
    }
    if not database_configured():
        summary["error"] = "DATABASE_URL is not configured."
        _print(summary)
        return 2

    try:
        result = prepare_single_paper_instance(
            keep_instance_id=args.keep_instance_id,
            confirm_staging_cleanup=bool(args.confirm_staging_cleanup),
        )
    except PrepareSinglePaperInstanceError as exc:
        summary.update({"error": str(exc)})
        _print(_sanitize(summary))
        return 2
    except Exception as exc:
        summary.update({"error_type": type(exc).__name__, "error": _safe_db_error_message(exc)})
        _print(_sanitize(summary))
        return 2

    summary.update(result)
    summary["ok"] = bool(result.get("ok"))
    _print(_sanitize(summary))
    return 0 if summary["ok"] else 3


def prepare_single_paper_instance(
    *,
    keep_instance_id: str | None = None,
    confirm_staging_cleanup: bool = False,
) -> dict[str, Any]:
    with session_scope() as db:
        db.execute(text("SELECT 1")).scalar()
        before_counts = _instance_counts(db)
        active_paper = _active_paper_instances(db)
        real_orders_before = _real_orders_signature(db)

        result: dict[str, Any] = {
            "before_counts": before_counts,
            "active_paper_instances": [_instance_summary(row) for row in active_paper],
            "kept_instance_id": keep_instance_id,
            "paused_instance_ids": [],
            "real_orders_unchanged": True,
        }

        if len(active_paper) == 1:
            result.update(
                {
                    "ok": True,
                    "status": "already_single_active_paper_instance",
                    "kept_instance_id": str(active_paper[0][0].id),
                    "after_counts": before_counts,
                }
            )
            return result
        if len(active_paper) == 0:
            result.update(
                {
                    "ok": False,
                    "status": "blocked_no_active_paper_instance",
                    "message": "No active paper_live_data instance exists; create one test paper instance first.",
                    "after_counts": before_counts,
                }
            )
            return result

        if not confirm_staging_cleanup:
            result.update(
                {
                    "ok": False,
                    "status": "blocked_multiple_active_paper_instances",
                    "message": "Pass --keep-instance-id and --confirm-staging-cleanup to pause other paper instances.",
                    "after_counts": before_counts,
                }
            )
            return result
        if not keep_instance_id:
            raise PrepareSinglePaperInstanceError("--keep-instance-id is required with --confirm-staging-cleanup.")

        keep_uuid = _coerce_uuid(keep_instance_id)
        active_by_id = {instance.id: instance for instance, _code, _version in active_paper}
        if keep_uuid not in active_by_id:
            raise PrepareSinglePaperInstanceError("keep-instance-id is not an active paper_live_data instance.")

        paused_ids = []
        for instance, _code, _version in active_paper:
            if instance.id == keep_uuid:
                continue
            instance.status = PAUSED_STATUS
            paused_ids.append(str(instance.id))
        db.flush()

        real_orders_after = _real_orders_signature(db)
        after_counts = _instance_counts(db)
        result.update(
            {
                "ok": after_counts.get("active_paper_live_data", 0) == 1 and real_orders_before == real_orders_after,
                "status": "cleanup_applied",
                "kept_instance_id": str(keep_uuid),
                "paused_instance_ids": paused_ids,
                "after_counts": after_counts,
                "real_orders_unchanged": real_orders_before == real_orders_after,
                "rollback_note": {
                    "paused_instance_ids": paused_ids,
                    "restore_sql_hint": "Set user_strategy_instances.status='active' for the paused IDs if rollback is needed.",
                },
            }
        )
        return result


def _active_paper_instances(db: Any) -> list[tuple[models.UserStrategyInstance, str | None, str | None]]:
    statement = (
        select(models.UserStrategyInstance, models.StrategyCatalog.code, models.StrategyVersion.version)
        .join(models.StrategyCatalog, models.StrategyCatalog.id == models.UserStrategyInstance.strategy_id)
        .outerjoin(models.StrategyVersion, models.StrategyVersion.id == models.UserStrategyInstance.strategy_version_id)
        .where(
            models.UserStrategyInstance.execution_mode == PAPER_MODE,
            models.UserStrategyInstance.status == ACTIVE_STATUS,
        )
        .order_by(models.UserStrategyInstance.created_at.asc(), models.UserStrategyInstance.id.asc())
    )
    return list(db.execute(statement).all())


def _instance_counts(db: Any) -> dict[str, int]:
    active_paper = int(
        db.scalar(
            select(func.count()).select_from(models.UserStrategyInstance).where(
                models.UserStrategyInstance.execution_mode == PAPER_MODE,
                models.UserStrategyInstance.status == ACTIVE_STATUS,
            )
        )
        or 0
    )
    active_real = int(
        db.scalar(
            select(func.count()).select_from(models.UserStrategyInstance).where(
                models.UserStrategyInstance.execution_mode == REAL_ORDERS_MODE,
                models.UserStrategyInstance.status == ACTIVE_STATUS,
            )
        )
        or 0
    )
    total_paper = int(
        db.scalar(
            select(func.count()).select_from(models.UserStrategyInstance).where(
                models.UserStrategyInstance.execution_mode == PAPER_MODE,
            )
        )
        or 0
    )
    return {
        "active_paper_live_data": active_paper,
        "active_real_orders": active_real,
        "total_paper_live_data": total_paper,
    }


def _real_orders_signature(db: Any) -> list[tuple[str, str, str]]:
    rows = db.scalars(
        select(models.UserStrategyInstance)
        .where(models.UserStrategyInstance.execution_mode == REAL_ORDERS_MODE)
        .order_by(models.UserStrategyInstance.id.asc())
    ).all()
    return [(str(row.id), str(row.status), str(row.updated_at)) for row in rows]


def _instance_summary(row: tuple[models.UserStrategyInstance, str | None, str | None]) -> dict[str, Any]:
    instance, strategy_code, version = row
    return {
        "id": instance.id,
        "user_id": instance.user_id,
        "strategy_code": strategy_code,
        "strategy_version": version,
        "execution_mode": instance.execution_mode,
        "status": instance.status,
        "created_at": instance.created_at,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-instance-id", help="Active paper_live_data instance ID to keep active.")
    parser.add_argument(
        "--confirm-staging-cleanup",
        action="store_true",
        help="Pause active paper_live_data instances other than --keep-instance-id.",
    )
    return parser.parse_args(argv)


def _coerce_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise PrepareSinglePaperInstanceError("keep-instance-id must be a valid UUID.") from exc


def _safe_target_summary() -> dict[str, Any]:
    if not database_configured():
        return {"driver": None, "host_masked": None, "database": None, "sslmode": None}
    try:
        url = make_url(normalize_database_url(settings.DATABASE_URL))
    except Exception:
        return {"driver": "unparseable", "host_masked": None, "database": None, "sslmode": None}
    return {
        "driver": url.drivername,
        "host_masked": _mask_host(url.host),
        "database": _safe_database_name(url.database),
        "sslmode": url.query.get("sslmode"),
    }


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
    if "connection refused" in text:
        return "Database connection was refused; verify host, port, and network access."
    if "timed out" in text or "timeout" in text:
        return "Database connection timed out; verify host, port, and network access."
    if type(exc).__module__.split(".", 1)[0] in {"sqlalchemy", "psycopg", "psycopg2"}:
        return "Database operation failed; verify DATABASE_URL credentials and connectivity."
    return "Database preparation check failed."


def _sanitize(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return _sanitize(asdict(value))
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(_sanitize(payload), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
