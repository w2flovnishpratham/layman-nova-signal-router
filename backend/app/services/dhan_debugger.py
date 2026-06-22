from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import DEFAULT_RUNTIME_SETTINGS, settings
from app.services.credential_vault import dhan_metadata, get_dhan_credentials, mask_secret
from app.services.security_id_resolver import DEFAULT_SECURITY_ID_WARNING


PINE_PAYLOAD_ERROR = "Invalid Dhan payload: Pine multi_leg_order fields detected. Backend must transform signal before calling Dhan."
_OUTGOING_IP_CACHE: dict[str, Any] = {"checked_at": 0.0, "result": None}


def get_outgoing_ip(timeout: float = 5.0, *, force: bool = False) -> dict[str, Any]:
    cached = _OUTGOING_IP_CACHE.get("result")
    if not force and isinstance(cached, dict) and time.time() - float(_OUTGOING_IP_CACHE.get("checked_at") or 0) < 300:
        return dict(cached)
    try:
        response = httpx.get("https://api.ipify.org", timeout=timeout)
        response.raise_for_status()
        result = {"outgoing_ip": response.text.strip(), "ok": True, "error": None}
    except Exception as exc:
        result = {"outgoing_ip": None, "ok": False, "error": str(exc)}
    _OUTGOING_IP_CACHE["checked_at"] = time.time()
    _OUTGOING_IP_CACHE["result"] = result
    return dict(result)


def build_dhan_headers_debug(client_id: str | None, access_token: str | None) -> dict[str, str | None]:
    """
    Build a masked version of Dhan request headers for safe logging.
    The access token is always masked (never logged in full).
    `client-id` is included only when DHAN_SEND_CLIENT_ID_HEADER=true (default).
    """
    headers: dict[str, str | None] = {
        "access-token": mask_secret(access_token),
        "Content-Type": "application/json",
    }
    if settings.DHAN_SEND_CLIENT_ID_HEADER:
        headers["client-id"] = client_id or ""
    return headers


def validate_dhan_env() -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []

    dhan_mode = settings.DHAN_MODE.upper()
    creds = get_dhan_credentials()
    meta = dhan_metadata()
    client_id_present = bool(creds)
    access_token_present = bool(creds and creds.access_token)
    public_base_url = settings.BACKEND_PUBLIC_BASE_URL.rstrip("/")
    public_webhook_url = f"{public_base_url}/webhook/tradingview" if public_base_url else ""

    if dhan_mode not in {"MOCK", "REAL"}:
        issues.append(f"DHAN_MODE must be MOCK or REAL, got {settings.DHAN_MODE!r}.")
    if dhan_mode == "REAL" and not client_id_present:
        issues.append("Dhan Client ID is missing while DHAN_MODE=REAL.")
    if dhan_mode == "REAL" and not access_token_present:
        issues.append("Dhan Access Token is missing while DHAN_MODE=REAL.")
    if not public_webhook_url:
        issues.append("BACKEND_PUBLIC_BASE_URL is missing.")
    if settings.ENABLE_LIVE_ORDERS and dhan_mode != "REAL":
        warnings.append("ENABLE_LIVE_ORDERS=true but DHAN_MODE is not REAL.")
    if public_webhook_url and "yourdomain.com" in public_webhook_url:
        warnings.append("BACKEND_PUBLIC_BASE_URL still uses the placeholder production domain.")
    if not settings.REQUIRE_MARKET_HOURS:
        warnings.append("REQUIRE_MARKET_HOURS=false; market-hours blocking depends on MARKET_CLOSED_DEBUG for live debug protection.")
    if settings.MARKET_CLOSED_DEBUG and not settings.FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED:
        warnings.append("MARKET_CLOSED_DEBUG=true; real orders are blocked while market is closed unless FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED=true.")
    if settings.ALLOW_DEFAULT_SECURITY_ID and settings.DEFAULT_SECURITY_ID:
        warnings.append(DEFAULT_SECURITY_ID_WARNING)
    if settings.DEFAULT_SECURITY_ID and not settings.ALLOW_DEFAULT_SECURITY_ID:
        warnings.append("DEFAULT_SECURITY_ID is configured but ignored because ALLOW_DEFAULT_SECURITY_ID=false.")

    return {
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "config": {
            "dhan_mode": dhan_mode,
            "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
            "client_id_present": client_id_present,
            "access_token_present": access_token_present,
            "client_id_masked": meta["client_id_masked"],
            "access_token_masked": meta["access_token_masked"],
            "public_webhook_url": public_webhook_url,
            "allow_default_security_id": settings.ALLOW_DEFAULT_SECURITY_ID,
            "default_security_id_present": bool(settings.DEFAULT_SECURITY_ID),
            "dhan_scrip_master_path": settings.DHAN_SCRIP_MASTER_PATH,
            "auto_resolve_security_id": settings.AUTO_RESOLVE_SECURITY_ID,
            "require_market_hours": settings.REQUIRE_MARKET_HOURS,
            "market_closed_debug": settings.MARKET_CLOSED_DEBUG,
            "force_allow_order_when_market_closed": settings.FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED,
        },
    }


def _pine_fields_detected(payload: dict[str, Any]) -> list[str]:
    detected: list[str] = []
    pine_only = {"alertType", "order_legs", "strike_price", "expiry_date", "option_type"}
    for field in pine_only:
        if field in payload:
            detected.append(field)

    transaction_type = payload.get("transactionType")
    if isinstance(transaction_type, str) and transaction_type.upper() in {"B", "S"}:
        detected.append("transactionType")

    legs = payload.get("order_legs")
    if isinstance(legs, list):
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            for field in ("transactionType", "strike_price", "expiry_date", "option_type"):
                if field in leg and field not in detected:
                    detected.append(field)

    return detected


def validate_dhan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Validate a fully-normalised Dhan v2 order payload before sending to POST /v2/orders.

    Field classification per Dhan v2 official docs:
      - BLOCKING required: dhanClientId, transactionType, exchangeSegment, productType,
        orderType, validity, quantity, price. Also securityId (not labeled *required* in
        Dhan docs, but must be resolved before any order placement).
      - Optional (warn only): correlationId (<=30 chars; recommended for tracking).
      - Optional with safe defaults (warn if absent):
          disclosedQuantity  -- safe default: 0
          triggerPrice       -- safe default: 0 (for MARKET/LIMIT; required for SL orders)
          afterMarketOrder   -- safe default: false
    """
    issues: list[str] = []
    warnings: list[str] = []

    if not isinstance(payload, dict):
        return {
            "ok": False,
            "issues": ["Payload must be an object."],
            "warnings": [],
            "missing_fields": [],
            "error": "Invalid Dhan payload: payload must be an object.",
        }

    pine_fields = _pine_fields_detected(payload)
    if pine_fields:
        return {
            "ok": False,
            "issues": [PINE_PAYLOAD_ERROR, f"Detected Pine fields: {', '.join(sorted(set(pine_fields)))}"],
            "warnings": warnings,
            "missing_fields": [],
            "error": PINE_PAYLOAD_ERROR,
            "raw_pine_detected": True,
        }

    # -- Core blocking-required fields (Dhan v2 official *required* label + securityId) --
    core_required_fields = [
        "dhanClientId",
        "transactionType",
        "exchangeSegment",
        "productType",
        "orderType",
        "validity",
        "securityId",
        "quantity",
        "price",
    ]
    missing_fields = [field for field in core_required_fields if payload.get(field) in (None, "")]
    if missing_fields:
        issues.append(f"Missing required Dhan fields: {', '.join(missing_fields)}")

    # -- transactionType value check --
    transaction_type = str(payload.get("transactionType") or "").upper()
    if transaction_type and transaction_type not in {"BUY", "SELL"}:
        issues.append("transactionType must be BUY or SELL in the final Dhan payload.")

    # -- quantity value check --
    try:
        quantity = int(payload.get("quantity"))
        if quantity <= 0:
            issues.append("quantity must be a positive integer.")
    except (TypeError, ValueError):
        issues.append("quantity must be a positive integer.")

    # -- securityId warning (already blocks above if missing; secondary note for clarity) --
    if payload.get("securityId") in (None, ""):
        warnings.append("securityId is empty; Dhan will reject real orders without a resolved security ID.")

    # -- Optional field notes (not blocking -- safe defaults exist) --
    if payload.get("correlationId") in (None, ""):
        warnings.append("correlationId not set; recommended (max 30 chars) for order tracking.")

    if payload.get("disclosedQuantity") is None:
        warnings.append("disclosedQuantity not set; Dhan default is 0.")

    order_type = str(payload.get("orderType") or "").upper()
    if payload.get("triggerPrice") is None:
        if order_type in {"SL", "SL-M", "STOP_LOSS"}:
            warnings.append("triggerPrice not set; required for SL/SL-M orders by Dhan.")
        else:
            warnings.append("triggerPrice not set; Dhan default is 0 for MARKET/LIMIT orders.")

    if payload.get("afterMarketOrder") is None:
        warnings.append("afterMarketOrder not set; Dhan default is false.")

    return {
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "missing_fields": missing_fields,
        "required_fields": core_required_fields,
        "error": None if not issues else f"Invalid Dhan payload: {issues[0]}",
        "raw_pine_detected": False,
    }
