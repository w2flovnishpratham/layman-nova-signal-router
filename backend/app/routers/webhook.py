from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import BLOCK_DUPLICATE_SIGNALS, settings
from app.schemas.signal import NormalizedSignal, WebhookResponse
from app.services.audit_logger import log_audit_event, log_error_event, log_webhook_event
from app.services.credential_vault import get_webhook_secret, webhook_secret_strength_error
from app.services.execution_router import route_signal
from app.services.signal_parser import PayloadParseError, UnsupportedPayloadFormatError, parse_webhook_payload
from app.services.signal_validator import validate_signal
from app.services.state_store import add_seen_signal, get_app_state, get_engine_mode, update_app_state, utc_now
from app.services.trading_security import (
    claim_webhook_nonce,
    claim_webhook_signal,
    complete_webhook_signal,
    request_principal_id,
)
from app.services.user_connections import find_user_id_by_webhook_secret
from app.services.user_context import set_current_user_id


router = APIRouter()
logger = logging.getLogger("webhook")
_PROCESSING_SIGNAL_IDS: set[str] = set()
_PROCESSING_LOCK = threading.RLock()
_STRATEGY_LOCKS: dict[str, threading.Lock] = {}
_STRATEGY_LOCKS_GUARD = threading.RLock()
_CLIENT_REQUESTS: dict[str, list[float]] = {}
_CLIENT_REQUESTS_LOCK = threading.RLock()
_SENSITIVE_WEBHOOK_KEYS = {
    "access_token",
    "access-token",
    "authorization",
    "secret",
    "token",
    "webhook_secret",
}


def _response(status_code: int, payload: WebhookResponse) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def _begin_processing_signal(signal_id: str) -> bool:
    with _PROCESSING_LOCK:
        if signal_id in _PROCESSING_SIGNAL_IDS:
            return False
        _PROCESSING_SIGNAL_IDS.add(signal_id)
        return True


def _finish_processing_signal(signal_id: str) -> None:
    with _PROCESSING_LOCK:
        _PROCESSING_SIGNAL_IDS.discard(signal_id)


def _strategy_lock_key(strategy_code: str | None) -> str:
    key = str(strategy_code or "DEFAULT").strip().upper()
    return key or "DEFAULT"


def _get_strategy_lock(strategy_code: str | None) -> tuple[str, threading.Lock]:
    key = _strategy_lock_key(strategy_code)
    with _STRATEGY_LOCKS_GUARD:
        lock = _STRATEGY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _STRATEGY_LOCKS[key] = lock
        return key, lock


def _webhook_rate_limited(client_host: str) -> bool:
    limit = max(int(settings.WEBHOOK_RATE_LIMIT_PER_MINUTE), 1)
    now = time.monotonic()
    cutoff = now - 60.0
    with _CLIENT_REQUESTS_LOCK:
        attempts = [item for item in _CLIENT_REQUESTS.get(client_host, []) if item >= cutoff]
        if len(attempts) >= limit:
            _CLIENT_REQUESTS[client_host] = attempts
            return True
        attempts.append(now)
        _CLIENT_REQUESTS[client_host] = attempts
        return False


def _safe_raw_body_for_log(raw_body: str) -> str:
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        encoded = raw_body.encode("utf-8", errors="replace")
        digest = hashlib.sha256(encoded).hexdigest()
        return f"[invalid JSON omitted; bytes={len(encoded)}; sha256={digest}]"

    def redact(value):
        if isinstance(value, dict):
            return {
                key: "[REDACTED]" if str(key).strip().lower() in _SENSITIVE_WEBHOOK_KEYS else redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    return json.dumps(redact(payload), separators=(",", ":"), ensure_ascii=True)


def _bind_webhook_runtime_scope(data: dict) -> str | None:
    if not isinstance(data, dict):
        return None
    secret = str(data.get("secret") or "").strip()
    if not secret:
        return None
    user_id = find_user_id_by_webhook_secret(secret)
    if user_id:
        set_current_user_id(user_id)
    return user_id


def _valid_webhook_signature(raw_body: str, secret: str, signature: str, timestamp: str) -> bool:
    signing_value = f"{timestamp}.{raw_body}"
    expected = hmac.new(secret.encode("utf-8"), signing_value.encode("utf-8"), hashlib.sha256).hexdigest()
    supplied = signature.removeprefix("sha256=").strip()
    return secrets.compare_digest(expected, supplied)


def _valid_legacy_webhook_signature(raw_body: str, secret: str, signature: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), raw_body.encode("utf-8"), hashlib.sha256).hexdigest()
    supplied = signature.removeprefix("sha256=").strip()
    return secrets.compare_digest(expected, supplied)


def _legacy_webhook_auth_allowed() -> bool:
    return (
        settings.APP_ENV.lower() in {"local", "test"}
        and settings.WEBHOOK_ALLOW_LEGACY_AUTH_LOCAL
        and not settings.ENABLE_LIVE_ORDERS
    )


def _timestamped_hmac_required() -> bool:
    return (
        settings.APP_ENV.lower() == "production"
        or settings.ENABLE_LIVE_ORDERS
        or settings.WEBHOOK_HMAC_REQUIRED
        or not _legacy_webhook_auth_allowed()
    )


def _validate_webhook_authentication(
    request: Request,
    *,
    raw_body: str,
    secret: str,
    now_epoch: int | None = None,
) -> tuple[int | None, str | None]:
    timestamp_text = str(request.headers.get("x-nova-timestamp") or "").strip()
    signature = str(request.headers.get("x-nova-signature") or "").strip()
    required = _timestamped_hmac_required()

    if not timestamp_text and not signature and not required:
        return None, None

    if not timestamp_text:
        if not required and signature and _valid_legacy_webhook_signature(raw_body, secret, signature):
            return None, None
        return None, "WEBHOOK_AUTH_FAILED"
    if not signature:
        return None, "WEBHOOK_AUTH_FAILED"

    try:
        request_timestamp = int(timestamp_text)
    except ValueError:
        return None, "WEBHOOK_AUTH_FAILED"

    now = int(time.time()) if now_epoch is None else int(now_epoch)
    tolerance = max(int(settings.WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS), 1)
    if request_timestamp < now - tolerance or request_timestamp > now + tolerance:
        return None, "WEBHOOK_AUTH_FAILED"
    if not _valid_webhook_signature(raw_body, secret, signature, timestamp_text):
        return None, "WEBHOOK_AUTH_FAILED"
    return request_timestamp, None


def _route_payload(payload: NormalizedSignal, client_host: str) -> tuple[dict | None, JSONResponse | None]:
    try:
        update_app_state(
            last_signal_id=payload.signal_id,
            last_alert_at=utc_now(),
            last_message=f"{payload.action} {payload.payload_format} alert accepted for routing",
        )
        log_audit_event(
            "SIGNAL_RECEIVED",
            (
                f"Existing Pine multi_leg_order alert received and normalized as {payload.action}/{payload.side}."
                if payload.payload_format == "PINE_MULTI_LEG"
                else f"NOVA {payload.action} signal received for {payload.trading_symbol or payload.symbol}"
            ),
            metadata={
                "signal_id": payload.signal_id,
                "action": payload.action,
                "side": payload.side,
                "payload_format": payload.payload_format,
                "qty": payload.qty,
                "symbol": payload.symbol,
                "strike": payload.strike,
                "expiry": payload.expiry,
                "option_side": payload.option_side,
            },
        )

        try:
            execution_result = route_signal(payload)
        except Exception as exc:
            message = f"Webhook routing failed: {exc}"
            log_error_event(
                "WEBHOOK_ROUTING_FAILED",
                message,
                metadata={
                    "signal_id": payload.signal_id,
                    "payload_format": payload.payload_format,
                    "action": payload.action,
                    "side": payload.side,
                    "client_host": client_host,
                },
            )
            update_app_state(
                state="ERROR",
                last_signal_id=payload.signal_id,
                last_alert_at=utc_now(),
                last_message=message,
            )
            return None, _response(
                500,
                WebhookResponse(
                    accepted=False,
                    signal_id=payload.signal_id,
                    action=payload.action,
                    payload_format=payload.payload_format,
                    status="ERROR",
                    message=message,
                ),
            )

        if BLOCK_DUPLICATE_SIGNALS:
            add_seen_signal(payload.signal_id)
        return execution_result, None
    finally:
        if BLOCK_DUPLICATE_SIGNALS:
            _finish_processing_signal(payload.signal_id)


@router.post("/tradingview")
async def tradingview_webhook(request: Request) -> JSONResponse:
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    client_host = request.client.host if request.client else "unknown"

    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        message = f"Invalid JSON payload: {exc.msg}"
        logger.warning("WEBHOOK_INVALID_JSON from %s: %s", client_host, message)
        return _response(400, WebhookResponse(accepted=False, status="INVALID_JSON", message=message))

    if not isinstance(data, dict):
        message = "Invalid JSON payload: top-level value must be an object."
        logger.warning("WEBHOOK_INVALID_JSON from %s: %s", client_host, message)
        return _response(400, WebhookResponse(accepted=False, status="INVALID_JSON", message=message))

    bound_user_id = _bind_webhook_runtime_scope(data)
    if (settings.AUTH_REQUIRED or settings.APP_ENV.lower() == "production") and bound_user_id is None:
        logger.warning("WEBHOOK_UNKNOWN_SECRET from %s", client_host)
        return _response(
            403,
            WebhookResponse(
                accepted=False,
                status="UNAUTHORIZED",
                message="Webhook rejected: unknown secret.",
            ),
        )

    expected_secret = get_webhook_secret()
    if not expected_secret:
        logger.warning("WEBHOOK_SETUP_INCOMPLETE from %s", client_host)
        return _response(
            403,
            WebhookResponse(
                accepted=False,
                status="SETUP_INCOMPLETE",
                message="Webhook authentication failed.",
            ),
        )

    secret_strength_error = webhook_secret_strength_error(expected_secret)
    if secret_strength_error:
        logger.warning("WEBHOOK_SECRET_WEAK from %s", client_host)
        return _response(
            403,
            WebhookResponse(
                accepted=False,
                status="SETUP_INCOMPLETE",
                message="Webhook authentication failed.",
            ),
        )

    request_timestamp, auth_error = _validate_webhook_authentication(
        request,
        raw_body=raw_body,
        secret=expected_secret,
    )
    if auth_error:
        logger.warning("WEBHOOK_AUTH_FAILED from %s", client_host)
        return _response(
            403,
            WebhookResponse(
                accepted=False,
                status="UNAUTHORIZED",
                message="Webhook authentication failed.",
            ),
        )

    if _webhook_rate_limited(f"{bound_user_id or 'anon'}|{client_host}"):
        log_audit_event(
            "WEBHOOK_RATE_LIMITED",
            "TradingView webhook request rate limit exceeded.",
            severity="WARNING",
            metadata={"client_host": client_host},
        )
        return _response(
            429,
            WebhookResponse(
                accepted=False,
                status="RATE_LIMITED",
                message="Webhook rate limit exceeded. Retry after one minute.",
            ),
        )

    log_webhook_event(
        {
            "event_type": "WEBHOOK_RAW_REQUEST",
            "client_host": client_host,
            "raw_body": _safe_raw_body_for_log(raw_body),
        }
    )

    engine_mode = get_engine_mode(legacy_fallback=False)
    if engine_mode is None and not bool(get_app_state().get("engine_started")):
        return _response(
            422,
            WebhookResponse(
                accepted=False,
                status="ENGINE_MODE_NOT_SET",
                message="Webhook rejected: select Paper or Live mode before routing signals.",
            ),
        )
    engine_mode = engine_mode or get_engine_mode()

    try:
        payload = parse_webhook_payload(data)
    except UnsupportedPayloadFormatError as exc:
        log_audit_event(
            "UNSUPPORTED_PAYLOAD_FORMAT",
            str(exc),
            severity="WARNING",
            metadata={"client_host": client_host},
        )
        return _response(
            400,
            WebhookResponse(
                accepted=False,
                action=data.get("action"),
                status="UNSUPPORTED_PAYLOAD_FORMAT",
                message=str(exc),
            ),
        )
    except PayloadParseError as exc:
        log_audit_event(
            "WEBHOOK_PARSE_FAILED",
            str(exc),
            severity="WARNING",
            metadata={"client_host": client_host, "signal_id": data.get("signal_id")},
        )
        return _response(
            422,
            WebhookResponse(
                accepted=False,
                signal_id=data.get("signal_id"),
                action=data.get("action"),
                status=getattr(exc, "status", "INVALID_PAYLOAD"),
                message=str(exc),
            ),
        )

    raw_signal_id = data.get("signal_id")
    if raw_signal_id in (None, "") and isinstance(data.get("order_legs"), list) and data["order_legs"]:
        first_leg = data["order_legs"][0]
        if isinstance(first_leg, dict):
            raw_signal_id = first_leg.get("signal_id")
    if (
        settings.REQUIRE_SIGNAL_ID_LIVE
        and (settings.ENABLE_LIVE_ORDERS or engine_mode == "live")
        and raw_signal_id in (None, "")
    ):
        log_audit_event(
            "LIVE_SIGNAL_ID_REQUIRED",
            "Live webhook blocked because signal_id was not explicitly provided.",
            severity="WARNING",
            metadata={"payload_format": payload.payload_format},
        )
        return _response(
            422,
            WebhookResponse(
                accepted=False,
                action=payload.action,
                payload_format=payload.payload_format,
                status="SIGNAL_ID_REQUIRED",
                message="Live webhook requires an explicit signal_id.",
            ),
        )

    log_webhook_event(
        {
            "event_type": "WEBHOOK_NORMALIZED",
            "client_host": client_host,
            "payload_format": payload.payload_format,
            "signal_id": payload.signal_id,
            "normalized_action": payload.action,
            "normalized_side": payload.side,
            "normalized_qty": payload.qty,
            "normalized_symbol": payload.symbol,
            "normalized_strike": payload.strike,
            "normalized_expiry": payload.expiry,
            "normalized_option_side": payload.option_side,
            "message": (
                "Existing Pine multi_leg_order alert received and normalized."
                if payload.payload_format == "PINE_MULTI_LEG"
                else "NOVA alert received and normalized."
            ),
        }
    )

    if payload.secret != expected_secret:
        log_audit_event(
            "WEBHOOK_AUTH_FAILED",
            "Invalid webhook secret.",
            severity="WARNING",
            metadata={
                "client_host": client_host,
                "signal_id": payload.signal_id,
                "payload_format": payload.payload_format,
            },
        )
        return _response(
            403,
            WebhookResponse(
                accepted=False,
                signal_id=payload.signal_id,
                action=payload.action,
                payload_format=payload.payload_format,
                status="UNAUTHORIZED",
                message="Webhook rejected: invalid secret.",
            ),
        )

    ok, error = validate_signal(payload)
    if not ok:
        log_audit_event(
            "SIGNAL_INVALID",
            error or "Signal invalid.",
            severity="WARNING",
            metadata={"signal_id": payload.signal_id, "action": payload.action},
        )
        return _response(
            422,
            WebhookResponse(
                accepted=False,
                signal_id=payload.signal_id,
                action=payload.action,
                payload_format=payload.payload_format,
                status="INVALID_SIGNAL",
                message=error or "Signal invalid.",
            ),
        )

    principal_id = request_principal_id(bound_user_id)
    nonce = str(request.headers.get("x-nova-nonce") or "").strip()
    if nonce:
        if request_timestamp is None or not 8 <= len(nonce) <= 128:
            return _response(
                403,
                WebhookResponse(
                    accepted=False,
                    signal_id=payload.signal_id,
                    action=payload.action,
                    payload_format=payload.payload_format,
                    status="UNAUTHORIZED",
                    message="Webhook authentication failed.",
                ),
            )
        nonce_claim = claim_webhook_nonce(principal_id, nonce, request_timestamp)
        if not nonce_claim.claimed:
            log_audit_event(
                "WEBHOOK_NONCE_REPLAY",
                "Webhook request blocked because its nonce was already used.",
                severity="WARNING",
                metadata={"signal_id": payload.signal_id, "payload_format": payload.payload_format},
            )
            return _response(
                409,
                WebhookResponse(
                    accepted=False,
                    signal_id=payload.signal_id,
                    action=payload.action,
                    payload_format=payload.payload_format,
                    status="REPLAY_BLOCKED",
                    message="Webhook replay rejected.",
                ),
            )

    signal_claim = claim_webhook_signal(principal_id, payload)
    if not signal_claim.claimed:
        suspicious = signal_claim.suspicious
        message = (
            f"Trade blocked: signal_id {payload.signal_id} was reused with different order data"
            if suspicious
            else f"Trade blocked: duplicate signal_id {payload.signal_id}"
        )
        log_audit_event(
            "SUSPICIOUS_DUPLICATE_SIGNAL" if suspicious else "DUPLICATE_SIGNAL",
            message,
            severity="WARNING",
            metadata={"signal_id": payload.signal_id, "payload_format": payload.payload_format},
        )
        update_app_state(
            state="DUPLICATE_SIGNAL",
            last_signal_id=payload.signal_id,
            last_alert_at=utc_now(),
            last_message=message,
        )
        return _response(
            409 if suspicious else 200,
            WebhookResponse(
                accepted=False,
                signal_id=payload.signal_id,
                action=payload.action,
                payload_format=payload.payload_format,
                status="BLOCKED",
                message=message,
                execution_result={
                    "blocked": True,
                    "reason": message,
                    "payload_format": payload.payload_format,
                    "normalized_action": payload.action,
                    "normalized_side": payload.side,
                    "normalized_qty": payload.qty,
                    "normalized_symbol": payload.symbol,
                    "normalized_strike": payload.strike,
                    "normalized_expiry": payload.expiry,
                    "normalized_option_side": payload.option_side,
                },
            ),
        )

    if BLOCK_DUPLICATE_SIGNALS and not _begin_processing_signal(payload.signal_id):
        message = f"Trade blocked: signal_id {payload.signal_id} is already being processed"
        log_audit_event(
            "DUPLICATE_SIGNAL",
            message,
            severity="WARNING",
            metadata={"signal_id": payload.signal_id, "payload_format": payload.payload_format},
        )
        update_app_state(
            state="DUPLICATE_SIGNAL",
            last_signal_id=payload.signal_id,
            last_alert_at=utc_now(),
            last_message=message,
        )
        complete_webhook_signal(signal_claim.record_id, status="duplicate_in_process", message=message)
        return _response(
            200,
            WebhookResponse(
                accepted=False,
                signal_id=payload.signal_id,
                action=payload.action,
                payload_format=payload.payload_format,
                status="BLOCKED",
                message=message,
                execution_result={
                    "blocked": True,
                    "reason": message,
                    "payload_format": payload.payload_format,
                    "normalized_action": payload.action,
                    "normalized_side": payload.side,
                    "normalized_qty": payload.qty,
                    "normalized_symbol": payload.symbol,
                    "normalized_strike": payload.strike,
                    "normalized_expiry": payload.expiry,
                    "normalized_option_side": payload.option_side,
                },
            ),
        )

    strategy_key, strategy_lock = _get_strategy_lock(payload.strategy_code)
    if not strategy_lock.acquire(blocking=False):
        log_audit_event(
            "STRATEGY_SIGNAL_QUEUED",
            f"Signal queued because strategy {strategy_key} is already processing another alert.",
            metadata={
                "signal_id": payload.signal_id,
                "strategy_code": payload.strategy_code,
                "payload_format": payload.payload_format,
            },
        )
        strategy_lock.acquire()

    try:
        execution_result, error_response = _route_payload(payload, client_host)
    finally:
        strategy_lock.release()

    if error_response is not None:
        complete_webhook_signal(
            signal_claim.record_id,
            status="routing_error",
            message="Webhook routing failed.",
        )
        return error_response
    if execution_result is None:
        message = "Webhook routing failed without an execution result."
        complete_webhook_signal(signal_claim.record_id, status="routing_error", message=message)
        return _response(
            500,
            WebhookResponse(
                accepted=False,
                signal_id=payload.signal_id,
                action=payload.action,
                payload_format=payload.payload_format,
                status="ERROR",
                message=message,
            ),
        )

    blocked = bool(execution_result.get("blocked"))
    success = execution_result.get("success")
    status = execution_result.get("status") or ("BLOCKED" if blocked else "ORDER_PLACED")
    if blocked:
        message = execution_result.get("reason") or "Trade blocked."
        accepted = False
    elif success is False:
        message = execution_result.get("reason") or execution_result.get("error") or "Dhan order request failed"
        accepted = False
    else:
        message = f"{payload.action.title()} order placed in {engine_mode.upper()} mode"
        accepted = True

    complete_webhook_signal(
        signal_claim.record_id,
        status="accepted" if accepted else "blocked" if blocked else "failed",
        message=message,
        correlation_id=execution_result.get("correlation_id"),
    )

    return _response(
        200,
        WebhookResponse(
            accepted=accepted,
            signal_id=payload.signal_id,
            action=payload.action,
            payload_format=payload.payload_format,
            status=status,
            message=message,
            execution_result=execution_result,
        ),
    )
