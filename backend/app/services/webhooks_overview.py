"""Owner-scoped read model for the Webhooks page.

Truthfulness notes (verified against the code, not the mockup):

* There is ONE webhook secret per USER - ``UserCredentialVault.webhook_secret_encrypted``.
  ``StrategySubscription`` has no webhook token/secret columns, so the mockup's
  "one endpoint per strategy, each with its own secret" does not exist and is not
  invented here.
* The secret is never returned. Only ``webhook_secret_metadata()`` is exposed,
  which yields ``{set, masked, source}``.
* Delivery history is the same ``WebhookEvent`` table the Signals page reads, so
  the recent list is delegated to ``signals_feed`` rather than duplicated.

Read-only. No writes, no migration.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.config import settings
from app.db import models
from app.db.engine import database_configured, session_scope
from app.services import signals_feed
from app.services.credential_vault import webhook_secret_metadata

RECENT_LIMIT = 10
WINDOW_HOURS = 24


def _endpoints() -> list[dict[str, Any]]:
    """The inbound endpoints that actually exist in this deployment."""
    base = (settings.BACKEND_PUBLIC_BASE_URL or "").rstrip("/")
    out: list[dict[str, Any]] = []
    if base:
        out.append({
            "key": "tradingview",
            "label": "TradingView alerts",
            "url": f"{base}/webhook/tradingview",
            "method": "POST",
            "description": "Public TradingView alert receiver, verified with your webhook secret.",
        })
        out.append({
            "key": "private",
            "label": "Private strategy webhook",
            "url": f"{base}/api/webhooks/private",
            "method": "POST",
            "description": "Private strategy receiver for your own imported strategies.",
        })
    return out


def build_webhooks_overview(user_id: uuid.UUID) -> dict[str, Any]:
    secret = webhook_secret_metadata()
    payload: dict[str, Any] = {
        "ok": True,
        "available": True,
        "endpoints": _endpoints(),
        # Masked metadata only - the raw secret is never serialised.
        "secret": {
            "set": bool(secret.get("set")),
            "masked": secret.get("masked"),
            "source": secret.get("source"),
        },
        "window_hours": WINDOW_HOURS,
        "deliveries": {"counts": {}, "signature_verified": 0, "last_delivery_at": None},
        "recent": [],
    }

    if not database_configured():
        payload["available"] = False
        return payload

    window_start = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    with session_scope() as db:
        rows = db.execute(
            select(models.WebhookEvent.processed_status, func.count())
            .where(
                models.WebhookEvent.user_id == user_id,
                models.WebhookEvent.received_at >= window_start,
            )
            .group_by(models.WebhookEvent.processed_status)
        ).all()
        counts = {str(state): int(total) for state, total in rows}
        counts["total"] = sum(counts.values())

        verified = db.scalar(
            select(func.count())
            .select_from(models.WebhookEvent)
            .where(
                models.WebhookEvent.user_id == user_id,
                models.WebhookEvent.received_at >= window_start,
                models.WebhookEvent.signature_ok.is_(True),
            )
        ) or 0

        last_at = db.scalar(
            select(func.max(models.WebhookEvent.received_at)).where(
                models.WebhookEvent.user_id == user_id
            )
        )

    payload["deliveries"] = {
        "counts": counts,
        "signature_verified": int(verified),
        "last_delivery_at": last_at.isoformat() if last_at else None,
    }
    # Same owner-scoped, payload-safe projection the Signals page uses.
    payload["recent"] = signals_feed.list_signals(user_id, limit=RECENT_LIMIT).get("items", [])
    return payload
