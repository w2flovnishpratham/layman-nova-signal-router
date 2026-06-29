"""Durable idempotency helpers for live Dhan order writes."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.db.engine import database_configured, session_scope


class OrderIdempotencyUnavailable(RuntimeError):
    """Raised when live order idempotency cannot use durable storage."""


@dataclass(frozen=True)
class OrderIntentClaim:
    status: str
    intent_id: str | None = None
    result_summary: dict[str, Any] | None = None
    message: str | None = None

    @property
    def fresh(self) -> bool:
        return self.status == "fresh"


def stable_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_key(value: str) -> str:
    key = str(value or "").strip()
    if not key:
        raise ValueError("idempotency key is required.")
    if len(key) > 255:
        raise ValueError("idempotency key is too long.")
    return key


def _find_intent(db, *, user_id: uuid.UUID, scope: str, idempotency_key: str):
    return db.scalar(
        select(models.LiveOrderIntent).where(
            models.LiveOrderIntent.user_id == user_id,
            models.LiveOrderIntent.scope == scope,
            models.LiveOrderIntent.idempotency_key == idempotency_key,
        )
    )


def claim_live_order_intent(
    *,
    user_id: uuid.UUID | str,
    scope: str,
    idempotency_key: str,
    payload_hash: str,
    signal_id: str | None = None,
    action: str | None = None,
    side: str | None = None,
    symbol: str | None = None,
    broker_correlation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> OrderIntentClaim:
    if not database_configured():
        raise OrderIdempotencyUnavailable("Durable order idempotency store is unavailable.")
    user_uuid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    normalized_scope = str(scope or "").strip().lower()
    if not normalized_scope:
        raise ValueError("idempotency scope is required.")
    normalized_key = _normalize_key(idempotency_key)
    now = models.utcnow()
    try:
        with session_scope() as db:
            row = models.LiveOrderIntent(
                user_id=user_uuid,
                scope=normalized_scope,
                idempotency_key=normalized_key,
                payload_hash=payload_hash,
                status="pending",
                signal_id=signal_id,
                action=action,
                side=side,
                symbol=symbol,
                broker_correlation_id=broker_correlation_id,
                intent_metadata=metadata or {},
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.flush()
            return OrderIntentClaim(status="fresh", intent_id=str(row.id))
    except IntegrityError:
        replay = lookup_live_order_intent(
            user_id=user_uuid,
            scope=normalized_scope,
            idempotency_key=normalized_key,
            payload_hash=payload_hash,
        )
        if replay.status == "missing":
            raise OrderIdempotencyUnavailable("Order intent conflict could not be re-read.")
        return replay


def lookup_live_order_intent(
    *,
    user_id: uuid.UUID | str,
    scope: str,
    idempotency_key: str,
    payload_hash: str,
) -> OrderIntentClaim:
    if not database_configured():
        raise OrderIdempotencyUnavailable("Durable order idempotency store is unavailable.")
    user_uuid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    normalized_scope = str(scope or "").strip().lower()
    if not normalized_scope:
        raise ValueError("idempotency scope is required.")
    normalized_key = _normalize_key(idempotency_key)
    with session_scope() as db:
        existing = _find_intent(
            db,
            user_id=user_uuid,
            scope=normalized_scope,
            idempotency_key=normalized_key,
        )
        if existing is None:
            return OrderIntentClaim(status="missing")
        if existing.payload_hash != payload_hash:
            return OrderIntentClaim(
                status="conflict",
                intent_id=str(existing.id),
                message="Idempotency key was already used for a different order payload.",
            )
        if existing.result_summary:
            return OrderIntentClaim(
                status="replay",
                intent_id=str(existing.id),
                result_summary=dict(existing.result_summary),
                message="Duplicate order request returned the original result.",
            )
        return OrderIntentClaim(
            status="in_progress",
            intent_id=str(existing.id),
            message="Order intent is already in progress. Reconcile broker state before retrying.",
        )


def mark_order_intent_submitted(
    intent_id: str,
    *,
    broker_correlation_id: str | None = None,
) -> None:
    if not intent_id or not database_configured():
        return
    intent_uuid = uuid.UUID(str(intent_id))
    with session_scope() as db:
        row = db.get(models.LiveOrderIntent, intent_uuid)
        if row is None:
            return
        row.status = "submitted"
        if broker_correlation_id:
            row.broker_correlation_id = broker_correlation_id
        row.updated_at = models.utcnow()


def complete_order_intent(
    intent_id: str,
    *,
    result_summary: dict[str, Any],
) -> None:
    if not intent_id or not database_configured():
        return
    intent_uuid = uuid.UUID(str(intent_id))
    success = bool(result_summary.get("success"))
    status = str(result_summary.get("status") or "").upper()
    if success and status not in {"ORDER_STATE_UNKNOWN"}:
        intent_status = "completed"
    elif status in {"PENDING_CONFIRMATION", "PARTIAL_FILL_PENDING", "PARTIAL_FILL", "ORDER_STATE_UNKNOWN"}:
        intent_status = "submitted"
    else:
        intent_status = "failed"
    with session_scope() as db:
        row = db.get(models.LiveOrderIntent, intent_uuid)
        if row is None:
            return
        row.status = intent_status
        row.result_summary = result_summary
        order_id = result_summary.get("order_id")
        if order_id not in (None, ""):
            row.broker_order_id = str(order_id)
        row.updated_at = models.utcnow()
