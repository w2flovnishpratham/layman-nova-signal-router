"""Payment provider webhook routes."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.services.razorpay_webhooks import (
    RazorpayWebhookConflict,
    RazorpayWebhookRejected,
    RazorpayWebhookStorageUnavailable,
    process_razorpay_webhook,
    verify_razorpay_signature,
)


router = APIRouter(prefix="/api/payments", tags=["Payments"])


@router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request) -> JSONResponse:
    """Receive public Razorpay webhooks protected by raw-body HMAC."""

    if settings.payment_provider_normalized != "razorpay":
        return _safe_error(400, "Payment webhook rejected.")

    signature = request.headers.get("X-Razorpay-Signature")
    raw_body = await request.body()
    try:
        verify_razorpay_signature(
            raw_body=raw_body,
            received_signature=signature,
            webhook_secret=settings.RAZORPAY_WEBHOOK_SECRET,
        )
    except RazorpayWebhookRejected as exc:
        message = str(exc) or "Payment webhook rejected."
        status_code = 401 if message == "Invalid Razorpay signature." else 400
        return _safe_error(status_code, message)

    event_id = request.headers.get("x-razorpay-event-id")
    if not (event_id or "").strip():
        return _safe_error(400, "Missing Razorpay event id.")

    try:
        result = process_razorpay_webhook(raw_body=raw_body, provider_event_id=event_id)
    except RazorpayWebhookConflict:
        return _safe_error(409, "Payment webhook rejected.")
    except RazorpayWebhookStorageUnavailable:
        return _safe_error(503, "Payment webhook rejected.")
    except RazorpayWebhookRejected as exc:
        return _safe_error(400, str(exc) or "Payment webhook rejected.")

    return JSONResponse(status_code=200, content=result.as_response())


def _safe_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"ok": False, "error": message})
