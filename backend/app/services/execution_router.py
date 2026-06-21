from __future__ import annotations

from typing import Any

from app.config import (
    DEFAULT_EXCHANGE_SEGMENT,
    DEFAULT_ORDER_TYPE,
    DEFAULT_PRODUCT_TYPE,
    DISABLED_OPTION_SL_PERCENT,
    DISABLED_OPTION_SL_PRICE_FRACTION,
    settings,
)
from app.schemas.signal import NormalizedSignal
from app.services.audit_logger import log_audit_event, log_error_event, log_order_event
from app.services.chat_event_publisher import publish_chat_result_from_sync
from app.services.credential_vault import dhan_token_age_metadata, get_dhan_credentials
from app.services.dhan_client import (
    DHAN_OPEN_ORDER_STATUSES,
    DHAN_TERMINAL_STATUSES,
    RealDhanClient,
    get_broker_client,
)
from app.services.normalized_errors import classify_failure, order_journey
from app.services.risk_manager import (
    RiskDecision,
    _market_is_open,
    evaluate_entry,
    evaluate_exit,
    evaluate_reversal_entry,
    option_side_is_allowed,
)
from app.services.security_id_resolver import resolve_security_id
from app.services.state_store import (
    clear_open_position,
    get_engine_mode,
    get_open_position,
    get_runtime_settings,
    record_entry_trade,
    set_open_position,
    update_app_state,
    utc_now,
)
from app.services.wallet_service import refresh_wallet_snapshot


def _live_broker_client():
    """Use per-user proxy routing, retaining legacy local test compatibility."""
    from app.services.execution_context import current_execution_user

    user = current_execution_user()
    if user is not None and not user.is_dev:
        return get_broker_client("live")
    if settings.EXECUTION_NODE_ROUTING_ENABLED:
        raise RuntimeError(
            "Context-free live routing is disabled while execution-node routing is enabled."
        )
    return RealDhanClient()


def _public_order_request(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    sanitized.pop("access_token", None)
    return sanitized


def _normalized_log_fields(signal: NormalizedSignal) -> dict[str, Any]:
    return {
        "payload_format": signal.payload_format,
        "normalized_action": signal.action,
        "normalized_side": signal.side,
        "normalized_qty": signal.qty,
        "normalized_symbol": signal.symbol,
        "normalized_strike": signal.strike,
        "normalized_expiry": signal.expiry,
        "normalized_option_side": signal.option_side,
    }


def _build_correlation_id(signal: NormalizedSignal, action: str) -> str:
    """
    Build a short correlationId for Dhan order tracking.
    Max 20 chars recommended; Dhan uses it as a client reference tag.
    """
    import hashlib
    raw = f"{signal.signal_id}-{action}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _build_dhan_payload_and_resolution(signal: NormalizedSignal, qty: int, action: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Build the final Dhan v2 order payload.

    QTY_MODE=ABSOLUTE: TradingView qty is sent directly as Dhan quantity after
    MAX_QTY_PER_ORDER checks. Lot conversion requires QTY_MODE=LOTS (phase 2).

    Required Dhan v2 fields per POST /orders:
      dhanClientId, correlationId (optional but recommended), transactionType,
      exchangeSegment, productType, orderType, validity, securityId,
      quantity, disclosedQuantity, price, triggerPrice, afterMarketOrder.
    """
    resolution = resolve_security_id(signal)
    creds = get_dhan_credentials()
    client_id = creds.client_id if creds else "MOCK_CLIENT"
    transaction_type = signal.side
    if action == "EXIT":
        transaction_type = signal.side or "SELL"

    order_type = signal.order_type or DEFAULT_ORDER_TYPE
    # triggerPrice: 0 for MARKET orders; non-zero only for SL/SL-M orders.
    trigger_price = 0
    # price: 0 for MARKET orders.
    price = 0

    return {
        "dhanClientId": client_id,
        "correlationId": _build_correlation_id(signal, action),
        "transactionType": transaction_type,
        "exchangeSegment": signal.exchange_segment or DEFAULT_EXCHANGE_SEGMENT,
        "productType": signal.product_type or DEFAULT_PRODUCT_TYPE,
        "orderType": order_type,
        "validity": "DAY",
        "tradingSymbol": resolution.trading_symbol or signal.trading_symbol or "",
        "securityId": resolution.security_id or "",
        "quantity": qty,
        "disclosedQuantity": 0,
        "price": price,
        "triggerPrice": trigger_price,
        "afterMarketOrder": False,
    }, resolution.model_dump()


def _build_dhan_payload(signal: NormalizedSignal, qty: int, action: str) -> dict[str, Any]:
    payload, _resolution = _build_dhan_payload_and_resolution(signal, qty, action)
    return payload


def _runtime_float(runtime: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(runtime.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _round_option_tick(price: float) -> float:
    return round(max(round(price / 0.05) * 0.05, 0.05), 2)


def _broker_exit_levels(reference_price: float, runtime: dict[str, Any]) -> tuple[float, float, float, float]:
    sl_percent = _runtime_float(runtime, "option_sl_percent", 10.0)
    tp_percent = _runtime_float(runtime, "option_tp_percent", 20.0)

    # SL disable — Dhan Super Order schema requires a stopLossPrice, so we
    # plant a floor that normal intraday price action will never touch
    # (max of Rs.0.10 and 0.1% of entry). The position is then exited by the
    # opposite Supertrend reversal, the TP leg, or the 15:15 IST EOD task.
    disable_sl = bool(runtime.get("option_disable_sl", True))
    if disable_sl:
        sl_percent = DISABLED_OPTION_SL_PERCENT
        stop_loss_price = _round_option_tick(max(0.10, reference_price * DISABLED_OPTION_SL_PRICE_FRACTION))
        # Recompute the implied sl_percent so audit logs reflect the floor.
        if reference_price > 0:
            sl_percent = round((reference_price - stop_loss_price) / reference_price * 100, 2)
    else:
        stop_loss_price = _round_option_tick(reference_price * (1 - sl_percent / 100))

    target_price = _round_option_tick(reference_price * (1 + tp_percent / 100))
    if target_price <= reference_price:
        target_price = _round_option_tick(reference_price + 0.05)
    if stop_loss_price >= reference_price:
        stop_loss_price = _round_option_tick(reference_price - 0.05)
    return sl_percent, tp_percent, stop_loss_price, target_price


def _option_exit_mode(runtime: dict[str, Any]) -> str:
    mode = str(runtime.get("option_exit_mode") or "DHAN_SUPER").strip().upper()
    return mode if mode in {"DHAN_SUPER", "SERVER"} else "DHAN_SUPER"


def _build_dhan_super_order_payload(
    *,
    base_payload: dict[str, Any],
    reference_ltp: float,
    runtime: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    sl_percent, tp_percent, stop_loss_price, target_price = _broker_exit_levels(reference_ltp, runtime)
    payload = {
        "dhanClientId": base_payload["dhanClientId"],
        "correlationId": base_payload["correlationId"],
        "transactionType": base_payload["transactionType"],
        "exchangeSegment": base_payload["exchangeSegment"],
        "productType": base_payload["productType"],
        "orderType": "MARKET",
        "securityId": base_payload["securityId"],
        "quantity": base_payload["quantity"],
        "price": 0,
        "targetPrice": target_price,
        "stopLossPrice": stop_loss_price,
        "trailingJump": 0,
    }
    levels = {
        "reference_ltp": reference_ltp,
        "provisional_exit_reference_price": reference_ltp,
        "sl_percent": sl_percent,
        "tp_percent": tp_percent,
        "stop_loss_price": stop_loss_price,
        "target_price": target_price,
        "levels_source": "pre_entry_ltp",
        "sl_disabled": bool(runtime.get("option_disable_sl", True)),
    }
    return payload, levels


def _order_result_snapshot(result: Any) -> dict[str, Any]:
    return {
        "success": bool(getattr(result, "success", False)),
        "order_id": getattr(result, "order_id", None),
        "status": getattr(result, "status", None),
        "error": getattr(result, "error", None),
        "interpreted_error": getattr(result, "interpreted_error", None),
        "raw_response": getattr(result, "raw_response", None),
    }


def _ltp_result_snapshot(result: Any) -> dict[str, Any]:
    return {
        "success": bool(getattr(result, "success", False)),
        "message": getattr(result, "message", None),
        "ltp": getattr(result, "ltp", None),
        "exchange_segment": getattr(result, "exchange_segment", None),
        "security_id": getattr(result, "security_id", None),
        "status_code": getattr(result, "status_code", None),
        "error": getattr(result, "error", None),
        "raw_response": getattr(result, "raw_response", None),
    }


def _sync_super_order_exit_levels(
    *,
    client: Any,
    client_id: str,
    access_token: str,
    order_id: str | None,
    fill_price: float | None,
    runtime: dict[str, Any],
    current_levels: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not order_id or fill_price is None or fill_price <= 0:
        return current_levels, None

    sl_percent, tp_percent, stop_loss_price, target_price = _broker_exit_levels(fill_price, runtime)
    target_payload = {
        "dhanClientId": client_id,
        "orderId": order_id,
        "legName": "TARGET_LEG",
        "targetPrice": target_price,
    }
    stop_payload = {
        "dhanClientId": client_id,
        "orderId": order_id,
        "legName": "STOP_LOSS_LEG",
        "stopLossPrice": stop_loss_price,
        "trailingJump": 0,
    }

    target_result = client.modify_super_order(
        client_id=client_id,
        access_token=access_token,
        order_id=order_id,
        payload=target_payload,
    )
    stop_result = client.modify_super_order(
        client_id=client_id,
        access_token=access_token,
        order_id=order_id,
        payload=stop_payload,
    )

    target_success = bool(target_result.success)
    stop_success = bool(stop_result.success)
    updated_levels = dict(current_levels or {})
    updated_levels.update(
        {
            "exact_exit_reference_price": fill_price,
            "exact_stop_loss_price": stop_loss_price,
            "exact_target_price": target_price,
            "exact_sl_percent": sl_percent,
            "exact_tp_percent": tp_percent,
            "post_fill_update_complete": target_success and stop_success,
        }
    )
    if target_success:
        updated_levels["target_price"] = target_price
    if stop_success:
        updated_levels["stop_loss_price"] = stop_loss_price
    if target_success or stop_success:
        updated_levels["levels_source"] = "post_fill_avg_price"

    update_result = {
        "attempted": True,
        "order_id": order_id,
        "reference_price": fill_price,
        "target_payload": target_payload,
        "stop_payload": stop_payload,
        "target_result": _order_result_snapshot(target_result),
        "stop_result": _order_result_snapshot(stop_result),
        "success": target_success and stop_success,
    }
    log_order_event(
        {
            "event": "DHAN_SUPER_ORDER_POST_FILL_LEVEL_SYNC",
            "order_id": order_id,
            "reference_price": fill_price,
            "updated_levels": updated_levels,
            "update_result": update_result,
        }
    )
    return updated_levels, update_result


def _cancel_super_order_exit_legs(position: dict[str, Any]) -> dict[str, Any] | None:
    if str(position.get("exit_management") or "").upper() != "DHAN_SUPER":
        return None
    order_id = str(position.get("entry_order_id") or "").strip()
    if not order_id:
        return {"attempted": False, "reason": "missing_super_order_id"}

    engine_mode = get_engine_mode()
    creds = get_dhan_credentials()
    if engine_mode == "live":
        if not creds:
            return {"attempted": False, "reason": "missing_dhan_credentials"}
        client = _live_broker_client()
        client_id = creds.client_id
        access_token = creds.access_token
    else:
        client = get_broker_client(engine_mode)
        client_id = creds.client_id if creds else "MOCK_CLIENT"
        access_token = creds.access_token if creds else ""

    results = {}
    for leg_name in ("TARGET_LEG", "STOP_LOSS_LEG"):
        result = client.cancel_super_order_leg(
            client_id=client_id,
            access_token=access_token,
            order_id=order_id,
            leg_name=leg_name,
        )
        results[leg_name] = _order_result_snapshot(result)

    summary = {
        "attempted": True,
        "order_id": order_id,
        "success": all(item.get("success") for item in results.values()),
        "legs": results,
    }
    log_order_event(
        {
            "event": "DHAN_SUPER_ORDER_EXIT_LEGS_CANCELLED_AFTER_MANUAL_EXIT",
            "order_id": order_id,
            "summary": summary,
            "trading_symbol": position.get("trading_symbol"),
            "security_id": position.get("security_id"),
        }
    )
    return summary


def _blocked(
    status: str,
    reason: str,
    signal: NormalizedSignal,
    *,
    security_id_resolution: dict[str, Any] | None = None,
    security_id: str | None = None,
    trading_symbol: str | None = None,
    audit_event: str | None = None,
    audit_metadata: dict[str, Any] | None = None,
    result_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mode = get_engine_mode(legacy_fallback=False) or get_engine_mode()
    normalized_error = classify_failure(
        reason,
        source=_error_source_from_reason(reason, signal),
        status=status,
        signal=signal,
        mode=mode,
        order_sent_to_broker=False,
        money_at_risk=False,
        raw_response=result_extra,
        debug_pack={
            "action": signal.action,
            "optionSide": signal.option_side,
            "strike": signal.strike,
            "qty": signal.qty,
            "securityId": security_id or signal.security_id,
            "tradingSymbol": trading_symbol or signal.trading_symbol,
        },
    )
    log_order_event(
        {
            "phase": "blocked",
            "signal_id": signal.signal_id,
            "action": signal.action,
            "side": signal.side,
            "status": status,
            "blocked": True,
            "reason": reason,
            "security_id": security_id or signal.security_id,
            "security_id_resolution": security_id_resolution,
            "trading_symbol": trading_symbol or signal.trading_symbol,
            "qty": signal.qty,
            "engine_mode": get_engine_mode(),
            "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
            **(result_extra or {}),
            **_normalized_log_fields(signal),
            "normalized_error": normalized_error,
        }
    )
    metadata = {"signal_id": signal.signal_id, "action": signal.action, "payload_format": signal.payload_format}
    if audit_metadata:
        metadata.update(audit_metadata)
    log_audit_event(
        audit_event or status,
        reason,
        severity="WARNING",
        metadata=metadata,
    )
    update_app_state(state="BLOCKED", last_message=reason)
    result = {
        "blocked": True,
        "success": False,
        "status": status,
        "reason": reason,
        "normalizedError": normalized_error,
        "payload_format": signal.payload_format,
        "normalized_action": signal.action,
        "normalized_side": signal.side,
        "normalized_qty": signal.qty,
        "normalized_symbol": signal.symbol,
        "normalized_strike": signal.strike,
        "normalized_expiry": signal.expiry,
        "normalized_option_side": signal.option_side,
        "security_id": security_id or signal.security_id,
        "security_id_resolution": security_id_resolution,
        "trading_symbol": trading_symbol or signal.trading_symbol,
    }
    if result_extra:
        result.update(result_extra)
    result["orderJourney"] = order_journey(signal=signal, execution_result=result, normalized_error=normalized_error)
    return result


def _error_source_from_reason(reason: str, signal: NormalizedSignal) -> str:
    lowered = str(reason or "").lower()
    if "dhan" in lowered:
        return "DHAN"
    if "paper" in lowered:
        return "PAPER_ENGINE"
    if any(term in lowered for term in ("risk", "blocked", "open position", "allow_entry", "global_kill", "emergency_stop", "qty", "quantity", "market-hours")):
        return "RISK_ENGINE"
    if signal.source in {"tradingview", "webhook"}:
        return "TRADINGVIEW"
    return "NOVA_BACKEND"


def _dhan_token_signal_preflight(signal: NormalizedSignal) -> dict[str, Any] | None:
    mode = get_engine_mode()
    if mode not in {"paper", "live"}:
        return None

    # Paper mode reads market data from the shared, auto-refreshed data-only
    # account (see shared_market_data). It does NOT use the per-user Dhan token,
    # so paper signals must not be blocked on per-user token expiry when the
    # shared feed is active. Live mode still requires the user's own fresh token.
    if mode == "paper":
        from app.services.shared_market_data import shared_market_data_configured

        if shared_market_data_configured():
            return None

    token_meta = dhan_token_age_metadata()
    age_minutes = token_meta.get("token_age_minutes")
    if token_meta.get("token_expired") is True:
        age_text = f" (age: {age_minutes} min)" if age_minutes is not None else ""
        reason = f"Dhan token expired{age_text}. Reconnect Dhan in Setup before routing signals."
        update_app_state(
            state="BLOCKED",
            last_signal_id=signal.signal_id,
            last_alert_at=utc_now(),
            last_message=reason,
        )
        return _blocked(
            "BLOCKED",
            reason,
            signal,
            audit_event="DHAN_TOKEN_EXPIRED_SIGNAL_BLOCK",
            audit_metadata={"token_age": token_meta},
            result_extra={
                "success": False,
                "block_code": "DHAN_TOKEN_EXPIRED",
                "token_age": token_meta,
            },
        )

    if token_meta.get("token_warn") is True:
        log_audit_event(
            "DHAN_TOKEN_EXPIRY_WARNING",
            f"Dhan token is approaching expiry (age: {age_minutes} min).",
            severity="WARNING",
            metadata={"signal_id": signal.signal_id, "token_age": token_meta},
        )

    return None


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _payload_number(signal: NormalizedSignal, key: str) -> float | None:
    raw_payload = signal.raw_payload if isinstance(signal.raw_payload, dict) else {}
    return _number(raw_payload.get(key))


def _raw_payload_number(signal: NormalizedSignal, *keys: str) -> float | None:
    raw_payload = signal.raw_payload if isinstance(signal.raw_payload, dict) else {}
    for key in keys:
        value = _number(raw_payload.get(key))
        if value is not None:
            return value
    return None


def _round_sr_option_price(price: float) -> float:
    return round(max(round(price * 2) / 2, 0.5), 2)


def _sr_exit_suggestion(signal: NormalizedSignal, order_result: dict[str, Any]) -> dict[str, Any]:
    entry_price = _number(order_result.get("avg_price"))
    nifty_price = _payload_number(signal, "nifty_price")
    stop_level = _payload_number(signal, "sl_level")
    target_level = _payload_number(signal, "tp_level")
    delta = _payload_number(signal, "delta") or 0.5
    option_side = str(signal.option_side or "").upper()
    basis = {
        "source": "tradingview_sr",
        "strategyCode": signal.strategy_code,
        "optionSide": option_side,
        "entryPrice": entry_price,
        "niftyPrice": nifty_price,
        "niftyStopLevel": stop_level,
        "niftyTargetLevel": target_level,
        "delta": delta,
        "thetaBuffer": 10.0,
    }
    unavailable = {
        "available": False,
        "accepted": False,
        "source": "tradingview_sr",
        "basis": basis,
    }
    if entry_price is None or entry_price <= 0:
        return {**unavailable, "message": "Suggested SL/TP unavailable until entry fill price is known."}
    if nifty_price is None or stop_level is None or target_level is None:
        return {**unavailable, "message": "TradingView S/R levels were not present on this alert."}
    if delta <= 0:
        return {**unavailable, "message": "TradingView delta must be positive to estimate option SL/TP."}

    if option_side == "PE":
        nifty_dist_tp = nifty_price - target_level
        nifty_dist_sl = stop_level - nifty_price
    else:
        nifty_dist_tp = target_level - nifty_price
        nifty_dist_sl = nifty_price - stop_level
    basis.update(
        {
            "niftyDistanceToTarget": nifty_dist_tp,
            "niftyDistanceToStop": nifty_dist_sl,
        }
    )
    if nifty_dist_tp <= 0 or nifty_dist_sl <= 0:
        return {**unavailable, "basis": basis, "message": "TradingView S/R levels are not on the expected side of NIFTY."}

    raw_target = entry_price + (nifty_dist_tp * delta) - 10.0
    raw_stop = entry_price - (nifty_dist_sl * delta) + 10.0
    target_price = _round_sr_option_price(raw_target)
    stop_price = _round_sr_option_price(max(raw_stop, entry_price * 0.15))
    basis.update(
        {
            "rawTargetPrice": round(raw_target, 2),
            "rawStopLossPrice": round(raw_stop, 2),
        }
    )
    if target_price <= entry_price or stop_price >= entry_price:
        return {**unavailable, "basis": basis, "message": "Suggested S/R levels did not produce valid option SL/TP prices."}

    return {
        "available": True,
        "accepted": False,
        "source": "tradingview_sr",
        "stopLossPrice": stop_price,
        "targetPrice": target_price,
        "basis": basis,
        "message": "TradingView S/R suggested SL/TP is ready for paper validation.",
    }


def _filled_qty_from_raw(data: dict[str, Any] | None) -> int | None:
    if not isinstance(data, dict):
        return None
    for key in (
        "filled_qty",
        "filledQty",
        "filledQuantity",
        "filled_quantity",
        "tradedQuantity",
        "tradedQty",
        "traded_quantity",
        "quantityTraded",
    ):
        qty = _int_value(data.get(key))
        if qty is not None:
            return qty
    return None


def _remaining_qty_from_raw(data: dict[str, Any] | None) -> int | None:
    if not isinstance(data, dict):
        return None
    for key in (
        "remainingQuantity",
        "remainingQty",
        "remaining_quantity",
        "pendingQuantity",
        "pendingQty",
        "pending_quantity",
        "unfilledQuantity",
    ):
        qty = _int_value(data.get(key))
        if qty is not None:
            return qty
    return None


def _is_active_dhan_position(row: dict[str, Any]) -> bool:
    net_qty = _number(_pick(row, "netQty", "netQuantity", "net_qty"))
    if net_qty is not None and abs(net_qty) > 0:
        return True

    buy_qty = _number(_pick(row, "buyQty", "buyQuantity", "buy_qty"))
    sell_qty = _number(_pick(row, "sellQty", "sellQuantity", "sell_qty"))
    if buy_qty is not None and sell_qty is not None and abs(buy_qty - sell_qty) > 0:
        return True

    position_type = str(_pick(row, "positionType", "position_type") or "").upper()
    return position_type in {"LONG", "SHORT", "OPEN"}


def _is_open_dhan_order(row: dict[str, Any]) -> bool:
    status = str(_pick(row, "orderStatus", "order_status", "status") or "").upper()
    if status in DHAN_OPEN_ORDER_STATUSES:
        return True
    if status in DHAN_TERMINAL_STATUSES:
        return False

    remaining_qty = _number(_pick(row, "remainingQuantity", "remainingQty", "pendingQuantity", "pendingQty"))
    if remaining_qty is not None:
        return remaining_qty > 0

    # Unknown non-terminal statuses are treated as active in live mode.
    return bool(status)


def _summarize_dhan_position(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "trading_symbol": _pick(row, "tradingSymbol", "trading_symbol"),
        "security_id": _pick(row, "securityId", "security_id"),
        "net_qty": _pick(row, "netQty", "netQuantity", "net_qty"),
        "position_type": _pick(row, "positionType", "position_type"),
        "product_type": _pick(row, "productType", "product_type"),
        "exchange_segment": _pick(row, "exchangeSegment", "exchange_segment"),
    }


def _summarize_dhan_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": _pick(row, "orderId", "order_id"),
        "order_status": _pick(row, "orderStatus", "order_status", "status"),
        "trading_symbol": _pick(row, "tradingSymbol", "trading_symbol"),
        "security_id": _pick(row, "securityId", "security_id"),
        "transaction_type": _pick(row, "transactionType", "transaction_type"),
        "quantity": _pick(row, "quantity", "qty"),
        "remaining_quantity": _pick(row, "remainingQuantity", "remainingQty", "pendingQuantity", "pendingQty"),
    }


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _dhan_exposure_reason(active_positions: list[dict[str, Any]], open_orders: list[dict[str, Any]]) -> str:
    if active_positions:
        position = active_positions[0]
        symbol = _first_non_empty(position.get("trading_symbol"), position.get("security_id"), "unknown symbol")
        qty = position.get("net_qty")
        qty_text = f" netQty={qty}" if qty not in (None, "") else ""
        return f"Trade blocked: Dhan already has open position {symbol}{qty_text}."

    order = open_orders[0]
    symbol = _first_non_empty(order.get("trading_symbol"), order.get("security_id"), order.get("order_id"), "unknown order")
    status = order.get("order_status") or "UNKNOWN"
    order_id = order.get("order_id")
    order_text = f" order_id={order_id}" if order_id not in (None, "") else ""
    return f"Trade blocked: Dhan already has open order {symbol} status={status}{order_text}."


def _dhan_entry_preflight(client: Any, *, client_id: str, access_token: str, signal: NormalizedSignal) -> dict[str, Any]:
    positions = client.get_positions_snapshot(client_id=client_id, access_token=access_token)
    orders = client.get_order_book(client_id=client_id, access_token=access_token)

    active_positions = [_summarize_dhan_position(row) for row in positions.items if _is_active_dhan_position(row)]
    open_orders = [_summarize_dhan_order(row) for row in orders.items if _is_open_dhan_order(row)]

    failures = []
    if not positions.success:
        failures.append(positions.message)
    if not orders.success:
        failures.append(orders.message)

    details = {
        "positions_success": positions.success,
        "positions_count": len(positions.items),
        "orders_success": orders.success,
        "orders_count": len(orders.items),
        "active_positions": active_positions[:3],
        "open_orders": open_orders[:3],
        "failures": failures,
    }
    allowed = not active_positions and not open_orders and not failures

    log_order_event(
        {
            "event": "DHAN_ENTRY_PREFLIGHT",
            "signal_id": signal.signal_id,
            "action": signal.action,
            "allowed": allowed,
            "engine_mode": get_engine_mode(),
            **details,
        }
    )

    if active_positions or open_orders:
        return {"allowed": False, "reason": _dhan_exposure_reason(active_positions, open_orders), "details": details}

    if failures:
        return {
            "allowed": False,
            "reason": "Trade blocked: could not verify Dhan positions/orders before live entry. " + " ".join(failures),
            "details": details,
        }

    return {"allowed": True, "reason": "Dhan pre-entry check passed.", "details": details}


def _reconcile_tracked_position_before_entry(signal: NormalizedSignal) -> RiskDecision | None:
    open_position = get_open_position()
    if not open_position.get("has_open_position"):
        return None
    if get_engine_mode() != "live" or not settings.ENABLE_LIVE_ORDERS:
        return None

    creds = get_dhan_credentials()
    if not creds:
        return RiskDecision(False, "Trade blocked: local open position exists and Dhan credentials are missing.")

    preflight = _dhan_entry_preflight(
        _live_broker_client(),
        client_id=creds.client_id,
        access_token=creds.access_token,
        signal=signal,
    )
    if preflight.get("allowed"):
        clear_open_position()
        message = "Tracked open position cleared because Dhan positions/orders are flat before entry."
        log_audit_event(
            "OPEN_POSITION_RECONCILED",
            message,
            metadata={"signal_id": signal.signal_id, "previous_open_position": open_position},
        )
        log_order_event(
            {
                "event": "LOCAL_OPEN_POSITION_RECONCILED",
                "signal_id": signal.signal_id,
                "previous_open_position": open_position,
                "dhan_preflight": preflight.get("details"),
            }
        )
        return None

    return RiskDecision(False, str(preflight.get("reason") or "Trade blocked: Dhan exposure check failed."))


def _place_order(
    signal: NormalizedSignal,
    qty: int,
    action: str,
    *,
    skip_entry_preflight: bool = False,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    engine_mode = get_engine_mode()
    explicit_engine_mode = get_engine_mode(legacy_fallback=False)
    request_payload, security_id_resolution = _build_dhan_payload_and_resolution(signal, qty, action)
    # C11 — Settings snapshot per signal: caller passes the snapshot taken
    # at signal arrival. Fall back to live read only when called directly
    # (e.g. server-side option monitor exits that aren't tied to a webhook).
    if runtime is None:
        runtime = get_runtime_settings()
    if not _market_is_open():
        return _blocked(
            "BLOCKED",
            "Trade blocked: market is closed; Dhan WebSocket and REST market-data fetching are disabled.",
            signal,
            security_id_resolution=security_id_resolution,
            security_id=request_payload.get("securityId"),
            trading_symbol=request_payload.get("tradingSymbol"),
        )
    creds = get_dhan_credentials()
    exit_mode = _option_exit_mode(runtime)
    use_super_order = engine_mode == "live" and action == "ENTRY" and exit_mode == "DHAN_SUPER"

    if engine_mode == "live":
        if not settings.ENABLE_LIVE_ORDERS:
            return _blocked(
                "BLOCKED",
                "REAL mode configured but ENABLE_LIVE_ORDERS=false",
                signal,
                security_id_resolution=security_id_resolution,
                security_id=request_payload.get("securityId"),
                trading_symbol=request_payload.get("tradingSymbol"),
            )
        if not creds:
            return _blocked(
                "BLOCKED",
                "REAL mode blocked: Dhan Client ID or Access Token missing",
                signal,
                security_id_resolution=security_id_resolution,
                security_id=request_payload.get("securityId"),
                trading_symbol=request_payload.get("tradingSymbol"),
            )
        if not security_id_resolution.get("ok") or not request_payload.get("securityId"):
            return _blocked(
                "BLOCKED",
                security_id_resolution.get("reason") or "REAL mode blocked: securityId missing",
                signal,
                security_id_resolution=security_id_resolution,
                security_id=request_payload.get("securityId"),
                trading_symbol=request_payload.get("tradingSymbol"),
            )
        lot_size = security_id_resolution.get("lot_size")
        try:
            lot_size_int = int(lot_size) if lot_size not in (None, "") else None
        except (TypeError, ValueError):
            lot_size_int = None
        if lot_size_int and qty % lot_size_int != 0:
            return _blocked(
                "BLOCKED",
                f"REAL mode blocked: quantity {qty} is not a multiple of Dhan lot size {lot_size_int}. "
                f"For one lot, send quantity {lot_size_int}.",
                signal,
                security_id_resolution=security_id_resolution,
                security_id=request_payload.get("securityId"),
                trading_symbol=request_payload.get("tradingSymbol"),
            )
        if (
            settings.MARKET_CLOSED_DEBUG
            and not settings.FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED
            and not _market_is_open()
        ):
            return _blocked(
                "BLOCKED",
                "MARKET_CLOSED_DEBUG=true blocked live order while market is closed; set FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED=true only for an explicit live test.",
                signal,
                security_id_resolution=security_id_resolution,
                security_id=request_payload.get("securityId"),
                trading_symbol=request_payload.get("tradingSymbol"),
            )
        client = _live_broker_client()
        client_id = creds.client_id
        access_token = creds.access_token
    else:
        paper_data_creds = None
        shared_data_status: dict[str, Any] | None = None
        if explicit_engine_mode == "paper":
            from app.services.shared_market_data import (
                market_data_credentials,
                shared_market_data_configured,
                shared_market_data_status,
            )

            paper_data_creds = market_data_credentials()
            if shared_market_data_configured():
                shared_data_status = shared_market_data_status()

        if explicit_engine_mode == "paper" and not paper_data_creds:
            shared_configured = bool(shared_data_status and shared_data_status.get("configured"))
            reason = (
                "Paper mode blocked: shared market-data token unavailable. "
                "Check DHAN_SHARED_* TOTP setup and Dhan auth status."
                if shared_configured
                else "Paper mode blocked: Dhan Client ID or Access Token missing. Real market data is required for paper fills."
            )
            return _blocked(
                "BLOCKED",
                reason,
                signal,
                security_id_resolution=security_id_resolution,
                security_id=request_payload.get("securityId"),
                trading_symbol=request_payload.get("tradingSymbol"),
                result_extra={
                    "block_code": "SHARED_MARKET_DATA_TOKEN_UNAVAILABLE"
                    if shared_configured
                    else "DHAN_CREDENTIALS_MISSING",
                    "shared_market_data": shared_data_status,
                } if explicit_engine_mode == "paper" else None,
            )
        if explicit_engine_mode == "paper" and (not security_id_resolution.get("ok") or not request_payload.get("securityId")):
            return _blocked(
                "BLOCKED",
                security_id_resolution.get("message") or "Paper mode blocked: securityId resolution failed.",
                signal,
                security_id_resolution=security_id_resolution,
                security_id=request_payload.get("securityId"),
                trading_symbol=request_payload.get("tradingSymbol"),
            )
        client = get_broker_client(engine_mode)
        selected_creds = paper_data_creds if explicit_engine_mode == "paper" else creds
        client_id = selected_creds.client_id if selected_creds else "MOCK_CLIENT"
        access_token = selected_creds.access_token if selected_creds else ""

    if engine_mode == "live" and action == "ENTRY" and not skip_entry_preflight:
        preflight = _dhan_entry_preflight(client, client_id=client_id, access_token=access_token, signal=signal)
        if not preflight.get("allowed"):
            return _blocked(
                "BLOCKED",
                str(preflight.get("reason") or "Trade blocked: Dhan exposure check failed."),
                signal,
                security_id_resolution=security_id_resolution,
                security_id=request_payload.get("securityId"),
                trading_symbol=request_payload.get("tradingSymbol"),
            )

    super_order_levels: dict[str, Any] | None = None
    order_request_payload = request_payload
    place_method = "orders"
    if use_super_order:
        ltp_result = client.get_ltp(
            client_id=client_id,
            access_token=access_token,
            exchange_segment=str(request_payload.get("exchangeSegment") or ""),
            security_id=str(request_payload.get("securityId") or ""),
        )
        if not ltp_result.success or ltp_result.ltp is None or float(ltp_result.ltp) <= 0:
            ltp_check = _ltp_result_snapshot(ltp_result)
            return _blocked(
                "BLOCKED",
                "Dhan Super Order blocked: could not fetch option LTP for broker-side SL/TP.",
                signal,
                security_id_resolution=security_id_resolution,
                security_id=request_payload.get("securityId"),
                trading_symbol=request_payload.get("tradingSymbol"),
                audit_metadata={"ltp_check": ltp_check},
                result_extra={
                    "success": False,
                    "block_code": "DHAN_SUPER_LTP_UNAVAILABLE",
                    "ltp_check": ltp_check,
                },
            )
        order_request_payload, super_order_levels = _build_dhan_super_order_payload(
            base_payload=request_payload,
            reference_ltp=float(ltp_result.ltp),
            runtime=runtime,
        )
        place_method = "super_orders"

    log_order_event(
        {
            "phase": "before_request",
            "signal_id": signal.signal_id,
            "action": action,
            "side": signal.side,
            "place_method": place_method,
            "exit_mode": exit_mode,
            "engine_mode": engine_mode,
            "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
            "market_closed_debug": settings.MARKET_CLOSED_DEBUG,
            "force_allow_order_when_market_closed": settings.FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED,
            "request": _public_order_request(order_request_payload),
            "super_order_levels": super_order_levels,
            "security_id": request_payload.get("securityId"),
            "security_id_resolution": security_id_resolution,
            "trading_symbol": request_payload.get("tradingSymbol") or signal.trading_symbol,
            "qty": qty,
            "order_type": order_request_payload["orderType"],
            "product_type": request_payload["productType"],
            **_normalized_log_fields(signal),
        }
    )

    if use_super_order:
        result = client.place_super_order(client_id=client_id, access_token=access_token, payload=order_request_payload)
    else:
        result = client.place_order(client_id=client_id, access_token=access_token, payload=order_request_payload)

    log_order_event(
        {
            "phase": "after_response",
            "signal_id": signal.signal_id,
            "action": action,
            "side": signal.side,
            "place_method": place_method,
            "exit_mode": exit_mode,
            "engine_mode": engine_mode,
            "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
            "success": result.success,
            "blocked": False,
            "order_id": result.order_id,
            "status": result.status,
            "avg_price": result.avg_price,
            "error": result.error,
            "interpreted_error": result.interpreted_error,
            "response": result.raw_response,
            "security_id": request_payload.get("securityId"),
            "security_id_resolution": security_id_resolution,
            "trading_symbol": request_payload.get("tradingSymbol") or signal.trading_symbol,
            "super_order_levels": super_order_levels,
            "qty": qty,
            "order_type": order_request_payload["orderType"],
            "product_type": request_payload["productType"],
            **_normalized_log_fields(signal),
        }
    )

    # --- Post-order status polling (MVP: poll a few times for final status) ---
    order_status_poll: dict[str, Any] | None = None
    final_status = result.status
    final_avg_price = result.avg_price
    final_success = result.success
    final_filled_qty = _filled_qty_from_raw(result.raw_response)
    final_remaining_qty = _remaining_qty_from_raw(result.raw_response)
    super_order_post_fill_update: dict[str, Any] | None = None
    active_super_order_levels = super_order_levels

    if result.success and result.order_id:
        # Status from placement response may be TRANSIT/PENDING — poll for confirmation.
        if result.status not in DHAN_TERMINAL_STATUSES or final_avg_price is None:
            poll = client.poll_order_status(
                client_id=client_id,
                access_token=access_token,
                order_id=result.order_id,
            )
            order_status_poll = {
                "order_status": poll.order_status,
                "is_terminal": poll.is_terminal,
                "is_filled": poll.is_filled,
                "avg_price": poll.avg_price,
                "filled_qty": poll.filled_qty,
                "remaining_qty": poll.remaining_qty,
                "error": poll.error,
            }
            if poll.filled_qty is not None:
                final_filled_qty = poll.filled_qty
            if poll.remaining_qty is not None:
                final_remaining_qty = poll.remaining_qty
            if poll.avg_price is not None:
                final_avg_price = poll.avg_price
            if poll.is_filled:
                final_status = "TRADED"
                final_success = True
            elif poll.is_terminal and not poll.is_filled:
                if final_filled_qty is not None and 0 < final_filled_qty < qty:
                    final_status = "PARTIAL_FILL"
                    final_success = True
                else:
                    # REJECTED / CANCELLED / EXPIRED
                    final_status = poll.order_status or final_status
                    final_success = False
            else:
                # Still pending after polling
                final_status = "PARTIAL_FILL_PENDING" if final_filled_qty and final_filled_qty < qty else "PENDING_CONFIRMATION"
                final_success = True  # order was accepted; fill pending

    if final_success and final_filled_qty is None and final_status == "TRADED":
        final_filled_qty = qty
    partial_fill = bool(final_filled_qty is not None and 0 < final_filled_qty < qty)
    final_qty = final_filled_qty if final_filled_qty is not None and final_filled_qty > 0 else qty
    if partial_fill and final_status == "TRADED":
        final_status = "PARTIAL_FILL"
    if partial_fill:
        log_audit_event(
            "PARTIAL_ORDER_FILL",
            f"Dhan filled {final_filled_qty} of requested {qty}; NOVA adjusted local quantity.",
            severity="WARNING",
            metadata={
                "signal_id": signal.signal_id,
                "action": action,
                "order_id": result.order_id,
                "requested_qty": qty,
                "filled_qty": final_filled_qty,
                "remaining_qty": final_remaining_qty,
                "raw_status": result.status,
                "final_status": final_status,
            },
        )

    if use_super_order and final_success and final_status == "TRADED" and final_avg_price is not None:
        active_super_order_levels, super_order_post_fill_update = _sync_super_order_exit_levels(
            client=client,
            client_id=client_id,
            access_token=access_token,
            order_id=result.order_id,
            fill_price=final_avg_price,
            runtime=runtime,
            current_levels=super_order_levels,
        )

    result_payload = {
        "blocked": False,
        "success": final_success,
        "order_id": result.order_id,
        "status": final_status,
        "broker_status": final_status,
        "avg_price": final_avg_price,
        "error": result.error,
        "interpreted_error": result.interpreted_error,
        "raw_response": result.raw_response,
        "order_status_poll": order_status_poll,
        "requested_qty": qty,
        "filled_qty": final_filled_qty,
        "remaining_qty": final_remaining_qty,
        "final_qty": final_qty,
        "partial_fill": partial_fill,
        "super_order_post_fill_update": super_order_post_fill_update,
        "place_method": place_method,
        "exit_management": "DHAN_SUPER" if use_super_order else "SERVER",
        "broker_sl_price": active_super_order_levels.get("stop_loss_price") if active_super_order_levels else None,
        "broker_tp_price": active_super_order_levels.get("target_price") if active_super_order_levels else None,
        "broker_entry_reference_price": active_super_order_levels.get("reference_ltp") if active_super_order_levels else None,
        "broker_exit_levels": active_super_order_levels,
        "order_type": order_request_payload.get("orderType"),
        "payload_format": signal.payload_format,
        "normalized_action": signal.action,
        "normalized_side": signal.side,
        "normalized_qty": signal.qty,
        "normalized_symbol": signal.symbol,
        "normalized_strike": signal.strike,
        "normalized_expiry": signal.expiry,
        "normalized_option_side": signal.option_side,
        "security_id": request_payload.get("securityId"),
        "trading_symbol": request_payload.get("tradingSymbol") or signal.trading_symbol,
        "security_id_resolution": security_id_resolution,
    }
    if not final_success or final_status in {"PENDING_CONFIRMATION", "PARTIAL_FILL_PENDING", "PARTIAL_FILL", "ORDER_STATE_UNKNOWN"}:
        normalized_error = classify_failure(
            result.error or final_status or "Dhan order status could not be verified.",
            source="DHAN" if engine_mode == "live" else "PAPER_ENGINE",
            status=final_status,
            signal=signal,
            mode=explicit_engine_mode or engine_mode,
            correlation_id=str(request_payload.get("correlationId") or ""),
            raw_response=result.raw_response,
            order_sent_to_broker=not bool(result.raw_response.get("payload_validation")) if isinstance(result.raw_response, dict) else True,
            money_at_risk=final_status in {"PENDING_CONFIRMATION", "PARTIAL_FILL_PENDING", "PARTIAL_FILL", "ORDER_STATE_UNKNOWN"},
            debug_pack={
                "action": action,
                "optionSide": signal.option_side,
                "strike": signal.strike,
                "qty": qty,
                "securityId": request_payload.get("securityId"),
                "tradingSymbol": request_payload.get("tradingSymbol") or signal.trading_symbol,
                "rawBrokerStatus": result.status,
            },
        )
        result_payload["normalizedError"] = normalized_error
    result_payload["orderJourney"] = order_journey(
        signal=signal,
        execution_result=result_payload,
        normalized_error=result_payload.get("normalizedError"),
    )
    return result_payload


def _entry_position(signal: NormalizedSignal, order_result: dict[str, Any], qty: int) -> dict[str, Any]:
    entry_price = order_result.get("avg_price")
    exit_management = order_result.get("exit_management") or "SERVER"
    broker_sl_price = order_result.get("broker_sl_price")
    broker_tp_price = order_result.get("broker_tp_price")
    partial_fill = bool(order_result.get("partial_fill"))
    sr_suggestion = _sr_exit_suggestion(signal, order_result)
    if entry_price is None:
        message = "Waiting for Dhan to confirm entry fill price."
    elif partial_fill:
        message = f"Partial entry fill: tracking filled quantity {qty}."
    elif exit_management == "DHAN_SUPER":
        sl_disabled = bool(order_result.get("broker_exit_levels", {}).get("sl_disabled"))
        if sl_disabled:
            message = "Dhan Super Order TP is active; SL is effectively disabled. Backend is display-only."
        else:
            message = "Dhan Super Order SL/TP is active; backend is display-only."
    else:
        message = "Server-side option premium monitor is armed."
    return {
        "has_open_position": True,
        "strategy_code": signal.strategy_code,
        "symbol": signal.symbol,
        "instrument_type": signal.instrument_type,
        "exchange_segment": signal.exchange_segment or DEFAULT_EXCHANGE_SEGMENT,
        "security_id": order_result.get("security_id") or signal.security_id,
        "trading_symbol": order_result.get("trading_symbol") or signal.trading_symbol,
        "option_side": signal.option_side,
        "strike": signal.strike,
        "expiry": signal.expiry,
        "qty": qty,
        "requested_qty": order_result.get("requested_qty") or qty,
        "filled_qty": order_result.get("filled_qty") or qty,
        "partial_fill": partial_fill,
        "entry_order_id": order_result.get("order_id"),
        "entry_price": entry_price,
        "exit_management": exit_management,
        "broker_sl_price": broker_sl_price,
        "broker_tp_price": broker_tp_price,
        "broker_entry_reference_price": order_result.get("broker_entry_reference_price"),
        "broker_exit_levels": order_result.get("broker_exit_levels"),
        "sr_suggestion": sr_suggestion,
        "active_exit_levels": None,
        "super_order_post_fill_update": order_result.get("super_order_post_fill_update"),
        "order_type": order_result.get("order_type") or signal.order_type or DEFAULT_ORDER_TYPE,
        "product_type": signal.product_type or DEFAULT_PRODUCT_TYPE,
        "opened_at": utc_now(),
        "live_pnl": {
            "source": "dhan_order_status" if entry_price is not None else "pending_entry_fill",
            "status": "partial_entry_fill" if partial_fill else "tracking_pending" if entry_price is not None else "waiting_entry_fill",
            "entry_price": entry_price,
            "qty": qty,
            "sl_price": broker_sl_price,
            "tp_price": broker_tp_price,
            "exit_management": exit_management,
            "message": message,
            "last_checked_at": utc_now(),
        },
    }


def _entry_filled_qty(order_result: dict[str, Any], requested_qty: int) -> int:
    if order_result.get("partial_fill"):
        filled_qty = _int_value(order_result.get("filled_qty"))
        if filled_qty is not None and filled_qty > 0:
            return filled_qty
    return requested_qty


def _failed_order_status(order_result: dict[str, Any]) -> str:
    return "ORDER_STATE_UNKNOWN" if order_result.get("status") == "ORDER_STATE_UNKNOWN" else "ORDER_REJECTED"


def _apply_partial_exit_fill(position: dict[str, Any], order_result: dict[str, Any], signal: NormalizedSignal) -> dict[str, Any] | None:
    if not order_result.get("partial_fill"):
        return None
    filled_qty = _int_value(order_result.get("filled_qty")) or 0
    original_qty = _int_value(position.get("qty")) or 0
    if filled_qty <= 0 or original_qty <= 0 or filled_qty >= original_qty:
        return None

    remaining_qty = original_qty - filled_qty
    updated = dict(position)
    updated["qty"] = remaining_qty
    updated["partial_exit"] = {
        "signal_id": signal.signal_id,
        "order_id": order_result.get("order_id"),
        "requested_qty": order_result.get("requested_qty") or original_qty,
        "filled_qty": filled_qty,
        "remaining_qty": remaining_qty,
        "status": order_result.get("status"),
        "checked_at": utc_now(),
    }
    live_pnl = dict(updated.get("live_pnl") or {})
    live_pnl.update(
        {
            "qty": remaining_qty,
            "status": "partial_exit_fill",
            "message": f"Partial exit fill: {filled_qty} filled, {remaining_qty} still open.",
            "last_checked_at": utc_now(),
        }
    )
    updated["live_pnl"] = live_pnl
    set_open_position(updated)
    log_audit_event(
        "PARTIAL_EXIT_FILL",
        f"Exit partially filled; {remaining_qty} quantity remains open.",
        severity="WARNING",
        metadata={
            "signal_id": signal.signal_id,
            "order_id": order_result.get("order_id"),
            "previous_qty": original_qty,
            "filled_qty": filled_qty,
            "remaining_qty": remaining_qty,
            "order_result": order_result,
        },
    )
    return updated


def _is_opposite_option_entry(signal: NormalizedSignal, position: dict[str, Any] | None = None) -> bool:
    position = position or get_open_position()
    if signal.action != "ENTRY" or signal.side != "BUY" or not position.get("has_open_position"):
        return False
    current_side = str(position.get("option_side") or "").upper()
    incoming_side = str(signal.option_side or "").upper()
    return {current_side, incoming_side} == {"CE", "PE"}


def _exit_signal_from_position(
    position: dict[str, Any],
    trigger_signal: NormalizedSignal,
    *,
    signal_id: str | None = None,
    source: str = "opposite_option_reversal",
    exit_reason: str | None = None,
) -> NormalizedSignal:
    raw_payload = {
        "reversal": source == "opposite_option_reversal",
        "reversal_trigger_signal_id": trigger_signal.signal_id,
        "reversal_from_option_side": position.get("option_side"),
        "reversal_to_option_side": trigger_signal.option_side,
    }
    if source != "opposite_option_reversal":
        raw_payload.update(
            {
                "tracked_position_exit": True,
                "trigger_signal_id": trigger_signal.signal_id,
                "trigger_source": trigger_signal.source,
            }
        )
    if exit_reason:
        raw_payload["exit_reason"] = exit_reason

    return NormalizedSignal(
        payload_format="NOVA",
        secret=trigger_signal.secret,
        signal_id=signal_id or f"{trigger_signal.signal_id}-REVERSAL-EXIT",
        strategy_code=position.get("strategy_code") or trigger_signal.strategy_code,
        action="EXIT",
        side="SELL",
        symbol=position.get("symbol") or trigger_signal.symbol,
        instrument_type=position.get("instrument_type") or trigger_signal.instrument_type,
        exchange_segment=position.get("exchange_segment") or trigger_signal.exchange_segment or DEFAULT_EXCHANGE_SEGMENT,
        security_id=position.get("security_id"),
        trading_symbol=position.get("trading_symbol"),
        option_side=position.get("option_side"),
        strike=position.get("strike"),
        expiry=position.get("expiry"),
        qty=int(position.get("qty") or trigger_signal.qty),
        order_type=position.get("order_type") or DEFAULT_ORDER_TYPE,
        product_type=position.get("product_type") or DEFAULT_PRODUCT_TYPE,
        source=source,
        raw_payload=raw_payload,
    )


def route_reversal_signal(
    signal: NormalizedSignal,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # C11 — Snapshot once on first entry. Reversal does TWO Dhan order
    # placements (exit-existing then enter-new); both must use the same
    # SL%/TP% snapshot.
    if runtime is None:
        runtime = get_runtime_settings()

    open_position = get_open_position()
    update_app_state(
        state="REVERSAL_SIGNAL_RECEIVED",
        last_signal_id=signal.signal_id,
        last_alert_at=utc_now(),
        last_message=(
            f"Opposite {signal.option_side} entry received while "
            f"{open_position.get('option_side')} is open. Reversal started."
        ),
    )

    reversal_decision = evaluate_reversal_entry(signal, runtime=runtime)
    if not reversal_decision.allowed:
        return _blocked("BLOCKED", reversal_decision.reason, signal)

    exit_signal = _exit_signal_from_position(open_position, signal)
    exit_decision = evaluate_exit(exit_signal, runtime=runtime)
    if not exit_decision.allowed:
        return _blocked("BLOCKED", exit_decision.reason, exit_signal)

    update_app_state(
        state="REVERSAL_EXIT_SENDING",
        last_message=f"Exiting {open_position.get('option_side')} before entering {signal.option_side}.",
    )
    exit_result = _place_order(exit_signal, exit_decision.final_qty, "EXIT", runtime=runtime)
    if exit_result.get("blocked"):
        return {
            **exit_result,
            "status": "REVERSAL_EXIT_BLOCKED",
            "reversal": {
                "from_option_side": open_position.get("option_side"),
                "to_option_side": signal.option_side,
            },
        }

    if not exit_result.get("success"):
        reason = exit_result.get("error") or "Reversal exit order failed."
        log_error_event("REVERSAL_EXIT_FAILED", reason, metadata={"signal_id": signal.signal_id, "exit_result": exit_result})
        update_app_state(state="ERROR", last_message=reason)
        return {
            **exit_result,
            "blocked": False,
            "success": False,
            "status": "REVERSAL_EXIT_FAILED",
            "reason": reason,
            "reversal": {
                "from_option_side": open_position.get("option_side"),
                "to_option_side": signal.option_side,
                "exit_result": exit_result,
            },
        }

    if exit_result.get("status") != "TRADED":
        reason = "Reversal exit accepted but not confirmed TRADED; opposite entry was not sent."
        _apply_partial_exit_fill(open_position, exit_result, exit_signal)
        current = dict(get_open_position())
        if current.get("has_open_position"):
            current["reversal_exit"] = {
                "status": exit_result.get("status"),
                "order_id": exit_result.get("order_id"),
                "checked_at": utc_now(),
                "message": reason,
            }
            set_open_position(current)
        log_audit_event(
            "REVERSAL_EXIT_PENDING",
            reason,
            severity="WARNING",
            metadata={"signal_id": signal.signal_id, "exit_result": exit_result},
        )
        update_app_state(state="WAITING_EXIT", last_message=reason)
        return {
            **exit_result,
            "blocked": False,
            "success": False,
            "status": "REVERSAL_EXIT_PENDING",
            "reason": reason,
            "reversal": {
                "from_option_side": open_position.get("option_side"),
                "to_option_side": signal.option_side,
                "exit_result": exit_result,
            },
        }

    super_order_leg_cancellations = _cancel_super_order_exit_legs(open_position)
    clear_open_position()
    refresh_wallet_snapshot(force=True, log_event=True)

    update_app_state(
        state="REVERSAL_ENTRY_SENDING",
        last_message=f"{open_position.get('option_side')} exited. Entering {signal.option_side}.",
    )
    entry_result = _place_order(
        signal,
        reversal_decision.final_qty,
        "ENTRY",
        skip_entry_preflight=True,
        runtime=runtime,
    )
    if entry_result.get("blocked"):
        return {
            **entry_result,
            "status": "REVERSAL_ENTRY_BLOCKED",
            "reversal": {
                "from_option_side": open_position.get("option_side"),
                "to_option_side": signal.option_side,
                "exit_result": exit_result,
                "super_order_leg_cancellations": super_order_leg_cancellations,
            },
        }

    if entry_result.get("success"):
        refresh_wallet_snapshot(force=True, log_event=True)
        position = _entry_position(signal, entry_result, _entry_filled_qty(entry_result, reversal_decision.final_qty))
        entry_result["sr_suggestion"] = position.get("sr_suggestion")
        entry_result["active_exit_levels"] = position.get("active_exit_levels")
        set_open_position(position)
        record_entry_trade(signal.signal_id)
        update_app_state(
            state="WAITING_EXIT",
            last_message=f"Reversal complete: entered {signal.option_side} in {get_engine_mode().upper()} mode",
        )
        msg = f"Exited {open_position.get('option_side')} and entered {signal.option_side}."
        levels = entry_result.get("broker_exit_levels")
        if levels and levels.get("sl_disabled"):
            msg += " SL effectively disabled."
        log_audit_event(
            "REVERSAL_ORDER_PLACED",
            msg,
            metadata={
                "signal_id": signal.signal_id,
                "exit_order_id": exit_result.get("order_id"),
                "entry_order_id": entry_result.get("order_id"),
                "super_order_leg_cancellations": super_order_leg_cancellations,
            },
        )
        entry_status = "PARTIAL_ENTRY_FILLED" if entry_result.get("partial_fill") else "REVERSAL_ORDER_PLACED"
        return {
            **entry_result,
            "status": entry_status,
            "reversal": {
                "from_option_side": open_position.get("option_side"),
                "to_option_side": signal.option_side,
                "exit_result": exit_result,
                "entry_result": entry_result,
                "super_order_leg_cancellations": super_order_leg_cancellations,
            },
        }

    reason = entry_result.get("error") or "Reversal entry order failed after exit completed."
    log_error_event("REVERSAL_ENTRY_FAILED", reason, metadata={"signal_id": signal.signal_id, "entry_result": entry_result})
    update_app_state(state="ERROR", last_message=reason)
    return {
        **entry_result,
        "blocked": False,
        "success": False,
        "status": "REVERSAL_ENTRY_FAILED",
        "reason": reason,
        "reversal": {
            "from_option_side": open_position.get("option_side"),
            "to_option_side": signal.option_side,
            "exit_result": exit_result,
            "entry_result": entry_result,
            "super_order_leg_cancellations": super_order_leg_cancellations,
        },
    }


def _route_side_filter_exit_only(signal: NormalizedSignal, runtime: dict[str, Any]) -> dict[str, Any]:
    open_position = get_open_position()
    exit_signal = _exit_signal_from_position(
        open_position,
        signal,
        signal_id=f"{signal.signal_id}-SIDE-FILTER-EXIT",
        source="side_filter_exit_only",
        exit_reason="SIDE_FILTER_EXIT_ONLY",
    )
    result = route_exit_signal(exit_signal, runtime=runtime)
    return {
        **result,
        "side_filter_exit_only": True,
        "blocked_entry_option_side": signal.option_side,
        "status": (
            "SIDE_FILTER_EXIT_ONLY"
            if result.get("success")
            else result.get("status") or "SIDE_FILTER_EXIT_FAILED"
        ),
    }


def _entry_request_block(signal: NormalizedSignal, runtime: dict[str, Any]) -> dict[str, Any] | None:
    if bool(runtime.get("allow_entry", True)):
        return None
    return _blocked(
        "ENTRY_REQUEST_BLOCKED",
        "Entry request ignored: Block Entry Requests is ON.",
        signal,
        audit_event="ENTRY_REQUEST_BLOCKED",
        result_extra={
            "success": False,
            "block_code": "ENTRY_REQUEST_BLOCKED",
        },
    )


def route_entry_signal(
    signal: NormalizedSignal,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # C11 — Snapshot once on first entry to the pipeline. All downstream
    # decisions (risk gate, broker payload build, SL/TP math) see the same
    # values, even if a user changes settings mid-flight on another tab.
    if runtime is None:
        runtime = get_runtime_settings()

    update_app_state(
        state="ENTRY_SIGNAL_RECEIVED",
        last_signal_id=signal.signal_id,
        last_alert_at=utc_now(),
        last_message=f"Entry alert received for {signal.trading_symbol or signal.symbol}",
    )
    entry_block = _entry_request_block(signal, runtime)
    if entry_block:
        return entry_block

    broker_reconcile = _reconcile_tracked_position_before_entry(signal)
    if broker_reconcile and not broker_reconcile.allowed:
        if _is_opposite_option_entry(signal):
            if not option_side_is_allowed(signal, runtime):
                return _route_side_filter_exit_only(signal, runtime)
            return route_reversal_signal(signal, runtime=runtime)
        return _blocked("BLOCKED", broker_reconcile.reason, signal)

    if _is_opposite_option_entry(signal):
        if not option_side_is_allowed(signal, runtime):
            return _route_side_filter_exit_only(signal, runtime)
        return route_reversal_signal(signal, runtime=runtime)

    decision = evaluate_entry(signal, runtime=runtime)
    if not decision.allowed:
        return _blocked("BLOCKED", decision.reason, signal)

    update_app_state(state="ENTRY_RISK_CHECK", last_message="Entry risk checks passed.")
    update_app_state(state="ENTRY_ORDER_SENDING", last_message="Sending entry order to Dhan.")
    order_result = _place_order(signal, decision.final_qty, "ENTRY", runtime=runtime)
    if order_result.get("blocked"):
        return order_result

    if order_result.get("success"):
        refresh_wallet_snapshot(force=True, log_event=True)
        position = _entry_position(signal, order_result, _entry_filled_qty(order_result, decision.final_qty))
        order_result["sr_suggestion"] = position.get("sr_suggestion")
        order_result["active_exit_levels"] = position.get("active_exit_levels")
        set_open_position(position)
        record_entry_trade(signal.signal_id)
        update_app_state(
            state="WAITING_EXIT",
            last_message=f"Entry order placed in {get_engine_mode().upper()} mode",
        )
        msg = f"Entry order placed in {get_engine_mode().upper()} mode"
        levels = order_result.get("broker_exit_levels")
        if levels and levels.get("sl_disabled"):
            msg += ". SL effectively disabled."
        log_audit_event(
            "ORDER_PLACED",
            msg,
            metadata={
                "signal_id": signal.signal_id,
                "order_id": order_result.get("order_id"),
                "payload_format": signal.payload_format,
            },
        )
        entry_status = "PARTIAL_ENTRY_FILLED" if order_result.get("partial_fill") else "ORDER_PLACED"
        return {**order_result, "status": entry_status}

    reason = order_result.get("error") or "Dhan order request failed"
    log_error_event("ENTRY_ORDER_FAILED", reason, metadata={"signal_id": signal.signal_id})
    update_app_state(state="ERROR", last_message=reason)
    return {**order_result, "blocked": False, "status": _failed_order_status(order_result), "reason": reason}


def route_exit_signal(
    signal: NormalizedSignal,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # C11 — Snapshot once on first entry to the pipeline.
    if runtime is None:
        runtime = get_runtime_settings()

    update_app_state(
        state="EXIT_SIGNAL_RECEIVED",
        last_signal_id=signal.signal_id,
        last_alert_at=utc_now(),
        last_message=f"Exit alert received for {signal.trading_symbol or signal.symbol}",
    )
    decision: RiskDecision = evaluate_exit(signal, runtime=runtime)
    if not decision.allowed:
        return _blocked("BLOCKED", decision.reason, signal)

    open_position = get_open_position()
    raw_payload = signal.raw_payload if isinstance(signal.raw_payload, dict) else {}
    exit_signal = _exit_signal_from_position(
        open_position,
        signal,
        signal_id=signal.signal_id,
        source=signal.source or "tracked_position_exit",
        exit_reason=raw_payload.get("exit_reason"),
    )
    qty = decision.final_qty
    update_app_state(state="EXIT_ORDER_SENDING", last_message="Exit risk checks passed. Sending exit order to Dhan.")
    order_result = _place_order(exit_signal, qty, "EXIT", runtime=runtime)
    if order_result.get("blocked"):
        return order_result

    if order_result.get("success"):
        partial_position = _apply_partial_exit_fill(open_position, order_result, exit_signal)
        if partial_position is not None:
            refresh_wallet_snapshot(force=True, log_event=True)
            update_app_state(
                state="WAITING_EXIT",
                last_message=(
                    f"Partial exit fill: {order_result.get('filled_qty')} filled, "
                    f"{partial_position.get('qty')} still open."
                ),
            )
            return {
                **order_result,
                "status": "PARTIAL_EXIT_FILLED",
                "remaining_open_position": partial_position,
            }

        super_order_leg_cancellations = _cancel_super_order_exit_legs(open_position)
        clear_open_position()
        refresh_wallet_snapshot(force=True, log_event=True)
        update_app_state(
            state="WAITING_ENTRY",
            last_message=f"Exit order placed in {get_engine_mode().upper()} mode. Waiting for next entry.",
        )
        log_audit_event(
            "EXIT_ORDER_PLACED",
            f"Exit order placed in {get_engine_mode().upper()} mode",
            metadata={
                "signal_id": signal.signal_id,
                "order_id": order_result.get("order_id"),
                "closed_security_id": open_position.get("security_id"),
                "super_order_leg_cancellations": super_order_leg_cancellations,
                "payload_format": signal.payload_format,
            },
        )
        return {**order_result, "status": "ORDER_PLACED", "super_order_leg_cancellations": super_order_leg_cancellations}

    reason = order_result.get("error") or "Dhan exit order request failed"
    log_error_event("EXIT_ORDER_FAILED", reason, metadata={"signal_id": signal.signal_id})
    update_app_state(state="ERROR", last_message=reason)
    return {**order_result, "blocked": False, "status": _failed_order_status(order_result), "reason": reason}


def route_signal(signal: NormalizedSignal) -> dict[str, Any]:
    # C11 — Settings snapshot per signal. Capture runtime once at the
    # absolute earliest point in the pipeline so two near-simultaneous
    # signals can't end up using different SL%/TP%/max_qty/etc. just
    # because a user clicked Save between them.
    runtime = get_runtime_settings()
    update_app_state(
        last_signal={
            "signalId": signal.signal_id,
            "action": signal.action,
            "side": signal.side,
            "optionSide": signal.option_side,
            "strike": signal.strike,
            "tradingSymbol": signal.trading_symbol,
            "source": signal.source,
            "niftyPrice": _raw_payload_number(signal, "nifty_price", "nifty", "spot", "niftySpot"),
            "dayChangePct": _raw_payload_number(signal, "day_change_pct", "dayChangePct", "change_pct"),
            "receivedAt": utc_now(),
        }
    )
    token_block = _dhan_token_signal_preflight(signal)
    if token_block:
        publish_chat_result_from_sync(signal, token_block)
        return token_block
    if signal.action == "ENTRY":
        result = route_entry_signal(signal, runtime=runtime)
    else:
        result = route_exit_signal(signal, runtime=runtime)
    publish_chat_result_from_sync(signal, result)
    return result
