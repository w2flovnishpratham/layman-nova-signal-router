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
