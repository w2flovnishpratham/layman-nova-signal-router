"""Read-only inspector for v2 paper fanout debug state."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select

from app.config import settings
from app.core.enums import StrategyExecutionMode, StrategyInstanceStatus
from app.core.feature_flags import MULTI_STRATEGY_FANOUT, V2_PAPER_RUNNER_DEBUG, is_feature_enabled
from app.db import models
from app.services import audit_logger, state_store


V2_PENDING = "v2_pending"
V2_LOCKED = "v2_locked"
V2_COMPLETED_DRY_RUN = "v2_completed_dry_run"
V2_FAILED_DRY_RUN = "v2_failed_dry_run"
V2_DEAD_LETTER = "v2_dead_letter"
V2_COMPLETED_PAPER = "v2_completed_paper"
V2_FAILED_PAPER = "v2_failed_paper"

V2_JOB_STATUSES = (
    V2_PENDING,
    V2_LOCKED,
    V2_COMPLETED_DRY_RUN,
    V2_FAILED_DRY_RUN,
    V2_DEAD_LETTER,
    V2_COMPLETED_PAPER,
    V2_FAILED_PAPER,
)

OPEN_POSITION_SAFE_FIELDS = (
    "strategy_code",
    "symbol",
    "instrument_type",
    "exchange_segment",
    "trading_symbol",
    "option_side",
    "strike",
    "expiry",
    "qty",
    "entry_price",
    "opened_at",
    "execution_mode",
    "source_signal_id",
    "v2_job_id",
)

REDACTED_KEYS = {
    "access_token",
    "client_id",
    "headers",
    "message_secret_hash",
    "password",
    "payload",
    "pin",
    "proxy_url",
    "raw_payload",
    "raw_response",
    "request",
    "response",
    "response_json",
    "response_text",
    "secret",
    "signal_payload",
    "token",
    "webhook_key_hash",
}

REDACTED_KEY_FRAGMENTS = ("access_token", "credential", "headers", "proxy_url", "secret")


def get_v2_paper_fanout_status(db: Any, limit: int = 20) -> dict[str, Any]:
    """Return a safe, read-only overview of v2 paper fanout state."""
    safe_limit = _coerce_limit(limit)
    job_counts = _v2_paper_job_counts_by_status(db)
    instance_counts = _active_instance_counts_by_execution_mode(db)

    status = {
        "ok": True,
        "limit": safe_limit,
        "flags": {
            "debug_enabled": bool(settings.DEBUG_ENABLED),
            "multi_strategy_fanout": is_feature_enabled(MULTI_STRATEGY_FANOUT),
            "v2_paper_runner_debug": is_feature_enabled(V2_PAPER_RUNNER_DEBUG),
        },
        "counts": {
            "paper_instances": _count_strategy_instances(
                db,
                execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
            ),
            "active_paper_instances": instance_counts.get(StrategyExecutionMode.PAPER_LIVE_DATA.value, 0),
            "real_order_instances": instance_counts.get(StrategyExecutionMode.REAL_ORDERS.value, 0),
            "signal_only_instances": instance_counts.get(StrategyExecutionMode.SIGNAL_ONLY.value, 0),
            "v2_paper_jobs": sum(job_counts.values()),
            "v2_non_paper_jobs": _count_non_paper_v2_jobs(db),
            "v2_pending_jobs": job_counts[V2_PENDING],
            "v2_locked_jobs": job_counts[V2_LOCKED],
            "v2_completed_paper_jobs": job_counts[V2_COMPLETED_PAPER],
            "v2_failed_paper_jobs": job_counts[V2_FAILED_PAPER],
            "strategy_signals": _count_rows(db, models.StrategySignal),
            "normalized_option_signals": _count_rows(db, models.NormalizedOptionSignal),
        },
        "jobs_by_status": job_counts,
        "recent_strategy_signals": _recent_strategy_signals(db, safe_limit),
        "recent_normalized_option_signals": _recent_normalized_option_signals(db, safe_limit),
        "recent_jobs": _recent_v2_paper_jobs(db, safe_limit),
        "analytics_summary": _instance_analytics_summary(safe_limit),
        "safety": {
            "read_only": True,
            "real_orders_supported": False,
            "startup_worker_registered": "unknown",
            "public_webhook_enabled": "unknown",
        },
    }
    return _sanitize(status)


def list_v2_paper_fanout_jobs(db: Any, limit: int = 20) -> dict[str, Any]:
    """Return recent v2 paper jobs with curated, non-sensitive fields only."""
    safe_limit = _coerce_limit(limit)
    jobs = _recent_v2_paper_jobs(db, safe_limit)
    return _sanitize(
        {
            "ok": True,
            "limit": safe_limit,
            "count": len(jobs),
            "jobs": jobs,
        }
    )


def list_v2_paper_strategy_instances(db: Any, limit: int = 20) -> dict[str, Any]:
    """Return paper strategy instances and safe per-instance position summaries."""
    safe_limit = _coerce_limit(limit)
    open_positions_by_instance = _read_open_positions_by_instance_read_only()
    statement = (
        select(models.UserStrategyInstance, models.StrategyCatalog.code)
        .join(models.StrategyCatalog, models.StrategyCatalog.id == models.UserStrategyInstance.strategy_id)
        .where(models.UserStrategyInstance.execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value)
        .order_by(models.UserStrategyInstance.updated_at.desc(), models.UserStrategyInstance.id.desc())
        .limit(safe_limit)
    )

    instances = []
    for instance, strategy_code in db.execute(statement).all():
        position = open_positions_by_instance.get(str(instance.id))
        position_summary = _open_position_summary(position)
        instances.append(
            {
                "id": instance.id,
                "user_id": instance.user_id,
                "strategy_code": strategy_code,
                "strategy_version_id": instance.strategy_version_id,
                "instance_label": instance.instance_label,
                "source_type": instance.source_type,
                "execution_mode": instance.execution_mode,
                "status": instance.status,
                "lots": instance.lots,
                "side_preference": instance.side_preference,
                "has_open_position": bool(position_summary.get("has_open_position")),
                "open_position_summary": position_summary,
                "created_at": instance.created_at,
                "updated_at": instance.updated_at,
            }
        )

    return _sanitize(
        {
            "ok": True,
            "limit": safe_limit,
            "count": len(instances),
            "instances": instances,
        }
    )


def _recent_v2_paper_jobs(db: Any, limit: int) -> list[dict[str, Any]]:
    statement = (
        select(models.StrategyExecutionJob)
        .where(
            models.StrategyExecutionJob.status.in_(V2_JOB_STATUSES),
            models.StrategyExecutionJob.execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value,
        )
        .order_by(models.StrategyExecutionJob.created_at.desc(), models.StrategyExecutionJob.id.desc())
        .limit(limit)
    )
    return [_job_summary(job) for job in db.scalars(statement).all()]


def _job_summary(job: models.StrategyExecutionJob) -> dict[str, Any]:
    result_summary = job.result_summary if isinstance(job.result_summary, dict) else {}
    error_code = result_summary.get("error_code")
    result_status = result_summary.get("status")
    return {
        "id": job.id,
        "user_id": job.user_id,
        "instance_id": job.instance_id,
        "strategy_version_id": job.strategy_version_id,
        "normalized_signal_id": job.normalized_signal_id,
        "strategy_signal_id": job.strategy_signal_id,
        "strategy_code": job.strategy_name,
        "signal_id": job.signal_id,
        "execution_mode": job.execution_mode,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "available_at": job.available_at,
        "locked_at": job.locked_at,
        "locked_by": job.locked_by,
        "completed_at": job.completed_at,
        "result_status": result_status,
        "error_code": error_code,
        "last_error": job.last_error,
        "dead_letter_reason": job.dead_letter_reason,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _recent_strategy_signals(db: Any, limit: int) -> list[dict[str, Any]]:
    statement = (
        select(models.StrategySignal)
        .order_by(models.StrategySignal.created_at.desc(), models.StrategySignal.id.desc())
        .limit(limit)
    )
    return [
        {
            "id": signal.id,
            "strategy_code": signal.strategy_name,
            "signal_id": signal.signal_id,
            "instance_id": signal.instance_id,
            "strategy_version_id": signal.strategy_version_id,
            "payload_version": signal.payload_version,
            "source_type": signal.source_type,
            "status": signal.status,
            "created_at": signal.created_at,
            "updated_at": signal.updated_at,
        }
        for signal in db.scalars(statement).all()
    ]


def _recent_normalized_option_signals(db: Any, limit: int) -> list[dict[str, Any]]:
    statement = (
        select(models.NormalizedOptionSignal)
        .order_by(models.NormalizedOptionSignal.created_at.desc(), models.NormalizedOptionSignal.id.desc())
        .limit(limit)
    )
    return [
        {
            "id": signal.id,
            "strategy_signal_id": signal.strategy_signal_id,
            "instance_id": signal.instance_id,
            "action": signal.action,
            "intent": signal.intent,
            "symbol": signal.symbol,
            "instrument_type": signal.instrument_type,
            "option_side": signal.option_side,
            "strike_mode": signal.strike_mode,
            "resolved_strike": signal.resolved_strike,
            "expiry_mode": signal.expiry_mode,
            "resolved_expiry": signal.resolved_expiry,
            "qty_mode": signal.qty_mode,
            "lots": signal.lots,
            "quantity": signal.quantity,
            "order_type": signal.order_type,
            "product_type": signal.product_type,
            "created_at": signal.created_at,
        }
        for signal in db.scalars(statement).all()
    ]


def _v2_paper_job_counts_by_status(db: Any) -> dict[str, int]:
    counts = {status: 0 for status in V2_JOB_STATUSES}
    statement = (
        select(models.StrategyExecutionJob.status, func.count())
        .where(
            models.StrategyExecutionJob.status.in_(V2_JOB_STATUSES),
            models.StrategyExecutionJob.execution_mode == StrategyExecutionMode.PAPER_LIVE_DATA.value,
        )
        .group_by(models.StrategyExecutionJob.status)
    )
    for status, count in db.execute(statement).all():
        counts[str(status)] = int(count or 0)
    return counts


def _active_instance_counts_by_execution_mode(db: Any) -> dict[str, int]:
    statement = (
        select(models.UserStrategyInstance.execution_mode, func.count())
        .where(models.UserStrategyInstance.status == StrategyInstanceStatus.ACTIVE.value)
        .group_by(models.UserStrategyInstance.execution_mode)
    )
    return {str(mode): int(count or 0) for mode, count in db.execute(statement).all()}


def _count_strategy_instances(db: Any, *, execution_mode: str) -> int:
    statement = select(func.count()).select_from(models.UserStrategyInstance).where(
        models.UserStrategyInstance.execution_mode == execution_mode,
    )
    return int(db.scalar(statement) or 0)


def _count_non_paper_v2_jobs(db: Any) -> int:
    statement = select(func.count()).select_from(models.StrategyExecutionJob).where(
        models.StrategyExecutionJob.status.in_(V2_JOB_STATUSES),
        models.StrategyExecutionJob.execution_mode != StrategyExecutionMode.PAPER_LIVE_DATA.value,
    )
    return int(db.scalar(statement) or 0)


def _count_rows(db: Any, model: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _instance_analytics_summary(limit: int) -> dict[str, Any]:
    records = audit_logger.read_jsonl("order", limit=limit)
    instance_ids: list[str] = []
    for record in records:
        metadata = record.get("metadata")
        metadata_instance_id = metadata.get("instance_id") if isinstance(metadata, dict) else None
        value = record.get("instance_id") or metadata_instance_id
        if value in (None, ""):
            continue
        text = str(value)
        if text not in instance_ids:
            instance_ids.append(text)
    return {
        "status": "read_only_log_summary",
        "source": "order_jsonl",
        "recent_order_events": len(records),
        "recent_order_events_with_instance_id": sum(1 for record in records if _log_record_has_instance_id(record)),
        "recent_instance_ids": instance_ids[:limit],
        "pairing_summary": "unknown",
    }


def _log_record_has_instance_id(record: dict[str, Any]) -> bool:
    if record.get("instance_id") not in (None, ""):
        return True
    metadata = record.get("metadata")
    return isinstance(metadata, dict) and metadata.get("instance_id") not in (None, "")


def _read_open_positions_by_instance_read_only() -> dict[str, Any]:
    path = state_store.scoped_runtime_path(state_store.OPEN_POSITIONS_BY_INSTANCE_FILE)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _open_position_summary(position: Any) -> dict[str, Any]:
    if not isinstance(position, dict) or not bool(position.get("has_open_position")):
        return {"has_open_position": False}
    summary: dict[str, Any] = {"has_open_position": True}
    for field in OPEN_POSITION_SAFE_FIELDS:
        if field in position:
            summary[field] = position[field]
    return summary


def _coerce_limit(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 20
    return max(1, min(parsed, 100))


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _is_redacted_key(key):
                continue
            sanitized[str(key)] = _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    return _jsonable(value)


def _is_redacted_key(key: Any) -> bool:
    lowered = str(key).lower()
    return lowered in REDACTED_KEYS or any(fragment in lowered for fragment in REDACTED_KEY_FRAGMENTS)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value
