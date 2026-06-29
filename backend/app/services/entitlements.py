"""Server-side entitlement checks for paid/trial product access."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import desc, select

from app.db import models
from app.db.engine import database_configured, session_scope


ACTIVE_STATUSES = {"active", "trialing"}
DENY_STATUSES = {"past_due", "cancelled", "expired", "revoked"}
VALID_SOURCES = {"manual_admin", "payment_provider", "trial", "migration", "test"}


class EntitlementError(RuntimeError):
    """Raised when a server-side entitlement requirement is not met."""


@dataclass(frozen=True)
class EntitlementEvaluation:
    valid: bool
    reason: str
    status: str | None
    source: str | None
    plan_code: str | None
    live_orders_enabled: bool = False
    static_ip_enabled: bool = False
    strategy_access_enabled: bool = False
    max_strategy_count: int | None = None

    def as_safe_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "status": self.status,
            "source": self.source,
            "plan_code": self.plan_code,
            "live_orders_enabled": self.live_orders_enabled,
            "static_ip_enabled": self.static_ip_enabled,
            "strategy_access_enabled": self.strategy_access_enabled,
            "max_strategy_count": self.max_strategy_count,
        }


def _coerce_user_id(user_id: uuid.UUID | str) -> uuid.UUID:
    return user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))


def _now(now: datetime | None = None) -> datetime:
    value = now or models.utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_user_entitlement(db, user_id: uuid.UUID | str) -> models.UserEntitlement | None:
    """Return the latest server-owned entitlement row for the user."""

    user_uuid = _coerce_user_id(user_id)
    return db.scalar(
        select(models.UserEntitlement)
        .where(models.UserEntitlement.user_id == user_uuid)
        .order_by(
            desc(models.UserEntitlement.updated_at),
            desc(models.UserEntitlement.created_at),
        )
        .limit(1)
    )


def evaluate_entitlement(
    entitlement: models.UserEntitlement | None,
    now: datetime | None = None,
) -> EntitlementEvaluation:
    """Evaluate a durable entitlement without trusting client payment fields.

    A missing expiry is allowed only for an explicitly active manual_admin
    entitlement. Trials and provider-backed entitlements must expire or renew.
    """

    current = _now(now)
    if entitlement is None:
        return EntitlementEvaluation(
            valid=False,
            reason="missing_entitlement",
            status=None,
            source=None,
            plan_code=None,
        )

    status = str(entitlement.status or "").strip().lower()
    source = str(entitlement.source or "").strip().lower()
    plan_code = str(entitlement.plan_code or "").strip() or None

    if status in DENY_STATUSES or status not in ACTIVE_STATUSES:
        return EntitlementEvaluation(
            valid=False,
            reason=f"status_{status or 'unknown'}",
            status=status or None,
            source=source or None,
            plan_code=plan_code,
        )

    if source not in VALID_SOURCES:
        return EntitlementEvaluation(
            valid=False,
            reason="invalid_source",
            status=status,
            source=source or None,
            plan_code=plan_code,
        )

    starts_at = _as_utc(entitlement.starts_at)
    expires_at = _as_utc(entitlement.expires_at)
    if starts_at is not None and starts_at > current:
        return EntitlementEvaluation(
            valid=False,
            reason="not_started",
            status=status,
            source=source,
            plan_code=plan_code,
        )
    if expires_at is not None and expires_at <= current:
        return EntitlementEvaluation(
            valid=False,
            reason="expired",
            status=status,
            source=source,
            plan_code=plan_code,
        )
    if expires_at is None and not (status == "active" and source == "manual_admin"):
        return EntitlementEvaluation(
            valid=False,
            reason="missing_expiry",
            status=status,
            source=source,
            plan_code=plan_code,
        )

    return EntitlementEvaluation(
        valid=True,
        reason="valid",
        status=status,
        source=source,
        plan_code=plan_code,
        live_orders_enabled=bool(entitlement.live_orders_enabled),
        static_ip_enabled=bool(entitlement.static_ip_enabled),
        strategy_access_enabled=bool(entitlement.strategy_access_enabled),
        max_strategy_count=entitlement.max_strategy_count,
    )


def has_live_entitlement(db, user_id: uuid.UUID | str, now: datetime | None = None) -> bool:
    result = evaluate_entitlement(get_user_entitlement(db, user_id), now=now)
    return result.valid and result.live_orders_enabled


def has_static_ip_entitlement(db, user_id: uuid.UUID | str, now: datetime | None = None) -> bool:
    result = evaluate_entitlement(get_user_entitlement(db, user_id), now=now)
    return result.valid and result.static_ip_enabled


def has_strategy_entitlement(db, user_id: uuid.UUID | str, now: datetime | None = None) -> bool:
    result = evaluate_entitlement(get_user_entitlement(db, user_id), now=now)
    return result.valid and result.strategy_access_enabled


def require_live_entitlement(db, user_id: uuid.UUID | str, now: datetime | None = None) -> EntitlementEvaluation:
    result = evaluate_entitlement(get_user_entitlement(db, user_id), now=now)
    if not (result.valid and result.live_orders_enabled):
        raise EntitlementError("Live entitlement required.")
    return result


def require_static_ip_entitlement(db, user_id: uuid.UUID | str, now: datetime | None = None) -> EntitlementEvaluation:
    result = evaluate_entitlement(get_user_entitlement(db, user_id), now=now)
    if not (result.valid and result.static_ip_enabled):
        raise EntitlementError("Static IP entitlement required.")
    return result


def require_strategy_entitlement(db, user_id: uuid.UUID | str, now: datetime | None = None) -> EntitlementEvaluation:
    result = evaluate_entitlement(get_user_entitlement(db, user_id), now=now)
    if not (result.valid and result.strategy_access_enabled):
        raise EntitlementError("Strategy entitlement required.")
    return result


def has_live_entitlement_for_user(user_id: uuid.UUID | str, now: datetime | None = None) -> bool:
    if not database_configured():
        return False
    try:
        with session_scope() as db:
            return has_live_entitlement(db, user_id, now=now)
    except Exception:
        return False


def has_static_ip_entitlement_for_user(user_id: uuid.UUID | str, now: datetime | None = None) -> bool:
    if not database_configured():
        return False
    try:
        with session_scope() as db:
            return has_static_ip_entitlement(db, user_id, now=now)
    except Exception:
        return False


def has_strategy_entitlement_for_user(user_id: uuid.UUID | str, now: datetime | None = None) -> bool:
    if not database_configured():
        return False
    try:
        with session_scope() as db:
            return has_strategy_entitlement(db, user_id, now=now)
    except Exception:
        return False


def require_live_entitlement_for_user(
    user_id: uuid.UUID | str,
    now: datetime | None = None,
) -> EntitlementEvaluation:
    if not database_configured():
        raise EntitlementError("Live entitlement is required.")
    try:
        with session_scope() as db:
            return require_live_entitlement(db, user_id, now=now)
    except Exception as exc:
        raise EntitlementError("Live entitlement is required.") from exc


def require_static_ip_entitlement_for_user(
    user_id: uuid.UUID | str,
    now: datetime | None = None,
) -> EntitlementEvaluation:
    if not database_configured():
        raise EntitlementError("Static IP entitlement is required.")
    try:
        with session_scope() as db:
            return require_static_ip_entitlement(db, user_id, now=now)
    except Exception as exc:
        raise EntitlementError("Static IP entitlement is required.") from exc


def require_strategy_entitlement_for_user(
    user_id: uuid.UUID | str,
    now: datetime | None = None,
) -> EntitlementEvaluation:
    if not database_configured():
        raise EntitlementError("Strategy entitlement is required.")
    try:
        with session_scope() as db:
            return require_strategy_entitlement(db, user_id, now=now)
    except Exception as exc:
        raise EntitlementError("Strategy entitlement is required.") from exc
