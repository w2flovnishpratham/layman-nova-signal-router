from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app.auth.db import session_scope
from app.auth.models import Strategy, StrategyVersion, User, UserStrategySubscription, utc_now_dt
from app.config import settings
from app.services.strategy_catalog_service import get_active_version, get_strategy_by_code


class SubscriptionError(ValueError):
    status_code = 400


class SubscriptionNotFound(SubscriptionError):
    status_code = 404


class SubscriptionConflict(SubscriptionError):
    status_code = 409


class SubscriptionForbidden(SubscriptionError):
    status_code = 403


@dataclass(frozen=True)
class SubscriptionRisk:
    max_qty: int | None
    max_daily_loss: float | None
    max_trades_per_day: int | None
    allowed_option_side: str


def subscribe_user(
    *,
    user_id: str,
    strategy_code: str,
    mode: str,
    risk: SubscriptionRisk,
) -> UserStrategySubscription:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"paper", "live"}:
        raise SubscriptionForbidden("Unsupported strategy subscription mode.")

    with session_scope() as session:
        _lock_user(session, user_id)
        strategy = get_strategy_by_code(session, strategy_code)
        if strategy is None or not strategy.is_active:
            raise SubscriptionNotFound("Strategy not found or inactive.")
        if normalized_mode == "paper" and not strategy.is_paper_allowed:
            raise SubscriptionForbidden("This strategy is not available for Paper mode.")
        version = get_active_version(session, int(strategy.id))
        if version is None:
            raise SubscriptionConflict("Strategy has no active version.")

        existing = session.exec(
            select(UserStrategySubscription).where(
                UserStrategySubscription.user_id == user_id,
                UserStrategySubscription.strategy_id == strategy.id,
                UserStrategySubscription.strategy_version_id == version.id,
                UserStrategySubscription.mode == normalized_mode,
            )
        ).first()
        if existing is not None and existing.status == "active":
            raise SubscriptionConflict(f"This {normalized_mode} strategy is already active.")

        if normalized_mode == "paper":
            _enforce_active_limit(session, user_id)
        else:
            from app.services.live_pilot_service import evaluate_live_readiness

            candidate = UserStrategySubscription(
                user_id=user_id,
                strategy_id=int(strategy.id),
                strategy_version_id=int(version.id),
                mode="live",
                max_qty=risk.max_qty,
                max_daily_loss=risk.max_daily_loss,
                max_trades_per_day=risk.max_trades_per_day,
                allowed_option_side=risk.allowed_option_side,
            )
            readiness = evaluate_live_readiness(
                user_id=user_id,
                strategy=strategy,
                version=version,
                subscription=candidate,
            )
            if not readiness.ready:
                raise SubscriptionForbidden(readiness.reason_message or "Live pilot readiness failed.")
        now = utc_now_dt()
        subscription = existing or UserStrategySubscription(
            user_id=user_id,
            strategy_id=int(strategy.id),
            strategy_version_id=int(version.id),
            mode=normalized_mode,
            created_at=now,
        )
        subscription.status = "active"
        subscription.max_qty = risk.max_qty
        subscription.max_daily_loss = risk.max_daily_loss
        subscription.max_trades_per_day = risk.max_trades_per_day
        subscription.allowed_option_side = risk.allowed_option_side
        subscription.updated_at = now
        session.add(subscription)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise SubscriptionConflict(f"This {normalized_mode} strategy subscription already exists.") from exc
        session.refresh(subscription)
        session.expunge(subscription)
        return subscription


def list_user_subscriptions(user_id: str) -> list[tuple[UserStrategySubscription, Strategy, StrategyVersion]]:
    with session_scope() as session:
        rows = session.exec(
            select(UserStrategySubscription, Strategy, StrategyVersion)
            .join(Strategy, Strategy.id == UserStrategySubscription.strategy_id)
            .join(StrategyVersion, StrategyVersion.id == UserStrategySubscription.strategy_version_id)
            .where(
                UserStrategySubscription.user_id == user_id,
                UserStrategySubscription.status != "disabled",
            )
            .order_by(UserStrategySubscription.created_at.desc())
        ).all()
        for row in rows:
            for item in row:
                session.expunge(item)
        return list(rows)


def pause_subscription(user_id: str, subscription_id: int) -> UserStrategySubscription:
    return _set_subscription_status(user_id, subscription_id, "paused")


def resume_subscription(user_id: str, subscription_id: int) -> UserStrategySubscription:
    with session_scope() as session:
        _lock_user(session, user_id)
        subscription = _get_user_subscription(session, user_id, subscription_id)
        if subscription.status == "disabled":
            raise SubscriptionConflict("Removed subscriptions cannot be resumed.")
        if subscription.status != "active" and subscription.mode == "paper":
            _enforce_active_limit(session, user_id)
        if subscription.mode == "live":
            from app.services.live_pilot_service import evaluate_live_readiness

            strategy = session.get(Strategy, subscription.strategy_id)
            version = session.get(StrategyVersion, subscription.strategy_version_id)
            readiness = evaluate_live_readiness(
                user_id=user_id,
                strategy=strategy,
                version=version,
                subscription=subscription,
            )
            if not readiness.ready:
                raise SubscriptionForbidden(readiness.reason_message or "Live pilot readiness failed.")
        return _commit_status(session, subscription, "active")


def disable_subscription(user_id: str, subscription_id: int) -> UserStrategySubscription:
    return _set_subscription_status(user_id, subscription_id, "disabled")


def _set_subscription_status(user_id: str, subscription_id: int, status: str) -> UserStrategySubscription:
    with session_scope() as session:
        subscription = _get_user_subscription(session, user_id, subscription_id)
        if subscription.status == "disabled" and status != "disabled":
            raise SubscriptionConflict("Removed subscriptions cannot be changed.")
        return _commit_status(session, subscription, status)


def _commit_status(
    session: Session,
    subscription: UserStrategySubscription,
    status: str,
) -> UserStrategySubscription:
    subscription.status = status
    subscription.updated_at = utc_now_dt()
    session.add(subscription)
    session.commit()
    session.refresh(subscription)
    session.expunge(subscription)
    return subscription


def _get_user_subscription(
    session: Session,
    user_id: str,
    subscription_id: int,
) -> UserStrategySubscription:
    subscription = session.exec(
        select(UserStrategySubscription).where(
            UserStrategySubscription.id == subscription_id,
            UserStrategySubscription.user_id == user_id,
        )
    ).first()
    if subscription is None:
        raise SubscriptionNotFound("Strategy subscription not found.")
    return subscription


def _lock_user(session: Session, user_id: str) -> None:
    user = session.exec(select(User).where(User.id == user_id).with_for_update()).first()
    if user is None:
        raise SubscriptionNotFound("User not found.")


def _enforce_active_limit(session: Session, user_id: str) -> None:
    limit = max(int(settings.FREE_MAX_ACTIVE_PAPER_STRATEGIES), 0)
    active_count = session.exec(
        select(func.count())
        .select_from(UserStrategySubscription)
        .where(
            UserStrategySubscription.user_id == user_id,
            UserStrategySubscription.mode == "paper",
            UserStrategySubscription.status == "active",
        )
    ).one()
    if int(active_count or 0) >= limit:
        raise SubscriptionForbidden(
            f"Free plan allows {limit} active Paper strategy. Pause the current strategy before starting another."
        )
