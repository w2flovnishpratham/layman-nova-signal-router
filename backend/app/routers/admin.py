# ruff: noqa: B008
"""Admin-only endpoints.

Gated by require_admin, which checks the is_admin flag derived from ADMIN_EMAILS.
This NEVER affects normal login — only access to these maintenance routes.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import require_admin
from app.config import settings
from app.db import crud, models
from app.db.engine import database_configured, session_scope
from app.services import entitlements
from app.services.user_context import CurrentUser

router = APIRouter(prefix="/api/admin", tags=["Admin"])


class PilotEntitlementGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_hours: int = Field(default=24, ge=1, le=168)
    live_orders_enabled: bool = False
    static_ip_enabled: bool = False
    strategy_access_enabled: bool = True
    max_strategy_count: int = Field(default=5, ge=1, le=20)
    reason: str = Field(min_length=3, max_length=200)


class PilotEntitlementRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=200)


def _require_database() -> None:
    if not database_configured():
        raise HTTPException(status_code=503, detail="Database unavailable.")


def _entitlement_response(row: models.UserEntitlement) -> dict:
    return {
        "id": str(row.id),
        "user_id": str(row.user_id),
        "status": row.status,
        "source": row.source,
        "plan_code": row.plan_code,
        "starts_at": row.starts_at.isoformat() if row.starts_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "evaluation": entitlements.evaluate_entitlement(row).as_safe_dict(),
    }


@router.get("/users")
def list_all_users(admin: CurrentUser = Depends(require_admin)) -> dict:
    if not database_configured():
        return {"users": [], "database_configured": False}
    with session_scope() as db:
        users = crud.list_users(db)
        return {
            "database_configured": True,
            "users": [
                {
                    "id": str(u.id),
                    "email": u.email,
                    "name": u.name,
                    "is_admin": bool(u.is_admin),
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                    "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                }
                for u in users
            ],
        }


@router.post("/users/{user_id}/pilot-entitlement")
def grant_pilot_entitlement(
    user_id: uuid.UUID,
    payload: PilotEntitlementGrant,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Grant a short-lived, auditable entitlement for a controlled pilot.

    This satisfies the normal entitlement checks; it does not bypass Dhan
    credentials, verified egress, Live acknowledgement, idempotency, risk, or
    position-safety gates.
    """

    _require_database()
    now = models.utcnow()
    with session_scope() as db:
        user = crud.get_user_by_id(db, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found.")
        row = models.UserEntitlement(
            user_id=user.id,
            plan_code="admin_pilot",
            status="trialing",
            source="trial",
            starts_at=now,
            expires_at=now + timedelta(hours=payload.duration_hours),
            live_orders_enabled=payload.live_orders_enabled,
            static_ip_enabled=payload.static_ip_enabled,
            strategy_access_enabled=payload.strategy_access_enabled,
            max_strategy_count=payload.max_strategy_count,
            metadata_json={
                "granted_by_admin_id": str(admin.id),
                "reason": payload.reason,
            },
        )
        db.add(row)
        db.flush()
        crud.add_audit_log(
            db,
            user_id=user.id,
            action="PILOT_ENTITLEMENT_GRANTED",
            metadata={
                "entitlement_id": str(row.id),
                "granted_by_admin_id": str(admin.id),
                "duration_hours": payload.duration_hours,
                "live_orders_enabled": payload.live_orders_enabled,
                "static_ip_enabled": payload.static_ip_enabled,
                "strategy_access_enabled": payload.strategy_access_enabled,
                "max_strategy_count": payload.max_strategy_count,
                "reason": payload.reason,
            },
        )
        return {"ok": True, "entitlement": _entitlement_response(row)}


@router.post("/users/{user_id}/pilot-entitlement/revoke")
def revoke_pilot_entitlement(
    user_id: uuid.UUID,
    payload: PilotEntitlementRevoke,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Revoke access immediately by making a revoked row the latest authority."""

    _require_database()
    now = models.utcnow()
    with session_scope() as db:
        user = crud.get_user_by_id(db, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found.")
        previous = entitlements.get_user_entitlement(db, user.id)
        row = models.UserEntitlement(
            user_id=user.id,
            plan_code="admin_pilot",
            status="revoked",
            source="manual_admin",
            starts_at=now,
            expires_at=now,
            live_orders_enabled=False,
            static_ip_enabled=False,
            strategy_access_enabled=False,
            max_strategy_count=None,
            metadata_json={
                "revoked_by_admin_id": str(admin.id),
                "previous_entitlement_id": str(previous.id) if previous else None,
                "reason": payload.reason,
            },
        )
        db.add(row)
        db.flush()
        crud.add_audit_log(
            db,
            user_id=user.id,
            action="PILOT_ENTITLEMENT_REVOKED",
            metadata={
                "entitlement_id": str(row.id),
                "previous_entitlement_id": str(previous.id) if previous else None,
                "revoked_by_admin_id": str(admin.id),
                "reason": payload.reason,
            },
        )
        return {"ok": True, "entitlement": _entitlement_response(row)}


@router.get("/runs")
def list_all_runs(admin: CurrentUser = Depends(require_admin), limit: int = 200) -> dict:
    if not database_configured():
        return {"runs": [], "database_configured": False}
    from sqlalchemy import select

    from app.db import models

    with session_scope() as db:
        rows = db.scalars(
            select(models.UserRun).order_by(models.UserRun.created_at.desc()).limit(limit)
        ).all()
        return {
            "database_configured": True,
            "runs": [
                {
                    "id": str(r.id),
                    "user_id": str(r.user_id),
                    "run_type": r.run_type,
                    "status": r.status,
                    "execution_mode": r.execution_mode,
                    "strategy_name": r.strategy_name,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }


@router.get("/health")
def admin_health(admin: CurrentUser = Depends(require_admin)) -> dict:
    return {
        "app_env": settings.APP_ENV,
        "auth_required": settings.AUTH_REQUIRED,
        "database_configured": database_configured(),
        "enable_live_orders": settings.ENABLE_LIVE_ORDERS,
        "webhook_trading_enabled": settings.WEBHOOK_TRADING_ENABLED,
        "dhan_mode": settings.DHAN_MODE.upper(),
        "admin_emails_count": len(settings.admin_emails),
    }
