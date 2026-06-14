from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.config import csv_setting, settings
from app.services.executor_signing import canonical_json_bytes
from app.services.live_pilot_service import strategy_has_active_approval
from app.services.signal_fanout_service import queue_strategy_signal_fanout
from app.services.strategy_signal_service import DuplicateStrategySignal, StrategySignalError, accept_strategy_signal


router = APIRouter()


class TradingViewRelayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(..., min_length=3, max_length=200)
    action: Literal["BUY", "SELL", "EXIT", "CLOSE"]
    symbol: str = Field(..., min_length=1, max_length=40)
    option_type: Literal["CE", "PE"]
    strike: float = Field(..., gt=0)
    expiry: str = Field(..., min_length=8, max_length=40)
    timeframe: str = Field(..., min_length=1, max_length=20)
    price: float = Field(..., gt=0)
    source: str = Field(default="tradingview", min_length=2, max_length=80)


@router.post("/relay/tradingview/{strategy_code}")
def tradingview_relay(
    strategy_code: str,
    body: TradingViewRelayRequest,
    authorization: str | None = Header(default=None),
    x_nova_relay_token: str | None = Header(default=None),
) -> dict[str, Any]:
    if not settings.RELAY_ENABLED:
        raise HTTPException(status_code=404, detail="TradingView relay is disabled.")
    secret = settings.RELAY_SHARED_SECRET.strip()
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="TradingView relay is not safely configured.")
    supplied = x_nova_relay_token or _bearer_token(authorization)
    if not supplied or not hmac.compare_digest(supplied, secret):
        raise HTTPException(status_code=401, detail="Invalid relay token.")
    normalized_code = strategy_code.strip().upper()
    if normalized_code not in csv_setting(settings.RELAY_ALLOWED_STRATEGIES):
        raise HTTPException(status_code=403, detail="Strategy is not allowed through this relay.")
    if normalized_code not in csv_setting(settings.LIVE_PILOT_ALLOWED_STRATEGIES):
        raise HTTPException(status_code=403, detail="Strategy is not enabled for the controlled live pilot.")
    if not strategy_has_active_approval(normalized_code):
        raise HTTPException(status_code=403, detail="No active admin-approved pilot user exists for this strategy.")
    timestamp = int(time.time())
    normalized = {"strategy_code": normalized_code, **body.model_dump(), "source": "tradingview-relay"}
    relay_body = canonical_json_bytes(normalized)
    relay_signature = hmac.new(
        secret.encode("utf-8"),
        str(timestamp).encode() + b"." + relay_body,
        hashlib.sha256,
    ).hexdigest()
    normalized["_nova_relay"] = {
        "verified": True,
        "timestamp": timestamp,
        "payload_sha256": hashlib.sha256(relay_body).hexdigest(),
        "signature": relay_signature,
    }
    try:
        signal = accept_strategy_signal(normalized)
    except (StrategySignalError, DuplicateStrategySignal) as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    queue_job = queue_strategy_signal_fanout(int(signal.id))
    if settings.PAPER_QUEUE_INLINE_LOCAL:
        from app.services.worker_runtime import drain_worker_jobs

        drain_worker_jobs()
    return {
        "status": "accepted",
        "strategy_signal_id": signal.id,
        "queue_job_id": queue_job.id,
    }


def _bearer_token(value: str | None) -> str | None:
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None
