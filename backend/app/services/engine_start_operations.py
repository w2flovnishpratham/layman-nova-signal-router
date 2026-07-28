"""Durable, owner-scoped idempotency for selected-strategy engine starts."""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.db.engine import session_scope

_LOCKS: dict[uuid.UUID, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class IdempotencyConflict(ValueError):
    """An idempotency key was reused for a different start request."""


@dataclass(frozen=True)
class StartClaim:
    operation_id: uuid.UUID
    status: str
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None


def payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _claim_existing(
    row: models.EngineStartOperation,
    expected_hash: str,
) -> StartClaim:
    if row.payload_hash != expected_hash:
        raise IdempotencyConflict(
            "Idempotency-Key was already used with a different engine-start payload."
        )
    return StartClaim(
        operation_id=row.id,
        status=row.status,
        result=dict(row.result_summary or {}) or None,
        error_code=row.error_code,
        error_message=row.error_message,
    )


def claim(
    *,
    user_id: uuid.UUID,
    idempotency_key: str,
    payload: dict[str, Any],
) -> StartClaim:
    key = idempotency_key.strip()
    if not key or len(key) > 255:
        raise ValueError("Idempotency-Key must contain 1 to 255 characters.")
    hashed = payload_hash(payload)
    with session_scope() as db:
        existing = db.scalar(
            select(models.EngineStartOperation)
            .where(
                models.EngineStartOperation.user_id == user_id,
                models.EngineStartOperation.idempotency_key == key,
            )
            .with_for_update()
        )
        if existing is not None:
            return _claim_existing(existing, hashed)
        operation = models.EngineStartOperation(
            user_id=user_id,
            idempotency_key=key,
            payload_hash=hashed,
            mode=str(payload["mode"]),
            strategy_instance_id=uuid.UUID(str(payload["strategy_instance_id"])),
            strategy_version_id=uuid.UUID(str(payload["strategy_version_id"])),
            configuration_revision_id=uuid.UUID(
                str(payload["configuration_revision_id"])
            ),
            configuration_revision=int(payload["configuration_revision"]),
            live_acknowledged=bool(payload["live_acknowledged"]),
            status="pending",
        )
        db.add(operation)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(
                select(models.EngineStartOperation).where(
                    models.EngineStartOperation.user_id == user_id,
                    models.EngineStartOperation.idempotency_key == key,
                )
            )
            if existing is None:
                raise
            return _claim_existing(existing, hashed)
        return StartClaim(
            operation_id=operation.id,
            status="new",
            result=None,
            error_code=None,
            error_message=None,
        )


def succeed(
    operation_id: uuid.UUID,
    *,
    result: dict[str, Any],
    run_id: uuid.UUID | None,
) -> None:
    with session_scope() as db:
        operation = db.get(models.EngineStartOperation, operation_id)
        if operation is None:
            raise RuntimeError("Engine start operation disappeared before completion.")
        operation.status = "succeeded"
        operation.started_run_id = run_id
        operation.result_summary = result
        operation.error_code = None
        operation.error_message = None
        operation.completed_at = models.utcnow()
        operation.updated_at = models.utcnow()


def fail(
    operation_id: uuid.UUID,
    *,
    status_code: int,
    error_code: str,
    message: str,
    detail: Any = None,
) -> None:
    with session_scope() as db:
        operation = db.get(models.EngineStartOperation, operation_id)
        if operation is None:
            return
        operation.status = "failed"
        operation.error_code = error_code[:80]
        operation.error_message = message
        operation.result_summary = {
            "status_code": status_code,
            "detail": detail if detail is not None else message,
        }
        operation.completed_at = models.utcnow()
        operation.updated_at = models.utcnow()


@contextmanager
def owner_start_lock(user_id: uuid.UUID) -> Iterator[None]:
    """Serialize all start attempts for an owner across workers.

    PostgreSQL uses a transaction-scoped advisory lock. SQLite tests and local
    no-Postgres development use a process lock with the same owner granularity.
    """

    with _LOCKS_GUARD:
        process_lock = _LOCKS.setdefault(user_id, threading.RLock())
    with process_lock, session_scope() as db:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            lock_key = int.from_bytes(user_id.bytes[:8], "big", signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        yield


__all__ = [
    "IdempotencyConflict",
    "StartClaim",
    "claim",
    "fail",
    "owner_start_lock",
    "payload_hash",
    "succeed",
]
