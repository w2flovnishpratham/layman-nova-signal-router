"""Signals read endpoint - owner-scoped list of inbound webhook signal events."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user
from app.services import risk_overview, signals_feed, webhooks_overview
from app.services.user_context import CurrentUser

router = APIRouter()


@router.get("/signals")
def list_signals(
    limit: int = Query(signals_feed.DEFAULT_LIMIT, ge=1, le=signals_feed.MAX_LIMIT),
    cursor: str | None = Query(None),
    status: str = Query("all"),
    since: datetime | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """Page the signed-in owner's signal events, newest first.

    Owner scoping comes from the session only - there is deliberately no
    ``user_id`` parameter, so one owner can never request another's rows.
    """
    result = signals_feed.list_signals(
        user.id, limit=limit, cursor=cursor, status=status, since=since
    )
    if not result.get("ok", True):
        return JSONResponse(status_code=400, content=result)
    return result


@router.get("/webhooks/overview")
def webhooks_overview_endpoint(user: CurrentUser = Depends(get_current_user)):
    """Owner-scoped webhook endpoints, masked secret metadata and delivery stats.

    The raw webhook secret is never returned - only {set, masked, source}.
    """
    return webhooks_overview.build_webhooks_overview(user.id)


@router.get("/risk/overview")
def risk_overview_endpoint(user: CurrentUser = Depends(get_current_user)):
    """Owner-scoped strategy fan-out risk limits and today's IST usage.

    Limits of 0 mean "no limit"; utilisation is then undefined rather than 0%.
    """
    return risk_overview.build_risk_overview(user.id)
