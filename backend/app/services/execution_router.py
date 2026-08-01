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
from app.services import order_idempotency, position_operations, risk_configuration
from app.services.audit_logger import log_audit_event, log_error_event, log_order_event
from app.services.chat_event_publisher import publish_chat_result_from_sync
from app.services.credential_vault import dhan_token_age_metadata, get_dhan_credentials
from app.services.dhan_client import (
    DHAN_OPEN_ORDER_STATUSES,
    DHAN_TERMINAL_STATUSES,
    DhanLtpResult,
    RealDhanClient,
    get_broker_client,
)
from app.services.execution_context import (
    LiveEgressGuardError,
    current_execution_user,
    require_verified_live_egress,
)
from app.services.normalized_errors import classify_failure, order_journey
from app.services.quote_service import get_quote_snapshot
from app.services.risk_manager import (
    RiskDecision,
    _market_is_open,
    evaluate_entry,
    evaluate_exit,
    evaluate_reversal_entry,
    option_side_is_allowed,
)
from app.services.security_id_resolver import resolve_security_id
from app.services.signal_parser import origin_from_source
from app.services.state_store import (
    claim_exit_operation,
    clear_open_position,
    complete_exit_operation,
    ensure_open_position_identity,
    get_engine_mode,
    get_open_position,
    get_runtime_settings,
    patch_open_position_cas,
    record_entry_trade,
    release_exit_operation,
    set_open_position,
    stamp_new_open_position,
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


def _authoritative_ltp_result(*, exchange_segment: str, security_id: str) -> DhanLtpResult:
    quote = get_quote_snapshot(
        exchange_segment=exchange_segment,
        security_id=security_id,
        max_age_seconds=2.0,
        allow_rest_fallback=True,
    )
    return DhanLtpResult(
        success=quote.get("ltp") is not None and not bool(quote.get("stale")),
        message=str(quote.get("message") or "Quote unavailable."),
        ltp=quote.get("ltp"),
        exchange_segment=exchange_segment,
        security_id=security_id,
        status_code=429 if quote.get("status") == "RATE_LIMITED" else None,
        raw_response={"source": quote.get("source"), "status": quote.get("status")},
        error=quote.get("error"),
    )


def _normalized_log_fields(signal: NormalizedSignal) -> dict[str, Any]:
    raw_payload = signal.raw_payload if isinstance(signal.raw_payload, dict) else {}
    return {
        "payload_format": signal.payload_format,
        "normalized_action": signal.action,
        "normalized_side": signal.side,
        "normalized_qty": signal.qty,
        "normalized_symbol": signal.symbol,
        "normalized_strike": signal.strike,
        "normalized_expiry": signal.expiry,
        "normalized_option_side": signal.option_side,
        "source": signal.source,
        "nifty_price": _raw_payload_number(signal, "nifty_price", "nifty", "spot", "niftySpot"),
        "nifty_sl_level": _raw_payload_number(signal, "sl_level", "stop_level", "stopLoss"),
        "nifty_target_level": _raw_payload_number(signal, "tp_level", "target_level", "takeProfit"),
        "exit_kind": str(raw_payload.get("exit_reason") or raw_payload.get("exitKind") or "").upper() or None,
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

    QTY_MODE=ABSOLUTE: TradingView qty is sent directly as Dhan quantity.
    Lot conversion requires QTY_MODE=LOTS (phase 2).

    Required Dhan v2 fields per POST /orders:
      dhanClientId, correlationId (optional but recommended), transactionType,
      exchangeSegment, productType, orderType, validity, securityId,
      quantity, disclosedQuantity, price, triggerPrice, afterMarketOrder.
    """
    resolution = resolve_security_id(signal)
    creds = get_dhan_credentials()
    shared_paper_client_id = (settings.DHAN_SHARED_CLIENT_ID or "").strip()
    if get_engine_mode(legacy_fallback=False) == "paper" and shared_paper_client_id:
        client_id = shared_paper_client_id
    else:
        client_id = creds.client_id if creds else ""
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


def _is_manual_order_signal(signal: NormalizedSignal) -> bool:
    return bool(isinstance(signal.raw_payload, dict) and signal.raw_payload.get("manual_order") is True)


def _live_order_scope_and_key(signal: NormalizedSignal, action: str, place_method: str) -> tuple[str, str | None]:
    if _is_manual_order_signal(signal):
        raw_payload = signal.raw_payload if isinstance(signal.raw_payload, dict) else {}
        key = str(raw_payload.get("idempotency_key") or "").strip()
        return "manual_order", key or None
    source = str(signal.source or signal.payload_format or "signal").strip().lower() or "signal"
    key = f"{signal.signal_id}:{action}:{place_method}"
    return f"{source}_order", key


def _live_order_intent_payload(
    *,
    signal: NormalizedSignal,
    qty: int,
    action: str,
    place_method: str,
    request_payload: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "action": action,
        "side": signal.side,
        "source": signal.source,
        "strategy_code": signal.strategy_code,
        "symbol": signal.symbol,
        "instrument_type": signal.instrument_type,
        "exchange_segment": request_payload.get("exchangeSegment") or signal.exchange_segment,
        "product_type": request_payload.get("productType") or signal.product_type,
        "order_type": request_payload.get("orderType") or signal.order_type,
        "transaction_type": request_payload.get("transactionType"),
        "security_id": request_payload.get("securityId") or signal.security_id,
        "trading_symbol": request_payload.get("tradingSymbol") or signal.trading_symbol,
        "option_side": signal.option_side,
        "strike": signal.strike,
        "expiry": signal.expiry,
        "qty": qty,
        "place_method": place_method,
    }
    if not _is_manual_order_signal(signal):
        payload["signal_id"] = signal.signal_id
    return payload


def _claim_live_order_intent_or_result(
    *,
    signal: NormalizedSignal,
    qty: int,
    action: str,
    place_method: str,
    request_payload: dict[str, Any],
    security_id_resolution: dict[str, Any],
    runtime: dict[str, Any],
) -> order_idempotency.OrderIntentClaim | dict[str, Any] | None:
    if get_engine_mode() != "live":
        return None

    user = current_execution_user()
    if user is None or user.is_dev:
        if settings.is_production:
            return _blocked(
                "BLOCKED",
                "Live order idempotency requires authenticated user context.",
                signal,
                security_id_resolution=security_id_resolution,
                security_id=request_payload.get("securityId"),
                trading_symbol=request_payload.get("tradingSymbol"),
                result_extra={"block_code": "LIVE_ORDER_IDEMPOTENCY_UNAVAILABLE"},
            )
        return None

    scope, key = _live_order_scope_and_key(signal, action, place_method)
    if not key:
        if settings.is_production and _is_manual_order_signal(signal):
            return _blocked(
                "BLOCKED",
                "Manual live order blocked: Idempotency-Key header is required.",
                signal,
                security_id_resolution=security_id_resolution,
                security_id=request_payload.get("securityId"),
                trading_symbol=request_payload.get("tradingSymbol"),
                result_extra={"block_code": "IDEMPOTENCY_KEY_REQUIRED"},
            )
        return None

    identity_payload = _live_order_intent_payload(
        signal=signal,
        qty=qty,
        action=action,
        place_method=place_method,
        request_payload=request_payload,
    )
    payload_hash = order_idempotency.stable_payload_hash(identity_payload)
    try:
        claim = order_idempotency.claim_live_order_intent(
            user_id=user.id,
            scope=scope,
            idempotency_key=key,
            payload_hash=payload_hash,
            signal_id=signal.signal_id,
            action=action,
            side=signal.side,
            symbol=signal.symbol,
            broker_correlation_id=str(request_payload.get("correlationId") or "") or None,
            risk_configuration_version_id=runtime.get("risk_configuration_version_id"),
            metadata={
                "place_method": place_method,
                "source": signal.source,
                "payload_format": signal.payload_format,
                "identity_hash": payload_hash,
                "risk_configuration_version": runtime.get("risk_configuration_version"),
            },
        )
    except Exception:  # noqa: BLE001 - fail closed on any idempotency backend failure
        if settings.is_production:
            return _blocked(
                "BLOCKED",
                "Live order idempotency store unavailable; refusing to submit.",
                signal,
                security_id_resolution=security_id_resolution,
                security_id=request_payload.get("securityId"),
                trading_symbol=request_payload.get("tradingSymbol"),
                result_extra={"block_code": "LIVE_ORDER_IDEMPOTENCY_UNAVAILABLE"},
            )
        return None

    if claim.status == "replay" and claim.result_summary:
        replay = dict(claim.result_summary)
        replay["idempotent_replay"] = True
        replay["order_intent_id"] = claim.intent_id
        return replay
    if claim.status == "conflict":
        return _blocked(
            "BLOCKED",
            claim.message or "Idempotency key was already used for a different order payload.",
            signal,
            security_id_resolution=security_id_resolution,
            security_id=request_payload.get("securityId"),
            trading_symbol=request_payload.get("tradingSymbol"),
            result_extra={
                "block_code": "IDEMPOTENCY_KEY_CONFLICT",
                "order_intent_id": claim.intent_id,
            },
        )
    if claim.status == "in_progress":
        return _blocked(
            "BLOCKED",
            claim.message or "Order intent is already in progress. Reconcile broker state before retrying.",
            signal,
            security_id_resolution=security_id_resolution,
            security_id=request_payload.get("securityId"),
            trading_symbol=request_payload.get("tradingSymbol"),
            result_extra={
                "block_code": "ORDER_INTENT_IN_PROGRESS",
                "order_intent_id": claim.intent_id,
            },
        )
    return claim


def _lookup_existing_live_order_intent_result(
    *,
    signal: NormalizedSignal,
    qty: int,
    action: str,
    place_method: str,
) -> dict[str, Any] | None:
    if get_engine_mode() != "live":
        return None
    user = current_execution_user()
    if user is None or user.is_dev:
        return None
    scope, key = _live_order_scope_and_key(signal, action, place_method)
    if not key:
        return None
    try:
        request_payload = _build_dhan_payload(signal, qty, action)
        identity_payload = _live_order_intent_payload(
            signal=signal,
            qty=qty,
            action=action,
            place_method=place_method,
            request_payload=request_payload,
        )
        lookup = order_idempotency.lookup_live_order_intent(
            user_id=user.id,
            scope=scope,
            idempotency_key=key,
            payload_hash=order_idempotency.stable_payload_hash(identity_payload),
        )
    except Exception:  # noqa: BLE001 - lookup failure yields no reusable result
        return None
    if lookup.status == "missing":
        return None
    if lookup.status == "replay" and lookup.result_summary:
        replay = dict(lookup.result_summary)
        replay["idempotent_replay"] = True
        replay["order_intent_id"] = lookup.intent_id
        return replay
    if lookup.status == "conflict":
        return _blocked(
            "BLOCKED",
            lookup.message or "Idempotency key was already used for a different order payload.",
            signal,
            result_extra={
                "block_code": "IDEMPOTENCY_KEY_CONFLICT",
                "order_intent_id": lookup.intent_id,
            },
        )
    if lookup.status == "in_progress":
        return _blocked(
            "BLOCKED",
            lookup.message or "Order intent is already in progress. Reconcile broker state before retrying.",
            signal,
            result_extra={
                "block_code": "ORDER_INTENT_IN_PROGRESS",
                "order_intent_id": lookup.intent_id,
            },
        )
    return None


def _runtime_float(runtime: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(runtime.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _round_option_tick(price: float) -> float:
    return round(max(round(price / 0.05) * 0.05, 0.05), 2)


def _signal_exit_overrides(signal: Any) -> dict[str, float]:
    """Per-signal TP/SL overrides (e.g. typed into the manual order panel).

    Manual entries carry targetProfitPct/stopLossPct in raw_payload; before this
    they were accepted by the API but silently ignored at placement time.
    """
    raw = getattr(signal, "raw_payload", None)
    if not isinstance(raw, dict):
        return {}
    overrides: dict[str, float] = {}
    tp = _number(raw.get("targetProfitPct"))
    if tp is not None and tp > 0:
        overrides["tp_percent"] = float(tp)
    sl = _number(raw.get("stopLossPct"))
    if sl is not None and sl > 0:
        overrides["sl_percent"] = float(sl)
    return overrides


def _broker_exit_levels(
    reference_price: float,
    runtime: dict[str, Any],
    overrides: dict[str, float] | None = None,
) -> tuple[float, float, float, float]:
    overrides = overrides or {}
    sl_percent = float(overrides.get("sl_percent") or _runtime_float(runtime, "option_sl_percent", 10.0))
    tp_percent = float(overrides.get("tp_percent") or _runtime_float(runtime, "option_tp_percent", 20.0))

    # SL disable — Dhan Super Order schema requires a stopLossPrice, so we
    # plant a floor that normal intraday price action will never touch
    # (max of Rs.0.10 and 0.1% of entry). The position is then exited by the
    # opposite Supertrend reversal, the TP leg, or the 15:15 IST EOD task.
    # An explicit per-signal stopLossPct override takes precedence over the
    # global disable flag: the user asked for a real SL on this order.
    disable_sl = bool(runtime.get("option_disable_sl", True)) and "sl_percent" not in overrides
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
    overrides: dict[str, float] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sl_percent, tp_percent, stop_loss_price, target_price = _broker_exit_levels(reference_ltp, runtime, overrides)
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
        "sl_disabled": bool(runtime.get("option_disable_sl", True)) and "sl_percent" not in (overrides or {}),
        "overrides": overrides or {},
    }
    return payload, levels


def _is_super_order_level_rejection(result: Any) -> bool:
    """True when Dhan rejected a Super Order because the TP/SL leg prices are on
    the wrong side of the current market price (a transient, race-driven error,
    e.g. 'Profit Price Should be greater than Order price')."""
    parts = [
        getattr(result, "error", None),
        getattr(result, "status", None),
        getattr(result, "raw_response", None),
        getattr(result, "interpreted_error", None),
    ]
    text = " ".join(str(part) for part in parts if part).lower()
    return any(
        term in text
        for term in (
            "profit price should be",
            "profit price must be",
            "stop loss price should be",
            "stoploss price should be",
            "target price should be",
        )
    )


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
        try:
            require_verified_live_egress()
        except LiveEgressGuardError as exc:
            return {"attempted": False, "reason": str(exc)}
        client = _live_broker_client()
        client_id = creds.client_id
        access_token = creds.access_token
    else:
        client = get_broker_client(engine_mode)
        client_id = creds.client_id if creds else ""
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


def apply_manual_super_order_exit_levels(
    position: dict[str, Any],
    *,
    stop_loss_price: float,
    target_price: float,
) -> dict[str, Any]:
    """Apply user-entered SL/TP to an open position.

    Paper (or non-Super) positions are handled locally by the caller and need no
    broker call. For a LIVE Dhan Super Order, this modifies the TARGET_LEG and
    STOP_LOSS_LEG using the exact same tested ``modify_super_order`` path the
    engine already uses to sync exit levels after fill - routed through the
    user's verified static-IP egress. Never raises; returns an {ok,...} dict.
    """
    if str(position.get("exit_management") or "").upper() != "DHAN_SUPER":
        return {"ok": True, "reason": "not_super_order"}

    engine_mode = get_engine_mode()
    if engine_mode != "live":
        # Paper Super positions keep SL/TP locally; no real broker modify needed.
        return {"ok": True, "reason": "paper_local"}

    order_id = str(position.get("entry_order_id") or "").strip()
    if not order_id:
        return {"ok": False, "reason": "missing_super_order_id", "message": "No Dhan Super Order id on this position."}

    creds = get_dhan_credentials()
    if not creds:
        return {"ok": False, "reason": "missing_dhan_credentials", "message": "Dhan credentials are not connected."}
    try:
        require_verified_live_egress()
    except LiveEgressGuardError as exc:
        return {"ok": False, "reason": "egress_not_verified", "message": str(exc)}

    client = _live_broker_client()
    client_id = creds.client_id
    access_token = creds.access_token
    sl = round(float(stop_loss_price), 2)
    tp = round(float(target_price), 2)

    target_result = client.modify_super_order(
        client_id=client_id,
        access_token=access_token,
        order_id=order_id,
        payload={"dhanClientId": client_id, "orderId": order_id, "legName": "TARGET_LEG", "targetPrice": tp},
    )
    stop_result = client.modify_super_order(
        client_id=client_id,
        access_token=access_token,
        order_id=order_id,
        payload={"dhanClientId": client_id, "orderId": order_id, "legName": "STOP_LOSS_LEG", "stopLossPrice": sl, "trailingJump": 0},
    )

    target_snap = _order_result_snapshot(target_result)
    stop_snap = _order_result_snapshot(stop_result)
    ok = bool(target_snap.get("success")) and bool(stop_snap.get("success"))
    summary: dict[str, Any] = {
        "ok": ok,
        "order_id": order_id,
        "stop_loss_price": sl,
        "target_price": tp,
        "target_result": target_snap,
        "stop_result": stop_snap,
    }
    if not ok:
        previous = (
            position.get("active_exit_levels")
            if isinstance(position.get("active_exit_levels"), dict)
            else {}
        )
        live_pnl = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
        previous_target = _number(previous.get("targetPrice") or live_pnl.get("tp_price"))
        previous_stop = _number(previous.get("stopLossPrice") or live_pnl.get("sl_price"))
        rollback_attempted = False
        rollback_ok = True
        rollback_results: dict[str, Any] = {}
        if target_snap.get("success"):
            rollback_attempted = True
            if previous_target is None:
                rollback_ok = False
            else:
                rollback = client.modify_super_order(
                    client_id=client_id,
                    access_token=access_token,
                    order_id=order_id,
                    payload={
                        "dhanClientId": client_id,
                        "orderId": order_id,
                        "legName": "TARGET_LEG",
                        "targetPrice": round(previous_target, 2),
                    },
                )
                rollback_results["target"] = _order_result_snapshot(rollback)
                rollback_ok = rollback_ok and bool(rollback_results["target"].get("success"))
        if stop_snap.get("success"):
            rollback_attempted = True
            if previous_stop is None:
                rollback_ok = False
            else:
                rollback = client.modify_super_order(
                    client_id=client_id,
                    access_token=access_token,
                    order_id=order_id,
                    payload={
                        "dhanClientId": client_id,
                        "orderId": order_id,
                        "legName": "STOP_LOSS_LEG",
                        "stopLossPrice": round(previous_stop, 2),
                        "trailingJump": 0,
                    },
                )
                rollback_results["stop"] = _order_result_snapshot(rollback)
                rollback_ok = rollback_ok and bool(rollback_results["stop"].get("success"))
        summary["rollback_attempted"] = rollback_attempted
        summary["rollback_confirmed"] = bool(rollback_attempted and rollback_ok)
        summary["rollback_results"] = rollback_results
        summary["reconciliation_required"] = bool(rollback_attempted and not rollback_ok)
        summary["message"] = (
            "Dhan accepted only part of the protection update and rollback could not be confirmed."
            if summary["reconciliation_required"]
            else target_snap.get("error")
            or stop_snap.get("error")
            or "Dhan rejected the SL/TP modification; previous confirmed protection remains active."
        )
    log_order_event(
        {
            "event": "DHAN_SUPER_ORDER_MANUAL_LEVEL_UPDATE",
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


def _live_egress_signal_preflight(signal: NormalizedSignal) -> dict[str, Any] | None:
    if get_engine_mode() != "live":
        return None
    try:
        require_verified_live_egress()
    except LiveEgressGuardError as exc:
        reason = str(exc)
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
            audit_event="LIVE_EGRESS_UNVERIFIED_SIGNAL_BLOCK",
            result_extra={
                "success": False,
                "block_code": "LIVE_EGRESS_NOT_VERIFIED",
            },
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

    egress_block = _live_egress_signal_preflight(signal)
    if egress_block:
        return RiskDecision(
            False,
            str(
                egress_block.get("reason")
                or egress_block.get("message")
                or "Live Dhan orders require a verified Nova Static IP."
            ),
        )

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
        position_operations.on_position_closed(
            signal, None,
            source=position_operations.BROKER_RECONCILIATION,
            reason="broker_flat_before_entry",
        )
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
        egress_block = _live_egress_signal_preflight(signal)
        if egress_block:
            return egress_block
        client = _live_broker_client()
        client_id = creds.client_id
        access_token = creds.access_token
    else:
        paper_data_creds = None
        shared_data_status: dict[str, Any] | None = None
        if explicit_engine_mode == "paper":
            from app.services.shared_market_data import (
                get_shared_market_credentials,
                shared_market_data_configured,
                shared_market_data_status,
            )

            paper_data_creds = get_shared_market_credentials()
            if shared_market_data_configured():
                shared_data_status = shared_market_data_status()

        if explicit_engine_mode == "paper" and not paper_data_creds:
            shared_configured = bool(shared_data_status and shared_data_status.get("configured"))
            reason = (
                "Paper mode blocked: shared market-data token unavailable. "
                "Check DHAN_SHARED_* TOTP setup and Dhan auth status."
                if shared_configured
                else "Paper mode blocked: shared market-data credentials are not configured. Set DHAN_SHARED_CLIENT_ID, DHAN_SHARED_PIN, and DHAN_SHARED_TOTP_SECRET for free paper mode."
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
                    else "SHARED_MARKET_DATA_NOT_CONFIGURED",
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
        if explicit_engine_mode == "paper":
            client_id = paper_data_creds.client_id
            access_token = paper_data_creds.access_token
        else:
            client_id = creds.client_id if creds else ""
            access_token = creds.access_token if creds else ""

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
        ltp_result = _authoritative_ltp_result(
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
            overrides=_signal_exit_overrides(signal),
        )
        place_method = "super_orders"

    order_intent_id: str | None = None
    if engine_mode == "live":
        intent_claim = _claim_live_order_intent_or_result(
            signal=signal,
            qty=qty,
            action=action,
            place_method=place_method,
            request_payload=request_payload,
            security_id_resolution=security_id_resolution,
            runtime=runtime,
        )
        if isinstance(intent_claim, dict):
            return intent_claim
        if intent_claim is not None:
            order_intent_id = intent_claim.intent_id

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

    if order_intent_id:
        try:
            order_idempotency.mark_order_intent_submitted(
                order_intent_id,
                broker_correlation_id=str(request_payload.get("correlationId") or "") or None,
            )
        except Exception:  # noqa: BLE001 - durable intent uncertainty must fail closed
            return _blocked(
                "BLOCKED",
                "Live order idempotency store unavailable; refusing to submit.",
                signal,
                security_id_resolution=security_id_resolution,
                security_id=request_payload.get("securityId"),
                trading_symbol=request_payload.get("tradingSymbol") or signal.trading_symbol,
                result_extra={
                    "block_code": "LIVE_ORDER_IDEMPOTENCY_UNAVAILABLE",
                    "order_intent_id": order_intent_id,
                },
            )

    def _tag_paper_origin(payload: dict[str, Any]) -> dict[str, Any]:
        # PaperBroker never sends this payload to Dhan, so an extra internal key
        # is safe here and lets apply_paper_entry/exit attribute the trade.
        # Never applied to a live (RealDhanClient) payload.
        if engine_mode == "paper":
            payload["_nova_origin"] = origin_from_source(signal.source)
        return payload

    if use_super_order:
        result = client.place_super_order(
            client_id=client_id, access_token=access_token, payload=_tag_paper_origin(order_request_payload)
        )
        # Dhan validates targetPrice/stopLossPrice against the CURRENT price on
        # their side ("Profit Price Should be greater than Order price"). Our
        # reference LTP is fetched moments earlier, so a fast premium move can
        # invalidate the levels in transit. That rejection is transient:
        # refetch LTP, recompute the legs, and retry exactly once.
        if not result.success and _is_super_order_level_rejection(result):
            retry_ltp = _authoritative_ltp_result(
                exchange_segment=str(request_payload.get("exchangeSegment") or ""),
                security_id=str(request_payload.get("securityId") or ""),
            )
            if retry_ltp.success and retry_ltp.ltp is not None and float(retry_ltp.ltp) > 0:
                order_request_payload, super_order_levels = _build_dhan_super_order_payload(
                    base_payload=request_payload,
                    reference_ltp=float(retry_ltp.ltp),
                    runtime=runtime,
                    overrides=_signal_exit_overrides(signal),
                )
                super_order_levels["levels_source"] = "pre_entry_ltp_retry"
                super_order_levels["retry_reason"] = str(result.error or result.status or "super_order_level_rejection")
                log_order_event(
                    {
                        "phase": "super_order_level_retry",
                        "signal_id": signal.signal_id,
                        "first_error": result.error,
                        "first_status": result.status,
                        "retry_reference_ltp": float(retry_ltp.ltp),
                        "request": _public_order_request(order_request_payload),
                        "super_order_levels": super_order_levels,
                    }
                )
                result = client.place_super_order(
                    client_id=client_id, access_token=access_token, payload=_tag_paper_origin(order_request_payload)
                )
    else:
        result = client.place_order(
            client_id=client_id, access_token=access_token, payload=_tag_paper_origin(order_request_payload)
        )

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

    if result.success and result.order_id:  # noqa: SIM102 - keeps broker poll flow readable
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

    # The first after_response event is written BEFORE polling, when a MARKET
    # order's avg_price is still null (TRANSIT). Without this enriched event
    # the portfolio ledger never learns the fill price (entry/exit show "-",
    # every realized-PnL metric reads 0). Emit a second after_response event
    # carrying the polled fill; portfolio_analytics dedupes by order_id and
    # prefers the leg that has avg_price.
    if order_status_poll is not None and (final_avg_price != result.avg_price or final_status != result.status):
        log_order_event(
            {
                "phase": "after_response",
                "fill_poll_update": True,
                "signal_id": signal.signal_id,
                "action": action,
                "side": signal.side,
                "place_method": place_method,
                "engine_mode": engine_mode,
                "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
                "success": final_success,
                "blocked": False,
                "order_id": result.order_id,
                "status": final_status,
                "avg_price": final_avg_price,
                "filled_qty": final_filled_qty,
                "remaining_qty": final_remaining_qty,
                "order_status_poll": order_status_poll,
                "security_id": request_payload.get("securityId"),
                "trading_symbol": request_payload.get("tradingSymbol") or signal.trading_symbol,
                "qty": qty,
                **_normalized_log_fields(signal),
            }
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
    if order_intent_id:
        result_payload["order_intent_id"] = order_intent_id
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
    if order_intent_id:
        try:
            order_idempotency.complete_order_intent(
                order_intent_id,
                result_summary=result_payload,
            )
        except Exception:  # noqa: BLE001 - post-submit audit failure must be recorded
            log_error_event(
                "LIVE_ORDER_INTENT_UPDATE_FAILED",
                "Live order was submitted but idempotency result update failed; retries will fail closed.",
                metadata={
                    "signal_id": signal.signal_id,
                    "order_intent_id": order_intent_id,
                    "order_id": result_payload.get("order_id"),
                    "status": result_payload.get("status"),
                },
            )
    return result_payload


def _entry_position(
    signal: NormalizedSignal,
    order_result: dict[str, Any],
    qty: int,
    *,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = runtime or {}
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
    risk_exit_mode = str(runtime.get("risk_exit_mode") or "CUSTOM_SL_TP").upper()
    stop_rule = {
        "mode": risk_exit_mode,
        "value": runtime.get("risk_stop_loss_value"),
        "basis": runtime.get("risk_stop_loss_basis") or "PERCENT",
    }
    target_rule = {
        "mode": risk_exit_mode,
        "value": runtime.get("risk_take_profit_value"),
        "basis": runtime.get("risk_take_profit_basis") or "PERCENT",
    }
    # Only synthesize exit levels when a real risk-configuration version was
    # actually resolved (risk_configuration.overlay_for_current_entry ran).
    # Otherwise risk_stop_loss_value/risk_take_profit_value default to 0,
    # which would place the "stop" at the entry price itself.
    active_risk_levels = (
        _entry_risk_levels(float(entry_price), runtime, qty)
        if entry_price is not None and runtime.get("risk_configuration_version_id") is not None
        else None
    )
    position = {
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
        "configuration_revision_id": runtime.get("configuration_revision_id"),
        "configuration_revision": runtime.get("configuration_revision"),
        "risk_configuration_version_id": runtime.get("risk_configuration_version_id"),
        "risk_configuration_version": runtime.get("risk_configuration_version"),
        "entry_stop_rule": stop_rule,
        "entry_target_rule": target_rule,
        "position_sizing_rule": {
            "lots_min": runtime.get("risk_lots_min"),
            "lots_max": runtime.get("risk_lots_max"),
            "configured_lots": runtime.get("configured_lots"),
        },
        "maximum_loss_rule": {
            "amount": runtime.get("risk_max_loss_per_trade"),
            "currency": "INR",
        },
        "exit_management": exit_management,
        "broker_sl_price": broker_sl_price,
        "broker_tp_price": broker_tp_price,
        "broker_entry_reference_price": order_result.get("broker_entry_reference_price"),
        "broker_exit_levels": order_result.get("broker_exit_levels"),
        "sr_suggestion": sr_suggestion,
        "active_exit_levels": active_risk_levels,
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
    # Stamp a stable position_id + version=1 once at entry so the monitor's
    # compare-and-set writes can detect an intervening exit and never resurrect
    # a closed position.
    return stamp_new_open_position(position)


def _entry_risk_levels(entry_price: float, runtime: dict[str, Any], qty: int) -> dict[str, Any]:
    mode = str(runtime.get("risk_exit_mode") or "CUSTOM_SL_TP").upper()
    stop_value = float(runtime.get("risk_stop_loss_value") or 0)
    target_value = float(runtime.get("risk_take_profit_value") or 0)
    stop_basis = str(runtime.get("risk_stop_loss_basis") or "PERCENT").upper()
    target_basis = str(runtime.get("risk_take_profit_basis") or "PERCENT").upper()
    if mode == "FLIPS_ONLY":
        stop_price, target_price = 0.1, 1_000_000.0
    else:
        target_price = (
            entry_price + target_value
            if target_basis == "POINTS"
            else entry_price * (1 + target_value / 100)
        )
        stop_price = 0.1 if mode == "TARGET_PROFIT" else (
            entry_price - stop_value
            if stop_basis == "POINTS"
            else entry_price * (1 - stop_value / 100)
        )
    max_loss = float(runtime.get("risk_max_loss_per_trade") or 0)
    if max_loss > 0 and qty > 0 and mode != "FLIPS_ONLY":
        stop_price = max(stop_price, entry_price - max_loss / qty)
    return {
        "stopLossPrice": round(max(0.1, stop_price), 2),
        "targetPrice": round(max(entry_price + 0.05, target_price), 2),
        "source": "entry_risk_configuration",
        "riskConfigurationVersionId": runtime.get("risk_configuration_version_id"),
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
    current = get_open_position()
    if (
        not current.get("has_open_position")
        or current.get("position_id") != position.get("position_id")
    ):
        return None
    updated = dict(current)
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
    applied, updated = patch_open_position_cas(
        position_id=str(current["position_id"]),
        position_version=int(current.get("position_version") or 0),
        patch={
            "qty": remaining_qty,
            "partial_exit": updated["partial_exit"],
            "live_pnl": live_pnl,
        },
    )
    if not applied:
        return None
    position_operations.on_partial_exit(signal, order_result, updated)
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
    runtime = risk_configuration.overlay_for_current_entry(runtime)

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
    position_operations.on_reversal_marked(
        signal,
        from_option_side=open_position.get("option_side"),
        to_option_side=signal.option_side,
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
            position_operations.on_exit_pending(signal, exit_result, current)
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
    position_operations.on_position_closed(
        exit_signal, exit_result, source=position_operations.STRATEGY_REVERSAL
    )
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
        position = _entry_position(
            signal,
            entry_result,
            _entry_filled_qty(entry_result, reversal_decision.final_qty),
            runtime=runtime,
        )
        entry_result["sr_suggestion"] = position.get("sr_suggestion")
        entry_result["active_exit_levels"] = position.get("active_exit_levels")
        set_open_position(position)
        position_operations.on_entry_placed(
            signal, entry_result, position, source=position_operations.STRATEGY_REVERSAL
        )
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
    runtime = risk_configuration.overlay_for_current_entry(runtime)

    update_app_state(
        state="ENTRY_SIGNAL_RECEIVED",
        last_signal_id=signal.signal_id,
        last_alert_at=utc_now(),
        last_message=f"Entry alert received for {signal.trading_symbol or signal.symbol}",
    )
    entry_block = _entry_request_block(signal, runtime)
    if entry_block:
        return entry_block
    entry_place_method = (
        "super_orders"
        if get_engine_mode() == "live" and _option_exit_mode(runtime) == "DHAN_SUPER"
        else "orders"
    )
    existing_intent = _lookup_existing_live_order_intent_result(
        signal=signal,
        qty=signal.qty,
        action="ENTRY",
        place_method=entry_place_method,
    )
    if existing_intent is not None:
        return existing_intent

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
        position = _entry_position(
            signal,
            order_result,
            _entry_filled_qty(order_result, decision.final_qty),
            runtime=runtime,
        )
        order_result["sr_suggestion"] = position.get("sr_suggestion")
        order_result["active_exit_levels"] = position.get("active_exit_levels")
        set_open_position(position)
        position_operations.on_entry_placed(signal, order_result, position)
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
    existing_intent = _lookup_existing_live_order_intent_result(
        signal=signal,
        qty=signal.qty,
        action="EXIT",
        place_method="orders",
    )
    if existing_intent is not None:
        return existing_intent
    # Idempotent retry after a durable Paper exit already completed: the position
    # is flat and its last exit operation is recorded. Return ALREADY_FLAT so a
    # re-submitted exit (retry, duplicate delivery) is a benign no-op rather than
    # a generic block — and never a second SELL.
    if get_engine_mode() == "paper":
        flat_check = get_open_position()
        last_op = flat_check.get("last_exit_operation")
        if not flat_check.get("has_open_position") and isinstance(last_op, dict) and last_op.get("status") == "COMPLETED":
            return {
                "success": True,
                "status": "ALREADY_FLAT",
                "idempotent": True,
                "exit_operation_id": last_op.get("operation_id"),
                "order_id": last_op.get("order_id"),
                "reason": "Paper position already exited; no additional SELL sent.",
            }
    decision: RiskDecision = evaluate_exit(signal, runtime=runtime)
    if not decision.allowed:
        return _blocked("BLOCKED", decision.reason, signal)

    open_position = ensure_open_position_identity()
    raw_payload = signal.raw_payload if isinstance(signal.raw_payload, dict) else {}
    exit_signal = _exit_signal_from_position(
        open_position,
        signal,
        signal_id=signal.signal_id,
        source=signal.source or "tracked_position_exit",
        exit_reason=raw_payload.get("exit_reason"),
    )
    qty = decision.final_qty
    paper_exit_operation_id: str | None = None
    if get_engine_mode() == "paper":
        paper_exit_operation_id = (
            f"PAPER_EXIT:{open_position['position_id']}:{signal.signal_id}"
        )
        claim_status, claimed_position = claim_exit_operation(
            position_id=str(open_position["position_id"]),
            position_version=int(open_position.get("position_version") or 0),
            operation_id=paper_exit_operation_id,
            signal_id=signal.signal_id,
            source=signal.source,
        )
        if claim_status != "CLAIMED":
            return {
                "success": claim_status in {"ALREADY_FLAT", "ALREADY_COMPLETED"},
                "blocked": claim_status not in {"ALREADY_FLAT", "ALREADY_COMPLETED"},
                "status": claim_status,
                "reason": (
                    "Paper exit is already owned by another durable operation."
                    if claim_status in {"EXIT_IN_PROGRESS", "ALREADY_CLAIMED"}
                    else "Tracked Paper position changed before exit ownership was acquired."
                ),
                "exit_operation_id": paper_exit_operation_id,
                "idempotent": True,
            }
        open_position = claimed_position
        exit_signal = _exit_signal_from_position(
            open_position,
            signal,
            signal_id=signal.signal_id,
            source=signal.source or "tracked_position_exit",
            exit_reason=raw_payload.get("exit_reason"),
        )
    update_app_state(state="EXIT_ORDER_SENDING", last_message="Exit risk checks passed. Sending exit order to Dhan.")
    order_result = _place_order(exit_signal, qty, "EXIT", runtime=runtime)
    if order_result.get("blocked"):
        if paper_exit_operation_id:
            release_exit_operation(
                position_id=str(open_position["position_id"]),
                operation_id=paper_exit_operation_id,
                result=order_result,
            )
        return order_result

    if order_result.get("success"):
        if order_result.get("partial_fill") and paper_exit_operation_id:
            release_exit_operation(
                position_id=str(open_position["position_id"]),
                operation_id=paper_exit_operation_id,
                result={**order_result, "status": "PARTIAL_EXIT_FILLED"},
            )
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
        if paper_exit_operation_id:
            close_status, _flat = complete_exit_operation(
                position_id=str(open_position["position_id"]),
                operation_id=paper_exit_operation_id,
                order_id=order_result.get("order_id"),
            )
            if close_status not in {"COMPLETED", "ALREADY_COMPLETED"}:
                reason = (
                    "Paper SELL filled, but confirmed-flat state could not be "
                    "bound to its durable exit operation; reconciliation is required."
                )
                log_error_event(
                    "PAPER_EXIT_CLOSE_CONFLICT",
                    reason,
                    metadata={
                        "signal_id": signal.signal_id,
                        "order_id": order_result.get("order_id"),
                        "exit_operation_id": paper_exit_operation_id,
                        "close_status": close_status,
                    },
                )
                update_app_state(state="ERROR", last_message=reason)
                return {
                    **order_result,
                    "success": False,
                    "status": "RECONCILIATION_REQUIRED",
                    "reason": reason,
                    "exit_operation_id": paper_exit_operation_id,
                }
        else:
            clear_open_position()
        position_operations.on_position_closed(signal, order_result)
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
        return {
            **order_result,
            "status": "ORDER_PLACED",
            "super_order_leg_cancellations": super_order_leg_cancellations,
            "exit_operation_id": paper_exit_operation_id,
        }

    reason = order_result.get("error") or "Dhan exit order request failed"
    if paper_exit_operation_id:
        release_exit_operation(
            position_id=str(open_position["position_id"]),
            operation_id=paper_exit_operation_id,
            result=order_result,
        )
    log_error_event("EXIT_ORDER_FAILED", reason, metadata={"signal_id": signal.signal_id})
    update_app_state(state="ERROR", last_message=reason)
    return {**order_result, "blocked": False, "status": _failed_order_status(order_result), "reason": reason}


def route_signal(
    signal: NormalizedSignal,
    *,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # C11 — Settings snapshot per signal. Capture runtime once at the
    # absolute earliest point in the pipeline so two near-simultaneous
    # signals can't end up using different SL%/TP%/max_qty/etc. just
    # because a user clicked Save between them.
    runtime = dict(runtime) if runtime is not None else get_runtime_settings()
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
