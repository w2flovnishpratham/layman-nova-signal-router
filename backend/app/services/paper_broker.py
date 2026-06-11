from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from app.services.audit_logger import log_order_event
from app.services.dhan_client import (
    DhanFundsResult,
    DhanListResult,
    DhanLtpResult,
    DhanOrderResult,
    DhanOrderStatusResult,
    DhanValidationResult,
    RealDhanClient,
)
from app.services.paper_portfolio import apply_paper_entry, apply_paper_exit, paper_wallet_snapshot
from app.services.state_store import LOG_FILES, get_engine_mode, get_paper_position, get_runtime_settings, scoped_log_file, utc_now


_ORDER_LOCK = threading.RLock()
_ORDERS: list[dict[str, Any]] = []


def _round_tick(price: float) -> float:
    return round(max(round(price / 0.05) * 0.05, 0.05), 2)


def _simulated_charges(qty: int, price: float, transaction_type: str) -> float:
    turnover = max(float(qty) * float(price), 0.0)
    transaction = transaction_type.upper()
    brokerage = 20.0
    stt = turnover * 0.000625 if transaction == "SELL" else 0.0
    exchange_txn = turnover * 0.00053
    gst = (brokerage + exchange_txn) * 0.18
    sebi = turnover * (10.0 / 10_000_000.0)
    stamp = turnover * 0.00003 if transaction == "BUY" else 0.0
    return round(brokerage + stt + exchange_txn + gst + sebi + stamp, 2)


def _record_order(order: dict[str, Any]) -> None:
    with _ORDER_LOCK:
        _ORDERS.append(dict(order))
        del _ORDERS[:-100]
        path = LOG_FILES.get("paper_orders")
        if path is None:
            return
        path = scoped_log_file(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": utc_now(), **order}, separators=(",", ":")) + "\n")


class PaperBroker:
    """High-fidelity paper broker. It never calls a Dhan order endpoint."""

    def _ltp(self, *, client_id: str, access_token: str, payload: dict[str, Any]) -> DhanLtpResult:
        result = RealDhanClient().get_ltp(
            client_id=client_id,
            access_token=access_token,
            exchange_segment=str(payload.get("exchangeSegment") or "NSE_FNO"),
            security_id=str(payload.get("securityId") or ""),
        )
        # Backward compatibility for old local tests that selected MOCK only
        # through the environment. Explicit Paper mode always requires real LTP.
        if not result.success and get_engine_mode(legacy_fallback=False) is None:
            return DhanLtpResult(
                success=True,
                message="Legacy local paper fill.",
                ltp=100.0,
                exchange_segment=str(payload.get("exchangeSegment") or "NSE_FNO"),
                security_id=str(payload.get("securityId") or ""),
                raw_response={"legacy_local_fallback": True},
            )
        return result

    def _place(self, *, client_id: str, access_token: str, payload: dict[str, Any], super_order: bool) -> DhanOrderResult:
        quote = self._ltp(client_id=client_id, access_token=access_token, payload=payload)
        if not quote.success or quote.ltp is None:
            return DhanOrderResult(
                success=False,
                order_id=None,
                status="REJECTED",
                avg_price=None,
                raw_response={"mode": "paper", "ltp": quote.raw_response},
                error=quote.message or quote.error or "Paper fill rejected because real Dhan LTP is unavailable.",
            )

        runtime = get_runtime_settings()
        slippage = max(float(runtime.get("paper_slippage_percent") or 0.10), 0.0) / 100
        transaction = str(payload.get("transactionType") or "BUY").upper()
        multiplier = 1 + slippage if transaction == "BUY" else 1 - slippage
        fill_price = _round_tick(float(quote.ltp) * multiplier)
        qty = max(int(payload.get("quantity") or 0), 0)
        order_id = f"PAPER-{'SUPER-' if super_order else ''}{uuid.uuid4().hex[:12].upper()}"
        charges = _simulated_charges(qty, fill_price, transaction)
        symbol = str(payload.get("tradingSymbol") or payload.get("securityId") or "NIFTY option")

        try:
            if transaction == "BUY":
                apply_paper_entry(qty=qty, price=fill_price, charges=charges, symbol=symbol, order_id=order_id)
            else:
                apply_paper_exit(qty=qty, exit_price=fill_price, charges=charges, symbol=symbol, order_id=order_id)
        except ValueError as exc:
            return DhanOrderResult(
                success=False,
                order_id=None,
                status="REJECTED",
                avg_price=None,
                raw_response={"mode": "paper"},
                error=str(exc),
            )

        raw = {
            "orderId": order_id,
            "orderStatus": "TRADED",
            "avgPrice": fill_price,
            "quantity": qty,
            "filledQty": qty,
            "remainingQuantity": 0,
            "transactionType": transaction,
            "tradingSymbol": symbol,
            "securityId": str(payload.get("securityId") or ""),
            "exchangeSegment": str(payload.get("exchangeSegment") or "NSE_FNO"),
            "paper": True,
            "mode": "paper",
            "simulatedCharges": charges,
            "sourceLtp": quote.ltp,
            "slippagePercent": slippage * 100,
            "superOrder": super_order,
            "targetPrice": payload.get("targetPrice"),
            "stopLossPrice": payload.get("stopLossPrice"),
        }
        _record_order(raw)
        log_order_event({"event": "PAPER_ORDER_FILLED", **raw})
        return DhanOrderResult(success=True, order_id=order_id, status="TRADED", avg_price=fill_price, raw_response=raw)

    def place_order(self, *, client_id: str, access_token: str, payload: dict[str, Any]) -> DhanOrderResult:
        return self._place(client_id=client_id, access_token=access_token, payload=payload, super_order=False)

    def place_super_order(self, *, client_id: str, access_token: str, payload: dict[str, Any]) -> DhanOrderResult:
        return self._unsupported_super_order()

    def modify_super_order(self, *, client_id: str, access_token: str, order_id: str, payload: dict[str, Any]) -> DhanOrderResult:
        return self._unsupported_super_order(order_id)

    def cancel_super_order_leg(self, *, client_id: str, access_token: str, order_id: str, leg_name: str) -> DhanOrderResult:
        return self._unsupported_super_order(order_id)

    @staticmethod
    def _unsupported_super_order(order_id: str | None = None) -> DhanOrderResult:
        return DhanOrderResult(
            success=False,
            order_id=order_id,
            status="REJECTED",
            avg_price=None,
            raw_response={"orderId": order_id, "orderStatus": "REJECTED", "paper": True},
            error="PaperBroker does not support Dhan Super Orders; paper exits are managed by the option monitor.",
        )

    def poll_order_status(
        self,
        *,
        client_id: str,
        access_token: str,
        order_id: str,
        max_polls: int = 4,
        poll_delay: float = 1.5,
    ) -> DhanOrderStatusResult:
        with _ORDER_LOCK:
            order = next((item for item in reversed(_ORDERS) if item.get("orderId") == order_id), None)
        return DhanOrderStatusResult(
            success=order is not None,
            order_id=order_id,
            order_status="TRADED" if order else "NOT_FOUND",
            is_terminal=True,
            is_filled=order is not None,
            avg_price=float(order["avgPrice"]) if order else None,
            raw_response=order,
            error=None if order else "Paper order not found.",
            filled_qty=int(order["quantity"]) if order else None,
            remaining_qty=0 if order else None,
        )

    def validate_token(self, *, client_id: str, access_token: str) -> DhanValidationResult:
        return RealDhanClient().validate_token(client_id=client_id, access_token=access_token)

    def get_positions(self, *, client_id: str, access_token: str) -> list[dict[str, Any]]:
        return self.get_positions_snapshot(client_id=client_id, access_token=access_token).items

    def get_positions_snapshot(self, *, client_id: str, access_token: str) -> DhanListResult:
        position = get_paper_position()
        if not position.get("has_open_position"):
            return DhanListResult(success=True, message="Paper position is flat.", items=[])
        item = {
            "tradingSymbol": position.get("trading_symbol"),
            "securityId": position.get("security_id"),
            "netQty": position.get("qty"),
            "positionType": "LONG",
            "productType": position.get("product_type"),
            "exchangeSegment": position.get("exchange_segment"),
            "buyAvg": position.get("entry_price"),
            "paper": True,
        }
        return DhanListResult(success=True, message="Paper position fetched.", items=[item], raw_response=[item])

    def get_order_book(self, *, client_id: str, access_token: str) -> DhanListResult:
        with _ORDER_LOCK:
            items = [dict(item) for item in _ORDERS]
        return DhanListResult(success=True, message="Paper order book fetched.", items=items, raw_response=items)

    def get_ltp(self, *, client_id: str, access_token: str, exchange_segment: str, security_id: str) -> DhanLtpResult:
        return RealDhanClient().get_ltp(
            client_id=client_id,
            access_token=access_token,
            exchange_segment=exchange_segment,
            security_id=security_id,
        )

    def get_fund_limit(self, *, client_id: str, access_token: str) -> DhanFundsResult:
        wallet = paper_wallet_snapshot()
        return DhanFundsResult(
            success=True,
            message="Paper portfolio fetched.",
            client_id=client_id,
            available_balance=wallet["available_balance"],
            withdrawable_balance=wallet["withdrawable_balance"],
            utilized_amount=wallet["utilized_amount"],
            sod_limit=wallet["sod_limit"],
            collateral_amount=wallet["collateral_amount"],
            blocked_payout_amount=wallet["blocked_payout_amount"],
            raw_response=wallet,
        )
