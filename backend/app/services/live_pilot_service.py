from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import select

from app.auth.db import session_scope
from app.auth.models import (
    DhanAccount,
    Strategy,
    StrategyVersion,
    UserExecutorAssignment,
    UserLivePilotApproval,
    UserStrategySubscription,
    utc_now_dt,
)
from app.config import csv_setting, settings
from app.services.executor_registry_service import assignment_state_for_user, verified_route_for_user
from app.services.strategy_catalog_service import get_active_version, get_strategy_by_code


class LivePilotError(ValueError):
    status_code = 400


class LivePilotForbidden(LivePilotError):
    status_code = 403


class LivePilotNotFound(LivePilotError):
    status_code = 404


@dataclass(frozen=True)
class LiveReadiness:
    ready: bool
    reason_code: str | None
    reason_message: str | None
    approval: UserLivePilotApproval | None
    assignment: UserExecutorAssignment | None
    executor_id: int | None


def approve_user(
    *,
    user_id: str,
    strategy_code: str,
    admin_user_id: str,
    max_qty: int,
    max_trades_per_day: int,
    max_daily_loss: float,
    allowed_option_side: str,
    expires_at: datetime,
) -> UserLivePilotApproval:
    expires_at = _aware(expires_at)
    if expires_at <= utc_now_dt():
        raise LivePilotError("Live pilot approval must expire in the future.")
    with session_scope() as session:
        strategy = get_strategy_by_code(session, strategy_code)
        if strategy is None:
            raise LivePilotNotFound("Strategy not found.")
        version = get_active_version(session, int(strategy.id))
        if version is None:
            raise LivePilotNotFound("Strategy has no active version.")
        approval = session.exec(
            select(UserLivePilotApproval).where(
                UserLivePilotApproval.user_id == user_id,
                UserLivePilotApproval.strategy_id == strategy.id,
                UserLivePilotApproval.strategy_version_id == version.id,
            )
        ).first()
        now = utc_now_dt()
        approval = approval or UserLivePilotApproval(
            user_id=user_id,
            strategy_id=int(strategy.id),
            strategy_version_id=int(version.id),
            approved_by_admin_user_id=admin_user_id,
            max_qty=max_qty,
            max_trades_per_day=max_trades_per_day,
            max_daily_loss=max_daily_loss,
            expires_at=expires_at,
            created_at=now,
        )
        approval.approved_by_admin_user_id = admin_user_id
        approval.status = "approved"
        approval.max_qty = max_qty
        approval.max_trades_per_day = max_trades_per_day
        approval.max_daily_loss = max_daily_loss
        approval.allowed_option_side = allowed_option_side
        approval.expires_at = expires_at
        approval.updated_at = now
        session.add(approval)
        session.commit()
        session.refresh(approval)
        session.expunge(approval)
        return approval


def revoke_approval(approval_id: int) -> UserLivePilotApproval:
    with session_scope() as session:
        approval = session.get(UserLivePilotApproval, approval_id)
        if approval is None:
            raise LivePilotNotFound("Live pilot approval not found.")
        approval.status = "revoked"
        approval.updated_at = utc_now_dt()
        session.add(approval)
        session.commit()
        session.refresh(approval)
        session.expunge(approval)
        return approval


def active_approval(user_id: str, strategy_id: int, strategy_version_id: int) -> UserLivePilotApproval | None:
    with session_scope() as session:
        approval = session.exec(
            select(UserLivePilotApproval).where(
                UserLivePilotApproval.user_id == user_id,
                UserLivePilotApproval.strategy_id == strategy_id,
                UserLivePilotApproval.strategy_version_id == strategy_version_id,
                UserLivePilotApproval.status == "approved",
            )
        ).first()
        if approval is None or _aware(approval.expires_at) <= utc_now_dt():
            return None
        session.expunge(approval)
        return approval


def strategy_has_active_approval(strategy_code: str) -> bool:
    with session_scope() as session:
        strategy = get_strategy_by_code(session, strategy_code)
        if strategy is None:
            return False
        approval = session.exec(
            select(UserLivePilotApproval).where(
                UserLivePilotApproval.strategy_id == strategy.id,
                UserLivePilotApproval.status == "approved",
                UserLivePilotApproval.expires_at > utc_now_dt(),
            )
        ).first()
        return approval is not None


def validate_real_pilot_assignments(strategy_code: str) -> bool:
    with session_scope() as session:
        strategy = get_strategy_by_code(session, strategy_code)
        if strategy is None:
            return False
        approvals = list(
            session.exec(
                select(UserLivePilotApproval).where(
                    UserLivePilotApproval.strategy_id == strategy.id,
                    UserLivePilotApproval.status == "approved",
                    UserLivePilotApproval.expires_at > utc_now_dt(),
                )
            ).all()
        )
    return bool(approvals) and all(verified_route_for_user(approval.user_id) is not None for approval in approvals)


def evaluate_live_readiness(
    *,
    user_id: str,
    strategy: Strategy,
    version: StrategyVersion,
    subscription: UserStrategySubscription | None = None,
) -> LiveReadiness:
    assignment, node = assignment_state_for_user(user_id)
    approval = active_approval(user_id, int(strategy.id), int(version.id))
    if not settings.LIVE_PILOT_ENABLED:
        return _blocked("skipped_live_disabled", "Controlled live pilot mode is disabled.", approval, assignment, node)
    if strategy.strategy_code not in csv_setting(settings.LIVE_PILOT_ALLOWED_STRATEGIES) or not strategy.is_live_allowed:
        return _blocked(
            "skipped_strategy_not_live_allowed",
            "Strategy is not enabled for the controlled live pilot.",
            approval,
            assignment,
            node,
        )
    if approval is None:
        return _blocked("skipped_live_not_approved", "No current admin live-pilot approval exists.", None, assignment, node)
    if assignment is None:
        return _blocked("skipped_executor_not_assigned", "No executor is assigned to this user.", approval, None, None)
    if verified_route_for_user(user_id) is None:
        return _blocked(
            "skipped_executor_not_verified",
            "Executor assignment or reserved egress IP is not verified.",
            approval,
            assignment,
            node,
        )
    with session_scope() as session:
        account = session.exec(select(DhanAccount).where(DhanAccount.user_id == user_id)).first()
        if account is None or account.status != "connected" or not account.access_token_present:
            return _blocked(
                "skipped_broker_not_connected",
                "A validated Dhan account is required.",
                approval,
                assignment,
                node,
            )
    if subscription is not None:
        if not _risk_matches_approval(subscription, approval):
            return _blocked(
                "skipped_risk_not_set",
                "Subscription risk limits are missing or exceed the admin pilot approval.",
                approval,
                assignment,
                node,
            )
    return LiveReadiness(True, None, None, approval, assignment, int(node.id) if node else None)


def readiness_for_user(user_id: str, strategy_code: str) -> dict:
    with session_scope() as session:
        strategy = get_strategy_by_code(session, strategy_code)
        if strategy is None:
            raise LivePilotNotFound("Strategy not found.")
        version = get_active_version(session, int(strategy.id))
        if version is None:
            raise LivePilotNotFound("Strategy version not found.")
        subscription = session.exec(
            select(UserStrategySubscription).where(
                UserStrategySubscription.user_id == user_id,
                UserStrategySubscription.strategy_id == strategy.id,
                UserStrategySubscription.strategy_version_id == version.id,
                UserStrategySubscription.mode == "live",
                UserStrategySubscription.status != "disabled",
            )
        ).first()
    result = evaluate_live_readiness(
        user_id=user_id,
        strategy=strategy,
        version=version,
        subscription=subscription,
    )
    assignment, node = assignment_state_for_user(user_id)
    return {
        "strategyCode": strategy.strategy_code,
        "ready": result.ready,
        "reasonCode": result.reason_code,
        "reasonMessage": result.reason_message,
        "brokerConnected": _broker_connected(user_id),
        "riskSaved": bool(subscription and _risk_matches_approval(subscription, result.approval)),
        "executorAssigned": assignment is not None,
        "executorVerified": bool(result.executor_id),
        "executorCode": node.executor_code if node else None,
        "reservedIp": node.reserved_ip if node else None,
        "whitelistStatus": assignment.status if assignment else "not_assigned",
        "approvalActive": result.approval is not None,
        "dryRunOnly": settings.LIVE_ORDER_DRY_RUN_ONLY,
        "liveOrdersEnabled": settings.ENABLE_LIVE_ORDERS,
    }


def _risk_matches_approval(
    subscription: UserStrategySubscription,
    approval: UserLivePilotApproval | None,
) -> bool:
    if approval is None:
        return False
    if (
        subscription.max_qty is None
        or subscription.max_daily_loss is None
        or subscription.max_trades_per_day is None
        or subscription.max_qty <= 0
        or subscription.max_trades_per_day <= 0
    ):
        return False
    if subscription.max_qty > approval.max_qty:
        return False
    if subscription.max_trades_per_day > approval.max_trades_per_day:
        return False
    if subscription.max_daily_loss > approval.max_daily_loss:
        return False
    if approval.allowed_option_side != "BOTH" and subscription.allowed_option_side != approval.allowed_option_side:
        return False
    return True


def _broker_connected(user_id: str) -> bool:
    with session_scope() as session:
        account = session.exec(select(DhanAccount).where(DhanAccount.user_id == user_id)).first()
        return bool(account and account.status == "connected" and account.access_token_present)


def _blocked(code, message, approval, assignment, node) -> LiveReadiness:
    return LiveReadiness(False, code, message, approval, assignment, int(node.id) if node else None)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
