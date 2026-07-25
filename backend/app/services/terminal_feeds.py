"""Curated owner-scoped read models for the Trading terminal.

Existing persisted sources are reused:
* WebhookEvent/StrategySignal for inbound automated activity;
* owner-scoped order JSONL for manual and automated executions;
* owner-scoped audit/error JSONL for engine history and historical alerts.

No raw webhook body, credentials, stack traces, request headers or broker
secrets are projected.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import models
from app.db.engine import database_configured, session_scope
from app.services.audit_logger import read_jsonl, sanitize_for_log
from app.services import signals_feed


def _event_key(kind: str, event: dict[str, Any]) -> str:
    identity = {
        "kind": kind,
        "timestamp": event.get("timestamp"),
        "event_type": event.get("event_type"),
        "signal_id": event.get("signal_id"),
        "order_id": event.get("order_id") or event.get("broker_order_id"),
        "message": event.get("message"),
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _stamp(value: Any) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except (TypeError, ValueError):
        return None


def _sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("occurred_at") or ""), str(item.get("id") or ""))


def activity(user_id: uuid.UUID, *, limit: int = 100) -> dict[str, Any]:
    automated = signals_feed.list_signals(user_id, limit=min(limit, 100)).get("items", [])
    items: list[dict[str, Any]] = [
        {
            "id": f"signal:{row['id']}",
            "occurred_at": row.get("received_at"),
            "source": "AUTOMATED",
            "strategy": row.get("strategy_name"),
            "signal": (row.get("summary") or {}).get("action") or (row.get("summary") or {}).get("side"),
            "instrument": (row.get("summary") or {}).get("symbol"),
            "order_type": "MARKET",
            "lots": (row.get("summary") or {}).get("qty"),
            "price": None,
            "status": row.get("processed_status"),
            "pnl": None,
        }
        for row in automated
    ]
    for event in read_jsonl("order", limit=limit):
        clean = sanitize_for_log(event)
        items.append(
            {
                "id": f"order:{_event_key('order', clean)}",
                "occurred_at": _stamp(clean.get("timestamp")),
                "source": "MANUAL" if clean.get("manual_order") else "AUTOMATED",
                "strategy": clean.get("strategy_code") or clean.get("strategy"),
                "signal": clean.get("normalized_action") or clean.get("action"),
                "instrument": clean.get("trading_symbol") or clean.get("symbol"),
                "order_type": clean.get("order_type") or "MARKET",
                "lots": clean.get("lots") or clean.get("qty"),
                "price": clean.get("average_price") or clean.get("price"),
                "status": clean.get("order_status") or clean.get("status"),
                "pnl": clean.get("realized_pnl") or clean.get("pnl"),
            }
        )
    items.sort(key=_sort_key, reverse=True)
    return {"ok": True, "items": items[:limit]}


def engine_log(*, limit: int = 100) -> dict[str, Any]:
    items = []
    for event in read_jsonl("audit", limit=limit):
        clean = sanitize_for_log(event)
        items.append(
            {
                "id": _event_key("audit", clean),
                "occurred_at": _stamp(clean.get("timestamp")),
                "level": str(clean.get("severity") or "INFO").upper(),
                "event_type": clean.get("event_type") or "ENGINE_EVENT",
                "message": str(clean.get("message") or "")[:500],
                "mode": clean.get("mode"),
            }
        )
    items.sort(key=_sort_key, reverse=True)
    return {"ok": True, "items": items[:limit]}


def executions(*, limit: int = 100) -> dict[str, Any]:
    items = []
    for event in read_jsonl("order", limit=limit):
        clean = sanitize_for_log(event)
        items.append(
            {
                "id": _event_key("execution", clean),
                "occurred_at": _stamp(clean.get("timestamp")),
                "source": "MANUAL" if clean.get("manual_order") else "AUTOMATED",
                "strategy": clean.get("strategy_code") or clean.get("strategy"),
                "side": clean.get("side") or clean.get("normalized_option_side"),
                "action": clean.get("normalized_action") or clean.get("action"),
                "instrument": clean.get("trading_symbol") or clean.get("symbol"),
                "qty": clean.get("filled_qty") or clean.get("qty"),
                "average_price": clean.get("average_price") or clean.get("price"),
                "status": clean.get("order_status") or clean.get("status"),
                "broker_order_id": clean.get("broker_order_id") or clean.get("order_id"),
                "pnl": clean.get("realized_pnl") or clean.get("pnl"),
            }
        )
    items.sort(key=_sort_key, reverse=True)
    return {"ok": True, "items": items[:limit]}


def alerts(user_id: uuid.UUID, *, runtime: dict[str, Any], limit: int = 100) -> dict[str, Any]:
    acknowledged = _acknowledged_keys(user_id)
    items: list[dict[str, Any]] = []
    historical = read_jsonl("error", limit=limit) + [
        row for row in read_jsonl("audit", limit=limit)
        if str(row.get("severity") or "").upper() in {"WARNING", "ERROR"}
    ]
    for event in historical:
        clean = sanitize_for_log(event)
        event_key = _event_key("alert", clean)
        items.append(
            {
                "id": event_key,
                "occurred_at": _stamp(clean.get("timestamp")),
                "severity": str(clean.get("severity") or "WARNING").upper(),
                "category": clean.get("event_type") or "ENGINE",
                "message": str(clean.get("message") or "")[:500],
                "active": False,
                "acknowledged": event_key in acknowledged,
            }
        )
    position = runtime.get("position") or {}
    ltp = position.get("ltp") or {}
    if position.get("has_open_position") and (ltp.get("stale") or ltp.get("value") is None):
        active = {
            "timestamp": ltp.get("received_at"),
            "event_type": "POSITION_QUOTE_STALE",
            "message": ltp.get("message") or "The active-position quote is stale or unavailable.",
        }
        event_key = _event_key("active-alert", active)
        items.append(
            {
                "id": event_key,
                "occurred_at": _stamp(active["timestamp"]),
                "severity": "WARNING",
                "category": active["event_type"],
                "message": active["message"],
                "active": True,
                "acknowledged": event_key in acknowledged,
            }
        )
    items.sort(key=_sort_key, reverse=True)
    return {
        "ok": True,
        "items": items[:limit],
        "unacknowledged_count": sum(1 for item in items if not item["acknowledged"]),
    }


def acknowledge(user_id: uuid.UUID, event_keys: list[str]) -> int:
    valid = {key for key in event_keys if len(key) == 64 and all(char in "0123456789abcdef" for char in key)}
    if not valid or not database_configured():
        return 0
    with session_scope() as db:
        existing = set(
            db.scalars(
                select(models.TerminalAlertAcknowledgement.event_key).where(
                    models.TerminalAlertAcknowledgement.user_id == user_id,
                    models.TerminalAlertAcknowledgement.event_key.in_(valid),
                )
            )
        )
        for key in valid - existing:
            db.add(models.TerminalAlertAcknowledgement(user_id=user_id, event_key=key))
        return len(valid - existing)


def _acknowledged_keys(user_id: uuid.UUID) -> set[str]:
    if not database_configured():
        return set()
    with session_scope() as db:
        return set(
            db.scalars(
                select(models.TerminalAlertAcknowledgement.event_key).where(
                    models.TerminalAlertAcknowledgement.user_id == user_id
                )
            )
        )
