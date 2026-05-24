from __future__ import annotations

from typing import Any

from app.config import DEFAULT_EXCHANGE_SEGMENT, DEFAULT_ORDER_TYPE, DEFAULT_PRODUCT_TYPE, settings
from app.schemas.signal import NormalizedSignal
from app.services.audit_logger import log_audit_event, log_error_event, log_order_event
from app.services.credential_vault import get_dhan_credentials
from app.services.dhan_client import DHAN_TERMINAL_STATUSES, MockDhanClient, RealDhanClient
from app.services.risk_manager import RiskDecision, _market_is_open, evaluate_entry, evaluate_exit
from app.services.security_id_resolver import resolve_security_id
from app.services.state_store import (
    clear_open_position,
    get_open_position,
    set_open_position,
    update_app_state,
    utc_now,
)
from app.services.wallet_service import refresh_wallet_snapshot


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


def _blocked(
    status: str,
    reason: str,
    signal: NormalizedSignal,
    *,
    security_id_resolution: dict[str, Any] | None = None,
    security_id: str | None = None,
    trading_symbol: str | None = None,
) -> dict[str, Any]:
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
            "dhan_mode": settings.DHAN_MODE.upper(),
            "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
            **_normalized_log_fields(signal),
        }
    )
    log_audit_event(
        status,
        reason,
        severity="WARNING",
        metadata={"signal_id": signal.signal_id, "action": signal.action, "payload_format": signal.payload_format},
    )
    update_app_state(state="BLOCKED", last_message=reason)
    return {
        "blocked": True,
        "status": status,
        "reason": reason,
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


def _place_order(signal: NormalizedSignal, qty: int, action: str) -> dict[str, Any]:
    dhan_mode = settings.DHAN_MODE.upper()
    creds = get_dhan_credentials()
    request_payload, security_id_resolution = _build_dhan_payload_and_resolution(signal, qty, action)

    if dhan_mode == "REAL":
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
        client = RealDhanClient()
        client_id = creds.client_id
        access_token = creds.access_token
    else:
        client = MockDhanClient()
        client_id = creds.client_id if creds else "MOCK_CLIENT"
        access_token = ""

    log_order_event(
        {
            "phase": "before_request",
            "signal_id": signal.signal_id,
            "action": action,
            "side": signal.side,
            "dhan_mode": dhan_mode,
            "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
            "market_closed_debug": settings.MARKET_CLOSED_DEBUG,
            "force_allow_order_when_market_closed": settings.FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED,
            "request": _public_order_request(request_payload),
            "security_id": request_payload.get("securityId"),
            "security_id_resolution": security_id_resolution,
            "trading_symbol": request_payload.get("tradingSymbol") or signal.trading_symbol,
            "qty": qty,
            "order_type": request_payload["orderType"],
            "product_type": request_payload["productType"],
            **_normalized_log_fields(signal),
        }
    )

    result = client.place_order(client_id=client_id, access_token=access_token, payload=request_payload)

    log_order_event(
        {
            "phase": "after_response",
            "signal_id": signal.signal_id,
            "action": action,
            "side": signal.side,
            "dhan_mode": dhan_mode,
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
            "qty": qty,
            "order_type": request_payload["orderType"],
            "product_type": request_payload["productType"],
            **_normalized_log_fields(signal),
        }
    )

    # --- Post-order status polling (MVP: poll a few times for final status) ---
    order_status_poll: dict[str, Any] | None = None
    final_status = result.status
    final_avg_price = result.avg_price
    final_success = result.success

    if result.success and result.order_id:
        # Status from placement response may be TRANSIT/PENDING — poll for confirmation.
        if result.status not in DHAN_TERMINAL_STATUSES or dhan_mode == "MOCK":
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
                "error": poll.error,
            }
            if poll.is_filled:
                final_status = "TRADED"
                final_avg_price = poll.avg_price or final_avg_price
                final_success = True
            elif poll.is_terminal and not poll.is_filled:
                # REJECTED / CANCELLED / EXPIRED
                final_status = poll.order_status or final_status
                final_success = False
            else:
                # Still pending after polling
                final_status = "PENDING_CONFIRMATION"
                final_success = True  # order was accepted; fill pending

    return {
        "blocked": False,
        "success": final_success,
        "order_id": result.order_id,
        "status": final_status,
        "avg_price": final_avg_price,
        "error": result.error,
        "interpreted_error": result.interpreted_error,
        "raw_response": result.raw_response,
        "order_status_poll": order_status_poll,
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


def _entry_position(signal: NormalizedSignal, order_result: dict[str, Any], qty: int) -> dict[str, Any]:
    return {
        "has_open_position": True,
        "strategy_code": signal.strategy_code,
        "security_id": order_result.get("security_id") or signal.security_id,
        "trading_symbol": order_result.get("trading_symbol") or signal.trading_symbol,
        "qty": qty,
        "entry_order_id": order_result.get("order_id"),
        "entry_price": order_result.get("avg_price"),
        "opened_at": utc_now(),
    }


def route_entry_signal(signal: NormalizedSignal) -> dict[str, Any]:
    update_app_state(
        state="ENTRY_SIGNAL_RECEIVED",
        last_signal_id=signal.signal_id,
        last_alert_at=utc_now(),
        last_message=f"Entry alert received for {signal.trading_symbol or signal.symbol}",
    )
    decision = evaluate_entry(signal)
    if not decision.allowed:
        return _blocked("BLOCKED", decision.reason, signal)

    update_app_state(state="ENTRY_RISK_CHECK", last_message="Entry risk checks passed.")
    update_app_state(state="ENTRY_ORDER_SENDING", last_message="Sending entry order to Dhan.")
    order_result = _place_order(signal, decision.final_qty, "ENTRY")
    if order_result.get("blocked"):
        return order_result

    if order_result.get("success"):
        if settings.DHAN_MODE.upper() == "REAL":
            refresh_wallet_snapshot(force=True, log_event=True)
        set_open_position(_entry_position(signal, order_result, decision.final_qty))
        update_app_state(
            state="WAITING_EXIT",
            last_message=f"Entry order placed in {settings.DHAN_MODE.upper()} mode",
        )
        log_audit_event(
            "ORDER_PLACED",
            f"Entry order placed in {settings.DHAN_MODE.upper()} mode",
            metadata={
                "signal_id": signal.signal_id,
                "order_id": order_result.get("order_id"),
                "payload_format": signal.payload_format,
            },
        )
        return {**order_result, "status": "ORDER_PLACED"}

    reason = order_result.get("error") or "Dhan order request failed"
    log_error_event("ENTRY_ORDER_FAILED", reason, metadata={"signal_id": signal.signal_id})
    update_app_state(state="ERROR", last_message=reason)
    return {**order_result, "blocked": False, "status": "ORDER_REJECTED", "reason": reason}


def route_exit_signal(signal: NormalizedSignal) -> dict[str, Any]:
    update_app_state(
        state="EXIT_SIGNAL_RECEIVED",
        last_signal_id=signal.signal_id,
        last_alert_at=utc_now(),
        last_message=f"Exit alert received for {signal.trading_symbol or signal.symbol}",
    )
    decision: RiskDecision = evaluate_exit(signal)
    if not decision.allowed:
        return _blocked("BLOCKED", decision.reason, signal)

    open_position = get_open_position()
    qty = decision.final_qty
    update_app_state(state="EXIT_ORDER_SENDING", last_message="Exit risk checks passed. Sending exit order to Dhan.")
    order_result = _place_order(signal, qty, "EXIT")
    if order_result.get("blocked"):
        return order_result

    if order_result.get("success"):
        clear_open_position()
        if settings.DHAN_MODE.upper() == "REAL":
            refresh_wallet_snapshot(force=True, log_event=True)
        update_app_state(
            state="WAITING_ENTRY",
            last_message=f"Exit order placed in {settings.DHAN_MODE.upper()} mode. Waiting for next entry.",
        )
        log_audit_event(
            "EXIT_ORDER_PLACED",
            f"Exit order placed in {settings.DHAN_MODE.upper()} mode",
            metadata={
                "signal_id": signal.signal_id,
                "order_id": order_result.get("order_id"),
                "closed_security_id": open_position.get("security_id"),
                "payload_format": signal.payload_format,
            },
        )
        return {**order_result, "status": "ORDER_PLACED"}

    reason = order_result.get("error") or "Dhan exit order request failed"
    log_error_event("EXIT_ORDER_FAILED", reason, metadata={"signal_id": signal.signal_id})
    update_app_state(state="ERROR", last_message=reason)
    return {**order_result, "blocked": False, "status": "ORDER_REJECTED", "reason": reason}


def route_signal(signal: NormalizedSignal) -> dict[str, Any]:
    if signal.action == "ENTRY":
        return route_entry_signal(signal)
    return route_exit_signal(signal)
