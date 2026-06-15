"""
Dhan client wrappers for MOCK and REAL modes.

The execution router decides whether a real order is allowed. This module only
performs the request it is given and never reads or exposes the access token.

Dhan v2 API base: https://api.dhan.co/v2
Order placement requires static IP whitelisting.
Read-only endpoints (orders, trades, funds, positions) are available for diagnostics.

Endpoints implemented:
  POST /orders            - place order
  GET  /orders/{order-id} - order status (poll after placement)
  GET  /orders            - full order book
  POST /super/orders      - place Super Order
  PUT  /super/orders/{id} - modify Super Order leg
  DELETE /super/orders/{id}/{leg} - cancel Super Order leg
  POST /marketfeed/ltp    - latest traded price snapshot
  GET  /fundlimit         - funds / wallet validation
  GET  /positions         - open positions
  GET  /profile           - token validation

Endpoints not yet implemented (phase 2 TODOs):
  GET  /trades            - trade book
  WebSocket /orderUpdate  - live order update stream
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.services.audit_logger import log_order_event
from app.services.dhan_debugger import build_dhan_headers_debug, get_outgoing_ip, validate_dhan_payload
from app.services.dhan_error_interpreter import interpret_dhan_error
from app.services.dhan_rate_limiter import after_dhan_response, before_dhan_request
from app.services.risk_manager import _market_is_open


logger = logging.getLogger("dhan_client")
DHAN_BASE_URL = "https://api.dhan.co/v2"

# Order statuses per Dhan v2 docs
DHAN_TERMINAL_STATUSES = {"TRADED", "REJECTED", "CANCELLED", "EXPIRED"}
DHAN_PENDING_STATUSES = {"TRANSIT", "PENDING"}
DHAN_OPEN_ORDER_STATUSES = DHAN_PENDING_STATUSES | {"PART_TRADED"}


@dataclass(frozen=True)
class DhanValidationResult:
    success: bool
    message: str
    status_code: int | None = None
    raw_response: dict[str, Any] | list[Any] | None = None


@dataclass(frozen=True)
class DhanFundsResult:
    success: bool
    message: str
    status_code: int | None = None
    client_id: str | None = None
    available_balance: float | None = None
    withdrawable_balance: float | None = None
    utilized_amount: float | None = None
    sod_limit: float | None = None
    collateral_amount: float | None = None
    blocked_payout_amount: float | None = None
    raw_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class DhanOrderStatusResult:
    """Result of polling GET /orders/{order-id}."""
    success: bool
    order_id: str | None
    order_status: str | None  # TRANSIT, PENDING, TRADED, REJECTED, CANCELLED, EXPIRED
    is_terminal: bool
    is_filled: bool  # True only when TRADED
    avg_price: float | None
    raw_response: dict[str, Any] | None
    error: str | None = None
    filled_qty: int | None = None
    remaining_qty: int | None = None


@dataclass(frozen=True)
class DhanListResult:
    success: bool
    message: str
    items: list[dict[str, Any]]
    status_code: int | None = None
    raw_response: dict[str, Any] | list[Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class DhanLtpResult:
    success: bool
    message: str
    ltp: float | None
    exchange_segment: str | None = None
    security_id: str | None = None
    status_code: int | None = None
    raw_response: dict[str, Any] | list[Any] | None = None
    error: str | None = None


class DhanOrderResult:
    def __init__(
        self,
        *,
        success: bool,
        order_id: str | None,
        status: str,
        avg_price: float | None,
        raw_response: dict[str, Any],
        error: str | None = None,
        interpreted_error: dict[str, str] | None = None,
    ) -> None:
        self.success = success
        self.order_id = order_id
        self.status = status
        self.avg_price = avg_price
        self.raw_response = raw_response
        self.error = error
        self.interpreted_error = interpreted_error


class RealDhanClient:
    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        expected_egress_ip: str | None = None,
    ) -> None:
        self.proxy_url = (proxy_url or "").strip() or None
        self.expected_egress_ip = (expected_egress_ip or "").strip() or None

    def _client(self, *, timeout: float) -> httpx.Client:
        options: dict[str, Any] = {
            "timeout": timeout,
            "event_hooks": {
                "request": [before_dhan_request],
                "response": [after_dhan_response],
            },
        }
        if self.proxy_url:
            options["proxy"] = self.proxy_url
        else:
            # Legacy single-user mode binds directly to the server's IPv4.
            options["transport"] = httpx.HTTPTransport(local_address="0.0.0.0")
        return httpx.Client(
            **options,
        )

    def _outgoing_ip_result(self) -> dict[str, Any]:
        if self.proxy_url:
            return {
                "ok": bool(self.expected_egress_ip),
                "outgoing_ip": self.expected_egress_ip,
                "source": "configured_proxy",
            }
        return get_outgoing_ip(timeout=2.0)

    def _headers(self, client_id: str, access_token: str) -> dict[str, str]:
        """
        Build Dhan v2 request headers.

        Per Dhan v2 official docs, only `access-token` is required as an auth header.
        `client-id` is NOT documented by Dhan but is included by default for compatibility
        (DHAN_SEND_CLIENT_ID_HEADER=true). Set DHAN_SEND_CLIENT_ID_HEADER=false to omit it --
        dhanClientId is always present in the order body, which is what Dhan requires.

        Headers always present:
          access-token: <jwt>          -- required for all Dhan v2 requests
          Content-Type: application/json -- required for POST requests
          Accept: application/json     -- recommended

        Headers conditionally present:
          client-id: <id>              -- only if DHAN_SEND_CLIENT_ID_HEADER=true (default)
        """
        headers: dict[str, str] = {
            "access-token": access_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if settings.DHAN_SEND_CLIENT_ID_HEADER:
            headers["client-id"] = client_id
        return headers

    def _parse_response(self, response: httpx.Response) -> dict[str, Any] | list[Any] | str:
        try:
            return response.json()
        except ValueError:
            return response.text

    def _error_message(self, data: dict[str, Any] | list[Any] | str, fallback: str) -> str:
        if isinstance(data, dict):
            for key in ("errorMessage", "internalErrorMessage", "remarks", "message", "error"):
                value = data.get(key)
                if value:
                    return str(value)
            error_code = data.get("errorCode") or data.get("internalErrorCode")
            if error_code:
                return str(error_code)
        if isinstance(data, str) and data.strip():
            return data.strip()
        return fallback

    def _optional_float(self, data: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = data.get(key)
            if value is None or value == "":
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def _optional_int(self, data: dict[str, Any], *keys: str) -> int | None:
        for key in keys:
            value = data.get(key)
            if value is None or value == "":
                continue
            try:
                return int(float(value))
            except (TypeError, ValueError):
                continue
        return None

    def _order_avg_price(self, data: dict[str, Any]) -> float | None:
        return self._optional_float(
            data,
            "avgPrice",
            "averageTradedPrice",
            "avgTradedPrice",
            "AvgTradedPrice",
            "TradedPrice",
            "tradedPrice",
        )

    def _order_filled_qty(self, data: dict[str, Any]) -> int | None:
        return self._optional_int(
            data,
            "filled_qty",
            "filledQty",
            "filledQuantity",
            "filled_quantity",
            "tradedQuantity",
            "tradedQty",
            "traded_quantity",
            "quantityTraded",
        )

    def _order_remaining_qty(self, data: dict[str, Any]) -> int | None:
        return self._optional_int(
            data,
            "remainingQuantity",
            "remainingQty",
            "remaining_quantity",
            "pendingQuantity",
            "pendingQty",
            "pending_quantity",
            "unfilledQuantity",
        )

    def _response_payload(self, parsed: dict[str, Any] | list[Any] | str) -> dict[str, Any]:
        return parsed if isinstance(parsed, dict) else {"raw_response": parsed}

    def _order_id(self, data: dict[str, Any]) -> str | None:
        value = data.get("orderId") or data.get("order_id") or data.get("id")
        return str(value).strip() if value not in (None, "") else None

    def _correlation_id(self, data: dict[str, Any]) -> str | None:
        value = (
            data.get("correlationId")
            or data.get("correlationID")
            or data.get("correlation_id")
            or data.get("clientOrderId")
            or data.get("client_order_id")
        )
        return str(value).strip() if value not in (None, "") else None

    def _recover_order_after_timeout(
        self,
        *,
        client_id: str,
        access_token: str,
        payload: dict[str, Any],
        timeout_message: str,
    ) -> DhanOrderResult:
        correlation_id = self._correlation_id(payload)
        if not correlation_id:
            return DhanOrderResult(
                success=False,
                order_id=None,
                status="ORDER_STATE_UNKNOWN",
                avg_price=None,
                raw_response={"unknown_order_state": True, "reason": "missing_correlation_id"},
                error=f"{timeout_message}; manual Dhan verification required before sending another order.",
                interpreted_error=interpret_dhan_error(None, timeout_message),
            )

        order_book = self.get_order_book(client_id=client_id, access_token=access_token)
        matched_order: dict[str, Any] | None = None
        if order_book.success:
            for row in order_book.items:
                if self._correlation_id(row) == correlation_id:
                    matched_order = row
                    break

        if matched_order:
            status = self._order_status_value(matched_order) or "PENDING"
            success = status not in {"REJECTED", "CANCELLED", "EXPIRED"}
            recovered = dict(matched_order)
            recovered.update(
                {
                    "recovered_after_timeout": True,
                    "correlationId": correlation_id,
                    "order_state_unknown": False,
                }
            )
            log_order_event(
                {
                    "event": "DHAN_ORDER_TIMEOUT_RECOVERED",
                    "correlation_id": correlation_id,
                    "order_id": self._order_id(matched_order),
                    "status": status,
                    "success": success,
                }
            )
            return DhanOrderResult(
                success=success,
                order_id=self._order_id(matched_order),
                status=status,
                avg_price=self._order_avg_price(matched_order),
                raw_response=recovered,
                error=None if success else self._error_message(matched_order, f"Dhan order recovered after timeout with status {status}."),
                interpreted_error=None if success else interpret_dhan_error(None, status),
            )

        raw_response = {
            "unknown_order_state": True,
            "correlationId": correlation_id,
            "order_book_success": order_book.success,
            "order_book_message": order_book.message,
            "order_book_status_code": order_book.status_code,
            "order_book_error": order_book.error,
        }
        log_order_event(
            {
                "event": "DHAN_ORDER_TIMEOUT_UNRESOLVED",
                "correlation_id": correlation_id,
                **raw_response,
            }
        )
        return DhanOrderResult(
            success=False,
            order_id=None,
            status="ORDER_STATE_UNKNOWN",
            avg_price=None,
            raw_response=raw_response,
            error=f"{timeout_message}; no matching order found by correlationId. Manual Dhan verification required.",
            interpreted_error=interpret_dhan_error(None, timeout_message),
        )

    def _order_status_payload(self, parsed: dict[str, Any] | list[Any] | str, order_id: str) -> dict[str, Any]:
        if isinstance(parsed, dict):
            if any(key in parsed for key in ("orderStatus", "order_status", "status", "orderId", "order_id")):
                return parsed
            for key in ("data", "order", "orders"):
                value = parsed.get(key)
                if isinstance(value, dict):
                    return self._order_status_payload(value, order_id)
                if isinstance(value, list):
                    return self._order_status_payload(value, order_id)
            return parsed

        if isinstance(parsed, list):
            rows = [item for item in parsed if isinstance(item, dict)]
            for row in rows:
                row_order_id = str(row.get("orderId") or row.get("order_id") or row.get("id") or "")
                if row_order_id == str(order_id):
                    return row
            if len(rows) == 1:
                return rows[0]
            return {"raw_response": parsed}

        return {"raw_response": parsed}

    def _order_status_value(self, data: dict[str, Any]) -> str:
        return str(data.get("orderStatus") or data.get("order_status") or data.get("status") or "").upper()

    def _list_response(self, parsed: dict[str, Any] | list[Any] | str) -> list[dict[str, Any]]:
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            for key in ("data", "orders", "positions"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def _get_list_endpoint(
        self,
        *,
        client_id: str,
        access_token: str,
        endpoint: str,
        label: str,
        timeout: float = 10.0,
    ) -> DhanListResult:
        url = f"{DHAN_BASE_URL}/{endpoint.lstrip('/')}"
        try:
            with self._client(timeout=timeout) as client:
                response = client.get(url, headers=self._headers(client_id, access_token))
            parsed = self._parse_response(response)
            raw_response = parsed if isinstance(parsed, (dict, list)) else None
            if 200 <= response.status_code <= 299:
                return DhanListResult(
                    success=True,
                    message=f"Dhan {label} fetched.",
                    items=self._list_response(parsed),
                    status_code=response.status_code,
                    raw_response=raw_response,
                )
            reason = self._error_message(parsed, f"Dhan {label} request failed ({response.status_code}).")
            return DhanListResult(
                success=False,
                message=f"Dhan {label} request failed: {reason}",
                items=[],
                status_code=response.status_code,
                raw_response=raw_response,
                error=reason,
            )
        except httpx.TimeoutException:
            return DhanListResult(
                success=False,
                message=f"Dhan {label} request timed out.",
                items=[],
                error="timeout",
            )
        except Exception as exc:
            logger.exception("Dhan %s request error", label)
            return DhanListResult(
                success=False,
                message=f"Dhan {label} request failed.",
                items=[],
                error=str(exc),
            )

    def place_order(self, *, client_id: str, access_token: str, payload: dict[str, Any]) -> DhanOrderResult:
        url = f"{DHAN_BASE_URL}/orders"
        outgoing_ip_result = self._outgoing_ip_result()
        outgoing_ip = outgoing_ip_result.get("outgoing_ip")
        headers_masked = build_dhan_headers_debug(client_id, access_token)
        payload_validation = validate_dhan_payload(payload)
        market_closed_possible = not _market_is_open()

        log_order_event(
            {
                "event": "DHAN_ORDER_ATTEMPT",
                "outgoing_ip": outgoing_ip,
                "outgoing_ip_check": outgoing_ip_result,
                "url": url,
                "headers_masked": headers_masked,
                "payload": payload,
                "payload_validation": payload_validation,
                "dhan_mode": "REAL",
                "live_orders_enabled": True,
                "market_closed_possible": market_closed_possible,
            }
        )

        if not payload_validation.get("ok"):
            if payload_validation.get("raw_pine_detected"):
                error_message = "BUG: Raw Pine payload reached Dhan client. This must be normalized first."
            else:
                error_message = payload_validation.get("error") or "Invalid Dhan payload."
            interpreted_error = interpret_dhan_error(None, error_message)
            raw_response = {"payload_validation": payload_validation}
            log_order_event(
                {
                    "event": "DHAN_ORDER_RESPONSE",
                    "outgoing_ip": outgoing_ip,
                    "url": url,
                    "status_code": None,
                    "response_text": error_message,
                    "response_json": raw_response,
                    "interpreted_error": interpreted_error,
                }
            )
            return DhanOrderResult(
                success=False,
                order_id=None,
                status="PAYLOAD_INVALID",
                avg_price=None,
                raw_response=raw_response,
                error=error_message,
                interpreted_error=interpreted_error,
            )

        try:
            with self._client(timeout=10.0) as client:
                response = client.post(url, json=payload, headers=self._headers(client_id, access_token))

            parsed = self._parse_response(response)
            data = self._response_payload(parsed)
            interpreted_error = interpret_dhan_error(response.status_code, parsed)
            response_json = parsed if isinstance(parsed, (dict, list)) else None
            log_order_event(
                {
                    "event": "DHAN_ORDER_RESPONSE",
                    "outgoing_ip": outgoing_ip,
                    "url": url,
                    "status_code": response.status_code,
                    "response_text": response.text[:4000],
                    "response_json": response_json,
                    "interpreted_error": interpreted_error,
                }
            )

            if response.status_code in (200, 201) and data.get("orderId"):
                return DhanOrderResult(
                    success=True,
                    order_id=data.get("orderId"),
                    status=data.get("orderStatus", "PENDING"),
                    avg_price=self._order_avg_price(data),
                    raw_response=data,
                    interpreted_error=None,
                )

            return DhanOrderResult(
                success=False,
                order_id=data.get("orderId"),
                status=data.get("orderStatus", "REJECTED"),
                avg_price=self._order_avg_price(data),
                raw_response=data,
                error=self._error_message(data, f"Dhan rejected order ({response.status_code})"),
                interpreted_error=interpreted_error,
            )
        except httpx.TimeoutException as exc:
            interpreted_error = interpret_dhan_error(None, "Dhan API timeout")
            log_order_event(
                {
                    "event": "DHAN_ORDER_EXCEPTION",
                    "outgoing_ip": outgoing_ip,
                    "url": url,
                    "exception_type": type(exc).__name__,
                    "exception_message": "Dhan API timeout",
                    "interpreted_error": interpreted_error,
                }
            )
            return self._recover_order_after_timeout(
                client_id=client_id,
                access_token=access_token,
                payload=payload,
                timeout_message="Dhan API timeout",
            )
        except Exception as exc:
            logger.error("Dhan place_order error: %s", exc)
            interpreted_error = interpret_dhan_error(None, str(exc))
            log_order_event(
                {
                    "event": "DHAN_ORDER_EXCEPTION",
                    "outgoing_ip": outgoing_ip,
                    "url": url,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "interpreted_error": interpreted_error,
                }
            )
            return DhanOrderResult(
                success=False,
                order_id=None,
                status="API_ERROR",
                avg_price=None,
                raw_response={},
                error=str(exc),
                interpreted_error=interpreted_error,
            )

    def place_super_order(self, *, client_id: str, access_token: str, payload: dict[str, Any]) -> DhanOrderResult:
        url = f"{DHAN_BASE_URL}/super/orders"
        outgoing_ip_result = self._outgoing_ip_result()
        outgoing_ip = outgoing_ip_result.get("outgoing_ip")
        headers_masked = build_dhan_headers_debug(client_id, access_token)

        log_order_event(
            {
                "event": "DHAN_SUPER_ORDER_ATTEMPT",
                "outgoing_ip": outgoing_ip,
                "outgoing_ip_check": outgoing_ip_result,
                "url": url,
                "headers_masked": headers_masked,
                "payload": payload,
                "dhan_mode": "REAL",
                "live_orders_enabled": True,
                "market_closed_possible": not _market_is_open(),
            }
        )

        try:
            with self._client(timeout=10.0) as client:
                response = client.post(url, json=payload, headers=self._headers(client_id, access_token))

            parsed = self._parse_response(response)
            data = self._response_payload(parsed)
            interpreted_error = interpret_dhan_error(response.status_code, parsed)
            response_json = parsed if isinstance(parsed, (dict, list)) else None
            log_order_event(
                {
                    "event": "DHAN_SUPER_ORDER_RESPONSE",
                    "outgoing_ip": outgoing_ip,
                    "url": url,
                    "status_code": response.status_code,
                    "response_text": response.text[:4000],
                    "response_json": response_json,
                    "interpreted_error": interpreted_error,
                }
            )

            if response.status_code in (200, 201) and data.get("orderId"):
                return DhanOrderResult(
                    success=True,
                    order_id=data.get("orderId"),
                    status=data.get("orderStatus", "PENDING"),
                    avg_price=self._order_avg_price(data),
                    raw_response=data,
                    interpreted_error=None,
                )

            return DhanOrderResult(
                success=False,
                order_id=data.get("orderId"),
                status=data.get("orderStatus", "REJECTED"),
                avg_price=self._order_avg_price(data),
                raw_response=data,
                error=self._error_message(data, f"Dhan rejected super order ({response.status_code})"),
                interpreted_error=interpreted_error,
            )
        except httpx.TimeoutException as exc:
            interpreted_error = interpret_dhan_error(None, "Dhan Super Order API timeout")
            log_order_event(
                {
                    "event": "DHAN_SUPER_ORDER_EXCEPTION",
                    "outgoing_ip": outgoing_ip,
                    "url": url,
                    "exception_type": type(exc).__name__,
                    "exception_message": "Dhan Super Order API timeout",
                    "interpreted_error": interpreted_error,
                }
            )
            return self._recover_order_after_timeout(
                client_id=client_id,
                access_token=access_token,
                payload=payload,
                timeout_message="Dhan Super Order API timeout",
            )
        except Exception as exc:
            logger.error("Dhan place_super_order error: %s", exc)
            interpreted_error = interpret_dhan_error(None, str(exc))
            log_order_event(
                {
                    "event": "DHAN_SUPER_ORDER_EXCEPTION",
                    "outgoing_ip": outgoing_ip,
                    "url": url,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "interpreted_error": interpreted_error,
                }
            )
            return DhanOrderResult(
                success=False,
                order_id=None,
                status="API_ERROR",
                avg_price=None,
                raw_response={},
                error=str(exc),
                interpreted_error=interpreted_error,
            )

    def modify_super_order(self, *, client_id: str, access_token: str, order_id: str, payload: dict[str, Any]) -> DhanOrderResult:
        url = f"{DHAN_BASE_URL}/super/orders/{order_id}"
        outgoing_ip_result = self._outgoing_ip_result()
        outgoing_ip = outgoing_ip_result.get("outgoing_ip")
        headers_masked = build_dhan_headers_debug(client_id, access_token)

        log_order_event(
            {
                "event": "DHAN_SUPER_ORDER_MODIFY_ATTEMPT",
                "outgoing_ip": outgoing_ip,
                "outgoing_ip_check": outgoing_ip_result,
                "url": url,
                "headers_masked": headers_masked,
                "payload": payload,
                "dhan_mode": "REAL",
                "live_orders_enabled": True,
            }
        )

        try:
            with self._client(timeout=10.0) as client:
                response = client.put(url, json=payload, headers=self._headers(client_id, access_token))

            parsed = self._parse_response(response)
            data = self._response_payload(parsed)
            interpreted_error = interpret_dhan_error(response.status_code, parsed)
            response_json = parsed if isinstance(parsed, (dict, list)) else None
            log_order_event(
                {
                    "event": "DHAN_SUPER_ORDER_MODIFY_RESPONSE",
                    "outgoing_ip": outgoing_ip,
                    "url": url,
                    "status_code": response.status_code,
                    "response_text": response.text[:4000],
                    "response_json": response_json,
                    "interpreted_error": interpreted_error,
                }
            )

            if 200 <= response.status_code <= 299:
                return DhanOrderResult(
                    success=True,
                    order_id=str(data.get("orderId") or order_id),
                    status=str(data.get("orderStatus") or "ACCEPTED"),
                    avg_price=self._order_avg_price(data),
                    raw_response=data,
                    interpreted_error=None,
                )

            return DhanOrderResult(
                success=False,
                order_id=str(data.get("orderId") or order_id),
                status=str(data.get("orderStatus") or "REJECTED"),
                avg_price=self._order_avg_price(data),
                raw_response=data,
                error=self._error_message(data, f"Dhan rejected Super Order modify ({response.status_code})"),
                interpreted_error=interpreted_error,
            )
        except httpx.TimeoutException as exc:
            interpreted_error = interpret_dhan_error(None, "Dhan Super Order modify API timeout")
            log_order_event(
                {
                    "event": "DHAN_SUPER_ORDER_MODIFY_EXCEPTION",
                    "outgoing_ip": outgoing_ip,
                    "url": url,
                    "exception_type": type(exc).__name__,
                    "exception_message": "Dhan Super Order modify API timeout",
                    "interpreted_error": interpreted_error,
                }
            )
            return DhanOrderResult(
                success=False,
                order_id=order_id,
                status="TIMEOUT",
                avg_price=None,
                raw_response={},
                error="Dhan Super Order modify API timeout",
                interpreted_error=interpreted_error,
            )
        except Exception as exc:
            logger.error("Dhan modify_super_order error: %s", exc)
            interpreted_error = interpret_dhan_error(None, str(exc))
            log_order_event(
                {
                    "event": "DHAN_SUPER_ORDER_MODIFY_EXCEPTION",
                    "outgoing_ip": outgoing_ip,
                    "url": url,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "interpreted_error": interpreted_error,
                }
            )
            return DhanOrderResult(
                success=False,
                order_id=order_id,
                status="API_ERROR",
                avg_price=None,
                raw_response={},
                error=str(exc),
                interpreted_error=interpreted_error,
            )

    def cancel_super_order_leg(self, *, client_id: str, access_token: str, order_id: str, leg_name: str) -> DhanOrderResult:
        url = f"{DHAN_BASE_URL}/super/orders/{order_id}/{leg_name}"
        outgoing_ip_result = self._outgoing_ip_result()
        outgoing_ip = outgoing_ip_result.get("outgoing_ip")
        headers_masked = build_dhan_headers_debug(client_id, access_token)

        log_order_event(
            {
                "event": "DHAN_SUPER_ORDER_CANCEL_ATTEMPT",
                "outgoing_ip": outgoing_ip,
                "outgoing_ip_check": outgoing_ip_result,
                "url": url,
                "headers_masked": headers_masked,
                "order_id": order_id,
                "leg_name": leg_name,
                "dhan_mode": "REAL",
                "live_orders_enabled": True,
            }
        )

        try:
            with self._client(timeout=10.0) as client:
                response = client.delete(url, headers=self._headers(client_id, access_token))

            parsed = self._parse_response(response)
            data = self._response_payload(parsed)
            interpreted_error = interpret_dhan_error(response.status_code, parsed)
            response_json = parsed if isinstance(parsed, (dict, list)) else None
            log_order_event(
                {
                    "event": "DHAN_SUPER_ORDER_CANCEL_RESPONSE",
                    "outgoing_ip": outgoing_ip,
                    "url": url,
                    "status_code": response.status_code,
                    "response_text": response.text[:4000],
                    "response_json": response_json,
                    "interpreted_error": interpreted_error,
                }
            )

            if 200 <= response.status_code <= 299:
                return DhanOrderResult(
                    success=True,
                    order_id=str(data.get("orderId") or order_id),
                    status=str(data.get("orderStatus") or "CANCELLED"),
                    avg_price=None,
                    raw_response=data,
                    interpreted_error=None,
                )

            return DhanOrderResult(
                success=False,
                order_id=str(data.get("orderId") or order_id),
                status=str(data.get("orderStatus") or "REJECTED"),
                avg_price=None,
                raw_response=data,
                error=self._error_message(data, f"Dhan rejected Super Order cancel ({response.status_code})"),
                interpreted_error=interpreted_error,
            )
        except httpx.TimeoutException as exc:
            interpreted_error = interpret_dhan_error(None, "Dhan Super Order cancel API timeout")
            log_order_event(
                {
                    "event": "DHAN_SUPER_ORDER_CANCEL_EXCEPTION",
                    "outgoing_ip": outgoing_ip,
                    "url": url,
                    "exception_type": type(exc).__name__,
                    "exception_message": "Dhan Super Order cancel API timeout",
                    "interpreted_error": interpreted_error,
                }
            )
            return DhanOrderResult(
                success=False,
                order_id=order_id,
                status="TIMEOUT",
                avg_price=None,
                raw_response={},
                error="Dhan Super Order cancel API timeout",
                interpreted_error=interpreted_error,
            )
        except Exception as exc:
            logger.error("Dhan cancel_super_order_leg error: %s", exc)
            interpreted_error = interpret_dhan_error(None, str(exc))
            log_order_event(
                {
                    "event": "DHAN_SUPER_ORDER_CANCEL_EXCEPTION",
                    "outgoing_ip": outgoing_ip,
                    "url": url,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "interpreted_error": interpreted_error,
                }
            )
            return DhanOrderResult(
                success=False,
                order_id=order_id,
                status="API_ERROR",
                avg_price=None,
                raw_response={},
                error=str(exc),
                interpreted_error=interpreted_error,
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
        """
        Poll GET /orders/{order-id} up to max_polls times until a terminal status is reached.

        Terminal statuses: TRADED, REJECTED, CANCELLED, EXPIRED.
        Pending statuses:  TRANSIT, PENDING.

        For MVP, if not terminal after max_polls, returns PENDING_CONFIRMATION.
        Dashboard should show ambiguous status and operator should verify in Dhan portal.
        """
        url = f"{DHAN_BASE_URL}/orders/{order_id}"
        last_status: str | None = None
        last_data: dict[str, Any] = {}

        for attempt in range(max_polls):
            try:
                with self._client(timeout=8.0) as client:
                    response = client.get(url, headers=self._headers(client_id, access_token))
                parsed = self._parse_response(response)
                data = self._order_status_payload(parsed, order_id)
                last_data = data
                order_status = self._order_status_value(data)
                last_status = order_status
                log_order_event({
                    "event": "DHAN_ORDER_STATUS_POLL",
                    "attempt": attempt + 1,
                    "order_id": order_id,
                    "status_code": response.status_code,
                    "order_status": order_status,
                })
                if order_status in DHAN_TERMINAL_STATUSES:
                    avg_price = self._order_avg_price(data)
                    return DhanOrderStatusResult(
                        success=True,
                        order_id=order_id,
                        order_status=order_status,
                        is_terminal=True,
                        is_filled=(order_status == "TRADED"),
                        avg_price=avg_price,
                        raw_response=data,
                        filled_qty=self._order_filled_qty(data),
                        remaining_qty=self._order_remaining_qty(data),
                    )
                if attempt < max_polls - 1:
                    time.sleep(poll_delay)
            except httpx.TimeoutException:
                logger.warning("Order status poll timeout for order_id=%s attempt=%d", order_id, attempt + 1)
                if attempt < max_polls - 1:
                    time.sleep(poll_delay)
            except Exception as exc:
                logger.error("Order status poll error: %s", exc)
                return DhanOrderStatusResult(
                    success=False,
                    order_id=order_id,
                    order_status=last_status,
                    is_terminal=False,
                    is_filled=False,
                    avg_price=None,
                    raw_response=last_data or None,
                    error=str(exc),
                    filled_qty=self._order_filled_qty(last_data) if last_data else None,
                    remaining_qty=self._order_remaining_qty(last_data) if last_data else None,
                )

        # Exhausted polls without reaching terminal status
        log_order_event({
            "event": "DHAN_ORDER_STATUS_PENDING_CONFIRMATION",
            "order_id": order_id,
            "last_status": last_status,
            "polls": max_polls,
            "message": "Order not yet terminal after polling. Dashboard shows PENDING_CONFIRMATION.",
        })
        return DhanOrderStatusResult(
            success=True,
            order_id=order_id,
            order_status=last_status or "PENDING_CONFIRMATION",
            is_terminal=False,
            is_filled=False,
            avg_price=None,
            raw_response=last_data or None,
            filled_qty=self._order_filled_qty(last_data) if last_data else None,
            remaining_qty=self._order_remaining_qty(last_data) if last_data else None,
        )

    def get_ltp(
        self,
        *,
        client_id: str,
        access_token: str,
        exchange_segment: str,
        security_id: str,
    ) -> DhanLtpResult:
        """
        Fetch an LTP snapshot with POST /marketfeed/ltp.

        Dhan's Market Quote API accepts a body like {"NSE_FNO": [49081]}
        and returns last_price under data.<segment>.<securityId>.
        """
        segment = str(exchange_segment or "").upper()
        sid = str(security_id or "").strip()
        if not segment or not sid:
            return DhanLtpResult(
                success=False,
                message="Dhan LTP request missing exchange segment or security id.",
                ltp=None,
                exchange_segment=segment or None,
                security_id=sid or None,
                error="missing_exchange_segment_or_security_id",
            )

        request_security_id: int | str = int(sid) if sid.isdigit() else sid
        request_body = {segment: [request_security_id]}
        url = f"{DHAN_BASE_URL}/marketfeed/ltp"

        try:
            with self._client(timeout=5.0) as client:
                response = client.post(url, json=request_body, headers=self._headers(client_id, access_token))
            parsed = self._parse_response(response)
            raw_response = parsed if isinstance(parsed, (dict, list)) else None

            if not (200 <= response.status_code <= 299):
                reason = self._error_message(parsed, f"Dhan LTP request failed ({response.status_code}).")
                return DhanLtpResult(
                    success=False,
                    message=f"Dhan LTP request failed: {reason}",
                    ltp=None,
                    exchange_segment=segment,
                    security_id=sid,
                    status_code=response.status_code,
                    raw_response=raw_response,
                    error=reason,
                )

            if not isinstance(parsed, dict):
                return DhanLtpResult(
                    success=False,
                    message="Dhan LTP response was not a JSON object.",
                    ltp=None,
                    exchange_segment=segment,
                    security_id=sid,
                    status_code=response.status_code,
                    raw_response=raw_response,
                    error="invalid_ltp_response",
                )

            data = parsed.get("data")
            if not isinstance(data, dict):
                return DhanLtpResult(
                    success=False,
                    message="Dhan LTP response did not include data.",
                    ltp=None,
                    exchange_segment=segment,
                    security_id=sid,
                    status_code=response.status_code,
                    raw_response=raw_response,
                    error="missing_data",
                )

            segment_data = data.get(segment)
            if segment_data is None:
                for key, value in data.items():
                    if str(key).upper() == segment:
                        segment_data = value
                        break
            if not isinstance(segment_data, dict):
                return DhanLtpResult(
                    success=False,
                    message=f"Dhan LTP response did not include segment {segment}.",
                    ltp=None,
                    exchange_segment=segment,
                    security_id=sid,
                    status_code=response.status_code,
                    raw_response=raw_response,
                    error="missing_segment",
                )

            quote = segment_data.get(sid) or segment_data.get(str(request_security_id))
            if quote is None:
                for key, value in segment_data.items():
                    if str(key) == sid:
                        quote = value
                        break
            if not isinstance(quote, dict):
                return DhanLtpResult(
                    success=False,
                    message=f"Dhan LTP response did not include security id {sid}.",
                    ltp=None,
                    exchange_segment=segment,
                    security_id=sid,
                    status_code=response.status_code,
                    raw_response=raw_response,
                    error="missing_security_id",
                )

            ltp = self._optional_float(quote, "last_price", "lastPrice", "ltp", "LTP")
            if ltp is None:
                return DhanLtpResult(
                    success=False,
                    message=f"Dhan LTP response had no last_price for security id {sid}.",
                    ltp=None,
                    exchange_segment=segment,
                    security_id=sid,
                    status_code=response.status_code,
                    raw_response=raw_response,
                    error="missing_last_price",
                )

            return DhanLtpResult(
                success=True,
                message="Dhan LTP fetched.",
                ltp=ltp,
                exchange_segment=segment,
                security_id=sid,
                status_code=response.status_code,
                raw_response=raw_response,
            )
        except httpx.TimeoutException:
            return DhanLtpResult(
                success=False,
                message="Dhan LTP request timed out.",
                ltp=None,
                exchange_segment=segment,
                security_id=sid,
                error="timeout",
            )
        except Exception as exc:
            logger.exception("Dhan LTP request error")
            return DhanLtpResult(
                success=False,
                message="Dhan LTP request failed.",
                ltp=None,
                exchange_segment=segment,
                security_id=sid,
                error=str(exc),
            )

    # TODO (Phase 2): GET /trades - trade book
    # def get_trade_book(self, *, client_id: str, access_token: str) -> list[dict[str, Any]]:
    #     url = f"{DHAN_BASE_URL}/trades"
    #     ...

    # TODO (Phase 2): POST /marketfeed/ltp - market quote LTP validation before order
    # def get_market_ltp(self, *, client_id: str, access_token: str,
    #                    exchange_segment: str, security_id: str) -> dict[str, Any]:
    #     url = f"{DHAN_BASE_URL}/marketfeed/ltp"
    #     ...

    def validate_token(self, *, client_id: str, access_token: str) -> DhanValidationResult:
        url = f"{DHAN_BASE_URL}/profile"
        try:
            with self._client(timeout=5.0) as client:
                response = client.get(url, headers=self._headers(client_id, access_token))
            data = self._parse_response(response)
            if 200 <= response.status_code <= 299:
                profile_client_id = data.get("dhanClientId") if isinstance(data, dict) else None
                if profile_client_id and str(profile_client_id) != str(client_id):
                    return DhanValidationResult(
                        success=False,
                        message=(
                            "Dhan token valid, but it belongs to client ID "
                            f"{profile_client_id}, not configured client ID {client_id}."
                        ),
                        status_code=response.status_code,
                        raw_response=data,
                    )
                return DhanValidationResult(
                    success=True,
                    message="Dhan token valid.",
                    status_code=response.status_code,
                    raw_response=data if isinstance(data, (dict, list)) else None,
                )
            reason = self._error_message(data, f"Dhan token validation failed ({response.status_code}).")
            return DhanValidationResult(
                success=False,
                message=f"Dhan token validation failed: {reason}",
                status_code=response.status_code,
                raw_response=data if isinstance(data, (dict, list)) else None,
            )
        except httpx.TimeoutException:
            return DhanValidationResult(success=False, message="Dhan token validation timed out.")
        except Exception:
            logger.exception("Dhan token validation error")
            return DhanValidationResult(success=False, message="Dhan token validation request failed.")

    def get_positions(self, *, client_id: str, access_token: str) -> list[dict[str, Any]]:
        result = self.get_positions_snapshot(client_id=client_id, access_token=access_token)
        return result.items if result.success else []

    def get_positions_snapshot(self, *, client_id: str, access_token: str) -> DhanListResult:
        return self._get_list_endpoint(
            client_id=client_id,
            access_token=access_token,
            endpoint="positions",
            label="positions",
        )

    def get_order_book(self, *, client_id: str, access_token: str) -> DhanListResult:
        return self._get_list_endpoint(
            client_id=client_id,
            access_token=access_token,
            endpoint="orders",
            label="order book",
        )

    def get_fund_limit(self, *, client_id: str, access_token: str) -> DhanFundsResult:
        url = f"{DHAN_BASE_URL}/fundlimit"
        try:
            with self._client(timeout=5.0) as client:
                response = client.get(url, headers=self._headers(client_id, access_token))
            parsed = self._parse_response(response)
            data = parsed if isinstance(parsed, dict) else {"raw_response": parsed}
            if 200 <= response.status_code <= 299:
                return DhanFundsResult(
                    success=True,
                    message="Dhan fund limit fetched.",
                    status_code=response.status_code,
                    client_id=str(data.get("dhanClientId") or client_id),
                    available_balance=self._optional_float(data, "availabelBalance", "availableBalance"),
                    withdrawable_balance=self._optional_float(data, "withdrawableBalance"),
                    utilized_amount=self._optional_float(data, "utilizedAmount"),
                    sod_limit=self._optional_float(data, "sodLimit"),
                    collateral_amount=self._optional_float(data, "collateralAmount"),
                    blocked_payout_amount=self._optional_float(data, "blockedPayoutAmount"),
                    raw_response=data,
                )
            reason = self._error_message(data, f"Dhan fund limit request failed ({response.status_code}).")
            return DhanFundsResult(
                success=False,
                message=f"Dhan fund limit request failed: {reason}",
                status_code=response.status_code,
                raw_response=data,
            )
        except httpx.TimeoutException:
            return DhanFundsResult(success=False, message="Dhan fund limit request timed out.")
        except Exception:
            logger.exception("Dhan fund limit error")
            return DhanFundsResult(success=False, message="Dhan fund limit request failed.")


def get_broker_client(engine_mode: str | None = None) -> Any:
    """Return the order-routing client for the selected runtime mode."""
    if engine_mode is None:
        from app.services.state_store import get_engine_mode

        engine_mode = get_engine_mode(legacy_fallback=False)
    mode = str(engine_mode or "").strip().lower()
    if mode == "paper":
        from app.services.paper_broker import PaperBroker

        return PaperBroker()
    if mode == "live":
        from app.services.execution_context import (
            current_egress_ip,
            current_execution_user,
            current_proxy_url,
        )

        user = current_execution_user()
        proxy_url = current_proxy_url()
        if user is not None and not user.is_dev:
            if not settings.EXECUTION_NODE_ROUTING_ENABLED:
                raise RuntimeError("Per-user execution-node routing is disabled.")
            if not proxy_url:
                raise RuntimeError("No whitelisted egress proxy is assigned to this user.")
        return RealDhanClient(
            proxy_url=proxy_url,
            expected_egress_ip=current_egress_ip(),
        )
    raise ValueError("engine_mode not set; cannot select broker client")
