"""Database access helpers for the multi-user layer.

All functions take an explicit SQLAlchemy Session so callers control the
transaction boundary (usually via app.db.engine.session_scope()).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.db import models


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def get_user_by_id(session, user_id: uuid.UUID | str) -> Optional[models.User]:
    if not isinstance(user_id, uuid.UUID):
        try:
            user_id = uuid.UUID(str(user_id))
        except (ValueError, TypeError):
            return None
    return session.get(models.User, user_id)


def get_user_by_email(session, email: str) -> Optional[models.User]:
    email = (email or "").strip().lower()
    if not email:
        return None
    return session.scalar(select(models.User).where(models.User.email == email))


def get_user_by_google_sub(session, google_sub: str) -> Optional[models.User]:
    if not google_sub:
        return None
    return session.scalar(select(models.User).where(models.User.google_sub == google_sub))


def upsert_google_user(
    session,
    *,
    google_sub: str,
    email: str,
    name: str | None,
    picture_url: str | None,
    is_admin: bool,
) -> models.User:
    """Create or update a user from a verified Google profile.

    is_admin is derived from ADMIN_EMAILS *after* login by the caller — it never
    gates whether the login itself is allowed.
    """
    email = (email or "").strip().lower()
    user = get_user_by_google_sub(session, google_sub) or get_user_by_email(session, email)
    now = utcnow()
    if user is None:
        user = models.User(
            google_sub=google_sub,
            email=email,
            name=name,
            picture_url=picture_url,
            is_admin=is_admin,
            created_at=now,
            updated_at=now,
            last_login_at=now,
        )
        session.add(user)
    else:
        user.google_sub = google_sub or user.google_sub
        user.email = email or user.email
        user.name = name if name is not None else user.name
        user.picture_url = picture_url if picture_url is not None else user.picture_url
        user.is_admin = is_admin
        user.updated_at = now
        user.last_login_at = now
    session.flush()
    return user


def list_users(session, limit: int = 500) -> list[models.User]:
    return list(session.scalars(select(models.User).order_by(models.User.created_at.desc()).limit(limit)))


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def create_session(
    session,
    *,
    user_id: uuid.UUID,
    ttl_seconds: int,
    metadata: dict[str, Any] | None = None,
) -> models.UserSession:
    now = utcnow()
    row = models.UserSession(
        user_id=user_id,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        session_metadata=metadata or {},
    )
    session.add(row)
    session.flush()
    return row


def get_active_session(session, session_id: uuid.UUID | str) -> Optional[models.UserSession]:
    if not isinstance(session_id, uuid.UUID):
        try:
            session_id = uuid.UUID(str(session_id))
        except (ValueError, TypeError):
            return None
    row = session.get(models.UserSession, session_id)
    if row is None:
        return None
    if row.revoked_at is not None:
        return None
    expires = row.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires is not None and expires < utcnow():
        return None
    return row


def revoke_session(session, session_id: uuid.UUID | str) -> None:
    row = session.get(models.UserSession, session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id)))
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
        session.flush()


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def create_run(
    session,
    *,
    user_id: uuid.UUID,
    run_type: str,
    strategy_name: str | None,
    execution_mode: str | None,
    mode_config: dict[str, Any] | None,
    status: str = "created",
) -> models.UserRun:
    run = models.UserRun(
        user_id=user_id,
        run_type=run_type,
        strategy_name=strategy_name,
        execution_mode=execution_mode,
        mode_config=mode_config or {},
        status=status,
        started_at=utcnow() if status == "running" else None,
    )
    session.add(run)
    session.flush()
    return run


def get_run(session, run_id: uuid.UUID | str) -> Optional[models.UserRun]:
    if not isinstance(run_id, uuid.UUID):
        try:
            run_id = uuid.UUID(str(run_id))
        except (ValueError, TypeError):
            return None
    return session.get(models.UserRun, run_id)


def get_active_run_for_user(session, user_id: uuid.UUID, run_type: str | None = None) -> Optional[models.UserRun]:
    stmt = select(models.UserRun).where(
        models.UserRun.user_id == user_id,
        models.UserRun.status == "running",
    )
    if run_type:
        stmt = stmt.where(models.UserRun.run_type == run_type)
    return session.scalar(stmt.order_by(models.UserRun.created_at.desc()))


def list_runs_for_user(session, user_id: uuid.UUID, limit: int = 100) -> list[models.UserRun]:
    return list(
        session.scalars(
            select(models.UserRun)
            .where(models.UserRun.user_id == user_id)
            .order_by(models.UserRun.created_at.desc())
            .limit(limit)
        )
    )


def update_run_status(
    session,
    run: models.UserRun,
    *,
    status: str,
    error_message: str | None = None,
) -> models.UserRun:
    run.status = status
    if status == "running" and run.started_at is None:
        run.started_at = utcnow()
    if status in {"stopped", "completed", "error"}:
        run.stopped_at = utcnow()
    if error_message is not None:
        run.error_message = error_message
    run.updated_at = utcnow()
    session.flush()
    return run


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def add_audit_log(
    session,
    *,
    user_id: uuid.UUID | None,
    action: str,
    metadata: dict[str, Any] | None = None,
) -> models.AuditLog:
    row = models.AuditLog(user_id=user_id, action=action, audit_metadata=metadata or {})
    session.add(row)
    session.flush()
    return row
