from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from app.auth.db import session_scope
from app.auth.models import ExecutorNonceReceipt
from app.config import settings
from app.executor_service.dhan_order_client import place_dhan_order
from app.services.executor_signing import verify_executor_signature


logger = logging.getLogger("nova_executor")

# Credential field names that must never appear in a dry-run request and that
# carry the short-lived token only in a real request. Kept lowercase.
_DRY_RUN_PROHIBITED_CREDENTIAL_FIELDS = {
    "dhan_access_token",
    "access_token",
    "access-token",
    "authorization",
    "token",
}

# Generic secret-like keys that must NEVER appear anywhere in any executor body.
# `dhan_access_token` and `broker_client_id` are the only permitted credential
# carriers and are handled explicitly below, so they are not in this set.
_PROHIBITED_SECRET_FIELDS = {
    "access_token",
    "access-token",
    "authorization",
    "cookie",
    "password",
    "secret",
}

_VALID_TRANSACTION_TYPES = {"BUY", "SELL"}


def validate_executor_startup() -> None:
    """Fail fast on unsafe executor configuration.

    * Executor identity (code) must be set.
    * Signing secret must be strong (>= 32 chars).
    * Reserved IP must be configured.
    * If real orders are enabled, every real-order prerequisite must hold.
    """
    if not settings.EXECUTOR_CODE.strip():
        raise RuntimeError("EXECUTOR_CODE must be configured for the executor service.")
    if len(settings.EXECUTOR_SHARED_SECRET.strip()) < 32:
        raise RuntimeError("EXECUTOR_SHARED_SECRET must be at least 32 characters.")
    if not settings.EXECUTOR_RESERVED_IP.strip():
        raise RuntimeError("EXECUTOR_RESERVED_IP must be configured for the executor service.")
    if settings.EXECUTOR_REAL_ORDERS_ENABLED:
        # Real orders require the full safe configuration. Reserved IP and a
        # strong secret are already enforced above; this makes the intent explicit
        # and leaves room for additional real-order prerequisites.
        if settings.APP_ENV.lower() not in {"production", "live_route_test"}:
            raise RuntimeError(
                "EXECUTOR_REAL_ORDERS_ENABLED=true requires APP_ENV=production (or live_route_test)."
            )
        if not settings.EXECUTOR_RESERVED_IP.strip():
            raise RuntimeError("EXECUTOR_REAL_ORDERS_ENABLED=true requires EXECUTOR_RESERVED_IP.")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_executor_startup()
    logger.info(
        "Nova executor starting: code=%s real_orders_enabled=%s",
        settings.EXECUTOR_CODE.strip().upper(),
        settings.EXECUTOR_REAL_ORDERS_ENABLED,
    )
    yield


app = FastAPI(title="Nova Executor", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    _require_executor_config()
    return {"status": "ok", "executor_code": settings.EXECUTOR_CODE.strip().upper()}


@app.get("/egress-ip")
def egress_ip() -> dict[str, str]:
    _require_executor_config()
    return {"egress_ip": _lookup_public_ip()}


@app.post("/execute-order")
async def execute_order(request: Request) -> dict[str, Any]:
    _require_executor_config()
    raw_body = await request.body()
    timestamp = _integer_header(request, "X-Nova-Executor-Timestamp")
    nonce = request.headers.get("X-Nova-Executor-Nonce", "").strip()
    signature = request.headers.get("X-Nova-Executor-Signature", "").strip()
    executor_code = request.headers.get("X-Nova-Executor-Code", "").strip().upper()
    expected_code = settings.EXECUTOR_CODE.strip().upper()
    if executor_code != expected_code:
        raise HTTPException(status_code=403, detail="Executor code mismatch.")
    if not nonce or len(nonce) > 200 or not signature:
        raise HTTPException(status_code=401, detail="Signed executor headers are required.")
    if abs(int(time.time()) - timestamp) > settings.EXECUTOR_TIMESTAMP_TOLERANCE_SECONDS:
        raise HTTPException(status_code=401, detail="Executor request timestamp is outside tolerance.")
    # The signature covers timestamp.nonce.raw_body, so any credential bytes in
    # the body are authenticated; tampering or unsigned credentials are rejected.
    if not verify_executor_signature(
        secret=settings.EXECUTOR_SHARED_SECRET,
        timestamp=timestamp,
        nonce=nonce,
        raw_body=raw_body,
        signature=signature,
    ):
        raise HTTPException(status_code=401, detail="Invalid executor request signature.")
    _record_nonce(expected_code, nonce, timestamp)
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Order request body must be valid JSON.") from exc

    _reject_generic_secret_fields(payload)
    if not isinstance(payload.get("order"), dict) or not payload.get("correlation_id") or not payload.get("user_id"):
        raise HTTPException(status_code=422, detail="correlation_id, user_id, and order are required.")

    is_dry_run = payload.get("dry_run") is True

    if is_dry_run:
        # Dry-run requests must never carry credentials.
        if _contains_credential_field(payload):
            logger.warning("Rejected dry-run executor request that included a credential field.")
            raise HTTPException(
                status_code=422,
                detail="Credential fields are prohibited in dry-run executor requests.",
            )
        return {
            "status": "dry_run_verified",
            "executor_code": expected_code,
            "correlation_id": payload["correlation_id"],
            "egress_ip": _lookup_public_ip(),
            "message": "Signed request verified; Dhan was not called.",
        }

    # ---- Real order path ----
    if not settings.EXECUTOR_REAL_ORDERS_ENABLED:
        raise HTTPException(status_code=403, detail="Real broker calls are disabled on this executor.")

    access_token = str(payload.get("dhan_access_token") or "").strip()
    broker_client_id = str(payload.get("broker_client_id") or "").strip()
    if not access_token or not broker_client_id:
        # Do not echo which field is missing beyond the generic requirement.
        raise HTTPException(
            status_code=422,
            detail="Real executor orders require broker_client_id and dhan_access_token.",
        )

    order = payload["order"]
    dhan_payload = _build_validated_dhan_payload(
        correlation_id=str(payload["correlation_id"]),
        broker_client_id=broker_client_id,
        order=order,
    )

    try:
        result = place_dhan_order(
            client_id=broker_client_id,
            access_token=access_token,
            dhan_payload=dhan_payload,
            timeout_seconds=settings.EXECUTOR_REQUEST_TIMEOUT_SECONDS,
            send_client_id_header=settings.DHAN_SEND_CLIENT_ID_HEADER,
        )
    finally:
        # Defensive: drop local references to credential material immediately.
        access_token = ""
        payload["dhan_access_token"] = "[REDACTED]"

    if result.status == "broker_timeout":
        raise HTTPException(status_code=504, detail="broker_timeout")
    if result.status in {"broker_rejected", "broker_error"}:
        return {
            "status": "broker_rejected",
            "executor_code": expected_code,
            "correlation_id": payload["correlation_id"],
            "order_id": result.order_id,
            "egress_ip": _lookup_public_ip(),
            "message": result.message,
        }
    return {
        "status": result.status,  # sent | confirmed
        "executor_code": expected_code,
        "correlation_id": payload["correlation_id"],
        "order_id": result.order_id,
        "egress_ip": _lookup_public_ip(),
        "message": result.message,
    }


def _build_validated_dhan_payload(*, correlation_id: str, broker_client_id: str, order: dict[str, Any]) -> dict[str, Any]:
    action = str(order.get("action") or "").strip().upper()
    if action not in _VALID_TRANSACTION_TYPES:
        raise HTTPException(status_code=422, detail="order.action must be BUY or SELL.")
    security_id = str(order.get("security_id") or "").strip()
    if not security_id:
        raise HTTPException(status_code=422, detail="order.security_id is required for a real order.")
    exchange_segment = str(order.get("exchange_segment") or "").strip()
    if not exchange_segment:
        raise HTTPException(status_code=422, detail="order.exchange_segment is required for a real order.")
    try:
        quantity = int(order.get("quantity"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="order.quantity must be an integer.") from exc
    if quantity <= 0:
        raise HTTPException(status_code=422, detail="order.quantity must be greater than zero.")
    order_type = str(order.get("order_type") or "MARKET").strip().upper()
    product_type = str(order.get("product_type") or "INTRADAY").strip().upper()
    return {
        "dhanClientId": broker_client_id,
        "correlationId": correlation_id[:25],
        "transactionType": action,
        "exchangeSegment": exchange_segment,
        "productType": product_type,
        "orderType": order_type,
        "validity": "DAY",
        "securityId": security_id,
        "quantity": quantity,
        "price": 0,
        "disclosedQuantity": 0,
        "afterMarketOrder": False,
    }


def _record_nonce(executor_code: str, nonce: str, timestamp: int) -> None:
    with session_scope() as session:
        session.add(
            ExecutorNonceReceipt(
                executor_code=executor_code,
                nonce=nonce,
                request_timestamp=timestamp,
            )
        )
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="Executor request replay rejected.") from exc


def _lookup_public_ip() -> str:
    response = httpx.get(settings.EXECUTOR_EGRESS_CHECK_URL, timeout=settings.EXECUTOR_REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    address = str(payload.get("ip") or payload.get("egress_ip") or "").strip()
    if not address:
        raise HTTPException(status_code=503, detail="Unable to determine executor egress IP.")
    return address


def _integer_header(request: Request, name: str) -> int:
    try:
        return int(request.headers.get(name, ""))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=f"{name} must be an integer.") from exc


def _contains_credential_field(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).strip().lower() in _DRY_RUN_PROHIBITED_CREDENTIAL_FIELDS:
                return True
            if _contains_credential_field(item):
                return True
    elif isinstance(value, list):
        return any(_contains_credential_field(item) for item in value)
    return False


def _reject_generic_secret_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).strip().lower() in _PROHIBITED_SECRET_FIELDS:
                logger.warning("Rejected executor request containing a prohibited secret field.")
                raise HTTPException(status_code=422, detail="Credential fields are prohibited in executor order bodies.")
            _reject_generic_secret_fields(item)
    elif isinstance(value, list):
        for item in value:
            _reject_generic_secret_fields(item)


def _require_executor_config() -> None:
    if not settings.EXECUTOR_CODE.strip() or len(settings.EXECUTOR_SHARED_SECRET.strip()) < 32:
        raise HTTPException(status_code=503, detail="Executor identity or signing secret is not configured.")
