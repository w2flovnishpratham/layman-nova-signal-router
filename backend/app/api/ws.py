from __future__ import annotations

import asyncio
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.auth.dependencies import get_current_websocket_user
from app.config import DISABLED_OPTION_SL_PERCENT, settings
from app.domain.events import event
from app.domain.state_machine import SetupState, StateTransitionError, validate_command
from app.routers.control import panic_exit
from app.routers.engine import StartEngineRequest, start_engine, stop_engine
from app.routers.setup import EngineModeRequest, configure_engine_mode, current_nifty_lot_size, validate_dhan_credentials
from app.services.credential_vault import save_dhan_credentials
from app.services.chat_event_publisher import active_trade_from_position
from app.services.execution_context import bind_user_execution_context
from app.services.state_store import get_engine_mode, get_open_position, get_wallet_snapshot, set_open_position, update_runtime_settings, utc_now
from app.services import strategy_fanout
from app.services.wallet_service import refresh_wallet_snapshot
from app.store.redis_session import session_store
from app.store.session_token import SessionTokenError, verify_session_token
from app.services.user_context import CurrentUser, dev_user


router = APIRouter(tags=["websocket"])


@router.websocket("/ws/session/{session_id}")
async def session_websocket(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(default=""),
    user: CurrentUser = Depends(get_current_websocket_user),
) -> None:
    session = await session_store.get(session_id)
    if session is None or session.user_id != user.id_str:
        await websocket.close(code=4404, reason="Session not found")
        return

    try:
        verify_session_token(token, session_id)
    except SessionTokenError as exc:
        await websocket.close(code=4401, reason=str(exc))
        return

    await websocket.accept()
    for item in session.events[-200:]:
        await websocket.send_json(item.model_dump(mode="json"))

    queue = await session_store.subscribe(session_id)
    sender = asyncio.create_task(_send_events(websocket, queue))
    receiver = asyncio.create_task(_receive_commands(websocket, session_id, user))

    done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        if not task.cancelled():
            task.result()
    await session_store.unsubscribe(session_id, queue)


async def _send_events(websocket: WebSocket, queue: asyncio.Queue[Any]) -> None:
    while True:
        item = await queue.get()
        await websocket.send_json(item.model_dump(mode="json"))


async def _receive_commands(websocket: WebSocket, session_id: str, user: CurrentUser) -> None:
    while True:
        try:
            payload = await websocket.receive_json()
        except WebSocketDisconnect:
            return

        command_type = payload.get("type")
        data = payload.get("data") or {}
        if not isinstance(command_type, str) or not isinstance(data, dict):
            await _error(session_id, "Client command must include type and data.")
            continue

        session = await session_store.get(session_id)
        if session is None:
            return

        with bind_user_execution_context(user):
            try:
                next_state, patch = validate_command(session.state, command_type, data)
                await _apply_production_command(command_type, data, user=user, session=session)
            except StateTransitionError as exc:
                await _error(session_id, str(exc))
                continue
            except HTTPException as exc:
                await _error(session_id, _http_error_message(exc))
                continue
            except Exception as exc:
                await _error(session_id, str(exc))
                continue

            await session_store.update_state(session_id, next_state, patch)
            if command_type == "setup.mode":
                await session_store.append_event(
                    session_id,
                    event(
                        "mode.update",
                        engineMode=get_engine_mode(legacy_fallback=False),
                        paperBalance=get_wallet_snapshot().get("available_balance") if get_engine_mode() == "paper" else None,
                    ),
                )
                if str(data.get("engineMode") or "").lower() == "live":
                    await session_store.append_event(
                        session_id,
                        event(
                            "setup.info",
                            sessionId=session.id,
                            webhookUrl=f"{settings.BACKEND_PUBLIC_BASE_URL.rstrip('/')}/api/webhook/strategy/supertrend",
                            webhookSecret=session.webhook_secret,
                        ),
                    )
            elif command_type == "setup.broker_creds":
                wallet = await asyncio.to_thread(get_wallet_snapshot)
                await session_store.append_event(
                    session_id,
                    event(
                        "funds.update",
                        wallet=_optional_number(wallet.get("available_balance")),
                        availableBalance=_optional_number(wallet.get("available_balance")),
                        utilizedAmount=_optional_number(wallet.get("utilized_amount")),
                        success=bool(wallet.get("success")),
                        message=wallet.get("message"),
                    ),
                )
            elif command_type == "setup.confirm_live":
                await session_store.append_event(
                    session_id,
                    event("system.event", kind="listening", label="Listening for TradingView signals"),
                )
            elif command_type == "session.apply_sr_suggestion":
                position = await asyncio.to_thread(get_open_position)
                active_trade = active_trade_from_position(position, get_engine_mode())
                await session_store.update_active_trade(session_id, active_trade)
                if active_trade:
                    await session_store.append_event(session_id, event("tick.pnl", **active_trade))
                await session_store.append_event(
                    session_id,
                    event(
                        "system.event",
                        kind="sr_suggestion_applied",
                        label="Suggested SL/TP applied",
                        message="Suggested SL/TP applied to the paper position.",
                    ),
                )
            elif command_type == "session.kill":
                await session_store.update_active_trade(session_id, None)
                wallet = await asyncio.to_thread(get_wallet_snapshot)
                await session_store.append_event(
                    session_id,
                    event(
                        "session.eod",
                        reason="manual_kill",
                        grossPnl=_number(wallet.get("session_pnl")),
                        realizedPnl=_number(wallet.get("session_pnl")),
                        unrealizedPnl=0,
                    ),
                )


async def _apply_production_command(
    command_type: str,
    data: dict[str, Any],
    *,
    user: CurrentUser | None = None,
    session=None,
) -> None:
    user = user or dev_user()
    session_config = (
        session.config
        if session is not None and isinstance(session.config, dict)
        else {}
    )
    if command_type == "setup.mode":
        raw_mode = str(data.get("engineMode") or "").strip().lower()
        if raw_mode not in {"paper", "live"}:
            raise ValueError("engineMode must be either 'paper' or 'live'.")
        await asyncio.to_thread(
            configure_engine_mode,
            EngineModeRequest(
                engine_mode=cast(Literal["paper", "live"], raw_mode),
                paper_starting_balance=float(data.get("paperStartingBalance") or 100000),
            ),
        )
        return

    if command_type == "setup.broker_creds":
        client_id = str(data.get("clientId", "")).strip()
        access_token = str(data.get("accessToken", "")).strip()
        ok, message, _funds, details = await asyncio.to_thread(
            validate_dhan_credentials,
            client_id,
            access_token,
        )
        if not ok:
            raise ValueError(f"{message} {details}".strip())
        await asyncio.to_thread(save_dhan_credentials, client_id, access_token)
        await asyncio.to_thread(refresh_wallet_snapshot, force=True, log_event=True)
        return

    if command_type == "setup.risk":
        lots = max(int(data.get("lots") or 1), 1)
        lot_size = await asyncio.to_thread(current_nifty_lot_size)
        await asyncio.to_thread(
            update_runtime_settings,
            max_qty_per_order=lots * lot_size,
            allowed_option_side=str(data.get("side") or "BOTH").upper(),
            max_trades_per_day=int(data.get("maxTrades") or 0),
            max_daily_loss=float(data.get("maxLoss") or 0),
        )
        return

    if command_type == "setup.exits":
        mode = str(data.get("mode") or "flip_only")
        target_pct = float(data.get("targetPct") or 5)
        stop_loss_pct = float(data.get("stopLossPct") or DISABLED_OPTION_SL_PERCENT)
        changes: dict[str, Any] = {
            "option_tp_percent": target_pct,
            "option_sl_percent": stop_loss_pct,
            "eod_squareoff_enabled": True,
        }
        if mode == "flip_only":
            changes.update(
                {
                    "option_exit_mode": "SERVER",
                    "server_side_exit_enabled": False,
                    "option_disable_sl": True,
                    "option_sl_percent": DISABLED_OPTION_SL_PERCENT,
                }
            )
        elif mode == "flip_tp":
            changes.update(
                {
                    "option_exit_mode": "DHAN_SUPER",
                    "server_side_exit_enabled": True,
                    "option_disable_sl": True,
                    "option_sl_percent": DISABLED_OPTION_SL_PERCENT,
                }
            )
        else:
            changes.update(
                {
                    "option_exit_mode": "DHAN_SUPER",
                    "server_side_exit_enabled": True,
                    "option_disable_sl": False,
                }
            )
        await asyncio.to_thread(update_runtime_settings, **changes)
        return

    if command_type == "setup.confirm_live":
        mode = get_engine_mode(legacy_fallback=False)
        if mode not in {"paper", "live"}:
            raise ValueError("Engine mode is not configured.")
        engine_mode = cast(Literal["paper", "live"], mode)
        if engine_mode == "live" and not user.is_dev:
            if (
                settings.DHAN_MODE.upper() != "REAL"
                or not settings.ENABLE_LIVE_ORDERS
                or settings.DHAN_READ_ONLY_REAL_DATA
            ):
                raise ValueError(
                    "Live Dhan orders are not armed by the server safety gates."
                )
            if not settings.EXECUTION_NODE_ROUTING_ENABLED:
                raise ValueError(
                    "Live routing is not armed on the server yet."
                )
            egress = await asyncio.to_thread(
                strategy_fanout.get_user_egress,
                user.id,
            )
            if egress is None or not egress.get("proxy_url"):
                raise ValueError(
                    "No verified Dhan static-IP execution node is assigned to this account."
                )
            if not egress.get("verified"):
                verification = await asyncio.to_thread(
                    strategy_fanout.verify_user_egress,
                    user.id,
                )
                if not verification.get("ok"):
                    detail = verification.get("error") or "verification failed"
                    raise ValueError(
                        "The assigned Dhan execution node has not passed its IP verification "
                        f"in the last 24 hours: {detail}"
                    )
        await asyncio.to_thread(
            start_engine,
            StartEngineRequest(
                engine_mode=engine_mode,
                confirm_live_orders=engine_mode == "live",
            ),
        )
        risk_data = session_config.get("risk")
        risk: dict[str, Any] = risk_data if isinstance(risk_data, dict) else {}
        strategy = str(session_config.get("strategy") or "supertrend")
        execution_mode = "real_orders" if engine_mode == "live" else "paper_live_data"
        if not user.is_dev:
            await asyncio.to_thread(
                strategy_fanout.subscribe_user,
                user.id,
                strategy,
                lots=max(int(risk.get("lots") or 1), 1),
                execution_mode=execution_mode,
            )
        return

    if command_type == "session.pause":
        await asyncio.to_thread(update_runtime_settings, allow_entry=False)
        if not user.is_dev:
            await asyncio.to_thread(
                strategy_fanout.set_subscription_active,
                user.id,
                str(session_config.get("strategy") or "supertrend"),
                False,
            )
        return

    if command_type == "session.resume":
        await asyncio.to_thread(update_runtime_settings, allow_entry=True)
        if not user.is_dev:
            await asyncio.to_thread(
                strategy_fanout.set_subscription_active,
                user.id,
                str(session_config.get("strategy") or "supertrend"),
                True,
            )
        return

    if command_type == "session.exit_open":
        position = await asyncio.to_thread(get_open_position)
        if not position.get("has_open_position"):
            raise ValueError("No tracked open position exists to exit.")
        result = await asyncio.to_thread(panic_exit)
        if isinstance(result, dict) and result.get("ok") is False:
            raise ValueError(str(result.get("message") or "Tracked position exit failed."))
        execution = result.get("execution_result") if isinstance(result, dict) else None
        if isinstance(execution, dict) and execution.get("success") is False:
            raise ValueError(execution.get("reason") or execution.get("error") or "Tracked position exit failed.")
        return

    if command_type == "session.apply_sr_suggestion":
        await asyncio.to_thread(_apply_sr_suggestion)
        return

    if command_type == "session.patch_risk":
        changes: dict[str, Any] = {}
        if data.get("side") is not None:
            changes["allowed_option_side"] = str(data["side"]).upper()
        if data.get("maxLoss") is not None:
            changes["max_daily_loss"] = float(data["maxLoss"])
        if changes:
            await asyncio.to_thread(update_runtime_settings, **changes)
        return

    if command_type == "session.kill":
        position = await asyncio.to_thread(get_open_position)
        if position.get("has_open_position"):
            result = await asyncio.to_thread(panic_exit)
            execution = result.get("execution_result") if isinstance(result, dict) else None
            if isinstance(execution, dict) and execution.get("success") is False:
                raise ValueError(execution.get("reason") or execution.get("error") or "Tracked position exit failed.")
        await asyncio.to_thread(stop_engine)
        if not user.is_dev:
            await asyncio.to_thread(
                strategy_fanout.set_subscription_active,
                user.id,
                str(session_config.get("strategy") or "supertrend"),
                False,
            )


def _suggestion_level(suggestion: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _optional_number(suggestion.get(key))
        if value is not None and value > 0:
            return value
    return None


def _apply_sr_suggestion() -> dict[str, Any]:
    if get_engine_mode() != "paper":
        raise ValueError("Suggested S/R SL/TP apply is enabled for paper testing only.")

    position = get_open_position()
    if not position.get("has_open_position"):
        raise ValueError("No tracked open position exists.")

    suggestion = position.get("sr_suggestion")
    if not isinstance(suggestion, dict) or not suggestion.get("available"):
        raise ValueError("No valid TradingView S/R suggestion exists for the active position.")

    stop_loss_price = _suggestion_level(suggestion, "stopLossPrice", "stop_loss_price", "sl")
    target_price = _suggestion_level(suggestion, "targetPrice", "target_price", "tp")
    entry_price = _optional_number(position.get("entry_price"))
    if stop_loss_price is None or target_price is None:
        raise ValueError("Suggested S/R SL/TP prices are incomplete.")
    if entry_price and (stop_loss_price >= entry_price or target_price <= entry_price):
        raise ValueError("Suggested S/R SL/TP prices are invalid for the active option entry.")

    accepted_at = utc_now()
    updated = dict(position)
    accepted = dict(suggestion)
    accepted["accepted"] = True
    accepted["acceptedAt"] = accepted_at
    updated["sr_suggestion"] = accepted
    updated["active_exit_levels"] = {
        "source": "sr_suggestion",
        "stopLossPrice": stop_loss_price,
        "targetPrice": target_price,
        "acceptedAt": accepted_at,
    }
    live_pnl = dict(updated.get("live_pnl") or {})
    live_pnl.update(
        {
            "sl_price": stop_loss_price,
            "tp_price": target_price,
            "exit_management": "SERVER",
            "message": "Paper S/R suggested SL/TP is armed.",
            "last_checked_at": accepted_at,
        }
    )
    updated["live_pnl"] = live_pnl
    return set_open_position(updated)


async def _error(session_id: str, message: str) -> None:
    await session_store.append_event(session_id, event("session.error", message=message))


def _http_error_message(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(detail)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
