"""User context + per-user runtime isolation.

A `CurrentUser` is a lightweight, DB-independent view of the logged-in user so
that auth works whether or not Neon is configured (important for local paper
mode without a database).

Runtime isolation rule (decided for backward compatibility):
  * The deterministic dev/anonymous user (AUTH_REQUIRED=false) maps to the
    EXISTING global runtime_state/ and runtime_logs/ directories, so the current
    single-user paper setup keeps working unchanged.
  * Every real logged-in user gets isolated folders:
        runtime_state/users/<user_id>/
        runtime_logs/users/<user_id>/
        exports/users/<user_id>/
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import RUNTIME_LOG_DIR, RUNTIME_STATE_DIR, settings

# Deterministic dev user. Stable UUID so dev runs/credentials are reproducible.
DEV_USER_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-0000000d0d00")
DEV_USER_ID = uuid.uuid5(DEV_USER_NAMESPACE, "nova-dev-user")
DEV_USER_EMAIL = "dev@local"


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    email: str
    name: str | None = None
    picture_url: str | None = None
    is_admin: bool = False
    is_dev: bool = False

    @property
    def id_str(self) -> str:
        return str(self.id)


def is_admin_user(email: str | None) -> bool:
    """True only if the email is in ADMIN_EMAILS. Never used to gate login."""
    if not email:
        return False
    return email.strip().lower() in settings.admin_emails


def dev_user() -> CurrentUser:
    return CurrentUser(
        id=DEV_USER_ID,
        email=DEV_USER_EMAIL,
        name="Local Dev User",
        picture_url=None,
        is_admin=is_admin_user(DEV_USER_EMAIL),
        is_dev=True,
    )


def current_user_from_model(user) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        email=user.email,
        name=user.name,
        picture_url=user.picture_url,
        is_admin=bool(user.is_admin),
        is_dev=False,
    )


# ---------------------------------------------------------------------------
# Per-user runtime paths
# ---------------------------------------------------------------------------
def _is_dev(user: CurrentUser) -> bool:
    return user.is_dev or user.id == DEV_USER_ID


def user_runtime_state_dir(user: CurrentUser) -> Path:
    if _is_dev(user):
        path = RUNTIME_STATE_DIR
    else:
        path = RUNTIME_STATE_DIR / "users" / user.id_str
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_runtime_log_dir(user: CurrentUser) -> Path:
    if _is_dev(user):
        path = RUNTIME_LOG_DIR
    else:
        path = RUNTIME_LOG_DIR / "users" / user.id_str
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_export_dir(user: CurrentUser) -> Path:
    if _is_dev(user):
        path = RUNTIME_STATE_DIR.parent / "exports"
    else:
        path = RUNTIME_STATE_DIR.parent / "exports" / "users" / user.id_str
    path.mkdir(parents=True, exist_ok=True)
    return path
