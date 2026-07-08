#!/usr/bin/env python
"""Manual read-only smoke check for v2 paper fanout inspector output.

Usage from backend/:

    python -m scripts.manual_v2_paper_fanout_inspector_check --direct-service
    python -m scripts.manual_v2_paper_fanout_inspector_check --server-url http://localhost:8001
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.core.feature_flags import (  # noqa: E402
    MULTI_STRATEGY_FANOUT,
    V2_PAPER_RUNNER_DEBUG,
    feature_flag_states,
    is_feature_enabled,
)
from app.db import models  # noqa: E402
from app.db.engine import database_configured, session_scope  # noqa: E402
from app.services import state_store  # noqa: E402
from app.services.strategy_paper_fanout_inspector_v2 import (  # noqa: E402
    get_v2_paper_fanout_status,
    list_v2_paper_fanout_jobs,
    list_v2_paper_strategy_instances,
)


ENDPOINTS = {
    "status": "/api/debug/v2/paper-fanout/status",
    "jobs": "/api/debug/v2/paper-fanout/jobs",
    "instances": "/api/debug/v2/paper-fanout/instances",
}

ALLOWED_JOB_KEYS = {
    "id",
    "user_id",
    "instance_id",
    "strategy_version_id",
    "normalized_signal_id",
    "strategy_signal_id",
    "strategy_code",
    "signal_id",
    "execution_mode",
    "status",
    "attempts",
    "max_attempts",
    "available_at",
    "locked_at",
    "locked_by",
    "completed_at",
    "result_status",
    "error_code",
    "last_error",
    "dead_letter_reason",
    "created_at",
    "updated_at",
}

ALLOWED_INSTANCE_KEYS = {
    "id",
    "user_id",
    "strategy_code",
    "strategy_version_id",
    "instance_label",
    "source_type",
    "execution_mode",
    "status",
    "lots",
    "side_preference",
    "has_open_position",
    "open_position_summary",
    "created_at",
    "updated_at",
}

ALLOWED_OPEN_POSITION_KEYS = {
    "has_open_position",
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
}

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

DB_SNAPSHOT_MODELS = {
    "strategy_catalog": models.StrategyCatalog,
    "strategy_versions": models.StrategyVersion,
    "user_strategy_instances": models.UserStrategyInstance,
    "strategy_signals": models.StrategySignal,
    "normalized_option_signals": models.NormalizedOptionSignal,
    "strategy_execution_jobs": models.StrategyExecutionJob,
    "portfolio_trades": models.PortfolioTrade,
    "portfolio_snapshots": models.PortfolioSnapshot,
}

RUNTIME_STATE_FILE_ATTRS = (
    "APP_STATE_FILE",
    "OPEN_POSITION_FILE",
    "PAPER_POSITION_FILE",
    "OPEN_POSITIONS_BY_INSTANCE_FILE",
    "PAPER_PORTFOLIO_FILE",
    "EXTERNAL_POSITIONS_FILE",
    "SEEN_SIGNALS_FILE",
    "SETTINGS_FILE",
)


class InspectorSmokeError(RuntimeError):
    """Raised when the smoke check refuses to run or verification fails."""


@dataclass(frozen=True)
class InspectorSmokeConfig:
    direct_service: bool = False
    server_url: str | None = None
    limit: int = 20
    timeout_seconds: float = 10.0


def run_inspector_smoke_check(
    config: InspectorSmokeConfig,
    *,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    _require_safe_environment(config)
    mode = "direct_service" if config.direct_service else "server"
    before_db = _database_snapshot()
    before_files = _runtime_file_snapshot()

    raw_outputs = _read_direct_service(config.limit) if config.direct_service else _read_server(config)
    sensitive_count = _sensitive_field_count(raw_outputs)
    job_field_issues = _job_field_issues(raw_outputs.get("jobs"))
    instance_field_issues = _instance_field_issues(raw_outputs.get("instances"))

    after_db = _database_snapshot()
    after_files = _runtime_file_snapshot()
    db_unchanged = before_db == after_db
    files_unchanged = before_files == after_files

    summary = {
        "ok": True,
        "mode": mode,
        "flags": feature_flag_states(),
        "settings": _settings_summary(),
        "readable": {
            "status": isinstance(raw_outputs.get("status"), dict),
            "jobs": isinstance(raw_outputs.get("jobs"), dict),
            "instances": isinstance(raw_outputs.get("instances"), dict),
        },
        "counts": _response_counts(raw_outputs),
        "safety": {
            "sensitive_field_count": sensitive_count,
            "jobs_safe_fields_only": len(job_field_issues) == 0,
            "instances_safe_fields_only": len(instance_field_issues) == 0,
            "db_unchanged": db_unchanged,
            "runtime_files_unchanged": files_unchanged,
        },
        "mutation_proof": {
            "db_before": before_db["counts"],
            "db_after": after_db["counts"],
            "db_signature_unchanged": before_db["signature"] == after_db["signature"],
            "runtime_file_signature_unchanged": before_files["signature"] == after_files["signature"],
            "runtime_files_checked": before_files["checked_count"],
        },
    }

    failures = []
    if sensitive_count:
        failures.append("Inspector exposed forbidden sensitive fields.")
    if job_field_issues:
        failures.append("Jobs response included unexpected fields.")
    if instance_field_issues:
        failures.append("Instances response included unexpected fields.")
    if not db_unchanged:
        failures.append("Database rows changed during read-only inspector smoke check.")
    if not files_unchanged:
        failures.append("Runtime state or log files changed during read-only inspector smoke check.")
    if failures:
        summary["ok"] = False
        _emit(out, "failure_summary", summary)
        raise InspectorSmokeError(" ".join(failures))

    _emit(out, "success_summary", summary)
    return _sanitize(summary)


def parse_args(argv: list[str] | None = None) -> InspectorSmokeConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--direct-service", action="store_true", help="Call inspector service functions in-process.")
    mode.add_argument("--server-url", help="Call existing debug inspector GET endpoints on this server base URL.")
    parser.add_argument("--limit", type=int, default=20, help="Rows to request from inspector endpoints.")
    parser.add_argument("--timeout-seconds", type=float, default=10.0, help="HTTP timeout for --server-url mode.")
    args = parser.parse_args(argv)
    return InspectorSmokeConfig(
        direct_service=bool(args.direct_service),
        server_url=str(args.server_url).rstrip("/") if args.server_url else None,
        limit=max(1, min(int(args.limit), 100)),
        timeout_seconds=max(float(args.timeout_seconds), 1.0),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        config = parse_args(argv)
        run_inspector_smoke_check(config)
    except InspectorSmokeError as exc:
        print(json.dumps({"ok": False, "error": _safe_error_message(exc)}, sort_keys=True))
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": _safe_error_message(exc),
                },
                sort_keys=True,
            )
        )
        return 1
    return 0


def _require_safe_environment(config: InspectorSmokeConfig) -> None:
    errors = []
    if not (config.direct_service or config.server_url):
        errors.append("Choose --direct-service or --server-url.")
    if not settings.DEBUG_ENABLED:
        errors.append("DEBUG_ENABLED must be true.")
    if not is_feature_enabled(MULTI_STRATEGY_FANOUT):
        errors.append("MULTI_STRATEGY_FANOUT must be true.")
    if not is_feature_enabled(V2_PAPER_RUNNER_DEBUG):
        errors.append("V2_PAPER_RUNNER_DEBUG must be true.")
    if not database_configured():
        errors.append("DATABASE_URL must be configured.")
    if errors:
        raise InspectorSmokeError(" ".join(errors))


def _read_direct_service(limit: int) -> dict[str, Any]:
    with session_scope() as db:
        return {
            "status": get_v2_paper_fanout_status(db, limit=limit),
            "jobs": list_v2_paper_fanout_jobs(db, limit=limit),
            "instances": list_v2_paper_strategy_instances(db, limit=limit),
        }


def _read_server(config: InspectorSmokeConfig) -> dict[str, Any]:
    if not config.server_url:
        raise InspectorSmokeError("server_url is required for server mode.")
    outputs = {}
    for name, path in ENDPOINTS.items():
        url = f"{urljoin(config.server_url + '/', path.lstrip('/'))}?limit={config.limit}"
        request = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raise InspectorSmokeError(f"Inspector endpoint returned HTTP {exc.code}.") from exc
        except URLError as exc:
            raise InspectorSmokeError("Inspector server request failed.") from exc
        outputs[name] = json.loads(raw)
    return outputs


def _database_snapshot() -> dict[str, Any]:
    with session_scope() as db:
        counts = {
            name: int(db.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in DB_SNAPSHOT_MODELS.items()
        }
        signatures = {
            "strategy_execution_jobs": _rows_signature(
                [
                    (
                        row.id,
                        row.status,
                        row.attempts,
                        row.locked_by,
                        row.completed_at,
                        row.last_error,
                        row.dead_letter_reason,
                        row.updated_at,
                    )
                    for row in db.scalars(
                        select(models.StrategyExecutionJob).order_by(models.StrategyExecutionJob.id)
                    ).all()
                ]
            ),
            "user_strategy_instances": _rows_signature(
                [
                    (
                        row.id,
                        row.status,
                        row.execution_mode,
                        row.lots,
                        row.side_preference,
                        row.updated_at,
                    )
                    for row in db.scalars(
                        select(models.UserStrategyInstance).order_by(models.UserStrategyInstance.id)
                    ).all()
                ]
            ),
            "strategy_signals": _rows_signature(
                [
                    (
                        row.id,
                        row.status,
                        row.updated_at,
                    )
                    for row in db.scalars(select(models.StrategySignal).order_by(models.StrategySignal.id)).all()
                ]
            ),
        }
    return {
        "counts": counts,
        "signature": _hash_json({"counts": counts, "signatures": signatures}),
    }


def _runtime_file_snapshot() -> dict[str, Any]:
    files: dict[str, Any] = {}
    for attr in RUNTIME_STATE_FILE_ATTRS:
        files[attr] = _file_signature(state_store.scoped_runtime_path(getattr(state_store, attr)))
    for name, path in state_store.LOG_FILES.items():
        files[f"LOG:{name}"] = _file_signature(state_store.scoped_runtime_path(path))
    return {
        "files": files,
        "checked_count": len(files),
        "signature": _hash_json(files),
    }


def _file_signature(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {"exists": False, "size": 0, "sha256": None}
    except OSError:
        return {"exists": False, "size": None, "sha256": "unreadable"}
    return {"exists": True, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _rows_signature(rows: list[tuple[Any, ...]]) -> str:
    return _hash_json([[ _jsonable(value) for value in row ] for row in rows])


def _response_counts(outputs: dict[str, Any]) -> dict[str, Any]:
    status = outputs.get("status") if isinstance(outputs.get("status"), dict) else {}
    jobs = outputs.get("jobs") if isinstance(outputs.get("jobs"), dict) else {}
    instances = outputs.get("instances") if isinstance(outputs.get("instances"), dict) else {}
    status_counts = status.get("counts") if isinstance(status.get("counts"), dict) else {}
    return {
        "status_counts": status_counts,
        "jobs_count": jobs.get("count"),
        "instances_count": instances.get("count"),
    }


def _job_field_issues(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["jobs_response_not_object"]
    jobs = value.get("jobs")
    if not isinstance(jobs, list):
        return ["jobs_not_list"]
    issues = []
    for item in jobs:
        if not isinstance(item, dict):
            issues.append("job_not_object")
            continue
        extra = set(item) - ALLOWED_JOB_KEYS
        if extra:
            issues.append("job_extra_fields")
    return issues


def _instance_field_issues(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["instances_response_not_object"]
    instances = value.get("instances")
    if not isinstance(instances, list):
        return ["instances_not_list"]
    issues = []
    for item in instances:
        if not isinstance(item, dict):
            issues.append("instance_not_object")
            continue
        if set(item) - ALLOWED_INSTANCE_KEYS:
            issues.append("instance_extra_fields")
        summary = item.get("open_position_summary")
        if isinstance(summary, dict) and set(summary) - ALLOWED_OPEN_POSITION_KEYS:
            issues.append("open_position_summary_extra_fields")
    return issues


def _sensitive_field_count(value: Any) -> int:
    if isinstance(value, dict):
        count = 0
        for key, item in value.items():
            if _is_redacted_key(key):
                count += 1
                continue
            count += _sensitive_field_count(item)
        return count
    if isinstance(value, list):
        return sum(_sensitive_field_count(item) for item in value)
    return 0


def _is_redacted_key(key: Any) -> bool:
    lowered = str(key).lower()
    return lowered in REDACTED_KEYS or any(fragment in lowered for fragment in REDACTED_KEY_FRAGMENTS)


def _settings_summary() -> dict[str, Any]:
    return {
        "debug_enabled": bool(settings.DEBUG_ENABLED),
        "database_configured": database_configured(),
    }


def _emit(out: Callable[[str], None], label: str, payload: Any) -> None:
    out(json.dumps({"event": label, "data": _sanitize(payload)}, sort_keys=True))


def _safe_error_message(exc: Exception) -> str:
    text = str(exc)
    if type(exc).__module__.split(".", 1)[0] in {"sqlalchemy", "psycopg", "psycopg2"}:
        return "Database operation failed; verify DATABASE_URL credentials and connectivity."
    if _sensitive_field_count({"error": text}):
        return "Smoke check failed."
    return text


def _sanitize(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return _sanitize(asdict(value))
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


def _hash_json(value: Any) -> str:
    payload = json.dumps(_sanitize(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
