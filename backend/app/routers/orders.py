from __future__ import annotations

# Reconciliation boundaries intentionally convert uncertain I/O failures into
# durable, user-visible operation states instead of leaking provider errors.
# ruff: noqa: BLE001
import time
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import (
    DEFAULT_EXCHANGE_SEGMENT,
    DEFAULT_ORDER_TYPE,
    DEFAULT_PRODUCT_TYPE,
    settings,
)
from app.db.engine import database_configured
from app.routers.setup import current_nifty_lot_size
from app.schemas.signal import NormalizedSignal
from app.services import entitlements, order_idempotency, setup_configuration
from app.services.atm_ltp_service import get_atm_option_snapshot
from app.services.audit_logger import read_jsonl
from app.services.credential_vault import get_webhook_secret
from app.services.execution_context import current_execution_user
from app.services.execution_router import route_signal
from app.services.normalized_errors import classify_failure
from app.services.paper_broker import confirm_position_adjustment_fill
from app.services.paper_portfolio import (
    apply_paper_add_fill,
    apply_paper_partial_exit,
    get_paper_portfolio,
    paper_wallet_snapshot,
)
from app.services.portfolio_analytics import persist_paper_trade
from app.services.quote_service import get_quote_snapshot
from app.services.risk_manager import _market_is_open
from app.services.security_id_resolver import (
    resolve_security_id,
    suggest_option_contract,
)
from app.services.state_store import (
    ensure_open_position_identity,
    get_app_state,
    get_engine_mode,
    get_open_position,
    get_runtime_settings,
    patch_open_position_cas,
    utc_now,
)

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
    unit: Literal["PERCENTAGE", "POINTS", "ABSOLUTE_TRIGGER"]
    stopLossValue: float = Field(gt=0)
    targetValue: float = Field(gt=0)
    expectedPositionVersion: int = Field(ge=1)


class QuantityRequest(BaseModel):
    qty: int = Field(ge=1)


class AddLotsRequest(BaseModel):
    lots: int = Field(ge=1, le=20)
    expectedPositionVersion: int = Field(ge=1)
    protectionMode: Literal["KEEP_EXISTING", "RECALCULATE_FROM_AVERAGE", "CUSTOM"]
    customStopLossPrice: float | None = Field(default=None, gt=0)
    customTargetPrice: float | None = Field(default=None, gt=0)


class PartialExitRequest(BaseModel):
    lots: int = Field(ge=1, le=20)
    expectedPositionVersion: int = Field(ge=1)


class QuoteRequest(BaseModel):
    side: str = Field(pattern="^(CE|PE)$")
    lots: int = Field(default=1, ge=1, le=20)
    strike: float | None = None
    expiry: str | None = None
    securityId: str | None = None
    tradingSymbol: str | None = None


def _missing_position_operation_key(request: Request) -> JSONResponse | None:
    if str(request.headers.get("Idempotency-Key") or "").strip():
        return None
    return JSONResponse(
        status_code=400,
        content={
            "ok": False,
            "operationState": "REJECTED",
            "message": "Idempotency-Key header is required for position operations.",
        },
    )


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
        execute=lambda: _manual_response(
            route_signal(signal, runtime=_selected_manual_entry_runtime()),
            operation="REVERSE",
        ),
    )


@router.patch("/orders/active-position/quantity")
def update_active_position_quantity(body: QuantityRequest) -> dict[str, Any]:
    return {
        "ok": False,
        "message": (
            "Direct quantity replacement is retired. Use Add Lots or Partial Exit "
            "with an idempotency key and the expected position version."
        ),
    }


@router.post("/orders/active-position/add-lots")
def add_active_position_lots(request: Request, body: AddLotsRequest) -> Any:
    missing_key = _missing_position_operation_key(request)
    if missing_key is not None:
        return missing_key
    return _run_manual_operation(
        request=request,
        operation="ADD_LOTS",
        payload=body.model_dump(),
        execute=lambda: _apply_paper_position_adjustment(
            operation="add",
            lots=body.lots,
            expected_version=body.expectedPositionVersion,
            operation_id=str(request.headers["Idempotency-Key"]),
            protection_mode=body.protectionMode,
            custom_stop_loss=body.customStopLossPrice,
            custom_target=body.customTargetPrice,
        ),
    )


@router.post("/orders/active-position/partial-exit")
def partial_exit_active_position(request: Request, body: PartialExitRequest) -> Any:
    missing_key = _missing_position_operation_key(request)
    if missing_key is not None:
        return missing_key
    return _run_manual_operation(
        request=request,
        operation="PARTIAL_EXIT",
        payload=body.model_dump(),
        execute=lambda: _apply_paper_position_adjustment(
            operation="partial_exit",
            lots=body.lots,
            expected_version=body.expectedPositionVersion,
            operation_id=str(request.headers["Idempotency-Key"]),
            protection_mode=None,
            custom_stop_loss=None,
            custom_target=None,
        ),
    )


def _apply_paper_position_adjustment(
    *,
    operation: str,
    lots: int | None,
    expected_version: int,
    operation_id: str,
    protection_mode: str | None,
    custom_stop_loss: float | None,
    custom_target: float | None,
) -> dict[str, Any]:
    mode = get_engine_mode(legacy_fallback=False) or get_engine_mode()
    if mode != "paper":
        return {
            "ok": False,
            "operationState": "REJECTED",
            "message": "Unavailable until broker verification",
        }
    position = ensure_open_position_identity()
    if not position.get("has_open_position"):
        return {"ok": False, "operationState": "REJECTED", "message": "No active position exists."}
    current_version = int(position.get("position_version") or 0)
    if current_version != int(expected_version):
        return {
            "ok": False,
            "operationState": "POSITION_CHANGED",
            "message": "The position changed. Refresh before submitting this operation.",
            "positionVersion": current_version,
        }
    if isinstance(position.get("exit_operation"), dict):
        return {"ok": False, "operationState": "EXIT_IN_PROGRESS", "message": "An exit is already in progress."}

    lot_size = max(int(current_nifty_lot_size() or 1), 1)
    current_qty = int(position.get("qty") or 0)
    if current_qty <= 0 or current_qty % lot_size:
        return {
            "ok": False,
            "operationState": "REJECTED",
            "message": "The tracked position is not a whole-lot position.",
        }
    current_lots = current_qty // lot_size
    live_pnl = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
    change_lots = int(lots or 0)
    if change_lots <= 0:
        return {"ok": False, "operationState": "REJECTED", "message": "Choose at least one lot."}
    if operation == "add" and current_lots + change_lots > 20:
        return {"ok": False, "operationState": "REJECTED", "message": "A position cannot exceed 20 lots."}
    if operation == "partial_exit" and change_lots >= current_lots:
        return {
            "ok": False,
            "operationState": "REJECTED",
            "message": "Partial exit must leave at least one whole lot open. Use Exit All to close the position.",
        }
    change_qty = change_lots * lot_size
    portfolio = get_paper_portfolio()
    open_trade = dict(portfolio.open_trade or {})
    if not open_trade or int(open_trade.get("qty") or 0) != current_qty:
        return {
            "ok": False,
            "operationState": "RECONCILIATION_REQUIRED",
            "message": "The Paper ledger does not match the tracked position.",
        }
    tracked_contract = str(position.get("trading_symbol") or position.get("security_id") or "")
    ledger_contract = str(open_trade.get("symbol") or "")
    if tracked_contract and ledger_contract and tracked_contract != ledger_contract:
        return {
            "ok": False,
            "operationState": "RECONCILIATION_REQUIRED",
            "message": "The Paper ledger contract does not match the tracked position.",
        }
    if operation == "add":
        risk_error = _paper_add_risk_error(change_qty=change_qty)
        if risk_error:
            return {"ok": False, "operationState": "REJECTED", "message": risk_error}
    fill = confirm_position_adjustment_fill(
        position=position,
        qty=change_qty,
        transaction_type="BUY" if operation == "add" else "SELL",
        operation_id=operation_id,
    )
    if not fill.get("success") or fill.get("status") != "TRADED":
        return {
            "ok": False,
            "operationState": "REJECTED_NO_FRESH_QUOTE",
            "message": str(fill.get("error") or "A confirmed simulated market fill is unavailable."),
        }
    fill_price = float(fill["fill_price"])
    charges = float(fill["charges"])
    fill_order_id = str(fill["order_id"])
    if operation == "add":
        required = change_qty * fill_price + charges
        if required > float(portfolio.available_balance or 0):
            return {"ok": False, "operationState": "REJECTED", "message": "Insufficient virtual balance."}
        entry_price = float(open_trade.get("entry_price") or 0)
        if entry_price <= 0:
            return {
                "ok": False,
                "operationState": "RECONCILIATION_REQUIRED",
                "message": "The original confirmed entry fill is unavailable.",
            }
        tracked_entry_price = _number(position.get("entry_price"))
        if tracked_entry_price is None or abs(tracked_entry_price - entry_price) > 0.0001:
            return {
                "ok": False,
                "operationState": "RECONCILIATION_REQUIRED",
                "message": "The Paper ledger average does not match the tracked confirmed entry.",
            }
        new_qty = current_qty + change_qty
        new_entry_price = round(
            ((entry_price * current_qty) + (fill_price * change_qty)) / new_qty,
            4,
        )
        levels = _add_lots_protection(
            position=position,
            live_pnl=live_pnl,
            mode=str(protection_mode or ""),
            new_average=new_entry_price,
            confirmed_fill=fill_price,
            custom_stop=custom_stop_loss,
            custom_target=custom_target,
        )
        if isinstance(levels, str):
            return {"ok": False, "operationState": "REJECTED", "message": levels}
    else:
        new_qty = current_qty - change_qty
        new_entry_price = float(position.get("entry_price") or open_trade.get("entry_price") or 0)
        levels = position.get("active_exit_levels")

    if not _mark_adjustment_stage(
        operation_id,
        "FILL_CONFIRMED",
        {"fill_order_id": fill_order_id, "fill_price": fill_price, "filled_qty": change_qty},
    ):
        return {
            "ok": False,
            "operationState": "RECONCILIATION_REQUIRED",
            "message": "The fill was confirmed but its durable operation stage could not be recorded.",
        }
    event = {
        "operation_id": operation_id,
        "fill_order_id": fill_order_id,
        "type": "ADD_LOTS" if operation == "add" else "PARTIAL_EXIT",
        "qty": change_qty,
        "fill_price": fill_price,
        "fill_status": "TRADED",
        "accepted_at": utc_now(),
    }

    new_live_pnl = dict(live_pnl)
    new_live_pnl["qty"] = new_qty
    new_live_pnl["entry_price"] = new_entry_price
    new_live_pnl["unrealized_pnl"] = round((fill_price - new_entry_price) * new_qty, 2)
    exposure = new_entry_price * new_qty
    new_live_pnl["pnl_percent"] = (
        round(float(new_live_pnl["unrealized_pnl"]) / exposure * 100, 2) if exposure else 0.0
    )
    new_live_pnl["last_checked_at"] = utc_now()
    position_patch: dict[str, Any] = {
        "qty": new_qty,
        "requested_qty": new_qty,
        "filled_qty": new_qty,
        "entry_price": new_entry_price,
        "live_pnl": new_live_pnl,
        "last_position_operation": event,
    }
    if operation == "add" and isinstance(levels, dict):
        position_patch["active_exit_levels"] = levels
        new_live_pnl["sl_price"] = levels["stopLossPrice"]
        new_live_pnl["tp_price"] = levels["targetPrice"]
    applied, updated = patch_open_position_cas(
        position_id=str(position["position_id"]),
        position_version=current_version,
        patch=position_patch,
        reject_when_exit_pending=True,
    )
    if not applied:
        _mark_adjustment_stage(
            operation_id,
            "RECONCILIATION_REQUIRED",
            {"reason": "position_cas_conflict"},
        )
        return {
            "ok": False,
            "operationState": "RECONCILIATION_REQUIRED",
            "message": "The fill was confirmed but the position changed before it could be applied.",
            "positionVersion": int(updated.get("position_version") or 0),
        }
    _mark_adjustment_stage(
        operation_id,
        "POSITION_APPLIED",
        {"position_version": int(updated.get("position_version") or 0)},
    )

    try:
        if operation == "add":
            apply_paper_add_fill(
                qty=change_qty,
                fill_price=fill_price,
                charges=charges,
                order_id=fill_order_id,
            )
        else:
            portfolio = apply_paper_partial_exit(
                qty=change_qty,
                exit_price=fill_price,
                charges=charges,
                symbol=str(position.get("trading_symbol") or position.get("symbol") or "NIFTY"),
                order_id=fill_order_id,
            )
            if portfolio.closed_trades:
                persist_paper_trade(portfolio.closed_trades[-1])
    except Exception:
        _mark_adjustment_stage(
            operation_id,
            "RECONCILIATION_REQUIRED",
            {"reason": "paper_ledger_write_failed"},
        )
        return {
            "ok": False,
            "operationState": "RECONCILIATION_REQUIRED",
            "message": "Position state changed, but the Paper ledger requires reconciliation.",
            "positionVersion": int(updated.get("position_version") or 0),
        }
    _mark_adjustment_stage(operation_id, "LEDGER_APPLIED", {"fill_order_id": fill_order_id})
    return {
        "ok": True,
        "operationState": "POSITION_UPDATED",
        "status": "TRADED",
        "message": "Lots added." if operation == "add" else "Partial exit completed.",
        "qty": new_qty,
        "lots": new_qty // lot_size,
        "positionVersion": int(updated.get("position_version") or 0),
        "fillPrice": fill_price,
        "entryPrice": new_entry_price,
        "fillOrderId": fill_order_id,
        "activeExitLevels": levels if isinstance(levels, dict) else None,
        "portfolio": paper_wallet_snapshot(),
    }


def _paper_add_risk_error(*, change_qty: int) -> str | None:
    runtime = get_runtime_settings()
    app_state = get_app_state()
    if bool(runtime.get("emergency_stop") or app_state.get("emergency_stop")):
        return "Add Lots is blocked while Emergency Stop is active."
    if bool(runtime.get("global_kill_switch") or app_state.get("global_kill_switch")):
        return "Add Lots is blocked while the global kill switch is active."
    max_qty = int(runtime.get("max_qty_per_order") or 0)
    if max_qty > 0 and change_qty > max_qty:
        return f"Add Lots exceeds the configured maximum order quantity of {max_qty}."
    max_daily_loss = float(runtime.get("max_daily_loss") or 0)
    session_pnl = paper_wallet_snapshot().get("session_pnl")
    if (
        max_daily_loss > 0
        and isinstance(session_pnl, (int, float))
        and float(session_pnl) <= -max_daily_loss
    ):
        return "Add Lots is blocked because the daily loss cap has been reached."
    return None


def _existing_protection(
    position: dict[str, Any],
    live_pnl: dict[str, Any],
) -> tuple[float | None, float | None, str]:
    active = (
        position.get("active_exit_levels")
        if isinstance(position.get("active_exit_levels"), dict)
        else {}
    )
    stop = _number(
        active.get("stopLossPrice")
        or live_pnl.get("sl_price")
        or position.get("broker_sl_price")
    )
    target = _number(
        active.get("targetPrice")
        or live_pnl.get("tp_price")
        or position.get("broker_tp_price")
    )
    return stop, target, str(active.get("source") or "existing")


def _add_lots_protection(
    *,
    position: dict[str, Any],
    live_pnl: dict[str, Any],
    mode: str,
    new_average: float,
    confirmed_fill: float,
    custom_stop: float | None,
    custom_target: float | None,
) -> dict[str, Any] | str:
    old_stop, old_target, old_source = _existing_protection(position, live_pnl)
    source = ""
    if mode == "KEEP_EXISTING":
        stop, target, source = old_stop, old_target, old_source
        if stop is None or target is None:
            return "Existing SL/TP triggers are unavailable. Choose recalculate or custom protection."
    elif mode == "RECALCULATE_FROM_AVERAGE":
        old_average = _number(position.get("entry_price") or live_pnl.get("entry_price"))
        stop_percent = _number(
            position.get("stop_loss_percent")
            or live_pnl.get("stop_loss_percent")
        )
        target_percent = _number(
            position.get("take_profit_percent")
            or live_pnl.get("take_profit_percent")
        )
        if stop_percent is None and old_average and old_stop:
            stop_percent = max((old_average - old_stop) / old_average * 100, 0)
        if target_percent is None and old_average and old_target:
            target_percent = max((old_target - old_average) / old_average * 100, 0)
        if not stop_percent or not target_percent:
            return "Saved SL/TP percentages are unavailable. Choose custom protection."
        stop = round(new_average * (1 - stop_percent / 100), 2)
        target = round(new_average * (1 + target_percent / 100), 2)
        source = "add_lots_recalculated"
    elif mode == "CUSTOM":
        stop = _number(custom_stop)
        target = _number(custom_target)
        source = "add_lots_custom"
        if stop is None or target is None:
            return "Custom protection requires both SL and TP trigger prices."
    else:
        return "Choose how SL/TP protection should apply after adding lots."
    if stop is None or target is None or stop <= 0 or target <= 0 or stop >= target:
        return "Protection requires a positive SL below a positive TP."
    if stop >= confirmed_fill or target <= confirmed_fill:
        return "Protection would trigger immediately at the confirmed fill price."
    return {
        "source": source,
        "stopLossPrice": round(stop, 2),
        "targetPrice": round(target, 2),
        "acceptedAt": utc_now(),
        "referenceAverage": new_average,
    }


def _mark_adjustment_stage(
    operation_id: str,
    stage: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    user = current_execution_user()
    if user is None:
        # Direct service tests have no request-bound owner. HTTP operations
        # cannot reach this point without owner-scoped durable idempotency.
        return True
    try:
        order_idempotency.mark_order_intent_stage(
            user_id=user.id,
            scope="manual_paper_operation",
            idempotency_key=operation_id,
            stage=stage,
            metadata=metadata,
        )
    except (order_idempotency.OrderIdempotencyUnavailable, ValueError):
        return False
    return True


def _normalize_exit_levels(
    *,
    unit: str,
    stop_value: float,
    target_value: float,
    reference_price: float,
) -> tuple[float, float] | str:
    if reference_price <= 0 or stop_value <= 0 or target_value <= 0:
        return "SL/TP values and the confirmed entry price must be positive."
    if unit == "PERCENTAGE":
        if stop_value >= 100:
            return "Stop-loss percentage must be below 100%."
        stop = reference_price * (1 - stop_value / 100)
        target = reference_price * (1 + target_value / 100)
    elif unit == "POINTS":
        stop = reference_price - stop_value
        target = reference_price + target_value
    elif unit == "ABSOLUTE_TRIGGER":
        stop = stop_value
        target = target_value
    else:
        return "SL/TP unit must be Percentage, Points, or Absolute Trigger."
    stop = round(stop, 2)
    target = round(target, 2)
    if stop <= 0 or not stop < reference_price < target:
        return "Normalized SL must be below the confirmed entry and TP must be above it."
    return stop, target


@router.patch("/orders/active-position/exit-levels")
def update_active_position_exit_levels(body: ExitLevelsRequest) -> dict[str, Any]:
    position = ensure_open_position_identity()
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
    current_version = int(position.get("position_version") or 0)
    if current_version != body.expectedPositionVersion:
        return {
            "ok": False,
            "message": "The position changed. Refresh before editing SL/TP.",
            "operationState": "POSITION_CHANGED",
            "positionVersion": current_version,
        }
    live_pnl = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
    entry_price = _number(position.get("entry_price") or live_pnl.get("entry_price"))
    if entry_price is None or entry_price <= 0:
        return _exit_level_error("The confirmed entry price is unavailable.", mode=mode)
    normalized = _normalize_exit_levels(
        unit=body.unit,
        stop_value=body.stopLossValue,
        target_value=body.targetValue,
        reference_price=entry_price,
    )
    if isinstance(normalized, str):
        return _exit_level_error(normalized, mode=mode)
    stop_loss, target = normalized
    ltp = _number(live_pnl.get("ltp"))
    if ltp is not None and (stop_loss >= ltp or target <= ltp):
        return _exit_level_error(
            "SL must be below current LTP and TP must be above current LTP.",
            mode=mode,
        )

    exit_management = str(position.get("exit_management") or "").upper()
    if mode == "live" and exit_management != "DHAN_SUPER":
        return _exit_level_error(
            "Live SL/TP changes require a confirmed Dhan Super Order.",
            mode=mode,
        )
    # Live display changes only after Dhan confirms both Super Order legs.
    if mode == "live":
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
            return {
                "ok": False,
                "message": error["userMessage"],
                "operationState": (
                    "RECONCILIATION_REQUIRED"
                    if broker_update.get("reconciliation_required")
                    else "REJECTED"
                ),
                "normalizedError": error,
            }

    levels = {
        "source": "manual",
        "requestUnit": body.unit,
        "stopLossValue": body.stopLossValue,
        "targetValue": body.targetValue,
        "stopLossPrice": stop_loss,
        "targetPrice": target,
        "acceptedAt": utc_now(),
    }
    updated_live_pnl = dict(live_pnl)
    updated_live_pnl.update(
        {
            "sl_price": stop_loss,
            "tp_price": target,
            "exit_management": exit_management or "SERVER",
            "message": "Manual SL/TP levels are armed.",
            "last_checked_at": utc_now(),
        }
    )
    applied, updated = patch_open_position_cas(
        position_id=str(position["position_id"]),
        position_version=current_version,
        patch={
            "active_exit_levels": levels,
            "live_pnl": updated_live_pnl,
            "last_position_operation": {
                "type": "EDIT_PROTECTION",
                "accepted_at": utc_now(),
                "stop_loss_price": stop_loss,
                "target_price": target,
            },
        },
        reject_when_exit_pending=True,
    )
    if not applied:
        return {
            "ok": False,
            "message": (
                "The position changed while SL/TP was being updated."
                if mode == "paper"
                else "Broker protection may have changed; reconciliation is required."
            ),
            "operationState": "POSITION_CHANGED" if mode == "paper" else "RECONCILIATION_REQUIRED",
            "positionVersion": int(updated.get("position_version") or 0),
        }
    return {
        "ok": True,
        "message": "SL/TP levels updated.",
        "activeExitLevels": levels,
        "positionVersion": int(updated.get("position_version") or 0),
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
        return _manual_response(
            route_signal(signal, runtime=_selected_manual_entry_runtime()),
            operation="ENTRY",
        )
    except Exception:
        return _manual_reconciliation_response(
            "Paper order state is uncertain because position persistence did not complete."
        )


def _selected_manual_entry_runtime() -> dict[str, Any]:
    """Use the latest confirmed owner revision for the next manual entry.

    This does not rewrite the current position. Exits continue to use the
    protection and quantity already persisted on that position.
    """
    runtime = get_runtime_settings()
    user = current_execution_user()
    mode = get_engine_mode(legacy_fallback=False)
    if (
        user is None
        or mode not in {"paper", "live"}
        or not database_configured()
    ):
        return runtime
    configuration = setup_configuration.selected_configuration(user.id, mode)
    if configuration is None:
        return runtime
    return {
        **runtime,
        **dict(configuration.get("risk") or {}),
        **dict(configuration.get("configuration") or {}),
        "configuration_revision_id": configuration["id"],
        "configuration_revision": int(configuration["revision"]),
    }


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
