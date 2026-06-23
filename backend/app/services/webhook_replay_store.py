"""Durable webhook replay and duplicate protection."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import models
from app.db.engine import database_configured, session_scope


class WebhookReplayStoreUnavailable(RuntimeError):
    """Raised when durable replay state cannot be written."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def raw_body_sha256(raw_body: str | bytes) -> str:
    data = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8", errors="replace")
    return hashlib.sha256(data).hexdigest()


def record_user_nonce(user_id: uuid.UUID | str, nonce: str) -> str:
    """Persist a nonce.

    Returns "fresh", "duplicate", or "unavailable" when no DB is configured.
    Any other database failure is raised so callers can fail closed.
    """
    if not database_configured():
        return "unavailable"
    try:
        user_uuid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
        with session_scope() as db:
            db.add(models.WebhookNonce(user_id=user_uuid, nonce=str(nonce), seen_at=utcnow()))
            db.flush()
        return "fresh"
    except IntegrityError:
        return "duplicate"


def claim_webhook_event(
    *,
    provider: str,
    event_id: str,
    raw_body: str | bytes,
    user_id: uuid.UUID | str | None = None,
    signature_ok: bool,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Claim an inbound webhook event by provider+event_id.

    Unique constraint conflicts are returned as duplicate/tampered outcomes; the
    caller must not fan out or route duplicates.
    """
    if not database_configured():
        return {"status": "unavailable"}

    provider = str(provider or "").strip().lower()
    event_id = str(event_id or "").strip()
    if not provider or not event_id:
        raise ValueError("provider and event_id are required.")
    digest = raw_body_sha256(raw_body)
    user_uuid = user_id if isinstance(user_id, uuid.UUID) or user_id is None else uuid.UUID(str(user_id))
    now = utcnow()
    try:
        with session_scope() as db:
            row = models.WebhookEvent(
                provider=provider,
                event_id=event_id,
                user_id=user_uuid,
                raw_body_sha256=digest,
                signature_ok=bool(signature_ok),
                replay_status="fresh",
                processed_status="received",
                event_metadata=metadata or {},
                received_at=now,
                updated_at=now,
            )
            db.add(row)
            db.flush()
        return {"status": "fresh", "raw_body_sha256": digest}
    except IntegrityError:
        with session_scope() as db:
            existing = db.scalar(
                select(models.WebhookEvent).where(
                    models.WebhookEvent.provider == provider,
                    models.WebhookEvent.event_id == event_id,
                )
            )
            if existing is None:
                raise WebhookReplayStoreUnavailable("Webhook event conflict could not be re-read.")
            return {
                "status": "duplicate" if existing.raw_body_sha256 == digest else "tampered",
                "raw_body_sha256": digest,
                "existing_raw_body_sha256": existing.raw_body_sha256,
                "processed_status": existing.processed_status,
                "error": existing.error,
            }


def update_webhook_event(
    *,
    provider: str,
    event_id: str,
    processed_status: str,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not database_configured():
        return
    provider = str(provider or "").strip().lower()
    event_id = str(event_id or "").strip()
    with session_scope() as db:
        row = db.scalar(
            select(models.WebhookEvent).where(
                models.WebhookEvent.provider == provider,
                models.WebhookEvent.event_id == event_id,
            )
        )
        if row is None:
            return
        row.processed_status = processed_status
        row.error = error
        if metadata:
            existing = row.event_metadata if isinstance(row.event_metadata, dict) else {}
            row.event_metadata = {**existing, **metadata}
        row.updated_at = utcnow()


def prune_webhook_replay_records(retention_seconds: int | None = None) -> dict[str, int]:
    if not database_configured():
        return {"events": 0, "nonces": 0}
    retention = max(
        int(retention_seconds or settings.WEBHOOK_REPLAY_RETENTION_SECONDS),
        600,
    )
    cutoff = utcnow() - timedelta(seconds=retention)
    with session_scope() as db:
        event_result = db.execute(delete(models.WebhookEvent).where(models.WebhookEvent.received_at < cutoff))
        nonce_result = db.execute(delete(models.WebhookNonce).where(models.WebhookNonce.seen_at < cutoff))
        return {
            "events": int(event_result.rowcount or 0),
            "nonces": int(nonce_result.rowcount or 0),
        }
