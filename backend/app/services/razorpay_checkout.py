"""Server-side Razorpay subscription checkout creation."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.services.user_context import CurrentUser

logger = logging.getLogger("nova_signal_router.razorpay")


RAZORPAY_SUBSCRIPTIONS_URL = "https://api.razorpay.com/v1/subscriptions"
RAZORPAY_PLANS_URL = "https://api.razorpay.com/v1/plans"
RAZORPAY_PAYMENT_LINKS_URL = "https://api.razorpay.com/v1/payment_links"
DEFAULT_SUBSCRIPTION_TOTAL_COUNT = 12
# A one-time purchase modeled as a single charge on a long-cycle Razorpay
# plan (e.g. a ~100-year billing interval) -- total_count=1 means the
# subscription completes after the first charge, never renews.
PAPER_PREMIUM_TOTAL_COUNT = 1
PREMIUM_PLAN_CODE = "premium_monthly"
PAPER_PLAN_CODE = "paper_premium"
VALID_PLAN_CODES = {PREMIUM_PLAN_CODE, PAPER_PLAN_CODE}


class RazorpayCheckoutError(RuntimeError):
    """Base class for safe checkout creation errors."""


class RazorpayProviderConfigError(RazorpayCheckoutError):
    """Raised when provider credentials are not configured."""


class RazorpayPlanConfigError(RazorpayCheckoutError):
    """Raised when the requested plan has no configured Razorpay plan id."""


class RazorpaySubscriptionCreateError(RazorpayCheckoutError):
    """Raised when Razorpay subscription creation fails."""


@dataclass(frozen=True)
class RazorpayPlan:
    plan_code: str
    plan_id: str


@dataclass(frozen=True)
class RazorpayCheckoutResult:
    ok: bool
    provider: str
    checkout_url: str | None
    short_url: str | None
    subscription_id: str
    plan_code: str
    status: str
    message: str

    def as_response(self) -> dict[str, object | None]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "checkout_url": self.checkout_url,
            "short_url": self.short_url,
            "subscription_id": self.subscription_id,
            "plan_code": self.plan_code,
            "status": self.status,
            "message": self.message,
        }


def resolve_razorpay_plan(plan_code: str) -> RazorpayPlan:
    normalized = (plan_code or "").strip().lower()
    if normalized not in VALID_PLAN_CODES:
        raise RazorpayPlanConfigError("Payment plan is not configured.")

    plan_id = (
        _configured_paper_premium_plan_id()
        if normalized == PAPER_PLAN_CODE
        else _configured_premium_plan_id()
    )
    if not plan_id:
        raise RazorpayPlanConfigError("Payment plan is not configured.")
    return RazorpayPlan(plan_code=normalized, plan_id=plan_id)


def _configured_premium_plan_id() -> str:
    return _safe_string(settings.RAZORPAY_PLAN_PREMIUM_MONTHLY)


def _configured_paper_premium_plan_id() -> str:
    return _safe_string(settings.RAZORPAY_PLAN_PAPER_PREMIUM)


def _create_paper_premium_payment_link(
    *,
    user: CurrentUser,
    plan: RazorpayPlan,
    key_id: str,
    key_secret: str,
) -> RazorpayCheckoutResult:
    """Create a one-time Razorpay Payment Link for Paper Premium.

    Paper Premium is a single ₹100 purchase that unlocks Paper mode
    permanently -- it is not recurring, and the UI says so. It was previously
    modelled as a Subscription on a plan with a ~100-year billing interval and
    total_count=1, purely to obtain a hosted checkout URL. A Payment Link is
    the API that actually matches this product, and it returns the same
    short_url the caller already redirects to.

    The webhook side needs no change: it activates Paper Premium off
    payment.captured / order.paid (which is all Razorpay ever emitted for this
    flow anyway), resolves the owner from the nova_user_id note, and resolves
    plan features from a plan id -- so razorpay_plan_id is carried in notes,
    since a payment link has no plan_id field of its own.

    Price comes from the configured Razorpay plan rather than a constant here,
    keeping Razorpay the single source of truth for what the product costs.
    """
    with httpx.Client(timeout=10.0, auth=(key_id, key_secret)) as client:
        plan_response = client.get(f"{RAZORPAY_PLANS_URL}/{plan.plan_id}")
        if plan_response.status_code >= 400:
            logger.error(
                "Razorpay plan lookup failed for payment link: HTTP %s plan_id=%s body=%s",
                plan_response.status_code,
                plan.plan_id,
                plan_response.text[:500],
            )
            raise RazorpaySubscriptionCreateError("Payment subscription could not be created.")
        item = (plan_response.json() or {}).get("item") or {}
        amount = item.get("amount")
        currency = _safe_string(item.get("currency")) or "INR"
        if not isinstance(amount, int) or amount <= 0:
            logger.error("Razorpay plan %s has no usable amount: %r", plan.plan_id, amount)
            raise RazorpaySubscriptionCreateError("Payment subscription could not be created.")

        request_payload: dict[str, Any] = {
            "amount": amount,
            "currency": currency,
            "accept_partial": False,
            "description": "Nova Paper Premium (one-time)",
            "reminder_enable": False,
            # Razorpay would otherwise email/SMS the payer; the user is already
            # being redirected straight to the link from the app.
            "notify": {"sms": False, "email": False},
            "notes": {
                "nova_user_id": user.id_str,
                "nova_plan_code": plan.plan_code,
                # Not decorative: the webhook derives entitlement features from
                # a Razorpay plan id, and a payment link carries none.
                "razorpay_plan_id": plan.plan_id,
            },
        }
        response = client.post(RAZORPAY_PAYMENT_LINKS_URL, json=request_payload)
        if response.status_code >= 400:
            logger.error(
                "Razorpay payment link create failed: HTTP %s plan_code=%s amount=%s body=%s",
                response.status_code,
                plan.plan_code,
                amount,
                response.text[:500],
            )
            raise RazorpaySubscriptionCreateError("Payment subscription could not be created.")
        provider_response = response.json()

    if not isinstance(provider_response, dict):
        logger.error("Razorpay payment link create returned a non-object response.")
        raise RazorpaySubscriptionCreateError("Payment subscription could not be created.")

    link_id = _safe_string(provider_response.get("id"))
    short_url = _safe_string(provider_response.get("short_url")) or None
    if not link_id or not short_url:
        logger.error("Razorpay payment link response missing id/short_url.")
        raise RazorpaySubscriptionCreateError("Payment subscription could not be created.")

    return RazorpayCheckoutResult(
        ok=True,
        provider="razorpay",
        checkout_url=short_url,
        short_url=short_url,
        # Same response field the caller already reads; for a one-time purchase
        # this carries the payment link id rather than a subscription id.
        subscription_id=link_id,
        plan_code=plan.plan_code,
        status=_safe_string(provider_response.get("status")) or "created",
        message="Razorpay payment link created. Complete checkout to activate entitlement.",
    )


def create_razorpay_subscription_checkout(
    *,
    user: CurrentUser,
    plan_code: str,
) -> RazorpayCheckoutResult:
    """Create a Razorpay checkout link without granting entitlement."""

    if settings.payment_provider_normalized != "razorpay":
        raise RazorpayProviderConfigError("Payment provider is not configured.")
    key_id = (settings.RAZORPAY_KEY_ID or "").strip()
    key_secret = (settings.RAZORPAY_KEY_SECRET or "").strip()
    if not key_id or not key_secret:
        raise RazorpayProviderConfigError("Payment provider is not configured.")

    plan = resolve_razorpay_plan(plan_code)
    if plan.plan_code == PAPER_PLAN_CODE:
        return _create_paper_premium_payment_link(
            user=user,
            plan=plan,
            key_id=key_id,
            key_secret=key_secret,
        )
    total_count = PAPER_PREMIUM_TOTAL_COUNT if plan.plan_code == PAPER_PLAN_CODE else DEFAULT_SUBSCRIPTION_TOTAL_COUNT
    request_payload = build_razorpay_subscription_payload(
        user=user,
        plan=plan,
        total_count=total_count,
    )
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                RAZORPAY_SUBSCRIPTIONS_URL,
                auth=(key_id, key_secret),
                json=request_payload,
            )
            # Razorpay explains a rejected payload in the response body (which
            # plan/field it objected to). Log it before raise_for_status turns
            # it into a bare status error -- without this the caller only ever
            # sees "could not be created" and cannot tell a malformed request
            # from a provider outage. The body describes the request, not the
            # credentials, and the payload carries no secret.
            if response.status_code >= 400:
                logger.error(
                    "Razorpay subscription create failed: HTTP %s plan_code=%s plan_id=%s total_count=%s body=%s",
                    response.status_code,
                    plan.plan_code,
                    plan.plan_id,
                    total_count,
                    response.text[:500],
                )
            response.raise_for_status()
            provider_response = response.json()
    except RazorpayCheckoutError:
        raise
    except Exception as exc:
        logger.error(
            "Razorpay subscription create errored: plan_code=%s %s: %s",
            plan.plan_code,
            type(exc).__name__,
            exc,
        )
        raise RazorpaySubscriptionCreateError("Payment subscription could not be created.") from exc

    if not isinstance(provider_response, dict):
        logger.error("Razorpay subscription create returned a non-object response.")
        raise RazorpaySubscriptionCreateError("Payment subscription could not be created.")

    subscription_id = _safe_string(provider_response.get("id"))
    if not subscription_id:
        logger.error("Razorpay subscription create response had no subscription id.")
        raise RazorpaySubscriptionCreateError("Payment subscription could not be created.")
    status = _safe_string(provider_response.get("status")) or "created"
    short_url = _safe_string(provider_response.get("short_url")) or None
    return RazorpayCheckoutResult(
        ok=True,
        provider="razorpay",
        checkout_url=short_url,
        short_url=short_url,
        subscription_id=subscription_id,
        plan_code=plan.plan_code,
        status=status,
        message="Razorpay subscription created. Complete checkout to activate entitlement.",
    )


def build_razorpay_subscription_payload(
    *,
    user: CurrentUser,
    plan: RazorpayPlan,
    total_count: int = DEFAULT_SUBSCRIPTION_TOTAL_COUNT,
) -> dict[str, Any]:
    count = max(int(total_count), 1)
    return {
        "plan_id": plan.plan_id,
        "total_count": count,
        "quantity": 1,
        "customer_notify": True,
        "notes": {
            "nova_user_id": user.id_str,
            "nova_plan_code": plan.plan_code,
        },
    }


def _safe_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
