"""Admin-only endpoints.

Gated by require_admin, which checks the is_admin flag derived from ADMIN_EMAILS.
This NEVER affects normal login — only access to these maintenance routes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.dependencies import require_admin
from app.config import settings
from app.db import crud
from app.db.engine import database_configured, session_scope
from app.services.user_context import CurrentUser

router = APIRouter(prefix="/api/admin", tags=["Admin"])


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
