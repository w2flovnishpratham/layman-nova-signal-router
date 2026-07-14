"""Verification-only FastAPI surface for finalized synthetic candles."""
from __future__ import annotations

from fastapi import Depends

from app.auth.dependencies import get_execution_scoped_user
from app.services import hosted_strategy_runtime
from scripts.private_webhook_soak_app import app


def feed_candles(history: list[dict]) -> dict:
    return {"ok": True, **hosted_strategy_runtime.enqueue_finalized_candle(history)}


app.add_api_route("/__soak__/hosted-candles", feed_candles, methods=["POST"], dependencies=[Depends(get_execution_scoped_user)], include_in_schema=False)
