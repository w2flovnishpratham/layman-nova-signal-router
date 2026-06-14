from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import select

from app.auth.db import session_scope
from app.auth.models import (
    ExecutorNode,
    LiveOrderJob,
    User,
    UserExecutorAssignment,
    UserLivePilotApproval,
)
from app.auth.security import current_user_from_request
from app.auth.service import admin_emails
from app.services.executor_registry_service import (
    ExecutorRegistryError,
    assign_executor_to_user,
    disable_executor,
    list_executor_nodes,
    register_executor_node,
    verify_assignment,
    verify_executor_egress_ip,
    verify_executor_health,
)
from app.services.live_pilot_service import (
    LivePilotError,
    approve_user,
    readiness_for_user,
    revoke_approval,
)


router = APIRouter()


class RegisterExecutorRequest(BaseModel):
    executor_code: str = Field(..., min_length=3, max_length=80)
    provider: Literal["digitalocean", "manual"] = "digitalocean"
    droplet_name: str = Field(..., min_length=2, max_length=120)
    region: str = Field(..., min_length=2, max_length=40)
    reserved_ip: str = Field(..., min_length=7, max_length=64)
    health_url: str = Field(..., max_length=500)
    execute_url: str = Field(..., max_length=500)
    egress_ip_url: str = Field(..., max_length=500)


class AssignExecutorRequest(BaseModel):
    user_id: str = Field(..., min_length=2, max_length=100)


class ApprovalRequest(BaseModel):
    user_id: str = Field(..., min_length=2, max_length=100)
    strategy_code: str = "SUPERTREND_FLIP"
    max_qty: int = Field(..., gt=0, le=100000)
    max_trades_per_day: int = Field(..., gt=0, le=100)
    max_daily_loss: float = Field(..., ge=0, le=10000000)
    allowed_option_side: Literal["CE", "PE", "BOTH"] = "BOTH"
    expires_at: datetime


def _require_user(request: Request) -> User:
    user = getattr(request.state, "auth_user", None) or current_user_from_request(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    return user


def _require_admin(request: Request) -> User:
    user = _require_user(request)
    if not admin_emails() or user.email.strip().lower() not in admin_emails():
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


@router.get("/me/live-readiness")
def my_live_readiness(
    strategy_code: str = "SUPERTREND_FLIP",
    user: User = Depends(_require_user),
) -> dict[str, Any]:
    return readiness_for_user(user.id, strategy_code)


@router.get("/me/live-order-jobs")
def my_live_order_jobs(user: User = Depends(_require_user)) -> dict[str, Any]:
    with session_scope() as session:
        jobs = list(
            session.exec(
                select(LiveOrderJob)
                .where(LiveOrderJob.user_id == user.id)
                .order_by(LiveOrderJob.created_at.desc())
                .limit(100)
            ).all()
        )
    return {"jobs": [_live_job_payload(job) for job in jobs]}


@router.get("/admin/executors")
def admin_executors(admin: User = Depends(_require_admin)) -> dict[str, Any]:
    del admin
    nodes = list_executor_nodes()
    with session_scope() as session:
        assignments = list(session.exec(select(UserExecutorAssignment)).all())
        users = {user.id: user for user in session.exec(select(User)).all()}
    by_node = {assignment.executor_node_id: assignment for assignment in assignments if assignment.status != "disabled"}
    return {
        "executors": [
            _executor_payload(node, by_node.get(int(node.id)), users)
            for node in nodes
        ]
    }


@router.post("/admin/executors", status_code=201)
def admin_register_executor(body: RegisterExecutorRequest, admin: User = Depends(_require_admin)) -> dict[str, Any]:
    del admin
    try:
        node = register_executor_node(**body.model_dump())
    except ExecutorRegistryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"executor": _executor_payload(node)}


@router.post("/admin/executors/{executor_id}/verify-health")
def admin_verify_health(executor_id: int, admin: User = Depends(_require_admin)) -> dict[str, Any]:
    del admin
    return {"executor": _registry_action(verify_executor_health, executor_id)}


@router.post("/admin/executors/{executor_id}/verify-egress")
def admin_verify_egress(executor_id: int, admin: User = Depends(_require_admin)) -> dict[str, Any]:
    del admin
    return {"executor": _registry_action(verify_executor_egress_ip, executor_id)}


@router.post("/admin/executors/{executor_id}/assign")
def admin_assign_executor(
    executor_id: int,
    body: AssignExecutorRequest,
    admin: User = Depends(_require_admin),
) -> dict[str, Any]:
    del admin
    try:
        assignment = assign_executor_to_user(body.user_id, executor_id)
    except ExecutorRegistryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"assignment": _assignment_payload(assignment)}


@router.post("/admin/executor-assignments/{assignment_id}/verify")
def admin_verify_assignment(assignment_id: int, admin: User = Depends(_require_admin)) -> dict[str, Any]:
    del admin
    try:
        assignment = verify_assignment(assignment_id)
    except ExecutorRegistryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"assignment": _assignment_payload(assignment)}


@router.post("/admin/executors/{executor_id}/disable")
def admin_disable_executor(executor_id: int, admin: User = Depends(_require_admin)) -> dict[str, Any]:
    del admin
    return {"executor": _registry_action(disable_executor, executor_id)}


@router.get("/admin/live-pilot/users")
def admin_pilot_users(admin: User = Depends(_require_admin)) -> dict[str, Any]:
    del admin
    with session_scope() as session:
        users = list(session.exec(select(User).where(User.status == "active").order_by(User.email)).all())
        approvals = list(session.exec(select(UserLivePilotApproval)).all())
    return {
        "users": [{"id": user.id, "email": user.email, "name": user.name} for user in users],
        "approvals": [_approval_payload(approval) for approval in approvals],
    }


@router.post("/admin/live-pilot/approvals", status_code=201)
def admin_approve_user(body: ApprovalRequest, admin: User = Depends(_require_admin)) -> dict[str, Any]:
    try:
        approval = approve_user(admin_user_id=admin.id, **body.model_dump())
    except LivePilotError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"approval": _approval_payload(approval)}


@router.post("/admin/live-pilot/approvals/{approval_id}/revoke")
def admin_revoke_user(approval_id: int, admin: User = Depends(_require_admin)) -> dict[str, Any]:
    del admin
    try:
        approval = revoke_approval(approval_id)
    except LivePilotError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"approval": _approval_payload(approval)}


def _registry_action(operation, executor_id):
    try:
        return _executor_payload(operation(executor_id))
    except (ExecutorRegistryError, httpx.HTTPError, ValueError) as exc:
        status = getattr(exc, "status_code", 502)
        raise HTTPException(status_code=status, detail=str(exc)) from exc


def _executor_payload(node: ExecutorNode, assignment=None, users=None):
    user = users.get(assignment.user_id) if users and assignment else None
    return {
        "id": node.id,
        "executorCode": node.executor_code,
        "provider": node.provider,
        "dropletName": node.droplet_name,
        "region": node.region,
        "reservedIp": node.reserved_ip,
        "status": node.status,
        "lastHealthStatus": node.last_health_status,
        "lastEgressIp": node.last_egress_ip,
        "lastSeenAt": node.last_seen_at.isoformat() if node.last_seen_at else None,
        "assignment": _assignment_payload(assignment, user) if assignment else None,
    }


def _assignment_payload(assignment, user=None):
    return {
        "id": assignment.id,
        "userId": assignment.user_id,
        "userEmail": user.email if user else None,
        "executorNodeId": assignment.executor_node_id,
        "status": assignment.status,
        "lastVerifiedEgressIp": assignment.last_verified_egress_ip,
        "verifiedAt": assignment.verified_at.isoformat() if assignment.verified_at else None,
    }


def _approval_payload(approval):
    return {
        "id": approval.id,
        "userId": approval.user_id,
        "strategyId": approval.strategy_id,
        "strategyVersionId": approval.strategy_version_id,
        "status": approval.status,
        "maxQty": approval.max_qty,
        "maxTradesPerDay": approval.max_trades_per_day,
        "maxDailyLoss": approval.max_daily_loss,
        "allowedOptionSide": approval.allowed_option_side,
        "expiresAt": approval.expires_at.isoformat(),
    }


def _live_job_payload(job):
    return {
        "id": job.id,
        "subscriptionId": job.subscription_id,
        "strategySignalId": job.strategy_signal_id,
        "executorNodeId": job.executor_node_id,
        "status": job.status,
        "reasonCode": job.reason_code,
        "reasonMessage": job.reason_message,
        "correlationId": job.correlation_id,
        "dhanOrderId": job.dhan_order_id,
        "dryRun": job.dry_run,
        "createdAt": job.created_at.isoformat(),
        "finishedAt": job.finished_at.isoformat() if job.finished_at else None,
    }
