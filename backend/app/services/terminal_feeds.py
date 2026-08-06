"""Owner-scoped, cursor-based read models for the Trading terminal."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db import models
from app.db.engine import database_configured, readonly_session_scope, session_scope
from app.services import signals_feed
from app.services.audit_logger import read_jsonl, sanitize_for_log
from app.services.execution_context import current_execution_user


class TerminalFeedScopeError(RuntimeError):
    """The requested owner does not match the bound runtime context."""


class ActiveAlertAcknowledgementError(ValueError):
    """Active safety conditions cannot be hidden or acknowledged."""


def _assert_owner_context(user_id: uuid.UUID) -> None:
    user = current_execution_user()
    if user is None or user.id != user_id:
        raise TerminalFeedScopeError("Terminal feed owner context is unavailable.")


def _event_key(kind: str, event: dict[str, Any]) -> str:
    identity = {
        "kind": kind,
        "timestamp": event.get("timestamp"),
        "event_type": event.get("event_type"),
        "signal_id": event.get("signal_id"),
        "operation_id": event.get("operation_id"),
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


def _metadata(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("metadata")
    return value if isinstance(value, dict) else {}


def _field(event: dict[str, Any], key: str) -> Any:
    return event.get(key) if event.get(key) not in (None, "") else _metadata(event).get(key)


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _result_field(result: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = result.get(key)
        if value not in (None, ""):
            return value
    # execution_result is where StrategyExecutionJob.result_summary actually
    # carries fill data (avg_price, normalized_strike, normalized_option_side,
    # ...) -- checking only the top level and "order" left avg_price/strike
    # unfindable for every automated paper trade.
    for nested_key in ("order", "execution_result"):
        nested = result.get(nested_key)
        if isinstance(nested, dict):
            for key in keys:
                value = nested.get(key)
                if value not in (None, ""):
                    return value
    return None


def _job_mode(execution_mode: str) -> str | None:
    normalized = str(execution_mode or "").lower()
    if normalized == "paper_live_data":
        return "paper"
    if normalized == "real_orders":
        return "live"
    return None


def _durable_execution_items(user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Project durable manual intents and automated jobs into terminal rows."""
    if not database_configured():
        return []
    items: list[dict[str, Any]] = []
    with readonly_session_scope() as db:
        intents = db.scalars(
            select(models.LiveOrderIntent)
            .where(models.LiveOrderIntent.user_id == user_id)
            .order_by(models.LiveOrderIntent.created_at.desc())
            .limit(500)
        ).all()
        jobs = db.scalars(
            select(models.StrategyExecutionJob)
            .where(models.StrategyExecutionJob.user_id == user_id)
            .order_by(models.StrategyExecutionJob.created_at.desc())
            .limit(500)
        ).all()

        for row in intents:
            result = dict(row.result_summary or {})
            metadata = dict(row.intent_metadata or {})
            requested_qty = _result_field(
                result,
                "requested_qty",
                "requestedQty",
                "normalized_qty",
                "qty",
            )
            filled_qty = _result_field(result, "filled_qty", "filledQty")
            requested_value = _integer(requested_qty)
            filled_value = _integer(filled_qty)
            mode = str(
                result.get("mode")
                or metadata.get("mode")
                or ("paper" if "paper" in row.scope else "live" if "live" in row.scope else "")
            ).lower() or None
            items.append(
                {
                    "id": f"order-intent:{row.id}",
                    "occurred_at": (row.updated_at or row.created_at).isoformat(),
                    "mode": mode,
                    "run_id": _result_field(result, "run_id", "runId"),
                    "source": "MANUAL" if metadata.get("manual_order") else "AUTOMATED",
                    "strategy": _result_field(result, "strategy_code", "strategy"),
                    "side": row.side or _result_field(result, "side", "option_side"),
                    "action": row.action or _result_field(result, "action", "operation"),
                    "instrument": row.symbol or _result_field(
                        result, "trading_symbol", "tradingSymbol", "symbol"
                    ),
                    "strike": _result_field(result, "normalized_strike", "strike"),
                    "option_side": _result_field(result, "normalized_option_side", "option_side"),
                    "requested_qty": requested_qty,
                    "filled_qty": filled_qty,
                    "remaining_qty": _result_field(
                        result, "remaining_qty", "remainingQuantity"
                    ),
                    "partial_fill": (
                        filled_value is not None
                        and requested_value is not None
                        and filled_value < requested_value
                    ),
                    "average_price": _result_field(
                        result, "average_price", "avg_price", "fillPrice", "price"
                    ),
                    "charges": _result_field(result, "charges", "simulatedCharges"),
                    "slippage": _result_field(
                        result, "slippage", "slippagePercent"
                    ),
                    "status": _result_field(
                        result, "status", "operationState"
                    ) or row.status,
                    "operation_id": str(row.id),
                    "order_id": row.broker_order_id
                    or _result_field(result, "order_id", "orderId", "fillOrderId"),
                    "broker_order_id": row.broker_order_id,
                    "broker_correlation_id": row.broker_correlation_id,
                    "pnl": _result_field(result, "realized_pnl", "pnl"),
                    "signal_id": row.signal_id,
                    "configuration_revision_id": _result_field(
                        result, "configuration_revision_id"
                    ),
                    "configuration_revision": _result_field(
                        result, "configuration_revision"
                    ),
                    "durable": True,
                }
            )

        for row in jobs:
            result = dict(row.result_summary or {})
            payload = dict(row.signal_payload or {})
            requested_qty = _result_field(
                result,
                "requested_qty",
                "requestedQty",
                "normalized_qty",
                "qty",
            ) or payload.get("qty")
            filled_qty = _result_field(result, "filled_qty", "filledQty")
            requested_value = _integer(requested_qty)
            filled_value = _integer(filled_qty)
            items.append(
                {
                    "id": f"strategy-job:{row.id}",
                    "occurred_at": (
                        row.completed_at or row.updated_at or row.created_at
                    ).isoformat(),
                    "mode": _job_mode(row.execution_mode),
                    "run_id": _result_field(result, "run_id", "runId"),
                    "source": "AUTOMATED",
                    "strategy": row.strategy_name,
                    "side": payload.get("option_side") or payload.get("side"),
                    "action": payload.get("action"),
                    "instrument": payload.get("trading_symbol") or payload.get("symbol"),
                    "strike": _result_field(result, "normalized_strike", "strike") or payload.get("strike"),
                    "option_side": _result_field(result, "normalized_option_side", "option_side") or payload.get("option_side"),
                    "requested_qty": requested_qty,
                    "filled_qty": filled_qty,
                    "remaining_qty": _result_field(
                        result, "remaining_qty", "remainingQuantity"
                    ),
                    "partial_fill": (
                        filled_value is not None
                        and requested_value is not None
                        and filled_value < requested_value
                    ),
                    "average_price": _result_field(
                        result, "average_price", "avg_price", "fillPrice", "price"
                    ),
                    "charges": _result_field(result, "charges", "simulatedCharges"),
                    "slippage": _result_field(
                        result, "slippage", "slippagePercent"
                    ),
                    "status": _result_field(result, "status") or row.status,
                    "operation_id": _result_field(
                        result, "operation_id", "order_intent_id"
                    ),
                    "order_id": _result_field(
                        result, "order_id", "orderId", "fillOrderId"
                    ),
                    "broker_order_id": _result_field(result, "broker_order_id"),
                    "broker_correlation_id": _result_field(
                        result, "broker_correlation_id"
                    ),
                    "pnl": _result_field(result, "realized_pnl", "pnl"),
                    "signal_id": row.signal_id,
                    "error": row.last_error,
                    "configuration_revision_id": (
                        str(row.configuration_revision_id)
                        if row.configuration_revision_id
                        else None
                    ),
                    "configuration_revision": row.configuration_revision,
                    "durable": True,
                }
            )

        # result_summary never carries PnL (only the paper portfolio computes
        # it, into PortfolioTrade.realized_pnl) -- backfill exit rows from there.
        missing_pnl_order_ids = {
            item["order_id"]
            for item in items
            if item.get("pnl") is None and item.get("order_id")
        }
        if missing_pnl_order_ids:
            trades = db.scalars(
                select(models.PortfolioTrade).where(
                    models.PortfolioTrade.user_id == user_id,
                    models.PortfolioTrade.exit_order_id.in_(missing_pnl_order_ids),
                )
            ).all()
            pnl_by_order_id = {trade.exit_order_id: trade.realized_pnl for trade in trades}
            for item in items:
                if item.get("pnl") is None and item.get("order_id") in pnl_by_order_id:
                    item["pnl"] = pnl_by_order_id[item["order_id"]]
    return items


def _matches(item: dict[str, Any], *, mode: str | None, run_id: str | None) -> bool:
    if mode and str(item.get("mode") or "").lower() != mode:
        return False
    return not (run_id and str(item.get("run_id") or "") != run_id)


_IST = ZoneInfo("Asia/Kolkata")


def _is_today_ist(occurred_at: Any) -> bool:
    if not occurred_at:
        return False
    try:
        stamp = datetime.fromisoformat(str(occurred_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(_IST).date() == datetime.now(_IST).date()


def _is_actionable(item: dict[str, Any]) -> bool:
    """The Signal & Order Activity tab is for what actually happened to a
    position -- buy, sell, lot changes, partial fills -- not every received
    signal or in-flight state. A row earns a place here once it carries real
    trade data (a durable execution record, or a quantity/price), not while
    it's still just "received"/"queued" with nothing to show yet."""
    return bool(item.get("durable")) or item.get("lots") not in (None, "") or item.get("price") not in (None, "")


def _encode_cursor(item: dict[str, Any]) -> str:
    raw = json.dumps(_sort_key(item), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str] | None:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(decoded, list) or len(decoded) != 2:
            return None
        return str(decoded[0]), str(decoded[1])
    except (ValueError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _page(
    items: list[dict[str, Any]],
    *,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    ordered = sorted(items, key=_sort_key)
    reconciliation_status = "CURRENT"
    if cursor:
        decoded = _decode_cursor(cursor)
        if decoded is None:
            reconciliation_status = "CURSOR_RESET"
            page = ordered[-limit:]
        else:
            page = [item for item in ordered if _sort_key(item) > decoded][:limit]
    else:
        page = ordered[-limit:]
    return {
        "ok": True,
        "items": page,
        "next_cursor": _encode_cursor(page[-1]) if page else cursor,
        "reconciliation_status": reconciliation_status,
    }


def activity(
    user_id: uuid.UUID,
    *,
    mode: str | None = None,
    run_id: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _assert_owner_context(user_id)
    requested_mode = str(mode or "").lower() or None
    automated = signals_feed.list_signals(user_id, limit=200).get("items", [])
    items: list[dict[str, Any]] = []
    lifecycle_by_signal: dict[str, list[dict[str, Any]]] = {}
    for row in automated:
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        signal_id = str(row.get("event_id") or row["id"])
        item = {
            "id": f"signal:{row['id']}",
            "occurred_at": row.get("received_at"),
            "source": "AUTOMATED",
            "strategy": row.get("strategy_name"),
            "signal": summary.get("action") or summary.get("side"),
            "signal_id": signal_id,
            "correlation_id": signal_id,
            "instrument": summary.get("symbol"),
            "order_type": "MARKET",
            "lots": summary.get("qty"),
            "price": None,
            "status": row.get("processed_status"),
            "pnl": None,
            "mode": str(summary.get("mode") or "").lower() or None,
            "run_id": summary.get("run_id"),
        }
        lifecycle_by_signal.setdefault(signal_id, []).append(
            {
                "stage": "SIGNAL_RECEIVED",
                "occurred_at": item["occurred_at"],
                "status": item["status"],
                "message": row.get("error") or "Signal persisted.",
            }
        )
        items.append(item)

    # The file-backed "order" JSONL log predates multi-user support and
    # carries no user_id on any entry -- every account's Trading Activity
    # tab was including every other account's (and the system's own
    # internal monitoring/reconciliation) entries verbatim, unfiltered.
    # signals_feed and _durable_execution_items below are both real
    # user_id-scoped DB queries; the JSONL stream is intentionally not
    # read here anymore rather than faked-scoped.

    durable = _durable_execution_items(user_id)
    known_correlations = {
        str(item.get("signal_id") or item.get("operation_id") or "")
        for item in items
        if item.get("signal_id") or item.get("operation_id")
    }
    for execution in durable:
        correlation = str(
            execution.get("signal_id")
            or execution.get("operation_id")
            or execution["id"]
        )
        stage = {
            "stage": str(execution.get("status") or "EXECUTION").upper(),
            "occurred_at": execution.get("occurred_at"),
            "status": execution.get("status"),
            "message": str(execution.get("error") or "")[:300],
            "order_id": execution.get("order_id"),
        }
        lifecycle_by_signal.setdefault(correlation, []).append(stage)
        if correlation in known_correlations:
            continue
        items.append(
            {
                "id": f"activity:{execution['id']}",
                "occurred_at": execution.get("occurred_at"),
                "source": execution.get("source"),
                "strategy": execution.get("strategy"),
                "signal": execution.get("action") or execution.get("side"),
                "signal_id": execution.get("signal_id"),
                "correlation_id": correlation,
                "instrument": execution.get("instrument"),
                "order_type": "MARKET",
                "lots": execution.get("requested_qty"),
                "price": execution.get("average_price"),
                "status": execution.get("status"),
                "pnl": execution.get("pnl"),
                "mode": execution.get("mode"),
                "run_id": execution.get("run_id"),
                "durable": True,
            }
        )
        known_correlations.add(correlation)

    filtered = [
        item for item in items
        if _matches(item, mode=requested_mode, run_id=run_id)
        and _is_today_ist(item.get("occurred_at"))
        and _is_actionable(item)
    ]
    for item in filtered:
        key = str(item.get("signal_id") or item.get("correlation_id") or item["id"])
        item["lifecycle"] = sorted(lifecycle_by_signal.get(key, []), key=_sort_key)
    return _page(filtered, limit=limit, cursor=cursor)


def engine_log(
    user_id: uuid.UUID,
    *,
    mode: str | None = None,
    run_id: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _assert_owner_context(user_id)
    requested_mode = str(mode or "").lower() or None
    items = []
    for event in read_jsonl("audit", limit=1000):
        clean = sanitize_for_log(event)
        item = {
            "id": _event_key("audit", clean),
            "occurred_at": _stamp(clean.get("timestamp")),
            "level": str(clean.get("severity") or "INFO").upper(),
            "event_type": clean.get("event_type") or "ENGINE_EVENT",
            "message": str(clean.get("message") or "")[:500],
            "mode": str(clean.get("mode") or "").lower() or None,
            "run_id": _field(clean, "run_id"),
        }
        # Day-scoped like activity() and executions(): the terminal shows the
        # current session, so every tab must clear at the next market open
        # rather than trailing yesterday's engine events into today.
        if _matches(item, mode=requested_mode, run_id=run_id) and _is_today_ist(
            item.get("occurred_at")
        ):
            items.append(item)
    return _page(items, limit=limit, cursor=cursor)


def executions(
    user_id: uuid.UUID,
    *,
    mode: str | None = None,
    run_id: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _assert_owner_context(user_id)
    requested_mode = str(mode or "").lower() or None
    # _durable_execution_items() is a real, user_id-scoped DB query
    # (LiveOrderIntent + StrategyExecutionJob) and already captures every
    # real execution for this user. The file-backed "order" JSONL log used
    # to be read here too as a supplement, but it predates multi-user
    # support and carries no user_id -- it was leaking every account's (and
    # the system's own internal monitoring) entries into every other
    # account's Executions tab. Same fix as activity() above: drop it
    # instead of pretending it's scoped.
    items = _durable_execution_items(user_id)
    filtered = [
        item
        for item in items
        if _matches(item, mode=requested_mode, run_id=run_id)
        and _is_today_ist(item.get("occurred_at"))
    ]
    return _page(filtered, limit=limit, cursor=cursor)


def _active_alert_items(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    position = runtime.get("position") or {}
    ltp = position.get("ltp") or {}
    if position.get("has_open_position") and (ltp.get("stale") or ltp.get("value") is None):
        active = {
            "timestamp": ltp.get("received_at"),
            "event_type": "POSITION_QUOTE_STALE",
            "message": ltp.get("message") or "The active-position quote is stale or unavailable.",
        }
        items.append(
            {
                "id": _event_key("active-alert", active),
                "occurred_at": _stamp(active["timestamp"]),
                "severity": "WARNING",
                "category": active["event_type"],
                "message": active["message"],
                "active": True,
                "acknowledged": False,
                "mode": str(runtime.get("mode") or "").lower() or None,
                "run_id": runtime.get("run_id"),
            }
        )
    return items


def alerts(
    user_id: uuid.UUID,
    *,
    runtime: dict[str, Any],
    mode: str | None = None,
    run_id: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _assert_owner_context(user_id)
    requested_mode = str(mode or "").lower() or None
    acknowledged = _acknowledged_keys(user_id)
    historical_items: list[dict[str, Any]] = []
    historical = read_jsonl("error", limit=1000) + [
        row for row in read_jsonl("audit", limit=1000)
        if str(row.get("severity") or "").upper() in {"WARNING", "ERROR"}
    ]
    for event in historical:
        clean = sanitize_for_log(event)
        event_key = _event_key("alert", clean)
        item = {
            "id": event_key,
            "occurred_at": _stamp(clean.get("timestamp")),
            "severity": str(clean.get("severity") or "WARNING").upper(),
            "category": clean.get("event_type") or "ENGINE",
            "message": str(clean.get("message") or "")[:500],
            "active": False,
            "acknowledged": event_key in acknowledged,
            "mode": str(clean.get("mode") or "").lower() or None,
            "run_id": _field(clean, "run_id"),
        }
        # Historical alerts are day-scoped like the other terminal tabs, so the
        # session starts clean each market open.
        if _matches(item, mode=requested_mode, run_id=run_id) and _is_today_ist(
            item.get("occurred_at")
        ):
            historical_items.append(item)
    # Active items are deliberately NOT day-scoped: they are conditions that are
    # true right now (e.g. a stale position quote). A live safety condition must
    # never be hidden because it first tripped before midnight.
    active_items = [
        item for item in _active_alert_items(runtime)
        if _matches(item, mode=requested_mode, run_id=run_id)
    ]
    page = _page(historical_items, limit=limit, cursor=cursor)
    page.update(
        {
            "active_items": sorted(active_items, key=_sort_key),
            "historical_items": page["items"],
            "items": sorted(active_items + page["items"], key=_sort_key),
            "unacknowledged_count": (
                len(active_items)
                + sum(1 for item in historical_items if not item["acknowledged"])
            ),
        }
    )
    return page


def acknowledge(
    user_id: uuid.UUID,
    event_keys: list[str],
    *,
    runtime: dict[str, Any],
) -> int:
    _assert_owner_context(user_id)
    active_keys = {item["id"] for item in _active_alert_items(runtime)}
    if active_keys.intersection(event_keys):
        raise ActiveAlertAcknowledgementError(
            "Active alerts remain visible until the underlying condition clears."
        )
    valid = {
        key for key in event_keys
        if len(key) == 64 and all(char in "0123456789abcdef" for char in key)
    }
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
    with readonly_session_scope() as db:
        return set(
            db.scalars(
                select(models.TerminalAlertAcknowledgement.event_key).where(
                    models.TerminalAlertAcknowledgement.user_id == user_id
                )
            )
        )
