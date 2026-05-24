"""
JSONL logging for the file-backed MVP runtime.

Dhan access tokens are never written to logs. Webhook secrets are masked before
they are exposed through the frontend logs endpoint.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.services.state_store import LOG_FILES, utc_now


logger = logging.getLogger("audit")
_LOG_LOCK = threading.RLock()
_SECRET_RE = re.compile(r'("secret"\s*:\s*")[^"]+(")', re.IGNORECASE)


def _mask_client_id(value: Any) -> Any:
    if value in (None, ""):
        return value
    text = str(value)
    if len(text) <= 4:
        return "****"
    return f"{'*' * max(len(text) - 4, 0)}{text[-4:]}"


def _mask_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if "token" in lowered or lowered in {"access_token", "secret"}:
                if isinstance(item, str) and "*" in item:
                    masked[key] = item
                else:
                    masked[key] = "***REDACTED***"
            elif lowered in {"client_id", "dhan_client_id", "dhanclientid", "client-id"}:
                masked[key] = _mask_client_id(item)
            else:
                masked[key] = _mask_sensitive(item)
        return masked
    if isinstance(value, list):
        return [_mask_sensitive(item) for item in value]
    if isinstance(value, str):
        return _SECRET_RE.sub(r'\1***REDACTED***\2', value)
    return value


def sanitize_for_log(data: Any) -> Any:
    return _mask_sensitive(deepcopy(data))


def _append_jsonl(path: Path, event: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": utc_now(), **sanitize_for_log(event)}
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
    with _LOG_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return record


def log_webhook_event(event: dict[str, Any]) -> dict[str, Any]:
    return _append_jsonl(LOG_FILES["webhook"], event)


def log_order_event(event: dict[str, Any]) -> dict[str, Any]:
    return _append_jsonl(LOG_FILES["order"], event)


def log_audit_event(
    event_type: str,
    message: str,
    *,
    severity: str = "INFO",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = _append_jsonl(
        LOG_FILES["audit"],
        {
            "event_type": event_type,
            "severity": severity,
            "message": message,
            "metadata": metadata or {},
        },
    )
    log_method = logger.warning if severity.upper() == "WARNING" else logger.info
    if severity.upper() == "ERROR":
        log_method = logger.error
    log_method("[%s] %s: %s", severity.upper(), event_type, message)
    return record


def log_error_event(event_type: str, message: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    logger.error("%s: %s", event_type, message)
    return _append_jsonl(
        LOG_FILES["error"],
        {
            "event_type": event_type,
            "severity": "ERROR",
            "message": message,
            "metadata": metadata or {},
        },
    )


def read_jsonl(log_name: str, limit: int = 100) -> list[dict[str, Any]]:
    path = LOG_FILES[log_name]
    if not path.exists():
        return []
    with _LOG_LOCK:
        lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append(
                {
                    "timestamp": None,
                    "event_type": "MALFORMED_LOG_LINE",
                    "message": line[:500],
                }
            )
    return records
