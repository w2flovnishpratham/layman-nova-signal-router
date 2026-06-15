from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import get_current_user
from app.config import DISABLED_OPTION_SL_PERCENT, settings
from app.domain.events import event
from app.domain.state_machine import SetupState
from app.routers.setup import current_nifty_lot_size
from app.services.credential_vault import get_dhan_credentials, get_webhook_secret, save_webhook_secret
from app.services.execution_context import bind_execution_context
from app.services.chat_event_publisher import active_trade_from_position
from app.services.state_store import get_app_state, get_daily_risk, get_engine_mode, get_open_position, get_runtime_settings, get_wallet_snapshot
from app.store.redis_session import session_store
from app.store.session_token import issue_session_token
from app.services.user_context import CurrentUser


router = APIRouter(prefix="/api/session", tags=["session"])


@router.post("/start")
async def start_session(user: CurrentUser = Depends(get_current_user)) -> dict[str, object]:
    with bind_execution_context(user):
        actual_webhook_secret = (
            (settings.STRATEGY_WEBHOOK_SECRET or "").strip()
            or get_webhook_secret()
        )
        if not actual_webhook_secret:
            actual_webhook_secret = secrets.token_urlsafe(32)
            save_webhook_secret(actual_webhook_secret)
        displayed_webhook_secret = "Managed server-side"

        state, config = _production_chat_snapshot()
        session = await session_store.create(
            webhook_secret=displayed_webhook_secret,
            user_id=user.id_str,
            state=state,
            config=config,
        )
        await _hydrate_production_session(session.id)
        token = issue_session_token(session.id)
        webhook_url = f"{settings.BACKEND_PUBLIC_BASE_URL.rstrip('/')}/api/webhook/strategy/supertrend"
        return {
            "sessionId": session.id,
            "sessionToken": token,
            "webhookSecret": displayed_webhook_secret,
            "webhookUrl": webhook_url,
            "lotSize": current_nifty_lot_size(),
        }


async def _hydrate_production_session(session_id: str) -> None:
    mode = get_engine_mode(legacy_fallback=False)
    wallet = get_wallet_snapshot()
    position = get_open_position()
    active_trade = active_trade_from_position(position, mode)
    if active_trade:
        await session_store.update_active_trade(session_id, active_trade)

    await session_store.append_event(
        session_id,
        event(
            "funds.update",
            wallet=wallet.get("available_balance"),
            availableBalance=wallet.get("available_balance"),
            utilizedAmount=wallet.get("utilized_amount"),
            sessionPnl=wallet.get("session_pnl"),
            realizedPnl=wallet.get("realized_pnl"),
            success=wallet.get("success"),
            mode=mode,
        ),
    )
    await session_store.append_event(
        session_id,
        event(
            "position.update",
            sessionPnl=wallet.get("session_pnl"),
            realizedPnl=wallet.get("realized_pnl") or wallet.get("session_pnl"),
            unrealizedPnl=active_trade.get("pnl") if active_trade else 0,
            tradesToday=int(get_daily_risk().get("entry_count") or 0),
            openPositions=[active_trade] if active_trade else [],
            mode=mode,
        ),
    )


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, object]:
    session = await session_store.get(session_id)
    if session is None or session.user_id != user.id_str:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.public_dict()


def _production_chat_snapshot() -> tuple[SetupState, dict[str, object]]:
    app_state = get_app_state()
    runtime = get_runtime_settings()
    is_live = bool(app_state.get("engine_started") and app_state.get("webhook_trading_enabled"))
    if not is_live:
        return SetupState.IDLE, {}

    state = SetupState.LIVE if bool(runtime.get("allow_entry", True)) else SetupState.PAUSED
    creds = get_dhan_credentials()
    lot_size = current_nifty_lot_size()
    max_qty = max(int(runtime.get("max_qty_per_order") or lot_size), 1)
    target_pct = float(runtime.get("option_tp_percent") or 20)
    stop_loss_pct = float(runtime.get("option_sl_percent") or DISABLED_OPTION_SL_PERCENT)
    exit_mode = str(runtime.get("option_exit_mode") or "DHAN_SUPER").upper()
    if exit_mode == "SERVER":
        chat_exit_mode = "flip_only"
    elif bool(runtime.get("option_disable_sl", True)):
        chat_exit_mode = "flip_tp"
        stop_loss_pct = DISABLED_OPTION_SL_PERCENT
    else:
        chat_exit_mode = "custom"

    return state, {
        "engineMode": get_engine_mode(legacy_fallback=False),
        "strategy": "supertrend",
        "broker": {
            "clientId": creds.client_id if creds else "",
            "status": "verified" if creds else "missing",
        },
        "risk": {
            "maxTrades": int(runtime.get("max_trades_per_day") or 0) or None,
            "maxLoss": float(runtime.get("max_daily_loss") or 0) or None,
            "lots": max(1, (max_qty + lot_size - 1) // lot_size),
            "side": str(runtime.get("allowed_option_side") or "BOTH").upper(),
        },
        "exits": {
            "mode": chat_exit_mode,
            "targetProfit": None if chat_exit_mode == "flip_only" else max(100, int(target_pct * 100)),
            "targetPct": target_pct,
            "stopLossPct": stop_loss_pct,
        },
        "live": {"confirmed": get_engine_mode() == "live"},
    }
