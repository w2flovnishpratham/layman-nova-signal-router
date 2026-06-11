from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.auth.db import session_scope
from app.auth.models import (
    DhanAccount,
    EgressNode,
    OrderRouteAudit,
    UserEgressAssignment,
    UserRuntimeProfile,
    utc_now_dt,
)
from app.config import settings
from app.services.credential_vault import mask_client_id
from app.services.user_context import current_user_id


class UserConnectionError(RuntimeError):
    pass


class NoEgressNodeAvailable(UserConnectionError):
    pass


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12).replace('-', '').replace('_', '')[:16]}"


def _hash_secret(value: str) -> str:
    key = settings.SESSION_TOKEN_SECRET.strip() or "change-me-in-production"
    return hmac.new(key.encode("utf-8"), value.strip().encode("utf-8"), hashlib.sha256).hexdigest()


def _hash_plain(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def active_user_id() -> str | None:
    return current_user_id()


def upsert_user_runtime_profile(user_id: str, *, webhook_secret: str | None = None) -> UserRuntimeProfile:
    with session_scope() as session:
        profile = session.get(UserRuntimeProfile, user_id)
        now = utc_now_dt()
        if profile is None:
            profile = UserRuntimeProfile(user_id=user_id, created_at=now)
            session.add(profile)
        if webhook_secret:
            profile.webhook_secret_hash = _hash_secret(webhook_secret)
            profile.webhook_secret_set_at = now
        profile.updated_at = now
        session.commit()
        session.refresh(profile)
        return profile


def find_user_id_by_webhook_secret(webhook_secret: str | None) -> str | None:
    secret = str(webhook_secret or "").strip()
    if not secret:
        return None
    secret_hash = _hash_secret(secret)
    with session_scope() as session:
        profile = session.exec(
            select(UserRuntimeProfile).where(UserRuntimeProfile.webhook_secret_hash == secret_hash)
        ).first()
        return profile.user_id if profile else None


def upsert_dhan_account_metadata(user_id: str, *, client_id: str, access_token_present: bool, validated: bool = True) -> DhanAccount:
    client_id = client_id.strip()
    if not client_id:
        raise UserConnectionError("Dhan Client ID is required.")
    with session_scope() as session:
        client_hash = _hash_plain(client_id)
        account = session.exec(select(DhanAccount).where(DhanAccount.user_id == user_id)).first()
        existing_for_client = session.exec(
            select(DhanAccount).where(DhanAccount.dhan_client_id_hash == client_hash)
        ).first()
        if existing_for_client and existing_for_client.user_id != user_id:
            raise UserConnectionError("This Dhan Client ID is already connected to another user.")
        now = utc_now_dt()
        if account is None:
            account = DhanAccount(
                id=_new_id("dhan"),
                user_id=user_id,
                dhan_client_id_hash=client_hash,
                connected_at=now,
            )
            session.add(account)
        account.dhan_client_id_hash = client_hash
        account.dhan_client_id_masked = mask_client_id(client_id)
        account.status = "connected"
        account.access_token_present = access_token_present
        account.last_validated_at = now if validated else account.last_validated_at
        account.updated_at = now
        session.commit()
        session.refresh(account)
        return account


def mark_dhan_account_disconnected(user_id: str) -> None:
    with session_scope() as session:
        account = session.exec(select(DhanAccount).where(DhanAccount.user_id == user_id)).first()
        if account is None:
            return
        account.status = "disconnected"
        account.access_token_present = False
        account.updated_at = utc_now_dt()
        session.commit()


def register_egress_node(
    *,
    name: str,
    public_ip: str,
    region: str,
    provider: str = "vultr",
    internal_base_url: str | None = None,
    status: str = "ready",
) -> EgressNode:
    with session_scope() as session:
        node = session.exec(select(EgressNode).where(EgressNode.public_ip == public_ip.strip())).first()
        now = utc_now_dt()
        if node is None:
            node = EgressNode(
                id=_new_id("egress"),
                name=name.strip(),
                provider=provider.strip().lower() or "vultr",
                region=region.strip(),
                public_ip=public_ip.strip(),
                internal_base_url=(internal_base_url or "").strip() or None,
                status=status.strip().lower() or "ready",
                created_at=now,
            )
            session.add(node)
        else:
            node.name = name.strip() or node.name
            node.provider = provider.strip().lower() or node.provider
            node.region = region.strip() or node.region
            node.internal_base_url = (internal_base_url or "").strip() or node.internal_base_url
            node.status = status.strip().lower() or node.status
        node.capacity = 1
        node.updated_at = now
        session.commit()
        session.refresh(node)
        return node


def get_active_egress_assignment(user_id: str) -> tuple[UserEgressAssignment, EgressNode] | None:
    with session_scope() as session:
        return _get_active_assignment(session, user_id)


def assign_unique_egress_node(user_id: str, *, preferred_node_id: str | None = None) -> tuple[UserEgressAssignment, EgressNode]:
    with session_scope() as session:
        existing = _get_active_assignment(session, user_id)
        if existing:
            return existing

        if preferred_node_id:
            node = session.get(EgressNode, preferred_node_id)
            if node is None:
                raise NoEgressNodeAvailable("Requested egress node does not exist.")
            if node.status != "ready":
                raise NoEgressNodeAvailable("Requested egress node is not ready.")
            if _node_has_active_assignment(session, node.id):
                raise NoEgressNodeAvailable("Requested egress node is already assigned.")
        else:
            node = _first_available_node(session)
            if node is None:
                raise NoEgressNodeAvailable("No ready unassigned egress node is available.")

        assignment = UserEgressAssignment(
            id=_new_id("assign"),
            user_id=user_id,
            egress_node_id=node.id,
            is_active=True,
            assigned_at=utc_now_dt(),
        )
        session.add(assignment)
        session.commit()
        session.refresh(assignment)
        session.refresh(node)
        return assignment, node


def release_egress_assignment(user_id: str, *, reason: str = "released") -> None:
    with session_scope() as session:
        result = _get_active_assignment(session, user_id)
        if result is None:
            return
        assignment, _node = result
        assignment.is_active = False
        assignment.released_at = utc_now_dt()
        assignment.release_reason = reason
        session.commit()


def connection_status(user_id: str | None) -> dict[str, Any]:
    if not user_id:
        return {
            "userId": None,
            "uniqueIpRequired": True,
            "egressAssigned": False,
            "egressNode": None,
            "dhanAccount": None,
        }
    with session_scope() as session:
        account = session.exec(select(DhanAccount).where(DhanAccount.user_id == user_id)).first()
        active = _get_active_assignment(session, user_id)
        assignment, node = active if active else (None, None)
        return {
            "userId": user_id,
            "uniqueIpRequired": True,
            "egressAssigned": node is not None,
            "egressNode": _public_node(node) if node else None,
            "assignment": _public_assignment(assignment) if assignment else None,
            "dhanAccount": _public_dhan_account(account) if account else None,
        }


def require_live_egress_assignment(user_id: str | None) -> dict[str, Any] | None:
    if not user_id:
        return None
    status = connection_status(user_id)
    if not status["egressAssigned"]:
        raise NoEgressNodeAvailable("Live mode requires a unique assigned execution IP before engine start.")
    return status


def record_order_route_audit(
    *,
    user_id: str,
    status: str,
    route_kind: str = "order",
    signal_id: str | None = None,
    request_id: str | None = None,
    order_id: str | None = None,
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> OrderRouteAudit:
    with session_scope() as session:
        account = session.exec(select(DhanAccount).where(DhanAccount.user_id == user_id)).first()
        active = _get_active_assignment(session, user_id)
        _assignment, node = active if active else (None, None)
        audit = OrderRouteAudit(
            id=_new_id("route"),
            user_id=user_id,
            dhan_account_id=account.id if account else None,
            egress_node_id=node.id if node else None,
            route_kind=route_kind,
            status=status,
            signal_id=signal_id,
            request_id=request_id,
            order_id=order_id,
            source_ip=node.public_ip if node else None,
            message=message,
            metadata_json=metadata or {},
            created_at=_now(),
        )
        session.add(audit)
        session.commit()
        session.refresh(audit)
        return audit


def _get_active_assignment(session: Session, user_id: str) -> tuple[UserEgressAssignment, EgressNode] | None:
    assignment = session.exec(
        select(UserEgressAssignment).where(
            UserEgressAssignment.user_id == user_id,
            UserEgressAssignment.is_active == True,  # noqa: E712
        )
    ).first()
    if assignment is None:
        return None
    node = session.get(EgressNode, assignment.egress_node_id)
    if node is None:
        return None
    return assignment, node


def _node_has_active_assignment(session: Session, node_id: str) -> bool:
    return (
        session.exec(
            select(UserEgressAssignment).where(
                UserEgressAssignment.egress_node_id == node_id,
                UserEgressAssignment.is_active == True,  # noqa: E712
            )
        ).first()
        is not None
    )


def _first_available_node(session: Session) -> EgressNode | None:
    ready_nodes = session.exec(select(EgressNode).where(EgressNode.status == "ready")).all()
    for node in ready_nodes:
        if not _node_has_active_assignment(session, node.id):
            return node
    return None


def _public_node(node: EgressNode | None) -> dict[str, Any] | None:
    if node is None:
        return None
    return {
        "id": node.id,
        "name": node.name,
        "provider": node.provider,
        "region": node.region,
        "publicIp": node.public_ip,
        "status": node.status,
    }


def _public_assignment(assignment: UserEgressAssignment | None) -> dict[str, Any] | None:
    if assignment is None:
        return None
    return {
        "id": assignment.id,
        "egressNodeId": assignment.egress_node_id,
        "isActive": assignment.is_active,
        "assignedAt": assignment.assigned_at.isoformat(),
    }


def _public_dhan_account(account: DhanAccount | None) -> dict[str, Any] | None:
    if account is None:
        return None
    return {
        "id": account.id,
        "clientIdMasked": account.dhan_client_id_masked,
        "status": account.status,
        "accessTokenPresent": account.access_token_present,
        "connectedAt": account.connected_at.isoformat(),
        "lastValidatedAt": account.last_validated_at.isoformat() if account.last_validated_at else None,
    }
