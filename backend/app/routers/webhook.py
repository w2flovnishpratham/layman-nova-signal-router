from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import BLOCK_DUPLICATE_SIGNALS, settings
from app.db.engine import database_configured
from app.schemas.signal import NormalizedSignal, WebhookResponse
from app.services.audit_logger import log_audit_event, log_error_event, log_webhook_event
from app.services.credential_vault import get_webhook_secret, webhook_secret_strength_error
from app.services.execution_router import route_signal
from app.services.normalized_errors import classify_failure, order_journey
from app.services.signal_parser import PayloadParseError, UnsupportedPayloadFormatError, parse_webhook_payload
from app.services.signal_validator import validate_signal
from app.services.state_store import add_seen_signal, get_app_state, get_engine_mode, has_seen_signal, update_app_state, utc_now
from app.services import webhook_replay_store


router = APIRouter()
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


def _webhook_error(
    message: str,
    *,
    status: str,
    status_code: int,
    payload: NormalizedSignal | None = None,
    action: str | None = None,
    payload_format: str | None = None,
) -> dict[str, Any]:
    return classify_failure(
        message,
        source="TRADINGVIEW",
        status=status,
        status_code=status_code,
        signal=payload,
        order_sent_to_broker=False,
        money_at_risk=False,
        debug_pack={
            "action": payload.action if payload is not None else action,
            "payloadFormat": payload.payload_format if payload is not None else payload_format,
        },
    )


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


def _valid_webhook_signature(raw_body: str, secret: str, signature: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), raw_body.encode("utf-8"), hashlib.sha256).hexdigest()
    supplied = signature.removeprefix("sha256=").strip()
    return secrets.compare_digest(expected, supplied)


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

        if BLOCK_DUPLICATE_SIGNALS and not database_configured():
            add_seen_signal(payload.signal_id)
        return execution_result, None
    finally:
        if BLOCK_DUPLICATE_SIGNALS:
            _finish_processing_signal(payload.signal_id)


@router.post("/tradingview")
async def tradingview_webhook(request: Request) -> JSONResponse:
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    client_host = request.client.host if request.client else "unknown"
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

    if _webhook_rate_limited(client_host):
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

    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        message = f"Invalid JSON payload: {exc.msg}"
        log_error_event("WEBHOOK_INVALID_JSON", message, metadata={"client_host": client_host})
        return _response(
            400,
            WebhookResponse(
                accepted=False,
                status="INVALID_JSON",
                message=message,
                error=_webhook_error(message, status="INVALID_JSON", status_code=400),
            ),
        )

    if not isinstance(data, dict):
        message = "Invalid JSON payload: top-level value must be an object."
        log_error_event("WEBHOOK_INVALID_JSON", message, metadata={"client_host": client_host})
        return _response(
            400,
            WebhookResponse(
                accepted=False,
                status="INVALID_JSON",
                message=message,
                error=_webhook_error(message, status="INVALID_JSON", status_code=400),
            ),
        )

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
                error=_webhook_error(str(exc), status="UNSUPPORTED_PAYLOAD_FORMAT", status_code=400, action=data.get("action")),
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
                error=_webhook_error(str(exc), status=getattr(exc, "status", "INVALID_PAYLOAD"), status_code=422, action=data.get("action")),
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

    expected_secret = get_webhook_secret()
    if not expected_secret:
        log_audit_event(
            "WEBHOOK_SETUP_INCOMPLETE",
            "Webhook secret is not configured. Complete setup first.",
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
                status="SETUP_INCOMPLETE",
                message="Webhook secret is not configured. Complete setup first.",
                error=_webhook_error("Webhook secret is not configured. Complete setup first.", status="SETUP_INCOMPLETE", status_code=403, payload=payload),
            ),
        )

    secret_strength_error = webhook_secret_strength_error(expected_secret)
    if secret_strength_error:
        log_audit_event(
            "WEBHOOK_SECRET_WEAK",
            secret_strength_error,
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
                status="SETUP_INCOMPLETE",
                message=secret_strength_error,
                error=_webhook_error(secret_strength_error, status="SETUP_INCOMPLETE", status_code=403, payload=payload),
            ),
        )

    signature = request.headers.get("x-nova-signature")
    if signature or settings.WEBHOOK_HMAC_REQUIRED:
        if not signature or not _valid_webhook_signature(raw_body, expected_secret, signature):
            log_audit_event(
                "WEBHOOK_HMAC_AUTH_FAILED",
                "Invalid or missing webhook HMAC signature.",
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
                    message="Webhook rejected: invalid or missing HMAC signature.",
                    error=_webhook_error("Webhook rejected: invalid or missing HMAC signature.", status="UNAUTHORIZED", status_code=403, payload=payload),
                ),
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
                error=_webhook_error("Webhook rejected: invalid secret.", status="UNAUTHORIZED", status_code=403, payload=payload),
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
                error=_webhook_error(error or "Signal invalid.", status="INVALID_SIGNAL", status_code=422, payload=payload),
            ),
        )

    duplicate_message: str | None = None
    duplicate_status_code = 200
    event_provider = "tradingview"
    event_claimed = False
    if BLOCK_DUPLICATE_SIGNALS:
        if database_configured():
            try:
                event_claim = webhook_replay_store.claim_webhook_event(
                    provider=event_provider,
                    event_id=payload.signal_id,
                    raw_body=raw_body,
                    signature_ok=True,
                    metadata={
                        "payload_format": payload.payload_format,
                        "action": payload.action,
                        "side": payload.side,
                    },
                )
            except Exception as exc:
                message = f"Webhook replay store unavailable: {exc}"
                log_error_event(
                    "WEBHOOK_REPLAY_STORE_UNAVAILABLE",
                    message,
                    metadata={"signal_id": payload.signal_id, "payload_format": payload.payload_format},
                )
                return _response(
                    503,
                    WebhookResponse(
                        accepted=False,
                        signal_id=payload.signal_id,
                        action=payload.action,
                        payload_format=payload.payload_format,
                        status="REPLAY_STORE_UNAVAILABLE",
                        message="Webhook replay store unavailable; refusing to route.",
                        error=_webhook_error(
                            "Webhook replay store unavailable; refusing to route.",
                            status="REPLAY_STORE_UNAVAILABLE",
                            status_code=503,
                            payload=payload,
                        ),
                    ),
                )
            if event_claim.get("status") == "fresh":
                event_claimed = True
            elif event_claim.get("status") == "tampered":
                duplicate_message = f"Webhook rejected: duplicate signal_id {payload.signal_id} has a different body."
                duplicate_status_code = 409
            else:
                duplicate_message = f"Trade blocked: duplicate signal_id {payload.signal_id}"
        elif has_seen_signal(payload.signal_id):
            duplicate_message = f"Trade blocked: duplicate signal_id {payload.signal_id}"

    if duplicate_message:
        message = duplicate_message
        normalized_error = _webhook_error(message, status="DUPLICATE_SIGNAL", status_code=200, payload=payload)
        execution_result = {
            "blocked": True,
            "success": False,
            "reason": message,
            "payload_format": payload.payload_format,
            "normalized_action": payload.action,
            "normalized_side": payload.side,
            "normalized_qty": payload.qty,
            "normalized_symbol": payload.symbol,
            "normalized_strike": payload.strike,
            "normalized_expiry": payload.expiry,
            "normalized_option_side": payload.option_side,
            "normalizedError": normalized_error,
        }
        execution_result["orderJourney"] = order_journey(signal=payload, execution_result=execution_result, normalized_error=normalized_error)
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
        return _response(
            duplicate_status_code,
            WebhookResponse(
                accepted=False,
                signal_id=payload.signal_id,
                action=payload.action,
                payload_format=payload.payload_format,
                status="BLOCKED",
                message=message,
                execution_result=execution_result,
                error=normalized_error,
            ),
        )

    if BLOCK_DUPLICATE_SIGNALS and not _begin_processing_signal(payload.signal_id):
        message = f"Trade blocked: signal_id {payload.signal_id} is already being processed"
        normalized_error = _webhook_error(message, status="DUPLICATE_SIGNAL", status_code=200, payload=payload)
        execution_result = {
            "blocked": True,
            "success": False,
            "reason": message,
            "payload_format": payload.payload_format,
            "normalized_action": payload.action,
            "normalized_side": payload.side,
            "normalized_qty": payload.qty,
            "normalized_symbol": payload.symbol,
            "normalized_strike": payload.strike,
            "normalized_expiry": payload.expiry,
            "normalized_option_side": payload.option_side,
            "normalizedError": normalized_error,
        }
        execution_result["orderJourney"] = order_journey(signal=payload, execution_result=execution_result, normalized_error=normalized_error)
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
        return _response(
            200,
            WebhookResponse(
                accepted=False,
                signal_id=payload.signal_id,
                action=payload.action,
                payload_format=payload.payload_format,
                status="BLOCKED",
                message=message,
                execution_result=execution_result,
                error=normalized_error,
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
        if event_claimed:
            try:
                webhook_replay_store.update_webhook_event(
                    provider=event_provider,
                    event_id=payload.signal_id,
                    processed_status="rejected",
                    error="routing_error",
                )
            except Exception:
                pass
        return error_response
    if execution_result is None:
        if event_claimed:
            try:
                webhook_replay_store.update_webhook_event(
                    provider=event_provider,
                    event_id=payload.signal_id,
                    processed_status="rejected",
                    error="missing_execution_result",
                )
            except Exception:
                pass
        message = "Webhook routing failed without an execution result."
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

    if event_claimed:
        try:
            webhook_replay_store.update_webhook_event(
                provider=event_provider,
                event_id=payload.signal_id,
                processed_status="rejected" if not accepted else "fanned_out",
                error=message if not accepted else None,
                metadata={"status": status},
            )
        except Exception:
            pass

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
            error=execution_result.get("normalizedError") if isinstance(execution_result.get("normalizedError"), dict) else None,
        ),
    )
