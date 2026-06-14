from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.security import auth_enabled, require_user_if_auth_enabled
from app.auth.service import admin_emails
from app.services.user_connections import (
    NoEgressNodeAvailable,
    assign_unique_egress_node,
    connection_status,
    register_egress_node,
)


router = APIRouter(dependencies=[Depends(require_user_if_auth_enabled)])


class RegisterEgressNodeRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    public_ip: str = Field(..., min_length=7, max_length=64)
    region: str = Field(..., min_length=2, max_length=32)
    provider: str = Field(default="vultr", min_length=2, max_length=40)
    internal_base_url: str | None = Field(default=None, max_length=500)
    status: str = Field(default="ready", max_length=40)


class AssignEgressRequest(BaseModel):
    egress_node_id: str | None = None


@router.get("/connection/status")
def current_connection_status(request: Request) -> dict[str, Any]:
    user = require_user_if_auth_enabled(request)
    return connection_status(user.id if user else None)


@router.post("/connection/assign")
def assign_current_user_egress(body: AssignEgressRequest, request: Request) -> dict[str, Any]:
    user = _require_authenticated_user(request)
    try:
        _assignment, node = assign_unique_egress_node(user.id, preferred_node_id=body.egress_node_id)
    except NoEgressNodeAvailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "success": True,
        "connection": connection_status(user.id),
        "message": f"Assigned execution IP {node.public_ip} to this user.",
    }


@router.post("/admin/egress-nodes")
def register_admin_egress_node(body: RegisterEgressNodeRequest, request: Request) -> dict[str, Any]:
    _require_admin_user(request)
    node = register_egress_node(
        name=body.name,
        public_ip=body.public_ip,
        region=body.region,
        provider=body.provider,
        internal_base_url=body.internal_base_url,
        status=body.status,
    )
    return {
        "success": True,
        "node": {
            "id": node.id,
            "name": node.name,
            "provider": node.provider,
            "region": node.region,
            "publicIp": node.public_ip,
            "status": node.status,
        },
    }


def _require_authenticated_user(request: Request) -> Any:
    if not auth_enabled():
        raise HTTPException(status_code=403, detail="Enable AUTH_REQUIRED before assigning user execution IPs.")
    user = require_user_if_auth_enabled(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    return user


def _require_admin_user(request: Request) -> Any:
    user = _require_authenticated_user(request)
    admins = admin_emails()
    if admins and user.email.lower() not in admins:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user
