from __future__ import annotations

import hashlib
import math
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from app.services.atm_ltp_service import get_atm_option_snapshot
from app.services.credential_vault import dhan_metadata
from app.services.execution_context import current_execution_user
from app.services.market_snapshot import get_shared_nifty_snapshot
from app.services.risk_manager import _market_is_open
from app.services.state_store import get_app_state, get_runtime_settings, get_wallet_snapshot
from app.workers.strategy_job_worker import strategy_job_worker_status


router = APIRouter()


def _marker_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _is_confirmed_chart_fill(event: dict[str, Any]) -> bool:
    return (
        event.get("phase") == "after_response"
        and event.get("success") is not False
        and str(event.get("status") or "").upper() in {"TRADED", "FILLED", "PARTIAL_FILL", "COMPLETED"}
        and (_marker_number(event.get("avg_price")) or 0) > 0
    )


def _marker_exit_kind(event: dict[str, Any]) -> str:
    if event.get("reversal_exit"):
        return "REVERSAL"
    reason = str(event.get("exit_reason") or "").strip().upper()
    if reason in {"SL", "STOP_LOSS"} or reason.startswith("TRAIL"):
        return "SL"
    if reason in {"TP", "TAKE_PROFIT", "TARGET"}:
        return "TARGET"
    if reason in {"EOD", "EOD_FLATTEN"}:
        return "EOD"
    return "EXIT"


def _build_nifty_markers(events: list[dict[str, Any]], *, mode: str, trading_date: date, limit: int) -> list[dict[str, Any]]:
    """Convert only confirmed, uniquely identified fills into safe chart data."""
    from app.services.market_chart_service import _IST
    from app.services.portfolio_analytics import _dedupe_legs_by_order_id

    legs = _dedupe_legs_by_order_id([
        event for event in events
        if isinstance(event, dict) and _is_confirmed_chart_fill(event) and str(event.get("mode") or "").lower() == mode
    ])
    markers: list[dict[str, Any]] = []
    for event in legs:
        action = str(event.get("normalized_action") or event.get("action") or "").upper()
        order_id = str(event.get("order_id") or "").strip()
        if action not in {"ENTRY", "EXIT"} or not order_id:
            continue
        try:
            timestamp = datetime.fromisoformat(str(event.get("timestamp")).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if timestamp.astimezone(_IST).date() != trading_date:
            continue
        option_side_value = str(event.get("normalized_option_side") or event.get("option_side") or "").upper()
        option_side = option_side_value if option_side_value in {"CE", "PE"} else None
        exit_kind = _marker_exit_kind(event) if action == "EXIT" else None
        labels = {"SL": "SL EXIT", "TARGET": "TARGET", "REVERSAL": "REV EXIT", "EOD": "EOD EXIT", "EXIT": "EXIT"}
        marker_id = hashlib.sha256(f"{order_id}|{timestamp.isoformat()}|{action}".encode()).hexdigest()[:24]
        contract = str(event.get("trading_symbol") or "").strip()[:80] or None
        quantity = _marker_number(event.get("filled_qty") or event.get("final_qty") or event.get("normalized_qty") or event.get("qty"))
        markers.append({
            "id": marker_id,
            "time": int(timestamp.timestamp()),
            "side": "BUY" if action == "ENTRY" else "SELL",
            "option_side": option_side,
            "exit_kind": exit_kind,
            "label": labels[exit_kind] if exit_kind else (f"BUY {option_side}" if option_side else "BUY"),
            "contract": contract,
            "execution_price": _marker_number(event.get("avg_price")),
            "quantity": quantity if quantity is not None and quantity > 0 else None,
            "pnl": _marker_number(event.get("realized_pnl") or event.get("pnl")) if action == "EXIT" else None,
            # Option premium is metadata only; it is never plotted on the NIFTY scale.
            "price": None,
            "approximate": True,
            "mode": mode,
            "source": "paper_trade" if mode == "paper" else "trade_execution",
        })
    # Python's stable sort preserves execution-log order for equal timestamps.
    return sorted(markers, key=lambda marker: marker["time"])[-limit:]


@router.get("/market/nifty-snapshot")
def nifty_snapshot() -> dict[str, Any]:
    return get_shared_nifty_snapshot()


@router.get("/market/atm-ltp")
def atm_ltp(
    side: str = Query(default="BOTH", pattern="^(CE|PE|BOTH)$"),
    lots: int = Query(default=1, ge=1, le=20),
) -> dict[str, Any]:
    return get_atm_option_snapshot(option_side=side, lots=lots, allow_rest_fallback=True)


@router.get("/market/nifty/candles")
def nifty_candles(interval: str = Query(default="5m", pattern="^5m$")) -> dict[str, Any]:
    """Global NIFTY candles from the shared Dhan market-data identity.

    Served from a global cache - no per-user Dhan credentials, entitlement,
    or egress required. Never includes tokens or client ids.
    """
    from app.services.market_chart_service import get_nifty_candles

    return get_nifty_candles(interval=interval)


@router.get("/market/nifty/chart-status")
def nifty_chart_status() -> dict[str, Any]:
    from app.services.market_chart_service import chart_status

    return chart_status()


@router.get("/market/nifty/markers")
def nifty_markers(
    mode: str | None = Query(default=None, pattern="^(paper|live)$"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """Current user's BUY/SELL execution markers for TODAY's NIFTY chart.

    Isolation guarantees:
    - USER: read_jsonl("order") resolves through scoped_runtime_path, so
      records come from the AUTHENTICATED user's private order log - another
      user's trades are physically in a different directory. The mode query
      param is a display filter only, never a permission boundary.
    - MODE: paper markers derive from paper legs in the user's runtime order
      log (temporary display data, never DB rows); live markers derive from
      the same durable per-user log's live legs. A paper leg can never appear
      when mode=live and vice versa.
    - DAY: only events on today's IST trading date are returned, so paper
      markers automatically expire when the trading date rolls over.
    """
    from app.services.audit_logger import read_jsonl
    from app.services.market_chart_service import chart_trading_date_ist
    from app.services.state_store import get_engine_mode

    active_mode = (mode or get_engine_mode(legacy_fallback=False) or get_engine_mode() or "").lower()
    today = chart_trading_date_ist()
    base = {"symbol": "NIFTY", "trading_date": today.isoformat(), "mode": active_mode or None}
    if active_mode not in {"paper", "live"}:
        return {**base, "markers": []}

    return {**base, "markers": _build_nifty_markers(read_jsonl("order", limit=1000), mode=active_mode, trading_date=today, limit=limit)}


@router.get("/system/health-strip")
def system_health_strip() -> dict[str, Any]:
    app_state = get_app_state()
    runtime = get_runtime_settings()
    dhan = dhan_metadata()
    worker = strategy_job_worker_status()
    feed = _feed_status()
    return {
        "dhan": _dhan_status(dhan),
        "staticIp": _static_ip_status(),
        "market": "open" if _market_is_open() else "closed",
        "feed": feed.get("state"),
        "feedReason": feed.get("reason"),
        "engine": "paused" if not bool(runtime.get("allow_entry", True)) else "listening" if app_state.get("webhook_trading_enabled") else "idle",
        "pubsub": "healthy" if worker.get("running") else "delayed" if worker.get("enabled") else "unknown",
        "lastSignalAt": (app_state.get("last_signal") or {}).get("receivedAt") if isinstance(app_state.get("last_signal"), dict) else app_state.get("last_alert_at"),
        "walletOk": bool(get_wallet_snapshot().get("success")),
    }


@router.get("/system/marketfeed-status")
def marketfeed_status() -> dict[str, Any]:
    """Full diagnostics for the Dhan market-feed WebSocket + shared data token."""
    from app.services.dhan_marketfeed_ws import marketfeed_ws_status
    from app.services.shared_market_data import shared_market_data_status

    return {
        "feed": _feed_status(),
        "websocket": marketfeed_ws_status(),
        "sharedMarketData": shared_market_data_status(),
        "marketOpen": _market_is_open(),
    }


FEED_STALE_AFTER_SECONDS = 30.0


def _feed_status() -> dict[str, Any]:
    """Summarize the Dhan market-feed WebSocket into live/stale/down/off + reason."""
    from datetime import datetime, timezone

    from app.services.dhan_marketfeed_ws import marketfeed_ws_status

    ws = marketfeed_ws_status()
    if not _market_is_open():
        return {"state": "off", "reason": "Market closed; feed idle.", "ws": ws}
    if not ws.get("thread_alive"):
        return {
            "state": "down",
            "reason": ws.get("last_error") or "Feed thread not running (no subscription requested yet).",
            "ws": ws,
        }
    last_message_age = _age_seconds(ws.get("last_message_at"), now=datetime.now(timezone.utc))
    if ws.get("connected") and last_message_age is not None and last_message_age <= FEED_STALE_AFTER_SECONDS:
        return {"state": "live", "reason": None, "ws": ws}
    if ws.get("connected"):
        return {
            "state": "stale",
            "reason": (
                f"Connected but no tick for {int(last_message_age)}s." if last_message_age is not None else "Connected but no ticks received yet."
            ),
            "ws": ws,
        }
    return {
        "state": "down",
        "reason": ws.get("last_error") or "WebSocket disconnected; reconnecting.",
        "ws": ws,
    }


def _age_seconds(value: Any, *, now: Any) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime

        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            from datetime import timezone

            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (now - parsed).total_seconds())
    except (TypeError, ValueError):
        return None


def _dhan_status(meta: dict[str, Any]) -> str:
    if not meta.get("connected"):
        return "unknown"
    if meta.get("token_expired") is True:
        return "auth_issue"
    return "connected"


def _static_ip_status() -> str:
    user = current_execution_user()
    if user is None or user.is_dev:
        return "unknown"
    try:
        from app.services.strategy_fanout import get_user_egress

        egress = get_user_egress(user.id)
    except Exception:
        return "failed"
    if not egress:
        return "unknown"
    return "verified" if egress.get("verified") else "failed"
