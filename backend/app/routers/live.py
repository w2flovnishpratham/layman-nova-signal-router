"""Per-user live-mode API.

POST /api/live/start    -> start a live run scoped to the user (gated by safety)
POST /api/live/stop     -> stop only the current user's run
GET  /api/live/status   -> current user's live status
GET  /api/live/logs     -> current user's logs
GET  /api/live/readiness-> safety checklist for the current user (no side effects)

Real orders are only ever placed when every safety check passes AND the global
env flags are set. Users can only see and control their own runs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.config import settings
from app.services import live_engine
from app.services.user_context import CurrentUser

router = APIRouter(prefix="/api/live", tags=["Live"])


class LiveStartPayload(BaseModel):
    strategy_name: str | None = Field(default=None, max_length=120)
    symbol: str = Field(default="NIFTY", max_length=40)
    quantity: int = Field(default=1, ge=1, le=100000)
    risk_config: dict = Field(default_factory=dict)
    execution_mode: str = Field(default="signal_only")
    uses_webhook: bool = False


class LiveStopPayload(BaseModel):
    run_id: str


@router.get("/readiness")
def live_readiness(
    execution_mode: str = "signal_only",
    uses_webhook: bool = False,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    return live_engine.evaluate_live_readiness(user, execution_mode, uses_webhook=uses_webhook)


@router.post("/start")
def live_start(payload: LiveStartPayload, user: CurrentUser = Depends(get_current_user)) -> dict:
    if payload.execution_mode not in live_engine.EXECUTION_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid execution_mode '{payload.execution_mode}'.")
    config = {
        "symbol": payload.symbol,
        "quantity": payload.quantity,
        "risk_config": payload.risk_config,
    }
    result = live_engine.start_run(
        user,
        strategy_name=payload.strategy_name,
        execution_mode=payload.execution_mode,
        config=config,
        uses_webhook=payload.uses_webhook,
    )
    if not result["ok"]:
        raise HTTPException(
            status_code=412,  # Precondition Failed — safety checks not met
            detail={"message": "Live run blocked by safety checks.", "readiness": result["readiness"]},
        )
    return result


@router.post("/stop")
def live_stop(payload: LiveStopPayload, user: CurrentUser = Depends(get_current_user)) -> dict:
    result = live_engine.stop_run(user, payload.run_id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result.get("error", "Run not found."))
    return result


@router.get("/status")
def live_status(user: CurrentUser = Depends(get_current_user)) -> dict:
    return live_engine.get_user_status(user)


@router.get("/logs")
def live_logs(run_id: str | None = None, limit: int = 200, user: CurrentUser = Depends(get_current_user)) -> dict:
    return {"logs": live_engine.get_user_logs(user, run_id=run_id, limit=limit)}
