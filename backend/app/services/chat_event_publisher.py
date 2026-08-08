from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.domain.events import event
from app.schemas.signal import NormalizedSignal
from app.services.normalized_errors import classify_failure, order_journey
from app.services.paper_portfolio import get_paper_portfolio
from app.store.redis_session import session_store
from app.services.state_store import get_engine_mode, get_wallet_snapshot
from app.domain.state_machine import SetupState


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

    coroutine = publish_chat_result(
        payload,
        execution_result,
        user_id=_current_execution_user_id(),
    )
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
        user_id=_current_execution_user_id(),
    )
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is loop:
        loop.create_task(coroutine)
    else:
        asyncio.run_coroutine_threadsafe(coroutine, loop)


def synchronize_runtime_sessions_from_sync(
    runtime: dict[str, Any],
    *,
    user_id: str | None = None,
) -> None:
    """Project owner-scoped runtime lifecycle into existing browser sessions."""
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed() or not user_id:
        return
    coroutine = synchronize_runtime_sessions(runtime, user_id=user_id)
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None
    if running_loop is loop:
        loop.create_task(coroutine)
    else:
        asyncio.run_coroutine_threadsafe(coroutine, loop)


def publish_market_snapshot_from_sync(
    *,
    snapshot: dict[str, Any] | None = None,
    snapshot_factory: Callable[[], dict[str, Any]] | None = None,
) -> bool:
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        return False
    if snapshot is None:
        if snapshot_factory is None:
            return False
        snapshot = snapshot_factory()

    coroutine = publish_market_snapshot(snapshot, user_id=_current_execution_user_id())
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is loop:
        loop.create_task(coroutine)
    else:
        asyncio.run_coroutine_threadsafe(coroutine, loop)
    return True


def publish_nifty_candles_from_sync(*, interval: str, series: dict[str, Any]) -> bool:
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        return False

    coroutine = publish_nifty_candles(interval=interval, series=series)
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is loop:
        loop.create_task(coroutine)
    else:
        asyncio.run_coroutine_threadsafe(coroutine, loop)
    return True


def publish_active_trade_from_sync(position: dict[str, Any], mode: str | None) -> None:
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        return

    coroutine = publish_active_trade(
        position,
        mode,
        user_id=_current_execution_user_id(),
    )
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is loop:
        loop.create_task(coroutine)
    else:
        asyncio.run_coroutine_threadsafe(coroutine, loop)


async def publish_market_snapshot(
    snapshot: dict[str, Any],
    *,
    user_id: str | None = None,
) -> None:
    for session_id in await _runtime_recipient_session_ids(user_id):
        await session_store.append_event(session_id, event("market.snapshot", **snapshot))


async def publish_nifty_candles(
    interval: str,
    *,
    series: dict[str, Any],
    user_id: str | None = None,
) -> None:
    for session_id in await _runtime_recipient_session_ids(user_id):
        await session_store.append_event(session_id, event("market.candles", **series))


async def publish_tick_pnl(
    *,
    symbol: str,
    security_id: str,
    ltp: float,
    pnl: float,
    pnl_pct: float | None,
    mode: str | None,
    user_id: str | None = None,
) -> None:
    for session_id in await _runtime_recipient_session_ids(user_id):
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


async def publish_active_trade(
    position: dict[str, Any],
    mode: str | None,
    *,
    user_id: str | None = None,
) -> None:
    active_trade = active_trade_from_position(position, mode)
    if active_trade is None or active_trade["avgPrice"] <= 0:
        return
    for session_id in await _runtime_recipient_session_ids(user_id):
        await session_store.update_active_trade(session_id, active_trade)
        await session_store.append_event(session_id, event("order.filled", **active_trade))


async def publish_chat_result(
    payload: NormalizedSignal,
    execution_result: dict[str, Any],
    *,
    user_id: str | None = None,
) -> None:
    for session_id in await _runtime_recipient_session_ids(user_id):
        await append_chat_result_to_session(session_id, payload, execution_result)


async def _runtime_recipient_session_ids(user_id: str | None) -> list[str]:
    if user_id:
        return await session_store.owner_session_ids(user_id)
    return await session_store.active_session_ids()


async def synchronize_runtime_sessions(runtime: dict[str, Any], *, user_id: str) -> None:
    engine = runtime.get("engine") if isinstance(runtime.get("engine"), dict) else {}
    lifecycle = str(engine.get("state") or "STOPPED").upper()
    accepting = bool(engine.get("accepting_signals"))
    if lifecycle == "RUNNING":
        state = SetupState.LIVE if accepting else SetupState.PAUSED
    elif lifecycle == "STOPPING":
        # PAUSED keeps exit controls/event delivery active while exposure is
        # being reconciled; STOPPED is published only after confirmed flat.
        state = SetupState.PAUSED
    else:
        state = SetupState.IDLE
    for session_id in await session_store.owner_session_ids(user_id):
        await session_store.synchronize_runtime_state(
            session_id,
            state,
            engine_mode=engine.get("mode"),
            runtime_state=lifecycle,
        )


async def append_chat_result_to_session(
    session_id: str,
    payload: NormalizedSignal,
    execution_result: dict[str, Any],
    *,
    restored_recent: bool = False,
) -> None:
    explicit_mode = get_engine_mode(legacy_fallback=False)
    mode = explicit_mode or get_engine_mode()
    paper_exit: dict[str, Any] = {}
    if explicit_mode == "paper" and payload.action == "EXIT" and execution_result.get("success"):
        closed_trades = get_paper_portfolio().closed_trades
        paper_exit = closed_trades[-1] if closed_trades else {}

    await session_store.append_event(
        session_id,
        event(
            "signal.received",
            message=_signal_message(payload),
            strategy=_strategy_label(payload),
            signalId=payload.signal_id,
            action=payload.action,
            optionSide=payload.option_side,
            strike=payload.strike,
            expiry=payload.expiry,
            qty=payload.qty,
            source=payload.source,
            mode=mode,
            restoredRecent=restored_recent,
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
        normalized_error = _normalized_error(payload, execution_result, mode)
        await session_store.append_event(
            session_id,
            event(
                "order.rejected",
                message=execution_result.get("reason") or execution_result.get("error") or normalized_error.get("userMessage") or "Trade blocked.",
                humanMessage=normalized_error.get("userMessage"),
                userTitle=normalized_error.get("userTitle"),
                signalId=payload.signal_id,
                status=execution_result.get("status"),
                reason=execution_result.get("reason") or execution_result.get("error"),
                normalizedError=normalized_error,
                orderJourney=execution_result.get("orderJourney") or order_journey(signal=payload, execution_result=execution_result, normalized_error=normalized_error),
                orderSentToBroker=normalized_error.get("orderSentToBroker"),
                moneyAtRisk=normalized_error.get("moneyAtRisk"),
                debugPack=normalized_error.get("debugPack"),
                mode=mode,
                restoredRecent=restored_recent,
            ),
        )
        return

    if payload.action == "EXIT" and execution_result.get("status") != "PARTIAL_EXIT_FILLED":
        await session_store.update_active_trade(session_id, None)
        if paper_exit:
            gross_pnl = _paper_gross_pnl(paper_exit)
            charges = _paper_charges(paper_exit)
            net_pnl = paper_exit.get("realized_pnl")
            charges_estimated = False
        else:
            gross_pnl, charges, net_pnl = _live_exit_pnl(payload, execution_result)
            charges_estimated = charges is not None
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
                grossPnl=gross_pnl,
                charges=charges,
                chargesEstimated=charges_estimated,
                netPnl=net_pnl,
                reason=_exit_reason(payload),
                status=execution_result.get("status"),
                mode=mode,
                orderJourney=execution_result.get("orderJourney") or order_journey(signal=payload, execution_result=execution_result),
                restoredRecent=restored_recent,
            ),
        )
        return

    await session_store.append_event(
        session_id,
        event(
            "order.placed",
            message=f"{payload.action.title()} order accepted by the production router.",
            signalId=payload.signal_id,
            orderId=execution_result.get("order_id"),
            status=execution_result.get("status"),
            mode=mode,
            orderJourney=execution_result.get("orderJourney") or order_journey(signal=payload, execution_result=execution_result),
            orderSentToBroker=True,
            moneyAtRisk=mode == "live",
            restoredRecent=restored_recent,
        ),
    )


def active_trade_from_position(position: dict[str, Any], mode: str | None) -> dict[str, Any] | None:
    if not position.get("has_open_position"):
        return None

    live_pnl = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
    entry_price = _number(position.get("entry_price") or live_pnl.get("entry_price"))
    ltp = _number(live_pnl.get("ltp") or entry_price)
    active_levels = position.get("active_exit_levels") if isinstance(position.get("active_exit_levels"), dict) else None
    sl_price = live_pnl.get("sl_price") or position.get("broker_sl_price")
    tp_price = live_pnl.get("tp_price") or position.get("broker_tp_price")
    if active_levels is None and (sl_price is not None or tp_price is not None):
        active_levels = {
            "source": "server_monitor" if str(live_pnl.get("exit_management") or "SERVER").upper() == "SERVER" else "broker",
            "stopLossPrice": sl_price,
            "targetPrice": tp_price,
        }
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
        "srSuggestion": position.get("sr_suggestion"),
        "activeExitLevels": active_levels,
        "riskArmed": (
            bool(position.get("risk_armed"))
            if position.get("risk_armed") is not None
            else True
            if sl_price is not None or tp_price is not None
            else None
        ),
        "riskSource": active_levels.get("source") if active_levels else None,
        "correlationId": "",
        "status": "OPEN",
    }


def _current_execution_user_id() -> str | None:
    from app.services.execution_context import current_execution_user

    user = current_execution_user()
    return user.id_str if user is not None and not user.is_dev else None


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


def _normalized_error(payload: NormalizedSignal, execution_result: dict[str, Any], mode: str | None) -> dict[str, Any]:
    existing = execution_result.get("normalizedError")
    if isinstance(existing, dict):
        return existing
    interpreted = execution_result.get("interpreted_error")
    if isinstance(interpreted, dict) and interpreted.get("errorId"):
        return interpreted
    source = "PAPER_ENGINE" if mode == "paper" else "DHAN" if execution_result.get("raw_response") else "RISK_ENGINE"
    return classify_failure(
        execution_result.get("reason") or execution_result.get("error") or execution_result.get("status") or "Trade blocked.",
        source=source,
        status=str(execution_result.get("status") or ""),
        signal=payload,
        mode=mode,
        raw_response=execution_result.get("raw_response") or execution_result,
        order_sent_to_broker=bool(execution_result.get("order_id")),
        money_at_risk=bool(execution_result.get("order_id") and mode == "live"),
        debug_pack={
            "action": payload.action,
            "optionSide": payload.option_side,
            "strike": payload.strike,
            "qty": payload.qty,
            "securityId": execution_result.get("security_id") or payload.security_id,
            "tradingSymbol": execution_result.get("trading_symbol") or payload.trading_symbol,
        },
    )


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
        "slippagePercent": fill.get("slippagePercent"),
        "srSuggestion": execution_result.get("sr_suggestion"),
        "activeExitLevels": execution_result.get("active_exit_levels"),
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


def _strategy_label(payload: NormalizedSignal) -> str:
    code = str(payload.strategy_code or "").upper()
    if code == "TRADINGVIEW_NIFTY_V1":
        return "Supertrend"
    return payload.strategy_code or "Strategy"


def _exit_reason(payload: NormalizedSignal) -> str | None:
    raw_payload = payload.raw_payload if isinstance(payload.raw_payload, dict) else {}
    reason = raw_payload.get("exit_reason")
    return str(reason) if reason not in (None, "") else None


def _live_exit_pnl(payload: NormalizedSignal, execution_result: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """Gross / estimated charges / net for a LIVE exit.

    Gross comes from actual fill prices: (exit avg - entry price) x qty.
    Charges use the same statutory model as paper mode (brokerage, STT,
    exchange txn, GST, SEBI, stamp) and are therefore estimates until the
    broker statement is reconciled.
    """
    raw_payload = payload.raw_payload if isinstance(payload.raw_payload, dict) else {}
    live_pnl = raw_payload.get("live_pnl") if isinstance(raw_payload.get("live_pnl"), dict) else {}

    def _num(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    exit_price = _num(execution_result.get("avg_price"))
    entry_price = _num(raw_payload.get("entry_price")) or _num(live_pnl.get("entry_price"))
    qty = _num(execution_result.get("filled_qty")) or _num(payload.qty)

    if exit_price is None or entry_price is None or not qty:
        # Fall back to the last unrealized-PnL snapshot if fills are unknown.
        return _exit_gross_pnl(payload), None, None

    gross = round((exit_price - entry_price) * qty, 2)
    try:
        from app.services.paper_broker import _simulated_charges

        charges = round(
            _simulated_charges(int(qty), entry_price, "BUY") + _simulated_charges(int(qty), exit_price, "SELL"),
            2,
        )
    except Exception:
        charges = None
    net = round(gross - charges, 2) if charges is not None else None
    return gross, charges, net


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
