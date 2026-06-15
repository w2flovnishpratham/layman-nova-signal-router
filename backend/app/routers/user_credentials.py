"""Per-user credential APIs.

GET    /api/user/credentials/status  -> masked status (never secrets)
POST   /api/user/credentials         -> encrypt + upsert, return masked status
DELETE /api/user/credentials         -> delete saved credentials + stop live run

All routes require a logged-in user (or the dev user when AUTH_REQUIRED=false).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.db import crud
from app.db.engine import database_configured, session_scope
from app.services import user_credential_vault as vault
from app.services.user_context import CurrentUser

router = APIRouter(prefix="/api/user/credentials", tags=["User Credentials"])


class CredentialPayload(BaseModel):
    dhan_client_id: str | None = Field(default=None, max_length=128)
    dhan_access_token: str | None = Field(default=None, max_length=4096)
    dhan_api_secret: str | None = Field(default=None, max_length=2048)
    webhook_secret: str | None = Field(default=None, max_length=512)


def _audit(user: CurrentUser, action: str, metadata: dict | None = None) -> None:
    if not database_configured() or user.is_dev:
        return
    try:
        with session_scope() as db:
            crud.add_audit_log(db, user_id=user.id, action=action, metadata=metadata or {})
    except Exception:
        pass


@router.get("/status")
def credentials_status(user: CurrentUser = Depends(get_current_user)) -> dict:
    return vault.user_credential_status(user.id)


@router.post("")
def save_credentials(payload: CredentialPayload, user: CurrentUser = Depends(get_current_user)) -> dict:
    if not database_configured():
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured; cannot store credentials.")
    if not vault.vault_ready():
        raise HTTPException(
            status_code=503,
            detail="Credential encryption key is not configured (CREDENTIAL_ENCRYPTION_KEY).",
        )
    if not any(
        [payload.dhan_client_id, payload.dhan_access_token, payload.dhan_api_secret, payload.webhook_secret]
    ):
        raise HTTPException(status_code=400, detail="No credential fields provided.")
    try:
        status = vault.save_user_credentials(
            user.id,
            dhan_client_id=payload.dhan_client_id,
            dhan_access_token=payload.dhan_access_token,
            dhan_api_secret=payload.dhan_api_secret,
            webhook_secret=payload.webhook_secret,
        )
    except vault.UserVaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(user, "CREDENTIALS_UPDATED", {"fields": [k for k, v in payload.model_dump().items() if v]})
    return status


@router.delete("")
def delete_credentials(user: CurrentUser = Depends(get_current_user)) -> dict:
    # Stop any active live run for this user before removing the credentials.
    from app.services import live_engine

    stopped = live_engine.stop_all_user_runs(user, reason="credentials_deleted")
    vault.delete_user_credentials(user.id)
    _audit(user, "CREDENTIALS_DELETED", {"stopped_runs": stopped})
    return {"ok": True, "stopped_runs": stopped, "status": vault.user_credential_status(user.id)}
