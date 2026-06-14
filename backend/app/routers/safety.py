from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlmodel import select

from app.auth.db import session_scope
from app.auth.models import WebhookSignalReceipt
from app.auth.security import auth_enabled, require_user_if_auth_enabled
from app.config import settings
from app.services.credential_vault import dhan_metadata, webhook_secret_metadata
from app.services.risk_manager import _market_is_open
from app.services.state_store import get_engine_mode, get_runtime_settings
from app.services.trading_security import request_principal_id
from app.services.user_connections import connection_status
from app.services.worker_policy import configured_worker_role


router = APIRouter(dependencies=[Depends(require_user_if_auth_enabled)])


@router.get("/safety/status")
def safety_status(request: Request) -> dict[str, Any]:
    user = require_user_if_auth_enabled(request)
    user_id = user.id if user else None
    connection = connection_status(user_id)
    broker = dhan_metadata()
    webhook = webhook_secret_metadata()
    runtime = get_runtime_settings()
    worker_role = configured_worker_role()
    trading_workers_enabled = worker_role == "trading-worker" and settings.ENABLE_TRADING_WORKERS
    paper_workers_enabled = settings.ENABLE_PAPER_WORKERS
    egress_node = connection.get("egressNode") or {}
    egress_ready = bool(
        settings.EXECUTION_NODE_ROUTING_ENABLED
        and connection.get("egressAssigned")
        and egress_node.get("status") == "ready"
    )
    replay_protection = bool(
        settings.WEBHOOK_HMAC_REQUIRED
        and settings.WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS > 0
        and settings.REQUIRE_SIGNAL_ID_LIVE
    )

    # These later live-routing capabilities do not exist yet, so report them
    # as unavailable instead of inferring readiness from configuration.
    signing_relay_configured = False
    authenticated_live_workers_ready = False
    market_hours_valid = not settings.REQUIRE_MARKET_HOURS or _market_is_open()

    reasons = _live_blockers(
        broker_connected=bool(broker["connected"]),
        replay_protection=replay_protection,
        egress_ready=egress_ready,
        market_hours_valid=market_hours_valid,
        authenticated_runtime=auth_enabled(),
    )
    latest_webhook = _latest_webhook_receipt(request_principal_id(user_id))
    dhan_account = connection.get("dhanAccount") or {}

    return {
        "mode": get_engine_mode(legacy_fallback=False),
        "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
        "auth_required": settings.AUTH_REQUIRED,
        "authenticated": bool(user) if auth_enabled() else True,
        "webhook_hmac_required": settings.WEBHOOK_HMAC_REQUIRED,
        "webhook_replay_protection": replay_protection,
        "legacy_unsigned_webhooks_disabled": not settings.WEBHOOK_ALLOW_LEGACY_AUTH_LOCAL,
        "worker_role": worker_role,
        "paper_workers_enabled": paper_workers_enabled,
        "trading_workers_enabled": trading_workers_enabled,
        "trading_worker_policy_safe": worker_role == "web" and not trading_workers_enabled,
        "authenticated_live_workers_ready": authenticated_live_workers_ready,
        "executor_routing_enabled": settings.EXECUTION_NODE_ROUTING_ENABLED,
        "unique_egress_required": settings.UNIQUE_EGRESS_PER_USER_REQUIRED,
        "egress_assigned": bool(connection.get("egressAssigned")),
        "executor_egress_verified": egress_ready,
        "signing_relay_configured": signing_relay_configured,
        "market_hours_valid": market_hours_valid,
        "single_operator_live_allowed": not reasons,
        "public_live_launch_allowed": False,
        "reasons_live_blocked": reasons,
        "broker": {
            "connected": bool(broker["connected"]),
            "client_id_masked": broker["client_id_masked"],
            "access_token_present": bool(broker["access_token_present"]),
            "token_saved_at": broker["token_saved_at"],
            "token_age_minutes": broker["token_age_minutes"],
            "token_estimated_expiry_at": broker["token_estimated_expiry_at"],
            "token_expired": broker["token_expired"],
            "token_warn": broker["token_warn"],
            "last_validated_at": dhan_account.get("lastValidatedAt"),
            "status": dhan_account.get("status"),
        },
        "webhook": {
            "url": f"{settings.BACKEND_PUBLIC_BASE_URL.rstrip('/')}/webhook/tradingview",
            "secret_set": bool(webhook["set"]),
            "secret_masked": webhook["masked"],
            **latest_webhook,
        },
        "runtime": {
            "emergency_stop": bool(runtime.get("emergency_stop")),
            "global_kill_switch": bool(runtime.get("global_kill_switch")),
            "allow_entry": bool(runtime.get("allow_entry", True)),
            "allow_exit": bool(runtime.get("allow_exit", True)),
        },
    }


def _live_blockers(
    *,
    broker_connected: bool,
    replay_protection: bool,
    egress_ready: bool,
    market_hours_valid: bool,
    authenticated_runtime: bool,
) -> list[str]:
    reasons: list[str] = []
    if not settings.ENABLE_LIVE_ORDERS:
        reasons.append("Live orders are disabled by system policy.")
    if not broker_connected:
        reasons.append("Dhan broker credentials are not connected and verified.")
    if not settings.WEBHOOK_HMAC_REQUIRED:
        reasons.append("Webhook HMAC authentication is not required.")
    if not replay_protection:
        reasons.append("Webhook timestamp and signal replay protection is incomplete.")
    if settings.WEBHOOK_ALLOW_LEGACY_AUTH_LOCAL:
        reasons.append("Legacy unsigned webhook compatibility is enabled.")
    reasons.append("Webhook signing relay is not configured.")
    if settings.UNIQUE_EGRESS_PER_USER_REQUIRED and not egress_ready:
        reasons.append("Executor routing and unique egress IP are not verified.")
    if authenticated_runtime:
        reasons.append("Authenticated multi-user trading workers are not production-ready.")
    if not market_hours_valid:
        reasons.append("The market-hours safety check is currently closed.")
    return reasons


def _latest_webhook_receipt(user_id: str) -> dict[str, str | None]:
    with session_scope() as session:
        receipt = session.exec(
            select(WebhookSignalReceipt)
            .where(WebhookSignalReceipt.user_id == user_id)
            .order_by(WebhookSignalReceipt.last_seen_at.desc())
        ).first()

    if receipt is None:
        return {
            "last_received_at": None,
            "last_status": None,
            "last_rejection_category": None,
        }

    rejection_category = (
        receipt.status
        if receipt.status in {"blocked", "duplicate", "suspicious_duplicate", "rejected"}
        else None
    )
    return {
        "last_received_at": receipt.last_seen_at.isoformat(),
        "last_status": receipt.status,
        "last_rejection_category": rejection_category,
    }
