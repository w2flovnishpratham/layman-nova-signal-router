from __future__ import annotations

import json
import threading

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import BLOCK_DUPLICATE_SIGNALS, settings
from app.schemas.signal import WebhookResponse
from app.services.audit_logger import log_audit_event, log_error_event, log_webhook_event
from app.services.credential_vault import get_webhook_secret
from app.services.execution_router import route_signal
from app.services.signal_parser import PayloadParseError, UnsupportedPayloadFormatError, parse_webhook_payload
from app.services.signal_validator import validate_signal
from app.services.state_store import add_seen_signal, has_seen_signal, update_app_state, utc_now


router = APIRouter()
_PROCESSING_SIGNAL_IDS: set[str] = set()
_PROCESSING_LOCK = threading.RLock()


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


@router.post("/tradingview")
async def tradingview_webhook(request: Request) -> JSONResponse:
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    client_host = request.client.host if request.client else "unknown"

    log_webhook_event(
        {
            "event_type": "WEBHOOK_RAW_REQUEST",
            "client_host": client_host,
            "raw_body": raw_body,
        }
    )

    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        message = f"Invalid JSON payload: {exc.msg}"
        log_error_event("WEBHOOK_INVALID_JSON", message, metadata={"client_host": client_host})
        return _response(400, WebhookResponse(accepted=False, status="INVALID_JSON", message=message))

    if not isinstance(data, dict):
        message = "Invalid JSON payload: top-level value must be an object."
        log_error_event("WEBHOOK_INVALID_JSON", message, metadata={"client_host": client_host})
        return _response(400, WebhookResponse(accepted=False, status="INVALID_JSON", message=message))

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

    if BLOCK_DUPLICATE_SIGNALS and has_seen_signal(payload.signal_id):
        message = f"Trade blocked: duplicate signal_id {payload.signal_id}"
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
            },
        )
        update_app_state(
            state="ERROR",
            last_signal_id=payload.signal_id,
            last_alert_at=utc_now(),
            last_message=message,
        )
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
    finally:
        if BLOCK_DUPLICATE_SIGNALS:
            _finish_processing_signal(payload.signal_id)
    if BLOCK_DUPLICATE_SIGNALS:
        add_seen_signal(payload.signal_id)
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
        mode = settings.DHAN_MODE.upper()
        message = f"{payload.action.title()} order placed in {mode} mode"
        accepted = True

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
