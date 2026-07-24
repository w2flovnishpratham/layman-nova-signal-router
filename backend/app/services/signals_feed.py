"""Owner-scoped read model for the Signals page.

Source of truth is ``WebhookEvent``: it carries ``user_id`` and the composite
index ``ix_webhook_events_user_received (user_id, received_at)``, so it can be
paged per owner. ``StrategySignal`` is deliberately NOT the driving table - it
has no ``user_id`` (it is a global idempotency record keyed by
``(strategy_name, signal_id)``), so querying it directly would leak rows across
owners. It is only used to enrich rows the owner already owns.

Read-only. No writes, no engine interaction, no migration.
"""
from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.db import models
from app.db.engine import database_configured, session_scope

# Real persisted values of WebhookEvent.processed_status. Deliberately NOT the
# mockup's "routed/blocked/duplicate" vocabulary - we expose what is stored.
SIGNAL_STATUSES = ("received", "accepted", "queued", "rejected")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
COUNTS_WINDOW_HOURS = 24


def _encode_cursor(received_at: datetime, row_id: uuid.UUID) -> str:
    raw = f"{received_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        stamp, _, ident = raw.partition("|")
        return datetime.fromisoformat(stamp), uuid.UUID(ident)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None


def _summary(event: models.WebhookEvent, signal: models.StrategySignal | None) -> dict[str, Any]:
    """Curated, non-sensitive projection. Never returns the raw payload."""
    meta = event.event_metadata if isinstance(event.event_metadata, dict) else {}
    out: dict[str, Any] = {}
    for key in ("action", "side", "symbol", "option_side", "strike", "expiry", "qty", "alert"):
        if key in meta:
            out[key] = meta[key]
    if signal is not None and isinstance(signal.result_summary, dict):
        for key in ("status", "reason", "order_id"):
            if key in signal.result_summary:
                out.setdefault(key, signal.result_summary[key])
    return out


def list_signals(
    user_id: uuid.UUID,
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    status: str = "all",
    since: datetime | None = None,
) -> dict[str, Any]:
    """Page one owner's inbound signal events, newest first."""
    if not database_configured():
        return {"ok": True, "items": [], "next_cursor": None, "counts": {}, "available": False}

    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    status = (status or "all").strip().lower()

    with session_scope() as db:
        query = select(models.WebhookEvent).where(models.WebhookEvent.user_id == user_id)

        if status != "all":
            if status not in SIGNAL_STATUSES:
                return {"ok": False, "error": f"Unknown status filter: {status}", "items": [], "next_cursor": None, "counts": {}}
            query = query.where(models.WebhookEvent.processed_status == status)
        if since is not None:
            query = query.where(models.WebhookEvent.received_at >= since)

        # Keyset pagination on (received_at, id) so a growing table never pays
        # for OFFSET and pages stay stable while new rows arrive.
        if cursor:
            decoded = _decode_cursor(cursor)
            if decoded is None:
                return {"ok": False, "error": "Invalid cursor.", "items": [], "next_cursor": None, "counts": {}}
            cur_at, cur_id = decoded
            query = query.where(
                (models.WebhookEvent.received_at < cur_at)
                | ((models.WebhookEvent.received_at == cur_at) & (models.WebhookEvent.id < cur_id))
            )

        query = query.order_by(
            models.WebhookEvent.received_at.desc(), models.WebhookEvent.id.desc()
        ).limit(limit + 1)

        rows = list(db.scalars(query))
        has_more = len(rows) > limit
        rows = rows[:limit]

        # Enrich only rows this owner already owns, matched by event_id.
        signals: dict[str, models.StrategySignal] = {}
        event_ids = [r.event_id for r in rows if r.event_id]
        if event_ids:
            for sig in db.scalars(
                select(models.StrategySignal).where(models.StrategySignal.signal_id.in_(event_ids))
            ):
                signals.setdefault(sig.signal_id, sig)

        items = []
        for row in rows:
            sig = signals.get(row.event_id)
            items.append({
                "id": str(row.id),
                "received_at": row.received_at.isoformat() if row.received_at else None,
                "provider": row.provider,
                "event_id": row.event_id,
                "signature_ok": bool(row.signature_ok),
                "replay_status": row.replay_status,
                "processed_status": row.processed_status,
                "error": row.error,
                "strategy_name": sig.strategy_name if sig else None,
                "signal_status": sig.status if sig else None,
                "summary": _summary(row, sig),
                # NOTE: raw payload and webhook secrets are never returned, only
                # the body digest, so the feed cannot leak alert contents.
                "raw_body_sha256": row.raw_body_sha256,
            })

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = _encode_cursor(last.received_at, last.id)

        window_start = datetime.now(timezone.utc) - timedelta(hours=COUNTS_WINDOW_HOURS)
        counts_rows = db.execute(
            select(models.WebhookEvent.processed_status, func.count())
            .where(
                models.WebhookEvent.user_id == user_id,
                models.WebhookEvent.received_at >= window_start,
            )
            .group_by(models.WebhookEvent.processed_status)
        ).all()
        counts = {str(state): int(total) for state, total in counts_rows}
        counts["total"] = sum(counts.values())

    return {
        "ok": True,
        "available": True,
        "items": items,
        "next_cursor": next_cursor,
        "counts": counts,
        "counts_window_hours": COUNTS_WINDOW_HOURS,
    }
