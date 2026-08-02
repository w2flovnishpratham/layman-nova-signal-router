"""Payment provider webhook routes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.auth.dependencies import get_current_user
from app.config import settings
from app.db.engine import database_configured, session_scope
from app.services import entitlements
from app.services.razorpay_checkout import (
    RazorpayPlanConfigError,
    RazorpayProviderConfigError,
    RazorpaySubscriptionCreateError,
    create_razorpay_subscription_checkout,
)
from app.services.razorpay_webhooks import (
    RazorpayWebhookConflict,
    RazorpayWebhookRejected,
    RazorpayWebhookStorageUnavailable,
    process_razorpay_webhook,
    verify_razorpay_signature,
)
from app.services.user_context import CurrentUser


router = APIRouter(prefix="/api/payments", tags=["Payments"])


class RazorpayCreateSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plan_code: str


@router.get("/entitlements/status")
def payment_entitlement_status(
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    if database_configured():
        with session_scope() as db:
            evaluation = entitlements.evaluate_entitlement(
                entitlements.get_user_entitlement(db, user.id)
            )
            paper_trading_enabled = entitlements.has_paper_entitlement(db, user.id)
    else:
        evaluation = entitlements.evaluate_entitlement(None)
        paper_trading_enabled = False
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "entitlement": {
                **evaluation.as_safe_dict(),
                "paper_trading_enabled": paper_trading_enabled,
            },
        },
    )


@router.post("/razorpay/create-subscription")
def create_razorpay_subscription(
    payload: RazorpayCreateSubscriptionRequest,
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    try:
        result = create_razorpay_subscription_checkout(
            user=user,
            plan_code=payload.plan_code,
        )
    except RazorpayPlanConfigError as exc:
        return _safe_error(400, str(exc) or "Payment plan is not configured.")
    except RazorpayProviderConfigError as exc:
        return _safe_error(503, str(exc) or "Payment provider is not configured.")
    except RazorpaySubscriptionCreateError as exc:
        return _safe_error(502, str(exc) or "Payment subscription could not be created.")
    return JSONResponse(status_code=200, content=result.as_response())


@router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request) -> JSONResponse:
    """Receive public Razorpay webhooks protected by raw-body HMAC."""

    if settings.payment_provider_normalized != "razorpay":
        return _safe_error(400, "Payment webhook rejected.")

    signature = request.headers.get("X-Razorpay-Signature")
    raw_body = await request.body()
    # TEMPORARY diagnostic logging to see the exact payload shape for
    # payment.*/order.* events on one-time (total_count=1) subscriptions --
    # remove once the user-resolution fallback is implemented.
    logging.getLogger("app.payments.webhook_debug").warning(
        "razorpay_webhook_payload event_id=%s body=%s",
        request.headers.get("x-razorpay-event-id"),
        raw_body.decode("utf-8", errors="replace"),
    )
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
