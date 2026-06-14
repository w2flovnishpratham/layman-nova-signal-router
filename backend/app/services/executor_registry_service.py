from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.auth.db import session_scope
from app.auth.models import DhanAccount, ExecutorNode, UserExecutorAssignment, utc_now_dt
from app.config import settings


class ExecutorRegistryError(ValueError):
    status_code = 400


class ExecutorRegistryConflict(ExecutorRegistryError):
    status_code = 409


class ExecutorRegistryNotFound(ExecutorRegistryError):
    status_code = 404


@dataclass(frozen=True)
class VerifiedExecutorRoute:
    assignment: UserExecutorAssignment
    node: ExecutorNode
    account: DhanAccount


def register_executor_node(
    *,
    executor_code: str,
    provider: str,
    droplet_name: str,
    region: str,
    reserved_ip: str,
    health_url: str,
    execute_url: str,
    egress_ip_url: str,
    metadata: dict[str, Any] | None = None,
) -> ExecutorNode:
    code = executor_code.strip().upper()
    provider = provider.strip().lower()
    if provider not in {"digitalocean", "manual"}:
        raise ExecutorRegistryError("Executor provider must be digitalocean or manual.")
    _validate_public_ip(reserved_ip)
    for value in (health_url, execute_url, egress_ip_url):
        _validate_executor_url(value)
    now = utc_now_dt()
    with session_scope() as session:
        existing = session.exec(select(ExecutorNode).where(ExecutorNode.executor_code == code)).first()
        node = existing or ExecutorNode(
            executor_code=code,
            provider=provider,
            droplet_name=droplet_name.strip(),
            region=region.strip(),
            reserved_ip=reserved_ip.strip(),
            health_url=health_url.strip(),
            execute_url=execute_url.strip(),
            egress_ip_url=egress_ip_url.strip(),
            created_at=now,
        )
        node.provider = provider
        node.droplet_name = droplet_name.strip()
        node.region = region.strip()
        node.reserved_ip = reserved_ip.strip()
        node.health_url = health_url.strip()
        node.execute_url = execute_url.strip()
        node.egress_ip_url = egress_ip_url.strip()
        node.metadata_json = metadata or {}
        node.updated_at = now
        if node.status == "disabled":
            node.status = "pending"
        session.add(node)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ExecutorRegistryConflict("Executor code or reserved IP is already registered.") from exc
        session.refresh(node)
        session.expunge(node)
        return node


def list_executor_nodes() -> list[ExecutorNode]:
    with session_scope() as session:
        nodes = list(session.exec(select(ExecutorNode).order_by(ExecutorNode.executor_code)).all())
        for node in nodes:
            session.expunge(node)
        return nodes


def verify_executor_health(executor_id: int, *, client: httpx.Client | None = None) -> ExecutorNode:
    with session_scope() as session:
        node = session.get(ExecutorNode, executor_id)
        if node is None:
            raise ExecutorRegistryNotFound("Executor node not found.")
        response = (client or httpx.Client(timeout=settings.EXECUTOR_REQUEST_TIMEOUT_SECONDS)).get(node.health_url)
        payload = response.json()
        healthy = response.is_success and payload.get("status") == "ok" and payload.get("executor_code") == node.executor_code
        node.last_health_status = "ok" if healthy else "failed"
        node.last_seen_at = utc_now_dt()
        node.updated_at = utc_now_dt()
        if not healthy:
            node.status = "pending"
        session.add(node)
        session.commit()
        session.refresh(node)
        if not healthy:
            raise ExecutorRegistryConflict("Executor health verification failed.")
        session.expunge(node)
        return node


def verify_executor_egress_ip(executor_id: int, *, client: httpx.Client | None = None) -> ExecutorNode:
    with session_scope() as session:
        node = session.get(ExecutorNode, executor_id)
        if node is None:
            raise ExecutorRegistryNotFound("Executor node not found.")
        response = (client or httpx.Client(timeout=settings.EXECUTOR_REQUEST_TIMEOUT_SECONDS)).get(node.egress_ip_url)
        payload = response.json()
        egress_ip = str(payload.get("egress_ip") or "").strip()
        node.last_egress_ip = egress_ip or None
        node.last_seen_at = utc_now_dt()
        node.updated_at = utc_now_dt()
        matched = response.is_success and egress_ip == node.reserved_ip
        node.status = "active" if matched and node.last_health_status == "ok" else "pending"
        session.add(node)
        session.commit()
        session.refresh(node)
        if not matched:
            raise ExecutorRegistryConflict("Executor egress IP does not match its reserved IP.")
        session.expunge(node)
        return node


def assign_executor_to_user(user_id: str, executor_id: int) -> UserExecutorAssignment:
    with session_scope() as session:
        node = session.get(ExecutorNode, executor_id)
        account = session.exec(select(DhanAccount).where(DhanAccount.user_id == user_id)).first()
        if node is None:
            raise ExecutorRegistryNotFound("Executor node not found.")
        if node.status != "active" or node.last_egress_ip != node.reserved_ip:
            raise ExecutorRegistryConflict("Executor must pass health and egress verification before assignment.")
        if account is None or account.status != "connected" or not account.access_token_present:
            raise ExecutorRegistryConflict("The user must connect a validated Dhan account first.")
        assignment = UserExecutorAssignment(
            user_id=user_id,
            executor_node_id=int(node.id),
            broker_client_id_hash=account.dhan_client_id_hash,
            status="pending_whitelist",
        )
        session.add(assignment)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ExecutorRegistryConflict("User or executor already has an active assignment.") from exc
        session.refresh(assignment)
        session.expunge(assignment)
        return assignment


def verify_assignment(assignment_id: int) -> UserExecutorAssignment:
    with session_scope() as session:
        assignment = session.get(UserExecutorAssignment, assignment_id)
        if assignment is None:
            raise ExecutorRegistryNotFound("Executor assignment not found.")
        node = session.get(ExecutorNode, assignment.executor_node_id)
        account = session.exec(select(DhanAccount).where(DhanAccount.user_id == assignment.user_id)).first()
        if node is None or node.status != "active" or node.last_egress_ip != node.reserved_ip:
            raise ExecutorRegistryConflict("Executor egress verification is not current.")
        if account is None or account.dhan_client_id_hash != assignment.broker_client_id_hash:
            raise ExecutorRegistryConflict("Assigned broker metadata no longer matches the user.")
        assignment.status = "verified"
        assignment.verified_at = utc_now_dt()
        assignment.last_verified_egress_ip = node.last_egress_ip
        assignment.updated_at = utc_now_dt()
        session.add(assignment)
        session.commit()
        session.refresh(assignment)
        session.expunge(assignment)
        return assignment


def disable_executor(executor_id: int) -> ExecutorNode:
    with session_scope() as session:
        node = session.get(ExecutorNode, executor_id)
        if node is None:
            raise ExecutorRegistryNotFound("Executor node not found.")
        node.status = "disabled"
        node.updated_at = utc_now_dt()
        for assignment in session.exec(
            select(UserExecutorAssignment).where(
                UserExecutorAssignment.executor_node_id == executor_id,
                UserExecutorAssignment.status != "disabled",
            )
        ).all():
            assignment.status = "disabled"
            assignment.updated_at = utc_now_dt()
            session.add(assignment)
        session.add(node)
        session.commit()
        session.refresh(node)
        session.expunge(node)
        return node


def verified_route_for_user(user_id: str) -> VerifiedExecutorRoute | None:
    with session_scope() as session:
        assignment = session.exec(
            select(UserExecutorAssignment).where(
                UserExecutorAssignment.user_id == user_id,
                UserExecutorAssignment.status == "verified",
            )
        ).first()
        if assignment is None:
            return None
        node = session.get(ExecutorNode, assignment.executor_node_id)
        account = session.exec(select(DhanAccount).where(DhanAccount.user_id == user_id)).first()
        if (
            node is None
            or account is None
            or node.status != "active"
            or node.last_egress_ip != node.reserved_ip
            or assignment.last_verified_egress_ip != node.reserved_ip
            or account.dhan_client_id_hash != assignment.broker_client_id_hash
        ):
            return None
        session.expunge(assignment)
        session.expunge(node)
        session.expunge(account)
        return VerifiedExecutorRoute(assignment, node, account)


def assignment_state_for_user(user_id: str) -> tuple[UserExecutorAssignment | None, ExecutorNode | None]:
    with session_scope() as session:
        assignment = session.exec(
            select(UserExecutorAssignment)
            .where(
                UserExecutorAssignment.user_id == user_id,
                UserExecutorAssignment.status != "disabled",
            )
            .order_by(UserExecutorAssignment.created_at.desc())
        ).first()
        node = session.get(ExecutorNode, assignment.executor_node_id) if assignment else None
        if assignment:
            session.expunge(assignment)
        if node:
            session.expunge(node)
        return assignment, node


def _validate_public_ip(value: str) -> None:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise ExecutorRegistryError("reserved_ip must be a valid IP address.") from exc
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise ExecutorRegistryError("reserved_ip must be a routable executor address.")


def _validate_executor_url(value: str) -> None:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ExecutorRegistryError("Executor URLs must be absolute HTTP(S) URLs.")
    if settings.APP_ENV.lower() == "production" and parsed.scheme != "https":
        raise ExecutorRegistryError("Production executor URLs must use HTTPS.")
