from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from app.config import settings
from app.services.audit_logger import log_audit_event
from app.services.credential_vault import get_dhan_credentials, mask_client_id
from app.services.dhan_client import DhanFundsResult, RealDhanClient
from app.services.paper_portfolio import paper_wallet_snapshot
from app.services.dhan_response_safety import sanitize_wallet_snapshot
from app.services.state_store import default_wallet_snapshot, get_engine_mode, get_wallet_snapshot, set_wallet_snapshot, utc_now


STALE_AFTER_SECONDS = 30
_IST = ZoneInfo("Asia/Kolkata")


def _ist_date_str() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _snapshot_from_result(result: DhanFundsResult, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or default_wallet_snapshot()
    available = result.available_balance

    # R1 — Daily reset of session_start_balance at IST midnight.
    # When the IST date changes, treat the current available balance as the
    # new session start and zero out session_pnl. This makes session_pnl
    # equivalent to "intraday realised P&L since today\'s first wallet read"
    # session_pnl is still used by the dashboard for the day's P&L display.
    today_ist = _ist_date_str()
    prev_date_ist = previous.get("session_start_date_ist")
    session_start = previous.get("session_start_balance")
    session_start_date_ist = prev_date_ist

    date_rollover = (
        prev_date_ist is not None
        and prev_date_ist != today_ist
        and available is not None
    )
    if date_rollover:
        session_start = available
        session_start_date_ist = today_ist
    elif session_start is None and available is not None:
        session_start = available
        session_start_date_ist = today_ist

    session_pnl = None
    if session_start is not None and available is not None:
        session_pnl = round(float(available) - float(session_start), 2)

    snapshot = default_wallet_snapshot()
    snapshot.update(
        {
            "success": result.success,
            "message": result.message,
            "client_id": mask_client_id(result.client_id),
            "available_balance": available,
            "withdrawable_balance": result.withdrawable_balance,
            "utilized_amount": result.utilized_amount,
            "sod_limit": result.sod_limit,
            "collateral_amount": result.collateral_amount,
            "blocked_payout_amount": result.blocked_payout_amount,
            "session_start_balance": session_start,
            "session_start_date_ist": session_start_date_ist,
            "session_pnl": session_pnl,
            "last_checked_at": utc_now(),
        }
    )
    return sanitize_wallet_snapshot(snapshot)


def wallet_is_stale(snapshot: dict[str, Any]) -> bool:
    last_checked = _parse_ts(snapshot.get("last_checked_at"))
    if last_checked is None:
        return True
    return datetime.now(last_checked.tzinfo) - last_checked > timedelta(seconds=STALE_AFTER_SECONDS)


def refresh_wallet_snapshot(
    *, force: bool = False, log_event: bool = False, proxy_url: str | None = None
) -> dict[str, Any]:
    """`proxy_url=""` bypasses the user's assigned execution-node proxy for this
    one call (used by the standalone credential-verify check, which is
    read-only and must not depend on static-IP infra). Leave unset (None) for
    every other caller — live wallet polling still correctly routes through
    the verified proxy."""
    if get_engine_mode(legacy_fallback=False) == "paper":
        snapshot = paper_wallet_snapshot()
        if log_event:
            log_audit_event("PAPER_WALLET_UPDATED", snapshot["message"], metadata=snapshot)
        return snapshot

    current = get_wallet_snapshot()
    if not force and not wallet_is_stale(current):
        return sanitize_wallet_snapshot(current)

    creds = get_dhan_credentials()
    if not creds:
        snapshot = default_wallet_snapshot()
        snapshot.update(
            {
                "success": False,
                "message": "Dhan Client ID or Access Token missing.",
                "last_checked_at": utc_now(),
            }
        )
        return sanitize_wallet_snapshot(set_wallet_snapshot(snapshot))

    # Only pass proxy_url when a caller explicitly overrides it, so the default
    # path is still a bare RealDhanClient() call (matches what test doubles
    # for RealDhanClient across the suite expect).
    client = RealDhanClient(proxy_url=proxy_url) if proxy_url is not None else RealDhanClient()
    result = client.get_fund_limit(
        client_id=creds.client_id,
        access_token=creds.access_token,
    )

    snapshot = set_wallet_snapshot(_snapshot_from_result(result, current))
    if log_event:
        log_audit_event(
            "WALLET_BALANCE_UPDATED",
            snapshot["message"],
            severity="INFO" if snapshot["success"] else "WARNING",
            metadata={
                "success": snapshot["success"],
                "client_id": snapshot["client_id"],
                "available_balance": snapshot["available_balance"],
                "withdrawable_balance": snapshot["withdrawable_balance"],
                "utilized_amount": snapshot["utilized_amount"],
                "session_pnl": snapshot["session_pnl"],
            },
        )
    return sanitize_wallet_snapshot(snapshot)
