from __future__ import annotations

import asyncio
from typing import Any

from app.domain.events import event
from app.schemas.signal import NormalizedSignal
from app.services.paper_portfolio import get_paper_portfolio
from app.store.redis_session import session_store
from app.services.state_store import get_engine_mode, get_wallet_snapshot


_MAIN_LOOP: asyncio.AbstractEventLoop | None = None


def bind_chat_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def clear_chat_event_loop() -> None:
    global _MAIN_LOOP
    _MAIN_LOOP = None


def publish_chat_result_from_sync(payload: NormalizedSignal, execution_result: dict[str, Any]) -> None:
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        return

    coroutine = publish_chat_result(payload, execution_result)
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is loop:
        loop.create_task(coroutine)
    else:
        asyncio.run_coroutine_threadsafe(coroutine, loop)


def publish_tick_pnl_from_sync(
    *,
    symbol: str,
    security_id: str,
    ltp: float,
    pnl: float,
    pnl_pct: float | None,
    mode: str | None,
) -> None:
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        return

    coroutine = publish_tick_pnl(
        symbol=symbol,
        security_id=security_id,
        ltp=ltp,
        pnl=pnl,
        pnl_pct=pnl_pct,
        mode=mode,
    )
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is loop:
        loop.create_task(coroutine)
    else:
        asyncio.run_coroutine_threadsafe(coroutine, loop)


def publish_active_trade_from_sync(position: dict[str, Any], mode: str | None) -> None:
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        return

    coroutine = publish_active_trade(position, mode)
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is loop:
        loop.create_task(coroutine)
    else:
        asyncio.run_coroutine_threadsafe(coroutine, loop)


async def publish_tick_pnl(
    *,
    symbol: str,
    security_id: str,
    ltp: float,
    pnl: float,
    pnl_pct: float | None,
    mode: str | None,
) -> None:
    for session_id in await session_store.active_session_ids():
        await session_store.append_event(
            session_id,
            event(
                "tick.pnl",
                symbol=symbol,
                securityId=security_id,
                ltp=ltp,
                pnl=pnl,
                pnlPct=pnl_pct,
                mode=mode,
            ),
        )


async def publish_active_trade(position: dict[str, Any], mode: str | None) -> None:
    active_trade = active_trade_from_position(position, mode)
    if active_trade is None or active_trade["avgPrice"] <= 0:
        return
    for session_id in await session_store.active_session_ids():
        await session_store.update_active_trade(session_id, active_trade)
        await session_store.append_event(session_id, event("order.filled", **active_trade))


async def publish_chat_result(payload: NormalizedSignal, execution_result: dict[str, Any]) -> None:
    explicit_mode = get_engine_mode(legacy_fallback=False)
    mode = explicit_mode or get_engine_mode()
    paper_exit: dict[str, Any] = {}
    if explicit_mode == "paper" and payload.action == "EXIT" and execution_result.get("success"):
        closed_trades = get_paper_portfolio().closed_trades
        paper_exit = closed_trades[-1] if closed_trades else {}
    for session_id in await session_store.active_session_ids():
        await session_store.append_event(
            session_id,
            event(
                "signal.received",
                message=_signal_message(payload),
                signalId=payload.signal_id,
                action=payload.action,
                optionSide=payload.option_side,
                strike=payload.strike,
                expiry=payload.expiry,
                qty=payload.qty,
                source=payload.source,
                mode=mode,
            ),
        )
        if _entry_fill_is_confirmed(explicit_mode, payload, execution_result):
            active_trade = _active_trade_from_fill(payload, execution_result, mode)
            await session_store.update_active_trade(session_id, active_trade)
            await session_store.append_event(session_id, event("order.filled", **active_trade))

        wallet = get_wallet_snapshot()
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
        if execution_result.get("blocked") or execution_result.get("success") is False:
            await session_store.append_event(
                session_id,
                event(
                    "order.rejected",
                    message=execution_result.get("reason") or execution_result.get("error") or "Trade blocked.",
                    signalId=payload.signal_id,
                    status=execution_result.get("status"),
                    mode=mode,
                ),
            )
            continue

        if payload.action == "EXIT" and execution_result.get("status") != "PARTIAL_EXIT_FILLED":
            await session_store.update_active_trade(session_id, None)
            await session_store.append_event(
                session_id,
                event(
                    "trade.exit",
                    message=f"Exit executed for {payload.trading_symbol or payload.symbol}.",
                    signalId=payload.signal_id,
                    orderId=execution_result.get("order_id"),
                    symbol=payload.trading_symbol or payload.symbol,
                    qty=execution_result.get("filled_qty") or payload.qty,
                    exitPrice=execution_result.get("avg_price"),
                    grossPnl=_paper_gross_pnl(paper_exit) if paper_exit else _exit_gross_pnl(payload),
                    charges=_paper_charges(paper_exit) if paper_exit else None,
                    netPnl=paper_exit.get("realized_pnl") if paper_exit else None,
                    reason=_exit_reason(payload),
                    status=execution_result.get("status"),
                    mode=mode,
                ),
            )
            continue

        await session_store.append_event(
            session_id,
            event(
                "order.placed",
                message=f"{payload.action.title()} order accepted by the production router.",
                signalId=payload.signal_id,
                orderId=execution_result.get("order_id"),
                status=execution_result.get("status"),
                mode=mode,
            ),
        )


def active_trade_from_position(position: dict[str, Any], mode: str | None) -> dict[str, Any] | None:
    if not position.get("has_open_position"):
        return None

    live_pnl = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
    entry_price = _number(position.get("entry_price") or live_pnl.get("entry_price"))
    ltp = _number(live_pnl.get("ltp") or entry_price)
    return {
        "mode": mode,
        "symbol": position.get("trading_symbol") or position.get("symbol") or "NIFTY option",
        "strike": _number(position.get("strike")),
        "optType": position.get("option_side") or "CE",
        "qty": int(position.get("qty") or 0),
        "avgPrice": entry_price,
        "ltp": ltp,
        "pnl": _number(live_pnl.get("unrealized_pnl")),
        "pnlPct": _number(live_pnl.get("pnl_percent")),
        "expiry": position.get("expiry"),
        "securityId": position.get("security_id"),
        "exitOn": str(position.get("exit_management") or "SERVER"),
        "orderId": position.get("entry_order_id") or "pending",
        "exchOrderId": position.get("exchange_order_id"),
        "sourceLtp": position.get("source_ltp"),
        "simulatedCharges": position.get("simulated_charges"),
        "correlationId": "",
        "status": "OPEN",
    }


def _entry_fill_is_confirmed(
    explicit_mode: str | None,
    payload: NormalizedSignal,
    execution_result: dict[str, Any],
) -> bool:
    if payload.action != "ENTRY":
        return False
    if explicit_mode == "live":
        broker_status = execution_result.get("broker_status")
        return (
            broker_status in {"TRADED", "PARTIAL_FILL", "PARTIAL_FILL_PENDING"}
            and _number(execution_result.get("avg_price")) > 0
            and _number(execution_result.get("filled_qty")) > 0
        )
    if explicit_mode == "paper":
        return execution_result.get("status") in {"TRADED", "ORDER_PLACED", "REVERSAL_ORDER_PLACED"}
    return False


def _active_trade_from_fill(
    payload: NormalizedSignal,
    execution_result: dict[str, Any],
    mode: str | None,
) -> dict[str, Any]:
    raw_response = execution_result.get("raw_response")
    fill = raw_response if isinstance(raw_response, dict) else {}
    avg_price = _number(execution_result.get("avg_price"))
    return {
        "mode": mode,
        "symbol": execution_result.get("trading_symbol") or payload.trading_symbol or payload.symbol,
        "strike": payload.strike or 0,
        "optType": payload.option_side or "CE",
        "qty": execution_result.get("filled_qty") or payload.qty,
        "avgPrice": avg_price,
        "ltp": avg_price,
        "pnl": 0,
        "pnlPct": 0,
        "expiry": payload.expiry,
        "securityId": execution_result.get("security_id") or payload.security_id,
        "exitOn": execution_result.get("exit_management") or "SERVER",
        "orderId": execution_result.get("order_id"),
        "exchOrderId": _pick(fill, "exchangeOrderId", "exchange_order_id"),
        "sourceLtp": fill.get("sourceLtp"),
        "simulatedCharges": fill.get("simulatedCharges"),
        "correlationId": "",
        "status": "OPEN",
    }


def _pick(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _signal_message(payload: NormalizedSignal) -> str:
    source = "TradingView" if payload.source in {"tradingview", "webhook"} else "NOVA"
    return f"{source} {payload.action.lower()} signal received for {payload.option_side or payload.symbol}."


def _exit_reason(payload: NormalizedSignal) -> str | None:
    raw_payload = payload.raw_payload if isinstance(payload.raw_payload, dict) else {}
    reason = raw_payload.get("exit_reason")
    return str(reason) if reason not in (None, "") else None


def _exit_gross_pnl(payload: NormalizedSignal) -> float | None:
    raw_payload = payload.raw_payload if isinstance(payload.raw_payload, dict) else {}
    live_pnl = raw_payload.get("live_pnl") if isinstance(raw_payload.get("live_pnl"), dict) else {}
    value = live_pnl.get("unrealized_pnl")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _paper_gross_pnl(trade: dict[str, Any]) -> float | None:
    try:
        return round(float(trade.get("qty") or 0) * float(trade.get("exit_price") or 0) - float(trade.get("entry_value") or 0), 2)
    except (TypeError, ValueError):
        return None


def _paper_charges(trade: dict[str, Any]) -> float | None:
    try:
        return round(float(trade.get("entry_charges") or 0) + float(trade.get("exit_charges") or 0), 2)
    except (TypeError, ValueError):
        return None
