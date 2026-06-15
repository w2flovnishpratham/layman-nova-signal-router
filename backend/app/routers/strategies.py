"""Strategy subscriptions, shared TradingView webhook, and egress assignment."""
from __future__ import annotations

import hmac
import threading
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, require_admin
from app.config import settings
from app.services import live_engine, strategy_fanout
from app.services.signal_parser import PayloadParseError, parse_webhook_payload
from app.services.signal_validator import validate_signal
from app.services.user_context import CurrentUser


router = APIRouter(tags=["Strategies"])
_WEBHOOK_REQUESTS: dict[str, list[float]] = {}
_WEBHOOK_RATE_LOCK = threading.RLock()


def _webhook_rate_limited(client_host: str) -> bool:
    limit = max(int(settings.WEBHOOK_RATE_LIMIT_PER_MINUTE), 1)
    now = time.monotonic()
    cutoff = now - 60
    with _WEBHOOK_RATE_LOCK:
        recent = [
            timestamp
            for timestamp in _WEBHOOK_REQUESTS.get(client_host, [])
            if timestamp >= cutoff
        ]
        if len(recent) >= limit:
            _WEBHOOK_REQUESTS[client_host] = recent
            return True
        recent.append(now)
        _WEBHOOK_REQUESTS[client_host] = recent
        return False


class SubscribePayload(BaseModel):
    strategy_name: str = Field(min_length=1, max_length=120)
    lots: int = Field(default=1, ge=1, le=1000)
    execution_mode: str = Field(default="signal_only")


@router.get("/api/strategies/subscriptions")
def my_subscriptions(user: CurrentUser = Depends(get_current_user)) -> dict:
    return {"subscriptions": strategy_fanout.list_user_subscriptions(user.id)}


@router.post("/api/strategies/subscribe")
def subscribe(
    payload: SubscribePayload,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    if payload.execution_mode not in live_engine.EXECUTION_MODES:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid execution_mode"},
        )
    subscription = strategy_fanout.subscribe_user(
        user.id,
        payload.strategy_name,
        lots=payload.lots,
        execution_mode=payload.execution_mode,
    )
    return {"ok": True, "subscription": subscription}


@router.delete("/api/strategies/subscribe")
def unsubscribe(
    strategy_name: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    return {
        "ok": strategy_fanout.unsubscribe_user(user.id, strategy_name),
    }


@router.get("/api/strategies/egress/status")
def egress_status(user: CurrentUser = Depends(get_current_user)) -> dict:
    return strategy_fanout.user_egress_status(user.id)


@router.post("/api/webhook/strategy/{strategy_name}")
async def strategy_webhook(
    strategy_name: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    secret = (settings.STRATEGY_WEBHOOK_SECRET or "").strip()
    if not secret:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "Strategy webhook secret not configured."},
        )
    client_host = request.client.host if request.client else "unknown"
    if _webhook_rate_limited(client_host):
        return JSONResponse(
            status_code=429,
            content={"ok": False, "error": "Webhook rate limit exceeded."},
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Invalid JSON."},
        )
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Payload must be an object."},
        )
    if not hmac.compare_digest(str(body.get("secret") or ""), secret):
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "Invalid secret."},
        )

    try:
        signal = parse_webhook_payload(body)
    except PayloadParseError as exc:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "error": str(exc)},
        )

    path_strategy = strategy_fanout.canonical_strategy_name(strategy_name)
    payload_strategy = strategy_fanout.canonical_strategy_name(
        signal.strategy_code
    )
    if path_strategy != payload_strategy:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "error": "Strategy path does not match payload."},
        )

    valid, error = validate_signal(signal)
    if not valid:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "error": error or "Invalid signal."},
        )
    if not strategy_fanout.claim_strategy_signal(path_strategy, signal.signal_id):
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": "Duplicate signal.",
                "signal_id": signal.signal_id,
            },
        )

    subscriber_count = strategy_fanout.active_subscriber_count(path_strategy)
    background_tasks.add_task(
        strategy_fanout.process_signal,
        path_strategy,
        signal,
    )
    return JSONResponse(
        status_code=202,
        content={
            "ok": True,
            "signal_id": signal.signal_id,
            "strategy_name": path_strategy,
            "subscriber_count": subscriber_count,
            "status": "accepted",
        },
    )


class EgressPayload(BaseModel):
    user_id: str
    public_ip: str = Field(min_length=3, max_length=64)
    proxy_url: str = Field(min_length=3, max_length=512)
    active: bool = True


@router.post("/api/admin/egress")
def assign_egress(
    payload: EgressPayload,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    try:
        user_id = uuid.UUID(payload.user_id)
        status = strategy_fanout.set_user_egress(
            user_id,
            public_ip=payload.public_ip,
            proxy_url=payload.proxy_url,
            active=payload.active,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": str(exc)},
        )
    return {"ok": True, "egress": status}


@router.post("/api/admin/egress/{user_id}/verify")
def verify_egress(
    user_id: uuid.UUID,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    result = strategy_fanout.verify_user_egress(user_id)
    return {"ok": bool(result.get("ok")), "egress": result}
