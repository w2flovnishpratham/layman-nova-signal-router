from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.config import settings
from app.services.audit_logger import log_audit_event
from app.services.credential_vault import get_dhan_credentials, mask_client_id
from app.services.dhan_client import DhanFundsResult, MockDhanClient, RealDhanClient
from app.services.state_store import default_wallet_snapshot, get_wallet_snapshot, set_wallet_snapshot, utc_now


STALE_AFTER_SECONDS = 30


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
    session_start = previous.get("session_start_balance")
    if session_start is None and available is not None:
        session_start = available

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
            "session_pnl": session_pnl,
            "last_checked_at": utc_now(),
            "raw_response": result.raw_response,
        }
    )
    return snapshot


def wallet_is_stale(snapshot: dict[str, Any]) -> bool:
    last_checked = _parse_ts(snapshot.get("last_checked_at"))
    if last_checked is None:
        return True
    return datetime.now(last_checked.tzinfo) - last_checked > timedelta(seconds=STALE_AFTER_SECONDS)


def refresh_wallet_snapshot(*, force: bool = False, log_event: bool = False) -> dict[str, Any]:
    current = get_wallet_snapshot()
    if not force and not wallet_is_stale(current):
        return current

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
        return set_wallet_snapshot(snapshot)

    if settings.DHAN_MODE.upper() == "REAL":
        result = RealDhanClient().get_fund_limit(
            client_id=creds.client_id,
            access_token=creds.access_token,
        )
    else:
        result = MockDhanClient().get_fund_limit(client_id=creds.client_id or "MOCK_CLIENT", access_token="")

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
    return snapshot
