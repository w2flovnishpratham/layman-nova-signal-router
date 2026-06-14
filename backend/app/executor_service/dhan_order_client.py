"""Minimal, isolated Dhan order client for the executor service.

This module is intentionally self-contained. It must NOT import main Nova
application state (audit logger, runtime state, rate limiter, risk manager).
The executor receives a fully prepared, validated order plus short-lived
credentials inside a single signed request, places exactly one order, and
returns a sanitized summary.

Security rules enforced here:
  * The Dhan access token is only ever read from the function argument.
  * The access token is never logged, never returned, and never stored.
  * Only a sanitized response summary (order id, status, message) is returned.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


DHAN_BASE_URL = "https://api.dhan.co/v2"
DHAN_ORDERS_ENDPOINT = f"{DHAN_BASE_URL}/orders"


@dataclass(frozen=True)
class ExecutorOrderResult:
    """Sanitized result of a single executor Dhan order placement.

    `status` is one of: sent, confirmed, broker_rejected, broker_timeout, broker_error.
    No credential material is ever placed on this object.
    """

    status: str
    order_id: str | None
    message: str
    http_status: int | None = None


def place_dhan_order(
    *,
    client_id: str,
    access_token: str,
    dhan_payload: dict[str, Any],
    timeout_seconds: float,
    send_client_id_header: bool = True,
) -> ExecutorOrderResult:
    """Place a single Dhan order.

    Never retries (idempotency/retry is owned by the main Nova live job).
    Never logs or returns the access token.
    """
    headers = {
        "access-token": access_token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if send_client_id_header:
        headers["client-id"] = client_id

    # Bind outbound to IPv4 so dual-stack DNS cannot pick an IPv6 source that
    # is not the reserved/whitelisted egress address.
    transport = httpx.HTTPTransport(local_address="0.0.0.0")  # nosec B104
    try:
        with httpx.Client(timeout=timeout_seconds, transport=transport) as client:
            response = client.post(DHAN_ORDERS_ENDPOINT, json=dhan_payload, headers=headers)
    except httpx.TimeoutException:
        return ExecutorOrderResult(
            status="broker_timeout",
            order_id=None,
            message="Dhan order request timed out; no confirmation received.",
            http_status=None,
        )
    except httpx.HTTPError:
        # Sanitized: never surface the underlying request (which carries headers).
        return ExecutorOrderResult(
            status="broker_error",
            order_id=None,
            message="Dhan order request failed before a response was received.",
            http_status=None,
        )

    data = _safe_json(response)
    order_id = _extract_order_id(data)
    if response.status_code in (200, 201) and order_id:
        order_status = str(data.get("orderStatus") or "").upper() if isinstance(data, dict) else ""
        status = "confirmed" if order_status in {"TRADED", "CONFIRMED"} else "sent"
        return ExecutorOrderResult(
            status=status,
            order_id=order_id,
            message=f"Dhan accepted the order ({order_status or 'PENDING'}).",
            http_status=response.status_code,
        )

    return ExecutorOrderResult(
        status="broker_rejected",
        order_id=order_id,
        message=_sanitized_reject_message(data, response.status_code),
        http_status=response.status_code,
    )


def _safe_json(response: httpx.Response) -> dict[str, Any] | list[Any] | None:
    try:
        return response.json()
    except ValueError:
        return None


def _extract_order_id(data: Any) -> str | None:
    if isinstance(data, dict):
        value = data.get("orderId") or data.get("order_id")
        if value:
            return str(value)
    return None


def _sanitized_reject_message(data: Any, status_code: int) -> str:
    if isinstance(data, dict):
        for key in ("errorMessage", "internalErrorMessage", "remarks", "message", "error"):
            value = data.get(key)
            if value:
                return f"Dhan rejected the order: {str(value)[:300]}"
        code = data.get("errorCode") or data.get("internalErrorCode")
        if code:
            return f"Dhan rejected the order (code {code})."
    return f"Dhan rejected the order (HTTP {status_code})."
