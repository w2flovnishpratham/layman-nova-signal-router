from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.audit_logger import read_jsonl
from app.config import DEFAULT_EXCHANGE_SEGMENT, DEFAULT_PRODUCT_TYPE, DEFAULT_ORDER_TYPE, settings
from app.routers.setup import current_nifty_lot_size
from app.schemas.signal import NormalizedSignal
from app.services.atm_ltp_service import get_atm_option_snapshot
from app.services.credential_vault import get_webhook_secret
from app.services import entitlements, order_idempotency
from app.services.execution_context import current_execution_user
from app.services.execution_router import route_signal
from app.services.normalized_errors import classify_failure
from app.services.paper_portfolio import paper_wallet_snapshot, resize_paper_open_trade_quantity
from app.services.quote_service import get_quote_snapshot
from app.services.risk_manager import _market_is_open
from app.services.security_id_resolver import resolve_security_id, suggest_option_contract
from app.services.state_store import get_app_state, get_engine_mode, get_open_position, set_open_position, utc_now


router = APIRouter()


class ManualEntryRequest(BaseModel):
    side: str = Field(pattern="^(CE|PE)$")
    lots: int = Field(default=1, ge=1, le=20)
    targetProfitPct: float | None = Field(default=None, ge=0)
    stopLossPct: float | None = Field(default=None, ge=0)
    strike: float | None = None
    expiry: str | None = None
    securityId: str | None = None
    tradingSymbol: str | None = None


class ManualExitRequest(BaseModel):
    reason: str | None = None


class ManualReverseRequest(BaseModel):
    lots: int | None = Field(default=None, ge=1, le=20)


class ExitLevelsRequest(BaseModel):
    stopLossPrice: float = Field(gt=0)
    targetPrice: float = Field(gt=0)


class QuantityRequest(BaseModel):
    qty: int = Field(ge=1)


class QuoteRequest(BaseModel):
    side: str = Field(pattern="^(CE|PE)$")
    lots: int = Field(default=1, ge=1, le=20)
    strike: float | None = None
    expiry: str | None = None
    securityId: str | None = None
    tradingSymbol: str | None = None


@router.get("/orders")
def orders(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
    rows = read_jsonl("order", limit=limit)
    parsed: list[dict] = []
    order_rows = [row for row in rows if row.get("phase") in {"before_request", "after_response", "blocked"}]
    for index, row in enumerate(reversed(order_rows), start=1):
        parsed.append(
            {
                "id": index,
                "created_at": row.get("timestamp"),
                "phase": row.get("phase"),
                "signal_id": row.get("signal_id"),
                "payload_format": row.get("payload_format"),
                "action": row.get("action"),
                "side": row.get("side"),
                "normalized_action": row.get("normalized_action"),
                "normalized_side": row.get("normalized_side"),
                "normalized_qty": row.get("normalized_qty"),
                "normalized_symbol": row.get("normalized_symbol"),
                "normalized_strike": row.get("normalized_strike"),
                "normalized_expiry": row.get("normalized_expiry"),
                "normalized_option_side": row.get("normalized_option_side"),
                "dhan_mode": row.get("dhan_mode"),
                "live_orders_enabled": row.get("live_orders_enabled"),
                "order_id": row.get("order_id"),
                "status": row.get("status"),
                "success": row.get("success"),
                "blocked": row.get("blocked", row.get("phase") == "blocked"),
                "reason": row.get("reason") or row.get("error"),
                "security_id": row.get("security_id"),
                "trading_symbol": row.get("trading_symbol"),
                "qty": row.get("qty"),
                "order_type": row.get("order_type"),
                "product_type": row.get("product_type"),
                "avg_price": row.get("avg_price"),
                "request": row.get("request"),
                "response": row.get("response"),
            }
        )
    return parsed


@router.post("/orders/manual-entry")
def manual_entry(request: Request, body: ManualEntryRequest) -> Any:
    signal = _manual_entry_signal(
        option_side=body.side,
        lots=body.lots,
        strike=body.strike,
        expiry=body.expiry,
        security_id=body.securityId,
        trading_symbol=body.tradingSymbol,
        raw_payload={
            "manual_order": True,
            "targetProfitPct": body.targetProfitPct,
            "stopLossPct": body.stopLossPct,
        },
    )
    blocked = _unresolved_contract_block_or_none(signal)
    if blocked is not None:
        return blocked
    blocked = _attach_manual_idempotency_or_block(signal, request)
    if blocked is not None:
        return blocked
    blocked = _manual_live_entitlement_or_block(signal)
    if blocked is not None:
        return blocked
    return _run_manual_operation(
        request=request,
        operation="ENTRY",
        payload=body.model_dump(),
        execute=lambda: _route_manual_entry(signal),
    )


@router.post("/orders/manual-exit")
def manual_exit(request: Request, body: ManualExitRequest) -> Any:
    position = get_open_position()
    if not position.get("has_open_position"):
        error = classify_failure(
            "Exit blocked: no open position exists.",
            source="RISK_ENGINE",
            mode=get_engine_mode(legacy_fallback=False) or get_engine_mode(),
            order_sent_to_broker=False,
            money_at_risk=False,
        )
        return {"ok": False, "message": error["userMessage"], "normalizedError": error}
    signal = NormalizedSignal(
        payload_format="NOVA",
        secret=get_webhook_secret() or "",
        signal_id=f"MANUAL-EXIT-{int(time.time() * 1000)}",
        strategy_code=position.get("strategy_code") or "MANUAL",
        action="EXIT",
        side="SELL",
        symbol=position.get("symbol") or "NIFTY",
        instrument_type=position.get("instrument_type") or "OPTIDX",
        exchange_segment=position.get("exchange_segment") or DEFAULT_EXCHANGE_SEGMENT,
        security_id=position.get("security_id"),
        trading_symbol=position.get("trading_symbol"),
        option_side=position.get("option_side"),
        strike=position.get("strike"),
        expiry=position.get("expiry"),
        qty=int(position.get("qty") or current_nifty_lot_size()),
        order_type=position.get("order_type") or DEFAULT_ORDER_TYPE,
        product_type=position.get("product_type") or DEFAULT_PRODUCT_TYPE,
        source="manual_panel",
        raw_payload={
            "manual_order": True,
            "manual_exit_reason": body.reason or "manual_panel",
            # Entry reference so the exit chat card can show gross/charges/net.
            "entry_price": position.get("entry_price"),
            "live_pnl": position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else None,
        },
    )
    blocked = _attach_manual_idempotency_or_block(signal, request)
    if blocked is not None:
        return blocked
    blocked = _manual_live_entitlement_or_block(signal)
    if blocked is not None:
        return blocked
    return _run_manual_operation(
        request=request,
        operation="EXIT",
        payload=body.model_dump(),
        execute=lambda: _manual_response(route_signal(signal), operation="EXIT"),
    )


@router.post("/orders/manual-reverse")
def manual_reverse(request: Request, body: ManualReverseRequest) -> Any:
    position = get_open_position()
    if not position.get("has_open_position"):
        error = classify_failure(
            "Reverse blocked: no open position exists.",
            source="RISK_ENGINE",
            mode=get_engine_mode(legacy_fallback=False) or get_engine_mode(),
            order_sent_to_broker=False,
            money_at_risk=False,
        )
        return {"ok": False, "message": error["userMessage"], "normalizedError": error}
    current_side = str(position.get("option_side") or "CE").upper()
    next_side = "PE" if current_side == "CE" else "CE"
    lots = body.lots or max(1, int(position.get("qty") or current_nifty_lot_size()) // current_nifty_lot_size())
    signal = _manual_entry_signal(
        option_side=next_side,
        lots=lots,
        strike=position.get("strike"),
        expiry=position.get("expiry"),
        security_id=None,
        trading_symbol=None,
        raw_payload={
            "manual_order": True,
            "manual_reverse": True,
            "fromOptionSide": current_side,
        },
    )
    blocked = _unresolved_contract_block_or_none(signal)
    if blocked is not None:
        return blocked
    blocked = _attach_manual_idempotency_or_block(signal, request)
    if blocked is not None:
        return blocked
    blocked = _manual_live_entitlement_or_block(signal)
    if blocked is not None:
        return blocked
    return _run_manual_operation(
        request=request,
        operation="REVERSE",
        payload=body.model_dump(),
        execute=lambda: _manual_response(route_signal(signal), operation="REVERSE"),
    )


@router.patch("/orders/active-position/quantity")
def update_active_position_quantity(body: QuantityRequest) -> dict[str, Any]:
    position = get_open_position()
    mode = get_engine_mode(legacy_fallback=False) or get_engine_mode()
    if not position.get("has_open_position"):
        error = classify_failure(
            "Quantity update blocked: no open position exists.",
            source="RISK_ENGINE",
            mode=mode,
            order_sent_to_broker=False,
            money_at_risk=False,
        )
        return {"ok": False, "message": error["userMessage"], "normalizedError": error}

    if mode != "paper":
        error = classify_failure(
            "Quantity update blocked: live position quantity changes require broker add/reduce order flow.",
            source="RISK_ENGINE",
            mode=mode,
            order_sent_to_broker=False,
            money_at_risk=True,
        )
        return {"ok": False, "message": error["userMessage"], "normalizedError": error}

    lot_size = max(int(current_nifty_lot_size() or 1), 1)
    qty = int(body.qty)
    if qty % lot_size != 0:
        return _quantity_error(f"Qty must be a whole lot of {lot_size}.", mode=mode)

    updated = dict(position)
    current_qty = int(updated.get("qty") or 0)
    updated["qty"] = qty
    updated["requested_qty"] = qty
    updated["filled_qty"] = qty
    updated["quantity_adjustment"] = {
        "source": "manual",
        "previousQty": current_qty,
        "qty": qty,
        "lots": qty // lot_size,
        "acceptedAt": utc_now(),
    }

    live_pnl = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
    updated_live_pnl = dict(live_pnl)
    updated_live_pnl["qty"] = qty
    entry_price = _number(position.get("entry_price") or live_pnl.get("entry_price"))
    ltp = _number(live_pnl.get("ltp") or entry_price)
    if entry_price is not None and ltp is not None:
        unrealized = round((ltp - entry_price) * qty, 2)
        exposure = entry_price * qty
        updated_live_pnl["unrealized_pnl"] = unrealized
        updated_live_pnl["pnl_percent"] = round((unrealized / exposure) * 100, 2) if exposure else 0.0
    updated_live_pnl["message"] = "Manual paper quantity is applied."
    updated_live_pnl["last_checked_at"] = utc_now()
    updated["live_pnl"] = updated_live_pnl

    try:
        resize_paper_open_trade_quantity(qty=qty)
    except ValueError as exc:
        return _quantity_error(str(exc), mode=mode)

    set_open_position(updated)
    return {
        "ok": True,
        "message": "Qty updated.",
        "qty": qty,
        "lots": qty // lot_size,
    }


@router.patch("/orders/active-position/exit-levels")
def update_active_position_exit_levels(body: ExitLevelsRequest) -> dict[str, Any]:
    position = get_open_position()
    if not position.get("has_open_position"):
        error = classify_failure(
            "SL/TP update blocked: no open position exists.",
            source="RISK_ENGINE",
            mode=get_engine_mode(legacy_fallback=False) or get_engine_mode(),
            order_sent_to_broker=False,
            money_at_risk=False,
        )
        return {"ok": False, "message": error["userMessage"], "normalizedError": error}

    mode = get_engine_mode(legacy_fallback=False) or get_engine_mode()
    stop_loss = round(float(body.stopLossPrice), 2)
    target = round(float(body.targetPrice), 2)
    live_pnl = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
    entry_price = _number(position.get("entry_price") or live_pnl.get("entry_price"))
    ltp = _number(live_pnl.get("ltp") or entry_price)
    if stop_loss >= target:
        return _exit_level_error("Stop loss must be below target price.", mode=mode)
    if ltp is not None and (stop_loss >= ltp or target <= ltp):
        return _exit_level_error("SL must be below current LTP and TP must be above current LTP.", mode=mode)

    # Live Dhan Super Order: push the new SL/TP to the broker legs. Paper and
    # server-managed positions are handled locally below (no broker call).
    if str(position.get("exit_management") or "").upper() == "DHAN_SUPER":
        from app.services.execution_router import apply_manual_super_order_exit_levels

        broker_update = apply_manual_super_order_exit_levels(
            position, stop_loss_price=stop_loss, target_price=target
        )
        if not broker_update.get("ok"):
            error = classify_failure(
                f"SL/TP update blocked: {broker_update.get('message') or 'Dhan Super Order modification failed.'}",
                source="DHAN",
                mode=mode,
                order_sent_to_broker=False,
                money_at_risk=mode == "live",
            )
            return {"ok": False, "message": error["userMessage"], "normalizedError": error}

    levels = {
        "source": "manual",
        "stopLossPrice": stop_loss,
        "targetPrice": target,
        "acceptedAt": utc_now(),
    }
    updated = dict(position)
    updated["active_exit_levels"] = levels
    updated_live_pnl = dict(live_pnl)
    updated_live_pnl.update(
        {
            "sl_price": stop_loss,
            "tp_price": target,
            "exit_management": str(position.get("exit_management") or "SERVER").upper(),
            "message": "Manual SL/TP levels are armed.",
            "last_checked_at": utc_now(),
        }
    )
    updated["live_pnl"] = updated_live_pnl
    set_open_position(updated)
    return {
        "ok": True,
        "message": "SL/TP levels updated.",
        "activeExitLevels": levels,
    }


@router.get("/orders/quote")
def order_quote(
    side: str = Query(pattern="^(CE|PE)$"),
    lots: int = Query(default=1, ge=1, le=20),
    strike: float | None = None,
    expiry: str | None = None,
    security_id: str | None = Query(default=None, alias="securityId"),
    trading_symbol: str | None = Query(default=None, alias="tradingSymbol"),
) -> dict[str, Any]:
    if not _market_is_open():
        atm = get_atm_option_snapshot(option_side=side, lots=lots, allow_rest_fallback=False)
        return _market_closed_quote(side=side, lots=lots, atm=atm)

    if not security_id and not trading_symbol and strike is None and not expiry:
        atm = get_atm_option_snapshot(option_side=side, lots=lots, allow_rest_fallback=True)
        return _order_quote_from_atm(side=side, lots=lots, atm=atm)

    signal = _manual_entry_signal(
        option_side=side,
        lots=lots,
        strike=strike,
        expiry=expiry,
        security_id=security_id,
        trading_symbol=trading_symbol,
        raw_payload={"manual_quote": True},
    )
    resolution = resolve_security_id(signal)
    qty = signal.qty
    base = {
        "ok": bool(resolution.ok),
        "mode": get_engine_mode(legacy_fallback=False) or get_engine_mode(),
        "side": side,
        "lots": lots,
        "qty": qty,
        "securityId": resolution.security_id,
        "tradingSymbol": resolution.trading_symbol or signal.trading_symbol,
        "estimatedPremium": None,
        "estimatedCost": None,
        "estimatedMargin": None,
        "resolution": resolution.model_dump(),
    }
    if not resolution.ok or not resolution.security_id:
        error = classify_failure(
            resolution.reason,
            source="NOVA_BACKEND",
            signal=signal,
            mode=base["mode"],
            order_sent_to_broker=False,
            money_at_risk=False,
        )
        return {**base, "ok": False, "message": error["userMessage"], "normalizedError": error}

    quote = get_quote_snapshot(
        exchange_segment=signal.exchange_segment or DEFAULT_EXCHANGE_SEGMENT,
        security_id=str(resolution.security_id),
        symbol=resolution.trading_symbol or signal.trading_symbol,
        max_age_seconds=2.0,
        allow_rest_fallback=True,
    )
    if quote.get("ltp") is None or quote.get("stale"):
        error = classify_failure(
            str(quote.get("message") or quote.get("error") or "Fresh LTP unavailable."),
            source="DHAN",
            signal=signal,
            mode=base["mode"],
            status_code=429 if quote.get("status") == "RATE_LIMITED" else None,
            order_sent_to_broker=False,
            money_at_risk=False,
        )
        return {
            **base,
            "ok": False,
            "message": error["userMessage"],
            "quoteStatus": quote.get("status"),
            "quoteSource": quote.get("source"),
            "retryAfterSeconds": quote.get("retry_after_seconds"),
            "normalizedError": error,
        }

    premium = float(quote["ltp"])
    cost = premium * qty
    return {
        **base,
        "ok": True,
        "message": "Quote fetched.",
        "estimatedPremium": premium,
        "estimatedCost": round(cost, 2),
        "estimatedMargin": round(cost, 2),
        "quoteStatus": quote.get("status"),
        "quoteSource": quote.get("source"),
        "lastUpdatedAt": utc_now(),
    }


@router.get("/orders/quotes")
def order_quotes(lots: int = Query(default=1, ge=1, le=20)) -> dict[str, Any]:
    """Return both ATM sides from one backend snapshot and one polling loop."""
    atm = get_atm_option_snapshot(
        option_side="BOTH",
        lots=lots,
        allow_rest_fallback=_market_is_open(),
    )
    return {
        "ok": bool(atm.get("ok")),
        "lots": lots,
        "quotes": {
            side: _order_quote_from_atm(side=side, lots=lots, atm=atm)
            for side in ("CE", "PE")
        },
        "quoteMetrics": {
            "source": "authoritative_quote_service",
            "computedAt": atm.get("computedAt"),
        },
    }


def _order_quote_from_atm(*, side: str, lots: int, atm: dict[str, Any]) -> dict[str, Any]:
    option = atm.get("options", {}).get(side) if isinstance(atm.get("options"), dict) else None
    option_data = option if isinstance(option, dict) else {}
    premium = _number(option_data.get("ltp"))
    base = {
        "ok": premium is not None and not bool(option_data.get("stale")),
        "mode": get_engine_mode(legacy_fallback=False) or get_engine_mode(),
        "side": side,
        "lots": lots,
        "qty": option_data.get("qty") or current_nifty_lot_size() * lots,
        "securityId": option_data.get("securityId"),
        "tradingSymbol": option_data.get("tradingSymbol"),
        "estimatedPremium": premium,
        "estimatedCost": option_data.get("estimatedCost"),
        "estimatedMargin": option_data.get("estimatedCost"),
        "quoteStatus": option_data.get("ltpStatus"),
        "quoteSource": option_data.get("ltpSource"),
        "resolution": {
            "ok": bool(option_data.get("securityId")),
            "security_id": option_data.get("securityId"),
            "method": "ATM_AUTO",
            "reason": "Auto-selected ATM option contract from Dhan market data."
            if option_data.get("securityId")
            else "Waiting for NIFTY spot before calculating ATM contract.",
            "trading_symbol": option_data.get("tradingSymbol"),
            "lot_size": option_data.get("lotSize"),
            "source_path": (option_data.get("autoContract") or {}).get("sourcePath")
            if isinstance(option_data.get("autoContract"), dict)
            else None,
        },
        "atm": atm,
        "lastUpdatedAt": atm.get("computedAt") or utc_now(),
    }
    if base["ok"]:
        return {**base, "message": "ATM quote fetched."}
    error = classify_failure(
        option_data.get("message") or atm.get("message") or "ATM option LTP is not available yet.",
        source="DHAN",
        mode=base["mode"],
        status=str(option_data.get("ltpStatus") or ""),
        order_sent_to_broker=False,
        money_at_risk=False,
        debug_pack={"atm": atm, "side": side},
    )
    return {**base, "ok": False, "message": error["userMessage"], "normalizedError": error}


def _manual_entry_signal(
    *,
    option_side: str,
    lots: int,
    strike: float | None,
    expiry: str | None,
    security_id: str | None,
    trading_symbol: str | None,
    raw_payload: dict[str, Any],
) -> NormalizedSignal:
    lot_size = current_nifty_lot_size()
    qty = max(int(lots), 1) * lot_size
    raw_payload = dict(raw_payload)
    if _manual_auto_contract_allowed() and not security_id and (strike is None or not expiry):
        atm = get_atm_option_snapshot(option_side=option_side, lots=lots, allow_rest_fallback=True)
        option = atm.get("options", {}).get(option_side) if isinstance(atm.get("options"), dict) else None
        auto_contract = option.get("autoContract") if isinstance(option, dict) else None
        if isinstance(option, dict) and option.get("securityId"):
            strike = _number(option.get("strike"))
            expiry = str(option.get("expiry") or "") or None
            security_id = str(option.get("securityId"))
            trading_symbol = str(option.get("tradingSymbol") or "") or None
            raw_payload["autoAtm"] = {
                "niftySpot": atm.get("niftySpot"),
                "niftySpotSource": atm.get("niftySpotSource"),
                "atmStrike": atm.get("atmStrike"),
                "optionLtp": option.get("ltp"),
                "optionLtpSource": option.get("ltpSource"),
                "contract": auto_contract,
            }
        elif atm.get("marketOpen") is not False:
            reference_price = _latest_nifty_reference_price()
            if strike is not None or reference_price is not None:
                suggestion = suggest_option_contract(
                    symbol="NIFTY",
                    option_side=option_side,
                    exchange_segment=DEFAULT_EXCHANGE_SEGMENT,
                    reference_price=reference_price,
                    expiry=expiry,
                    strike=strike,
                )
                if suggestion is not None:
                    strike = suggestion.strike
                    expiry = suggestion.expiry
                    security_id = suggestion.security_id
                    trading_symbol = suggestion.trading_symbol
                    raw_payload["autoContract"] = suggestion.model_dump()
    return NormalizedSignal(
        payload_format="NOVA",
        secret=get_webhook_secret() or "",
        signal_id=f"MANUAL-ENTRY-{option_side}-{int(time.time() * 1000)}",
        strategy_code="MANUAL",
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment=DEFAULT_EXCHANGE_SEGMENT,
        security_id=security_id,
        trading_symbol=trading_symbol,
        option_side=option_side,
        strike=strike,
        expiry=expiry,
        qty=qty,
        order_type=DEFAULT_ORDER_TYPE,
        product_type=DEFAULT_PRODUCT_TYPE,
        source="manual_panel",
        raw_payload=raw_payload,
    )


def _manual_auto_contract_allowed() -> bool:
    mode = get_engine_mode(legacy_fallback=False) or get_engine_mode()
    return str(mode or "").lower() == "paper"


def _manual_live_order_requires_idempotency_key() -> bool:
    mode = get_engine_mode(legacy_fallback=False) or get_engine_mode()
    return settings.is_production and mode == "live" and settings.ENABLE_LIVE_ORDERS


def _manual_order_can_place_real_orders() -> bool:
    mode = get_engine_mode(legacy_fallback=False) or get_engine_mode()
    return (
        mode == "live"
        and bool(settings.ENABLE_LIVE_ORDERS)
        and settings.DHAN_MODE.upper() == "REAL"
        and not settings.DHAN_READ_ONLY_REAL_DATA
    )


def _manual_live_entitlement_or_block(signal: NormalizedSignal) -> JSONResponse | None:
    if not _manual_order_can_place_real_orders():
        return None
    user = current_execution_user()
    if user is not None and user.is_dev and not settings.is_production:
        return None
    if user is None:
        return _manual_block_response(
            signal,
            "Live entitlement is required.",
            status_code=403,
            block_code="LIVE_ENTITLEMENT_REQUIRED",
        )
    try:
        entitlements.require_live_entitlement_for_user(user.id)
    except entitlements.EntitlementError:
        return _manual_block_response(
            signal,
            "Live entitlement is required.",
            status_code=403,
            block_code="LIVE_ENTITLEMENT_REQUIRED",
        )
    return None


def _attach_manual_idempotency_or_block(signal: NormalizedSignal, request: Request) -> JSONResponse | None:
    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    if idempotency_key:
        if len(idempotency_key) > 255:
            reason = "Manual live order blocked: Idempotency-Key is too long."
            return _manual_block_response(signal, reason, status_code=409, block_code="IDEMPOTENCY_KEY_INVALID")
        raw_payload = dict(signal.raw_payload or {})
        raw_payload["idempotency_key"] = idempotency_key
        raw_payload["idempotency_source"] = "Idempotency-Key"
        signal.raw_payload = raw_payload
        return None
    if _manual_live_order_requires_idempotency_key():
        reason = "Manual live order blocked: Idempotency-Key header is required."
        return _manual_block_response(signal, reason, status_code=409, block_code="IDEMPOTENCY_KEY_REQUIRED")
    return None


def _unresolved_contract_block_or_none(signal: NormalizedSignal) -> JSONResponse | None:
    """Block ENTRY signals whose option contract never resolved (no securityId and
    no strike/expiry) BEFORE they reach the execution router, with a reason that
    explains WHY resolution failed instead of a generic 'securityId not resolved'."""
    if signal.security_id or (signal.strike is not None and signal.expiry):
        return None

    from app.services.dhan_marketfeed_ws import marketfeed_ws_status

    atm = get_atm_option_snapshot(option_side=signal.option_side or "CE", lots=1, allow_rest_fallback=True)
    spot_status = str(atm.get("niftySpotStatus") or "unknown")
    spot_available = atm.get("niftySpot") is not None
    feed = atm.get("marketfeed") if isinstance(atm.get("marketfeed"), dict) else marketfeed_ws_status()
    feed_error = feed.get("last_error") if isinstance(feed, dict) else None
    feed_connected = bool(feed.get("connected")) if isinstance(feed, dict) else False

    if spot_available:
        # Market data is fine — the contract simply wasn't supplied (live mode
        # does not auto-resolve ATM contracts; the quote panel must provide one).
        reason = (
            "Entry blocked before broker: no securityId/strike/expiry was supplied and "
            "auto ATM contract resolution is disabled in live mode. "
            "Wait for the quote panel to show a resolved contract, then retry."
        )
        return _manual_block_response(signal, reason, status_code=422, block_code="CONTRACT_NOT_SUPPLIED")

    detail = f"spot status: {spot_status}; feed connected: {feed_connected}"
    if feed_error:
        detail += f"; feed error: {feed_error}"
    reason = (
        "Entry blocked before broker: NIFTY spot is unavailable from the Dhan market feed, "
        f"so the ATM strike and option contract could not be resolved ({detail}). "
        "No order was sent. Wait for the market feed to recover, then resend."
    )
    return _manual_block_response(signal, reason, status_code=503, block_code="MARKET_DATA_UNAVAILABLE")


def _manual_block_response(
    signal: NormalizedSignal,
    reason: str,
    *,
    status_code: int,
    block_code: str,
) -> JSONResponse:
    mode = get_engine_mode(legacy_fallback=False) or get_engine_mode()
    error = classify_failure(
        reason,
        source="NOVA_BACKEND",
        signal=signal,
        mode=mode,
        order_sent_to_broker=False,
        money_at_risk=mode == "live",
    )
    execution_result = {
        "blocked": True,
        "success": False,
        "status": "BLOCKED",
        "reason": reason,
        "block_code": block_code,
        "normalizedError": error,
        "payload_format": signal.payload_format,
        "normalized_action": signal.action,
        "normalized_side": signal.side,
        "normalized_qty": signal.qty,
        "normalized_symbol": signal.symbol,
        "normalized_strike": signal.strike,
        "normalized_expiry": signal.expiry,
        "normalized_option_side": signal.option_side,
    }
    return JSONResponse(
        status_code=status_code,
        content=_manual_response(execution_result),
    )


def _latest_nifty_reference_price() -> float | None:
    latest_signal = get_app_state().get("last_signal")
    if not isinstance(latest_signal, dict):
        return None
    for key in ("niftyPrice", "niftySpot"):
        value = latest_signal.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _route_manual_entry(signal: NormalizedSignal) -> dict[str, Any]:
    position = get_open_position()
    if position.get("has_open_position"):
        return {
            "ok": False,
            "operationState": "REJECTED_EXISTING_POSITION",
            "message": "Order not placed: exit the current position before opening another.",
            "position": position,
            "portfolio": paper_wallet_snapshot() if get_engine_mode(legacy_fallback=False) == "paper" else None,
        }
    try:
        return _manual_response(route_signal(signal), operation="ENTRY")
    except Exception:
        return _manual_reconciliation_response(
            "Paper order state is uncertain because position persistence did not complete."
        )


def _run_manual_operation(
    *,
    request: Request,
    operation: str,
    payload: dict[str, Any],
    execute,
) -> Any:
    """Durably deduplicate manual requests by owner and operation key."""
    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    if not idempotency_key:
        return execute()
    user = current_execution_user()
    if user is None:
        return _manual_reconciliation_response(
            "Manual order idempotency requires authenticated owner context."
        )
    mode = get_engine_mode(legacy_fallback=False) or get_engine_mode() or "unknown"
    scope = f"manual_{str(mode).lower()}_operation"
    payload_hash = order_idempotency.stable_payload_hash(
        {"operation": operation, "mode": mode, "payload": payload}
    )
    try:
        claim = order_idempotency.claim_live_order_intent(
            user_id=user.id,
            scope=scope,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            action=operation,
            metadata={"mode": mode, "manual_order": True},
        )
    except (order_idempotency.OrderIdempotencyUnavailable, ValueError):
        return _manual_reconciliation_response(
            "Manual order idempotency is unavailable; no new order was submitted."
        )

    if claim.status == "conflict":
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "operationState": "REJECTED",
                "message": "Idempotency key was already used for a different manual order.",
            },
        )
    if claim.status == "replay":
        return {**(claim.result_summary or {}), "idempotentReplay": True}
    if not claim.fresh:
        return {
            "ok": False,
            "operationState": "RECONCILIATION_REQUIRED",
            "message": "The original manual order is still being reconciled.",
            "idempotentReplay": True,
        }

    try:
        result = execute()
    except Exception:
        result = _manual_reconciliation_response(
            "Manual order state is uncertain because authoritative persistence did not complete."
        )
    if isinstance(result, JSONResponse):
        return result
    try:
        order_idempotency.complete_order_intent(
            claim.intent_id or "",
            result_summary=dict(result),
        )
    except Exception:
        return _manual_reconciliation_response(
            "Manual order completed but its durable replay record could not be finalized."
        )
    return result


def _manual_response(execution_result: dict[str, Any], *, operation: str | None = None) -> dict[str, Any]:
    blocked = bool(execution_result.get("blocked"))
    position = get_open_position()
    operation_name = str(operation or execution_result.get("normalized_action") or "").upper()
    provider_status = str(execution_result.get("status") or "").upper()
    successful_request = not blocked and execution_result.get("success") is not False
    operation_state = "FAILED"
    ok = False

    if blocked:
        block_code = str(execution_result.get("block_code") or "")
        operation_state = {
            "MARKET_DATA_UNAVAILABLE": "REJECTED_NO_FRESH_QUOTE",
            "CONTRACT_NOT_SUPPLIED": "REJECTED_INVALID_CONTRACT",
            "ENTRY_REQUEST_BLOCKED": "REJECTED_ENGINE_STATE",
        }.get(block_code, "REJECTED")
    elif operation_name in {"ENTRY", "REVERSE"}:
        proof = _open_position_proof(position)
        if successful_request and proof["valid"]:
            ok = True
            operation_state = "POSITION_OPEN"
        elif successful_request and provider_status in {"PENDING_CONFIRMATION", "PAPER_ORDER_ACCEPTED"}:
            operation_state = "PAPER_ORDER_ACCEPTED"
        elif successful_request:
            operation_state = "RECONCILIATION_REQUIRED"
    elif operation_name == "EXIT":
        if successful_request and not position.get("has_open_position"):
            ok = True
            operation_state = "POSITION_CLOSED"
        elif successful_request:
            operation_state = "RECONCILIATION_REQUIRED"
    elif successful_request:
        # Non-order callers of this helper retain a truthful request result,
        # but never receive POSITION_OPEN without persisted position proof.
        operation_state = "REQUEST_COMPLETED"
        ok = True

    message = (
        "Position opened."
        if operation_state == "POSITION_OPEN"
        else "Position closed."
        if operation_state == "POSITION_CLOSED"
        else "Order status uncertain. New entries are blocked while NOVA reconciles the position."
        if operation_state == "RECONCILIATION_REQUIRED"
        else (
            execution_result.get("reason")
            or execution_result.get("error")
            or execution_result.get("message")
            or execution_result.get("status")
            or "Manual order flow completed."
        )
    )
    portfolio = paper_wallet_snapshot() if get_engine_mode(legacy_fallback=False) == "paper" else None
    return {
        "ok": ok,
        "operationState": operation_state,
        "message": message,
        "position": position if position.get("has_open_position") else None,
        "portfolio": portfolio,
        "executionResult": execution_result,
        "normalizedError": execution_result.get("normalizedError") if isinstance(execution_result.get("normalizedError"), dict) else None,
        "orderJourney": execution_result.get("orderJourney"),
    }


def _open_position_proof(position: dict[str, Any]) -> dict[str, Any]:
    required = {
        "position_id": position.get("entry_order_id"),
        "entry_price": _number(position.get("entry_price")),
        "qty": int(position.get("qty") or 0),
        "contract": position.get("security_id") or position.get("trading_symbol"),
    }
    return {
        **required,
        "valid": bool(
            position.get("has_open_position")
            and required["position_id"]
            and required["entry_price"] is not None
            and required["entry_price"] > 0
            and required["qty"] > 0
            and required["contract"]
        ),
    }


def _manual_reconciliation_response(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "operationState": "RECONCILIATION_REQUIRED",
        "message": message,
        "position": get_open_position() or None,
        "portfolio": paper_wallet_snapshot() if get_engine_mode(legacy_fallback=False) == "paper" else None,
        "executionResult": {"success": False, "status": "RECONCILIATION_REQUIRED"},
        "normalizedError": None,
        "orderJourney": None,
    }


def _market_closed_quote(*, side: str, lots: int, atm: dict[str, Any]) -> dict[str, Any]:
    mode = get_engine_mode(legacy_fallback=False) or get_engine_mode()
    error = classify_failure(
        "Market is closed; Dhan WebSocket and REST LTP fetching are disabled.",
        source="DHAN",
        mode=mode,
        order_sent_to_broker=False,
        money_at_risk=False,
        debug_pack={"atm": atm, "side": side},
    )
    return {
        "ok": False,
        "mode": mode,
        "side": side,
        "lots": lots,
        "qty": current_nifty_lot_size() * lots,
        "securityId": None,
        "tradingSymbol": None,
        "estimatedPremium": None,
        "estimatedCost": None,
        "estimatedMargin": None,
        "resolution": {
            "ok": False,
            "security_id": None,
            "method": "MARKET_CLOSED",
            "reason": "Market is closed; LTP fetching is disabled.",
        },
        "atm": atm,
        "lastUpdatedAt": atm.get("computedAt") or utc_now(),
        "message": error["userMessage"],
        "normalizedError": error,
    }


def _exit_level_error(message: str, *, mode: str | None) -> dict[str, Any]:
    error = classify_failure(
        message,
        source="RISK_ENGINE",
        mode=mode,
        order_sent_to_broker=False,
        money_at_risk=False,
    )
    return {"ok": False, "message": error["userMessage"], "normalizedError": error}


def _quantity_error(message: str, *, mode: str | None) -> dict[str, Any]:
    error = classify_failure(
        message,
        source="RISK_ENGINE",
        mode=mode,
        order_sent_to_broker=False,
        money_at_risk=False,
    )
    return {"ok": False, "message": error["userMessage"], "normalizedError": error}


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
