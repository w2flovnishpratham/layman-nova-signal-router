from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Query

from app.config import settings
from app.routers.setup import tradingview_webhook_url
from app.services.audit_logger import read_jsonl
from app.services.credential_vault import dhan_metadata, get_dhan_credentials, webhook_secret_metadata
from app.services.portfolio_analytics import build_portfolio_analytics
from app.services.position_reconciler import get_reconciled_open_position
from app.services.state_store import get_app_state, get_external_positions, get_runtime_settings
from app.services.wallet_service import refresh_wallet_snapshot


router = APIRouter()

CHAT_FEED_EVENT_LIMIT = 100
CHAT_FEED_ORDER_EVENT_LIMIT = 1000


FLOW_STEPS = [
    "WAITING_ENTRY",
    "ENTRY_SIGNAL_RECEIVED",
    "ENTRY_RISK_CHECK",
    "ENTRY_ORDER_SENDING",
    "ENTERED",
    "WAITING_EXIT",
    "EXIT_SIGNAL_RECEIVED",
    "EXIT_ORDER_SENDING",
    "EXITED",
]

STATE_TO_STEP = {
    "WAITING_ENTRY": "WAITING_ENTRY",
    "ENTRY_SIGNAL_RECEIVED": "ENTRY_SIGNAL_RECEIVED",
    "ENTRY_RISK_CHECK": "ENTRY_RISK_CHECK",
    "ENTRY_ORDER_SENDING": "ENTRY_ORDER_SENDING",
    "WAITING_EXIT": "WAITING_EXIT",
    "EXIT_SIGNAL_RECEIVED": "EXIT_SIGNAL_RECEIVED",
    "EXIT_ORDER_SENDING": "EXIT_ORDER_SENDING",
    "CLOSED": "EXITED",
    "BLOCKED": "ENTRY_RISK_CHECK",
    "DUPLICATE_SIGNAL": "ENTRY_SIGNAL_RECEIVED",
    "ERROR": "ENTRY_ORDER_SENDING",
}


def _latest(log_name: str) -> dict | None:
    rows = read_jsonl(log_name, limit=1)
    return rows[-1] if rows else None


@router.get("/dashboard/summary")
def dashboard_summary() -> dict:
    open_position = get_reconciled_open_position(reason="dashboard_summary")
    app_state = get_app_state()
    runtime_settings = get_runtime_settings()

    tradingview_url = tradingview_webhook_url()
    creds = get_dhan_credentials()
    meta = dhan_metadata()
    webhook_meta = webhook_secret_metadata()
    has_token = bool(creds and creds.access_token)
    wallet = refresh_wallet_snapshot(force=False, log_event=False) if has_token else app_state.get("wallet")

    return {
        "dhan_connected": bool(meta["connected"]),
        "dhan_client_id_masked": meta["client_id_masked"],
        "webhook_secret_set": bool(webhook_meta["set"]),
        "engine_started": bool(app_state.get("webhook_trading_enabled")),
        "app_state": app_state,
        "open_position": open_position,
        "external_positions": get_external_positions(),
        "wallet": wallet,
        "settings": runtime_settings,
        "webhook_url": tradingview_url,
        "mode": {
            "dhan_mode": settings.DHAN_MODE.upper(),
            "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
            "webhook_trading_enabled": bool(app_state.get("webhook_trading_enabled")),
            "dhan_token_configured": has_token,
        },
        "last_logs": {
            "webhook": _latest("webhook"),
            "order": _latest("order"),
            "audit": _latest("audit"),
            "error": _latest("error"),
        },
    }



@router.get("/dashboard/portfolio")
def dashboard_portfolio() -> dict:
    """Analytics for the user's portfolio dashboard.

    Only round-trips NOVA actually executed (an entry order it placed and the
    matching exit it placed) are included. Returns KPIs, equity curve, daily
    PnL, side/symbol breakdowns and the closed-trade ledger.
    """
    return build_portfolio_analytics()


@router.get("/dashboard/live-flow")
def live_flow() -> list[dict]:
    app_state = get_app_state()
    current_step = STATE_TO_STEP.get(app_state.get("state"), "WAITING_ENTRY")
    current_index = FLOW_STEPS.index(current_step)
    blocked = app_state.get("state") in {"BLOCKED", "DUPLICATE_SIGNAL"}
    error = app_state.get("state") == "ERROR"
    latest_order = _latest("order") or {}

    steps: list[dict] = []
    for index, step in enumerate(FLOW_STEPS):
        if index < current_index:
            status = "done"
        elif index == current_index:
            status = "blocked" if blocked else "error" if error else "active"
        else:
            status = "pending"

        steps.append(
            {
                "step": step,
                "status": status,
                "timestamp": app_state.get("last_alert_at") if index == current_index else None,
                "message": app_state.get("last_message") if index == current_index else None,
                "order_id": latest_order.get("order_id") if step in {"ENTERED", "EXITED"} else None,
                "reason": app_state.get("last_message") if status in {"blocked", "error"} else None,
            }
        )
    return steps


@router.get("/logs")
def logs(limit: int = Query(default=100, ge=1, le=1000)) -> dict:
    return {
        "webhook_events": read_jsonl("webhook", limit=limit),
        "order_events": read_jsonl("order", limit=limit),
        "audit_events": read_jsonl("audit", limit=limit),
        "error_events": read_jsonl("error", limit=limit),
    }


@router.get("/system/webhook-url")
def webhook_url() -> dict:
    return {
        "public_base_url": settings.BACKEND_PUBLIC_BASE_URL.rstrip("/"),
        "tradingview_webhook_url": tradingview_webhook_url(),
        "webhook_trading_enabled": bool(get_app_state().get("webhook_trading_enabled")),
        "dhan_mode": settings.DHAN_MODE.upper(),
        "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
    }


@router.get("/dashboard/chat-feed")
def chat_feed(today_only: bool = Query(default=False)) -> list[dict]:
    # Read latest events from log files
    webhook_events = read_jsonl("webhook", limit=CHAT_FEED_EVENT_LIMIT)
    order_events = read_jsonl("order", limit=CHAT_FEED_ORDER_EVENT_LIMIT)
    audit_events = read_jsonl("audit", limit=CHAT_FEED_EVENT_LIMIT)
    error_events = read_jsonl("error", limit=CHAT_FEED_EVENT_LIMIT)

    # Optionally restrict to today's IST date so the Setup page only shows
    # the current session's activity. Full history remains available via
    # /logs (LogsPage) and the dedicated Activities table.
    if today_only:
        ist = ZoneInfo("Asia/Kolkata")
        ist_today_start = datetime.now(ist).replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = ist_today_start.astimezone(timezone.utc)

        def _ev_ts(ev: dict) -> datetime:
            ts = ev.get("timestamp")
            if not ts:
                return datetime.fromisoformat("1970-01-01T00:00:00+00:00")
            try:
                parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
            except Exception:
                return datetime.fromisoformat("1970-01-01T00:00:00+00:00")

        def _today_only(events: list[dict]) -> list[dict]:
            return [ev for ev in events if _ev_ts(ev) >= today_start_utc]

        webhook_events = _today_only(webhook_events)
        order_events = _today_only(order_events)
        audit_events = _today_only(audit_events)
        error_events = _today_only(error_events)

    # Parse ISO timestamp to datetime object
    def parse_ts(ts_str: str | None) -> datetime:
        if not ts_str:
            return datetime.fromisoformat("1970-01-01T00:00:00+00:00")
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            return datetime.fromisoformat("1970-01-01T00:00:00+00:00")

    # Group events by signal_id (for those that have one)
    signal_groups: dict[str, list[dict]] = defaultdict(list)
    standalone_system_events: list[dict] = []

    for ev in webhook_events + order_events + audit_events + error_events:
        sig_id = ev.get("signal_id")
        if not sig_id:
            metadata = ev.get("metadata")
            if isinstance(metadata, dict):
                sig_id = metadata.get("signal_id")

        if sig_id:
            signal_groups[sig_id].append(ev)
        else:
            standalone_system_events.append(ev)

    # Combine items for chronological sorting
    items_to_sort = []

    # Add signal groups
    for sig_id, group_evs in signal_groups.items():
        # Sort internal events of the group chronologically
        group_evs.sort(key=lambda x: parse_ts(x.get("timestamp")))
        start_time = parse_ts(group_evs[0].get("timestamp"))
        items_to_sort.append({
            "type": "signal_group",
            "id": sig_id,
            "events": group_evs,
            "timestamp": start_time
        })

    # Add standalone system events
    for ev in standalone_system_events:
        items_to_sort.append({
            "type": "system_event",
            "event": ev,
            "timestamp": parse_ts(ev.get("timestamp"))
        })

    # Sort items chronologically
    items_to_sort.sort(key=lambda x: x["timestamp"])

    # Build the final chat feed
    feed: list[dict] = []

    # Initial static greetings
    feed.append({
        "id": "init-welcome",
        "timestamp": "2026-05-22T00:00:00.000Z",
        "sender": "bot",
        "text": "👋 Hey, I’m NOVA Signal Router.\nI’m listening for TradingView alerts and will route them through backend safety checks before any Dhan order.",
        "type": "text"
    })
    feed.append({
        "id": "init-waiting",
        "timestamp": "2026-05-22T00:00:00.100Z",
        "sender": "bot",
        "text": "⏳ Waiting for TradingView entry alert...",
        "type": "text"
    })

    # Process items in chronological order
    for item in items_to_sort:
        if item["type"] == "signal_group":
            sig_id = item["id"]
            group_evs = item["events"]

            # Identify key events in group
            normalized_event = None
            before_request = None
            after_response = None
            blocked_event = None
            placed_event = None
            failed_event = None

            for ev in group_evs:
                ev_type = ev.get("event_type")
                phase = ev.get("phase")

                if ev_type == "WEBHOOK_NORMALIZED":
                    normalized_event = ev
                elif phase == "before_request":
                    before_request = ev
                elif phase == "after_response":
                    after_response = ev
                elif phase == "blocked" or ev_type in {"BLOCKED", "DUPLICATE_SIGNAL", "SIGNAL_INVALID"}:
                    blocked_event = ev
                elif ev_type in {"ORDER_PLACED", "EXIT_ORDER_PLACED"}:
                    placed_event = ev
                elif ev_type in {"ENTRY_ORDER_FAILED", "EXIT_ORDER_FAILED", "WEBHOOK_ROUTING_FAILED"}:
                    failed_event = ev

            # Extract signal properties across all group events
            action = None
            side = None
            qty = 0
            symbol = ""
            trading_symbol = ""
            strike = None
            expiry = None
            option_side = None
            payload_format = "NOVA"

            for ev in group_evs:
                if "normalized_action" in ev and ev["normalized_action"]:
                    action = ev["normalized_action"]
                elif "action" in ev and ev["action"]:
                    action = ev["action"]

                if "normalized_side" in ev and ev["normalized_side"]:
                    side = ev["normalized_side"]
                elif "side" in ev and ev["side"]:
                    side = ev["side"]

                if "normalized_qty" in ev and ev["normalized_qty"] is not None:
                    qty = ev["normalized_qty"]
                elif "qty" in ev and ev["qty"] is not None:
                    qty = ev["qty"]

                if "normalized_symbol" in ev and ev["normalized_symbol"]:
                    symbol = ev["normalized_symbol"]
                elif "symbol" in ev and ev["symbol"]:
                    symbol = ev["symbol"]

                if "trading_symbol" in ev and ev["trading_symbol"]:
                    trading_symbol = ev["trading_symbol"]
                elif "normalized_symbol" in ev and ev["normalized_symbol"]:
                    trading_symbol = ev["normalized_symbol"]

                if "normalized_strike" in ev and ev["normalized_strike"] is not None:
                    strike = ev["normalized_strike"]
                elif "strike" in ev and ev["strike"] is not None:
                    strike = ev["strike"]

                if "normalized_expiry" in ev and ev["normalized_expiry"]:
                    expiry = ev["normalized_expiry"]
                elif "expiry" in ev and ev["expiry"]:
                    expiry = ev["expiry"]

                if "normalized_option_side" in ev and ev["normalized_option_side"]:
                    option_side = ev["normalized_option_side"]
                elif "option_side" in ev and ev["option_side"]:
                    option_side = ev["option_side"]

                if "payload_format" in ev and ev["payload_format"]:
                    payload_format = ev["payload_format"]

            if not action:
                action = "ENTRY"

            start_time_dt = item["timestamp"]

            def get_time(offset_seconds: float) -> str:
                try:
                    dt = start_time_dt + timedelta(seconds=offset_seconds)
                    return dt.isoformat()
                except Exception:
                    return start_time_dt.isoformat()

            # Message 1: Alert received
            alert_text = "📩 Exit alert received." if action == "EXIT" else "📩 TradingView alert received."
            feed.append({
                "id": f"alert-{sig_id}",
                "timestamp": get_time(0),
                "sender": "bot",
                "text": alert_text,
                "type": "text"
            })

            # Message 2: Mini signal card
            feed.append({
                "id": f"card-{sig_id}",
                "timestamp": get_time(0.1),
                "sender": "bot",
                "text": "",
                "type": "signal_card",
                "metadata": {
                    "payload_format": payload_format,
                    "action": action,
                    "side": side,
                    "symbol": symbol,
                    "trading_symbol": trading_symbol,
                    "strike": strike,
                    "expiry": expiry,
                    "option_side": option_side,
                    "qty": qty
                }
            })

            # Message 3: Running risk checks
            feed.append({
                "id": f"risk-start-{sig_id}",
                "timestamp": get_time(0.2),
                "sender": "bot",
                "text": "🛡 Running backend risk checks...",
                "type": "text"
            })

            # Message 4: Risk Check result
            if blocked_event:
                reason = blocked_event.get("reason") or blocked_event.get("message") or "Trade blocked."
                feed.append({
                    "id": f"risk-result-{sig_id}",
                    "timestamp": blocked_event.get("timestamp") or get_time(0.3),
                    "sender": "bot",
                    "text": f"🚫 Trade blocked: {reason}",
                    "type": "text"
                })
            elif failed_event and not before_request and not after_response:
                reason = failed_event.get("message") or "Routing failed before an order request was written."
                feed.append({
                    "id": f"risk-result-{sig_id}",
                    "timestamp": failed_event.get("timestamp") or get_time(0.3),
                    "sender": "bot",
                    "text": f"⚠️ Routing failed: {reason}",
                    "type": "text"
                })
            elif before_request:
                pass_time = before_request.get("timestamp")
                feed.append({
                    "id": f"risk-result-{sig_id}",
                    "timestamp": pass_time,
                    "sender": "bot",
                    "text": "✅ Risk check passed",
                    "type": "text"
                })

                # Message 5: Sending order to Dhan
                send_time = get_time(0.4)
                send_text = "🚪 Sending exit order to Dhan..." if action == "EXIT" else "🚀 Sending entry order to Dhan..."
                feed.append({
                    "id": f"order-sending-{sig_id}",
                    "timestamp": send_time,
                    "sender": "bot",
                    "text": send_text,
                    "type": "text"
                })

                # Message 6: Order response
                if placed_event or (after_response and after_response.get("success")):
                    res_event = after_response or placed_event
                    order_id = res_event.get("order_id")
                    if not order_id and isinstance(res_event.get("metadata"), dict):
                        order_id = res_event.get("metadata", {}).get("order_id")
                    avg_price = res_event.get("avg_price")
                    order_status = res_event.get("status")
                    dhan_mode = res_event.get("dhan_mode")

                    result_time = res_event.get("timestamp") or get_time(0.5)

                    if action == "EXIT":
                        feed.append({
                            "id": f"order-result-{sig_id}",
                            "timestamp": result_time,
                            "sender": "bot",
                            "text": "✅ Trade exited. Waiting for next entry alert.",
                            "type": "text"
                        })
                    else:
                        feed.append({
                            "id": f"order-result-{sig_id}",
                            "timestamp": result_time,
                            "sender": "bot",
                            "text": "✅ Entry order placed.",
                            "type": "text"
                        })

                    # Order card
                    feed.append({
                        "id": f"order-card-{sig_id}",
                        "timestamp": result_time,
                        "sender": "bot",
                        "text": "",
                        "type": "order_card",
                        "metadata": {
                            "order_id": order_id,
                            "qty": qty,
                            "trading_symbol": trading_symbol or symbol,
                            "avg_price": avg_price,
                            "status": order_status,
                            "action": action,
                            "side": side,
                            "dhan_mode": dhan_mode,
                            "executed_at": result_time,
                        }
                    })

                    # Position open helper message
                    if action != "EXIT":
                        feed.append({
                            "id": f"pos-open-{sig_id}",
                            "timestamp": get_time(0.6),
                            "sender": "bot",
                            "text": "📊 Position open. Waiting for TradingView exit alert.",
                            "type": "text"
                        })

                elif failed_event or (after_response and not after_response.get("success")):
                    res_event = failed_event or after_response
                    reason = res_event.get("message") or res_event.get("error") or "Dhan order rejected"
                    result_time = res_event.get("timestamp") or get_time(0.5)

                    feed.append({
                        "id": f"order-result-{sig_id}",
                        "timestamp": result_time,
                        "sender": "bot",
                        "text": f"⚠️ Dhan order rejected: {reason}",
                        "type": "text"
                    })

                    if "token" in reason.lower() or "unauthorized" in reason.lower():
                        feed.append({
                            "id": f"order-token-err-{sig_id}",
                            "timestamp": get_time(0.6),
                            "sender": "bot",
                            "text": "🔐 Token invalid. Update Dhan token before trading.",
                            "type": "text"
                        })

                else:
                    feed.append({
                        "id": f"order-pending-{sig_id}",
                        "timestamp": get_time(0.5),
                        "sender": "bot",
                        "text": "Waiting for Dhan order response...",
                        "type": "text"
                    })
            else:
                feed.append({
                    "id": f"routing-pending-{sig_id}",
                    "timestamp": get_time(0.3),
                    "sender": "bot",
                    "text": "Routing did not write an order event yet. Check backend errors before resending.",
                    "type": "text"
                })

        elif item["type"] == "system_event":
            ev = item["event"]
            ev_type = ev.get("event_type")
            timestamp = ev.get("timestamp")

            if ev_type == "EMERGENCY_STOP_ON":
                feed.append({
                    "id": f"user-estop-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "user",
                    "text": "emergency stop",
                    "type": "text"
                })
                feed.append({
                    "id": f"bot-estop-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "bot",
                    "text": "🚨 Emergency stop activated. All new entries are blocked.",
                    "type": "text"
                })
            elif ev_type == "EMERGENCY_STOP_OFF":
                feed.append({
                    "id": f"user-resume-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "user",
                    "text": "resume",
                    "type": "text"
                })
                feed.append({
                    "id": f"bot-resume-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "bot",
                    "text": "✅ Emergency stop cleared. Ready for entries.",
                    "type": "text"
                })
            elif ev_type == "ALLOW_ENTRY_DISABLED":
                feed.append({
                    "id": f"user-pause-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "user",
                    "text": "pause",
                    "type": "text"
                })
                feed.append({
                    "id": f"bot-pause-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "bot",
                    "text": "⏸️ Entry signals paused. Existing positions can still be exited.",
                    "type": "text"
                })
            elif ev_type == "ALLOW_ENTRY_ENABLED":
                feed.append({
                    "id": f"user-resume-ent-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "user",
                    "text": "resume entries",
                    "type": "text"
                })
                feed.append({
                    "id": f"bot-resume-ent-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "bot",
                    "text": "▶️ Entry signals resumed.",
                    "type": "text"
                })
            elif ev_type == "PANIC_EXIT_TRIGGERED":
                feed.append({
                    "id": f"user-panic-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "user",
                    "text": "panic exit",
                    "type": "text"
                })
                feed.append({
                    "id": f"bot-panic-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "bot",
                    "text": "🚨 Panic exit triggered! Initiating exit flow...",
                    "type": "text"
                })
            elif ev_type == "RUNTIME_STATE_RESET":
                feed.append({
                    "id": f"user-reset-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "user",
                    "text": "reset",
                    "type": "text"
                })
                feed.append({
                    "id": f"bot-reset-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "bot",
                    "text": "🔄 State reset. Cleared open position and reset state to WAITING_ENTRY.",
                    "type": "text"
                })
            elif ev_type == "SEEN_SIGNALS_CLEARED":
                feed.append({
                    "id": f"user-clear-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "user",
                    "text": "clear duplicates",
                    "type": "text"
                })
                feed.append({
                    "id": f"bot-clear-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "bot",
                    "text": "🧹 Duplicate signals cache cleared.",
                    "type": "text"
                })

            elif ev_type == "WALLET_BALANCE_UPDATED":
                metadata = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
                available = metadata.get("available_balance")
                pnl = metadata.get("session_pnl")
                available_text = f"Rs.{available:.2f}" if isinstance(available, (int, float)) else "-"
                pnl_text = f"Rs.{pnl:.2f}" if isinstance(pnl, (int, float)) else "-"
                feed.append({
                    "id": f"wallet-{timestamp}",
                    "timestamp": timestamp,
                    "sender": "bot",
                    "text": f"Wallet updated. Available balance: `{available_text}`. Session PnL: `{pnl_text}`.",
                    "type": "text"
                })

    return feed
