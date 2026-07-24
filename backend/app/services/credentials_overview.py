"""Owner-scoped, read-only credential facts for the Credentials page.

Every value comes from an existing authority: the credential vault (already
masked), the runtime broker metadata, and the egress record. Nothing here can
return a stored secret - the vault's status projection never decrypts a token,
and this module never reads the encrypted columns.

Dhan is the only broker with an execution adapter, so it is the only broker
reported. A second broker card would be a claim the router cannot honour.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.config import settings
from app.services import user_credential_vault as vault


def _egress(user_id: uuid.UUID) -> dict[str, Any]:
    """Static-IP status, only where an authoritative record exists."""
    try:
        from app.services.strategy_fanout import get_user_egress

        record = get_user_egress(user_id)
    except Exception:  # noqa: BLE001 - a lookup failure is not "not configured".
        return {"available": False, "status": "unknown", "reason": "Egress status is unavailable."}
    if not record:
        return {"available": False, "status": "not_configured", "reason": "No static egress IP is assigned."}
    # Only these three keys are copied. The egress record also carries a
    # decrypted proxy_url, which must never reach the browser.
    return {
        "available": True,
        "status": "verified" if record.get("verified") else "unverified",
        "verified_at": record.get("last_verified_at"),
        "ip": record.get("public_ip"),
    }


def build_credentials_overview(user_id: uuid.UUID) -> dict[str, Any]:
    status = vault.user_credential_status(user_id)
    connected = bool(status.get("has_dhan_client_id") and status.get("has_dhan_access_token"))

    live_orders_enabled = bool(settings.ENABLE_LIVE_ORDERS)
    dhan_mode = str(settings.DHAN_MODE or "").upper()

    live_blockers: list[str] = []
    if not live_orders_enabled:
        live_blockers.append("Live orders are disabled on this deployment.")
    if dhan_mode != "REAL":
        live_blockers.append(f"Broker mode is {dhan_mode or 'unset'}, not REAL.")
    if not connected:
        live_blockers.append("Dhan credentials are not fully configured.")

    return {
        "ok": True,
        "broker": {
            "key": "dhan",
            "name": "Dhan",
            "connected": connected,
            # Masked by the vault; the full value is never serialised.
            "client_id_masked": status.get("dhan_client_id_masked"),
            "has_client_id": bool(status.get("has_dhan_client_id")),
            "has_access_token": bool(status.get("has_dhan_access_token")),
            "has_api_secret": bool(status.get("has_dhan_api_secret")),
            "has_webhook_secret": bool(status.get("has_webhook_secret")),
            # The vault stores when the token was saved. It does not store an
            # expiry, so none is reported rather than guessed.
            "token_saved_at": status.get("dhan_token_saved_at"),
            "token_expires_at": None,
            "expiry_known": False,
            "last_updated_at": status.get("updated_at"),
        },
        "static_ip": _egress(user_id),
        "eligibility": {
            "paper": connected,
            "paper_reason": None if connected else "Connect Dhan to route Paper orders against live prices.",
            "live": connected and live_orders_enabled and dhan_mode == "REAL",
            "live_blockers": live_blockers,
        },
        "mode": {"dhan_mode": dhan_mode, "live_orders_enabled": live_orders_enabled},
        "supported_brokers": ["dhan"],
    }
