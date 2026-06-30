"""Razorpay webhook verification and entitlement application."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import crud, models
from app.db.engine import DatabaseNotConfigured, session_scope


PROVIDER = "razorpay"
ACTIVE_SUBSCRIPTION_EVENTS = {
    "subscription.activated",
    "subscription.resumed",
    "subscription.updated",
}
CHARGED_SUBSCRIPTION_EVENT = "subscription.charged"
REVOKE_EVENT_STATUS = {
    "subscription.cancelled": "cancelled",
    "subscription.paused": "past_due",
    "subscription.halted": "past_due",
    "subscription.completed": "expired",
}
REVOKE_SUBSCRIPTION_STATUS = {
    "cancelled": "cancelled",
    "paused": "past_due",
    "halted": "past_due",
    "completed": "expired",
}
USER_ID_NOTE_KEYS = ("nova_user_id", "user_id")
PLAN_ID_NOTE_KEYS = ("razorpay_plan_id", "plan_id", "nova_plan_id")


class RazorpayWebhookError(RuntimeError):
    """Base class for safe Razorpay webhook errors."""


class RazorpayWebhookRejected(RazorpayWebhookError):
    """Raised for safely rejectable webhook input/configuration errors."""


class RazorpayWebhookStorageUnavailable(RazorpayWebhookError):
    """Raised when a valid webhook cannot be durably stored."""


class RazorpayWebhookConflict(RazorpayWebhookError):
    """Raised when a duplicate provider event id has a different body hash."""


@dataclass(frozen=True)
class PlanFeatures:
    plan_id: str
    plan_code: str
    live_orders_enabled: bool = False
    static_ip_enabled: bool = False
    strategy_access_enabled: bool = False


@dataclass(frozen=True)
class WebhookDecision:
    action: str
    event_status: str
    processed: bool
    reason: str
    entitlement_status: str | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class RazorpayWebhookResult:
    ok: bool
    duplicate: bool
    processed: bool
    event_status: str
    action: str
    reason: str

    def as_response(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "duplicate": self.duplicate,
            "processed": self.processed,
            "event_status": self.event_status,
            "action": self.action,
            "reason": self.reason,
        }


def verify_razorpay_signature(
    *,
    raw_body: bytes,
    received_signature: str | None,
    webhook_secret: str,
) -> bool:
    """Verify Razorpay's raw-body HMAC-SHA256 signature."""

    signature = (received_signature or "").strip()
    secret = (webhook_secret or "").strip()
    if not signature:
        raise RazorpayWebhookRejected("Missing Razorpay signature.")
    if not secret:
        raise RazorpayWebhookRejected("Payment webhook rejected.")

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise RazorpayWebhookRejected("Invalid Razorpay signature.")
    return True


def process_razorpay_webhook(
    *,
    raw_body: bytes,
    provider_event_id: str,
    now: datetime | None = None,
) -> RazorpayWebhookResult:
    """Store a signed Razorpay webhook and apply safe entitlement changes."""

    if settings.payment_provider_normalized != PROVIDER:
        raise RazorpayWebhookRejected("Payment webhook rejected.")
    if not (settings.RAZORPAY_WEBHOOK_SECRET or "").strip():
        raise RazorpayWebhookRejected("Payment webhook rejected.")

    event_id = (provider_event_id or "").strip()
    if not event_id:
        raise RazorpayWebhookRejected("Missing Razorpay event id.")
    if len(event_id) > 255:
        raise RazorpayWebhookRejected("Payment webhook rejected.")

    payload = _decode_payload(raw_body)
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    current = _now(now)

    try:
        return _process_with_session(
            raw_body_hash=payload_hash,
            provider_event_id=event_id,
            payload=payload,
            now=current,
        )
    except IntegrityError as exc:
        return _handle_duplicate_after_race(event_id, payload_hash, exc)
    except DatabaseNotConfigured as exc:
        raise RazorpayWebhookStorageUnavailable("Payment webhook rejected.") from exc


def _process_with_session(
    *,
    raw_body_hash: str,
    provider_event_id: str,
    payload: dict[str, Any],
    now: datetime,
) -> RazorpayWebhookResult:
    with session_scope() as db:
        existing = _get_payment_event(db, provider_event_id)
        if existing is not None:
            return _duplicate_result(existing, raw_body_hash)

        event_type = _safe_string(payload.get("event"))
        if not event_type:
            raise RazorpayWebhookRejected("Payment webhook rejected.")

        user_id = _resolve_user_id(db, payload)
        plan_id = _extract_plan_id(payload)
        plan_features = _features_for_plan(plan_id)
        decision = _decide_entitlement_action(payload, event_type, plan_features, now)
        metadata = _safe_metadata(
            payload=payload,
            plan_id=plan_id,
            plan_features=plan_features,
            user_id=user_id,
            decision=decision,
        )
        if user_id is None:
            decision = WebhookDecision(
                action="stored",
                event_status="unresolved_user",
                processed=False,
                reason="unresolved_user",
            )
            metadata["action"] = decision.action
            metadata["reason"] = decision.reason
        elif plan_features is None:
            decision = WebhookDecision(
                action="stored",
                event_status="unknown_plan",
                processed=False,
                reason="unknown_plan",
            )
            metadata["action"] = decision.action
            metadata["reason"] = decision.reason

        event = models.PaymentEvent(
            user_id=user_id,
            provider=PROVIDER,
            provider_event_id=provider_event_id,
            event_type=event_type,
            event_status=decision.event_status,
            amount=_extract_amount(payload),
            currency=_extract_currency(payload),
            payload_hash=raw_body_hash,
            processed=False,
            processed_at=None,
            received_at=now,
            metadata_json=metadata,
            created_at=now,
        )
        db.add(event)
        db.flush()

        if user_id is not None and plan_features is not None and decision.action == "activate":
            _activate_entitlement(
                db,
                user_id=user_id,
                plan_features=plan_features,
                starts_at=decision.starts_at,
                expires_at=decision.expires_at,
                metadata=metadata,
                now=now,
            )
            event.processed = True
            event.processed_at = now
        elif user_id is not None and plan_features is not None and decision.action == "revoke":
            _revoke_entitlement(
                db,
                user_id=user_id,
                plan_features=plan_features,
                status=decision.entitlement_status or "revoked",
                metadata=metadata,
                now=now,
            )
            event.processed = True
            event.processed_at = now

        db.flush()
        return RazorpayWebhookResult(
            ok=True,
            duplicate=False,
            processed=event.processed,
            event_status=event.event_status,
            action=decision.action,
            reason=decision.reason,
        )


def _handle_duplicate_after_race(
    provider_event_id: str,
    raw_body_hash: str,
    exc: IntegrityError,
) -> RazorpayWebhookResult:
    try:
        with session_scope() as db:
            existing = _get_payment_event(db, provider_event_id)
            if existing is None:
                raise RazorpayWebhookStorageUnavailable("Payment webhook rejected.") from exc
            return _duplicate_result(existing, raw_body_hash)
    except DatabaseNotConfigured as db_exc:
        raise RazorpayWebhookStorageUnavailable("Payment webhook rejected.") from db_exc


def _duplicate_result(
    existing: models.PaymentEvent,
    raw_body_hash: str,
) -> RazorpayWebhookResult:
    if existing.payload_hash != raw_body_hash:
        raise RazorpayWebhookConflict("Payment webhook rejected.")
    return RazorpayWebhookResult(
        ok=True,
        duplicate=True,
        processed=bool(existing.processed),
        event_status=existing.event_status,
        action="duplicate",
        reason="duplicate_event",
    )


def _decode_payload(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RazorpayWebhookRejected("Payment webhook rejected.") from exc
    if not isinstance(payload, dict):
        raise RazorpayWebhookRejected("Payment webhook rejected.")
    return payload


def _get_payment_event(db, provider_event_id: str) -> models.PaymentEvent | None:
    return db.scalar(
        select(models.PaymentEvent).where(
            models.PaymentEvent.provider == PROVIDER,
            models.PaymentEvent.provider_event_id == provider_event_id,
        )
    )


def _resolve_user_id(db, payload: dict[str, Any]) -> uuid.UUID | None:
    for notes in _iter_notes(payload):
        for key in USER_ID_NOTE_KEYS:
            raw_user_id = notes.get(key)
            if raw_user_id in (None, ""):
                continue
            try:
                user_id = uuid.UUID(str(raw_user_id))
            except (TypeError, ValueError):
                continue
            if crud.get_user_by_id(db, user_id) is not None:
                return user_id
    return None


def _features_for_plan(plan_id: str | None) -> PlanFeatures | None:
    normalized = _safe_string(plan_id)
    if not normalized:
        return None

    premium_plan_ids = _premium_plan_ids()
    if normalized in premium_plan_ids:
        return PlanFeatures(
            plan_id=normalized,
            plan_code="razorpay_premium_monthly",
            live_orders_enabled=True,
            static_ip_enabled=True,
            strategy_access_enabled=True,
        )

    flags = {
        "live_orders_enabled": False,
        "static_ip_enabled": False,
        "strategy_access_enabled": False,
    }
    codes: list[str] = []
    configured = (
        ("razorpay_live_monthly", "RAZORPAY_PLAN_LIVE_MONTHLY", "live_orders_enabled"),
        ("razorpay_static_ip_monthly", "RAZORPAY_PLAN_STATIC_IP_MONTHLY", "static_ip_enabled"),
        ("razorpay_strategy_monthly", "RAZORPAY_PLAN_STRATEGY_MONTHLY", "strategy_access_enabled"),
    )
    for code, setting_name, flag in configured:
        configured_plan_id = _safe_string(getattr(settings, setting_name, ""))
        if configured_plan_id and configured_plan_id == normalized:
            flags[flag] = True
            codes.append(code)
    if not codes:
        return None
    return PlanFeatures(
        plan_id=normalized,
        plan_code="+".join(codes),
        live_orders_enabled=flags["live_orders_enabled"],
        static_ip_enabled=flags["static_ip_enabled"],
        strategy_access_enabled=flags["strategy_access_enabled"],
    )


def _premium_plan_ids() -> set[str]:
    premium_ids = {_safe_string(settings.RAZORPAY_PLAN_PREMIUM_MONTHLY)}
    premium_ids.discard("")
    legacy_ids = {
        _safe_string(settings.RAZORPAY_PLAN_LIVE_MONTHLY),
        _safe_string(settings.RAZORPAY_PLAN_STATIC_IP_MONTHLY),
        _safe_string(settings.RAZORPAY_PLAN_STRATEGY_MONTHLY),
    }
    legacy_ids.discard("")
    if not premium_ids and len(legacy_ids) == 1:
        premium_ids.update(legacy_ids)
    return premium_ids


def _decide_entitlement_action(
    payload: dict[str, Any],
    event_type: str,
    plan_features: PlanFeatures | None,
    now: datetime,
) -> WebhookDecision:
    if event_type == "payment.failed":
        return WebhookDecision(
            action="stored",
            event_status="failed_payment",
            processed=False,
            reason="failed_payment",
        )

    subscription = _entity(payload, "subscription")
    subscription_status = _safe_string(subscription.get("status")).lower()
    payment_status = _safe_string(_entity(payload, "payment").get("status")).lower()

    if event_type in REVOKE_EVENT_STATUS or subscription_status in REVOKE_SUBSCRIPTION_STATUS:
        entitlement_status = REVOKE_EVENT_STATUS.get(event_type) or REVOKE_SUBSCRIPTION_STATUS.get(
            subscription_status,
            "revoked",
        )
        return WebhookDecision(
            action="revoke",
            event_status=entitlement_status,
            processed=plan_features is not None,
            reason=f"subscription_{entitlement_status}",
            entitlement_status=entitlement_status,
        )

    if event_type in ACTIVE_SUBSCRIPTION_EVENTS:
        if subscription_status != "active":
            return WebhookDecision(
                action="stored",
                event_status="inactive_subscription",
                processed=False,
                reason="inactive_subscription",
            )
        return _activation_decision(subscription, now)

    if event_type == CHARGED_SUBSCRIPTION_EVENT:
        if subscription_status != "active" or payment_status != "captured":
            return WebhookDecision(
                action="stored",
                event_status="failed_payment",
                processed=False,
                reason="failed_payment",
            )
        return _activation_decision(subscription, now)

    return WebhookDecision(
        action="ignored_unknown_event",
        event_status="ignored",
        processed=False,
        reason="ignored_unknown_event",
    )


def _activation_decision(subscription: dict[str, Any], now: datetime) -> WebhookDecision:
    starts_at = _datetime_from_epoch(subscription.get("current_start")) or _datetime_from_epoch(
        subscription.get("created_at")
    ) or now
    expires_at = _datetime_from_epoch(subscription.get("current_end"))
    if expires_at is None or expires_at <= now:
        return WebhookDecision(
            action="stored",
            event_status="missing_period_end",
            processed=False,
            reason="missing_period_end",
        )
    return WebhookDecision(
        action="activate",
        event_status="active",
        processed=True,
        reason="entitlement_activated",
        starts_at=starts_at,
        expires_at=expires_at,
    )


def _activate_entitlement(
    db,
    *,
    user_id: uuid.UUID,
    plan_features: PlanFeatures,
    starts_at: datetime | None,
    expires_at: datetime | None,
    metadata: dict[str, Any],
    now: datetime,
) -> None:
    if expires_at is None:
        return
    entitlement = _latest_payment_provider_entitlement(db, user_id)
    if entitlement is None:
        entitlement = models.UserEntitlement(
            user_id=user_id,
            plan_code=plan_features.plan_code,
            status="active",
            source="payment_provider",
            starts_at=starts_at or now,
            expires_at=expires_at,
            live_orders_enabled=plan_features.live_orders_enabled,
            static_ip_enabled=plan_features.static_ip_enabled,
            strategy_access_enabled=plan_features.strategy_access_enabled,
            max_strategy_count=None,
            metadata_json=metadata,
            created_at=now,
            updated_at=now,
        )
        db.add(entitlement)
        return

    entitlement.status = "active"
    entitlement.source = "payment_provider"
    entitlement.plan_code = _merge_plan_codes(entitlement.plan_code, plan_features.plan_code)
    entitlement.starts_at = _earliest_datetime(entitlement.starts_at, starts_at or now)
    entitlement.expires_at = _latest_datetime(entitlement.expires_at, expires_at)
    entitlement.live_orders_enabled = bool(entitlement.live_orders_enabled or plan_features.live_orders_enabled)
    entitlement.static_ip_enabled = bool(entitlement.static_ip_enabled or plan_features.static_ip_enabled)
    entitlement.strategy_access_enabled = bool(
        entitlement.strategy_access_enabled or plan_features.strategy_access_enabled
    )
    entitlement.metadata_json = metadata
    entitlement.updated_at = now


def _revoke_entitlement(
    db,
    *,
    user_id: uuid.UUID,
    plan_features: PlanFeatures,
    status: str,
    metadata: dict[str, Any],
    now: datetime,
) -> None:
    entitlement = _latest_payment_provider_entitlement(db, user_id)
    if entitlement is None:
        entitlement = models.UserEntitlement(
            user_id=user_id,
            plan_code=plan_features.plan_code,
            status=status,
            source="payment_provider",
            starts_at=now,
            expires_at=now,
            live_orders_enabled=False,
            static_ip_enabled=False,
            strategy_access_enabled=False,
            max_strategy_count=None,
            metadata_json=metadata,
            created_at=now,
            updated_at=now,
        )
        db.add(entitlement)
        return

    if _has_premium_plan_code(entitlement.plan_code) and not _has_premium_plan_code(plan_features.plan_code):
        entitlement.metadata_json = metadata
        entitlement.updated_at = now
        return

    entitlement.status = status
    entitlement.plan_code = _merge_plan_codes(entitlement.plan_code, plan_features.plan_code)
    entitlement.expires_at = now
    entitlement.live_orders_enabled = False
    entitlement.static_ip_enabled = False
    entitlement.strategy_access_enabled = False
    entitlement.metadata_json = metadata
    entitlement.updated_at = now


def _latest_payment_provider_entitlement(db, user_id: uuid.UUID) -> models.UserEntitlement | None:
    return db.scalar(
        select(models.UserEntitlement)
        .where(
            models.UserEntitlement.user_id == user_id,
            models.UserEntitlement.source == "payment_provider",
        )
        .order_by(
            desc(models.UserEntitlement.updated_at),
            desc(models.UserEntitlement.created_at),
        )
        .limit(1)
    )


def _safe_metadata(
    *,
    payload: dict[str, Any],
    plan_id: str | None,
    plan_features: PlanFeatures | None,
    user_id: uuid.UUID | None,
    decision: WebhookDecision,
) -> dict[str, Any]:
    subscription = _entity(payload, "subscription")
    payment = _entity(payload, "payment")
    metadata = {
        "razorpay_subscription_id": _safe_string(subscription.get("id")),
        "razorpay_payment_id": _safe_string(payment.get("id")),
        "razorpay_plan_id": _safe_string(plan_id),
        "subscription_status": _safe_string(subscription.get("status")).lower(),
        "plan_code": plan_features.plan_code if plan_features else None,
        "resolved_user_id": str(user_id) if user_id else None,
        "action": decision.action,
        "reason": decision.reason,
    }
    return {key: value for key, value in metadata.items() if value not in (None, "")}


def _extract_amount(payload: dict[str, Any]) -> int | None:
    for entity_name in ("payment", "invoice", "subscription"):
        value = _entity(payload, entity_name).get("amount")
        if value is None and entity_name == "payment":
            value = _entity(payload, entity_name).get("amount_paid")
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _extract_currency(payload: dict[str, Any]) -> str | None:
    for entity_name in ("payment", "invoice", "subscription"):
        currency = _safe_string(_entity(payload, entity_name).get("currency")).upper()
        if currency:
            return currency[:12]
    return None


def _extract_plan_id(payload: dict[str, Any]) -> str | None:
    for entity_name in ("subscription", "payment", "invoice"):
        entity = _entity(payload, entity_name)
        plan_id = _safe_string(entity.get("plan_id"))
        if plan_id:
            return plan_id
    for notes in _iter_notes(payload):
        for key in PLAN_ID_NOTE_KEYS:
            plan_id = _safe_string(notes.get(key))
            if plan_id:
                return plan_id
    return None


def _entity(payload: dict[str, Any], name: str) -> dict[str, Any]:
    payload_section = payload.get("payload")
    if not isinstance(payload_section, dict):
        return {}
    wrapper = payload_section.get(name)
    if not isinstance(wrapper, dict):
        return {}
    entity = wrapper.get("entity")
    return entity if isinstance(entity, dict) else {}


def _iter_notes(payload: dict[str, Any]):
    for entity_name in ("subscription", "payment", "invoice"):
        notes = _entity(payload, entity_name).get("notes")
        if isinstance(notes, dict):
            yield notes


def _datetime_from_epoch(value: Any) -> datetime | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    if timestamp > 10_000_000_000:
        timestamp = timestamp // 1000
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _now(now: datetime | None) -> datetime:
    value = now or models.utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _merge_plan_codes(existing: str | None, incoming: str) -> str:
    codes = {
        code
        for source in (existing or "", incoming)
        for code in source.split("+")
        if code
    }
    if "razorpay_premium_monthly" in codes:
        return "razorpay_premium_monthly"
    return "+".join(sorted(codes))


def _has_premium_plan_code(value: str | None) -> bool:
    return "razorpay_premium_monthly" in {
        code
        for code in (value or "").split("+")
        if code
    }


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _latest_datetime(left: datetime | None, right: datetime) -> datetime:
    left_utc = _as_utc(left)
    right_utc = _as_utc(right) or right
    if left_utc is None or right_utc > left_utc:
        return right_utc
    return left_utc


def _earliest_datetime(left: datetime | None, right: datetime) -> datetime:
    left_utc = _as_utc(left)
    right_utc = _as_utc(right) or right
    if left_utc is None or right_utc < left_utc:
        return right_utc
    return left_utc
