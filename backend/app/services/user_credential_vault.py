"""Per-user encrypted credential vault (Neon-backed).

Each user's Dhan client id, access token, optional API secret, and webhook
secret are encrypted with Fernet (CREDENTIAL_ENCRYPTION_KEY, falling back to the
legacy TOKEN_ENCRYPTION_KEY) BEFORE being written to the `user_credential_vaults`
table. Plaintext never touches the database and decrypted values are never
returned to the frontend — only masked status is exposed.
"""
from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import settings
from app.db import models
from app.db.engine import database_configured, session_scope
from app.services.credential_vault import (
    mask_client_id,
    mask_secret,
    webhook_secret_strength_error,
)

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:  # pragma: no cover
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment]


class UserVaultError(RuntimeError):
    pass


@dataclass(frozen=True)
class UserDhanCredentials:
    client_id: str
    access_token: str
    api_secret: str | None = None
    source: str = "user_vault"


def _encryption_key() -> str:
    return settings.credential_encryption_key


def _fernet():
    key = _encryption_key()
    if not key:
        raise UserVaultError("Credential encryption key is missing.")
    if Fernet is None:  # pragma: no cover
        raise UserVaultError("Encrypted credential vault is unavailable.")
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, base64.binascii.Error) as exc:
        raise UserVaultError("Credential encryption key is invalid.") from exc


def vault_ready() -> bool:
    try:
        _fernet()
        return True
    except UserVaultError:
        return False


def vault_status() -> dict[str, Any]:
    try:
        _fernet()
        return {"ready": True, "backend": "db", "error": None, "database_configured": database_configured()}
    except UserVaultError:
        return {
            "ready": False,
            "backend": "db",
            "error": "Credential vault is not available.",
            "database_configured": database_configured(),
        }


def _encrypt(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:  # pragma: no cover
        raise UserVaultError("Stored credential could not be decrypted with the current key.") from exc


def generate_webhook_secret() -> str:
    import secrets

    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def save_user_credentials(
    user_id: uuid.UUID,
    *,
    dhan_client_id: str | None = None,
    dhan_access_token: str | None = None,
    dhan_api_secret: str | None = None,
    webhook_secret: str | None = None,
) -> dict[str, Any]:
    """Encrypt + upsert credentials for one user. Returns masked status only.

    Only the fields provided (non-None) are updated; omitted fields are left as-is.
    """
    if not database_configured():
        raise UserVaultError("DATABASE_URL is not configured.")
    _fernet()  # fail fast if key invalid

    dhan_client_id = (dhan_client_id or "").strip() or None
    dhan_access_token = (dhan_access_token or "").strip() or None
    dhan_api_secret = (dhan_api_secret or "").strip() or None
    webhook_secret = (webhook_secret or "").strip() or None

    if webhook_secret is not None:
        err = webhook_secret_strength_error(webhook_secret)
        if err:
            raise UserVaultError(err)

    with session_scope() as db:
        vault = _get_vault(db, user_id)
        if vault is None:
            vault = models.UserCredentialVault(user_id=user_id, broker_name="dhan", mode="paper")
            db.add(vault)

        if dhan_client_id is not None:
            vault.dhan_client_id_encrypted = _encrypt(dhan_client_id)
            vault.connection_status = "NOT_CONFIGURED"
        if dhan_access_token is not None:
            vault.dhan_access_token_encrypted = _encrypt(dhan_access_token)
            vault.dhan_token_saved_at = datetime.now(timezone.utc)
            vault.connection_status = "NOT_CONFIGURED"
        if dhan_api_secret is not None:
            vault.dhan_api_secret_encrypted = _encrypt(dhan_api_secret)
        if webhook_secret is not None:
            vault.webhook_secret_encrypted = _encrypt(webhook_secret)
        vault.updated_at = datetime.now(timezone.utc)
        db.flush()
        return _status_from_vault(vault)


def _get_vault(db, user_id: uuid.UUID) -> Optional[models.UserCredentialVault]:
    from sqlalchemy import select

    return db.scalar(
        select(models.UserCredentialVault).where(models.UserCredentialVault.user_id == user_id)
    )


# ---------------------------------------------------------------------------
# Read (server-side only)
# ---------------------------------------------------------------------------
def get_user_dhan_credentials(user_id: uuid.UUID) -> Optional[UserDhanCredentials]:
    """Decrypt and return Dhan credentials. SERVER-SIDE ONLY — never serialize."""
    if not database_configured():
        return None
    with session_scope() as db:
        vault = _get_vault(db, user_id)
        if vault is None:
            return None
        client_id = _decrypt(vault.dhan_client_id_encrypted)
        access_token = _decrypt(vault.dhan_access_token_encrypted)
        api_secret = _decrypt(vault.dhan_api_secret_encrypted)
    if client_id and access_token:
        return UserDhanCredentials(
            client_id=client_id,
            access_token=access_token,
            api_secret=api_secret,
            source="user_vault",
        )
    return None


def get_user_webhook_secret(user_id: uuid.UUID) -> Optional[str]:
    """Decrypt and return the user's webhook secret. SERVER-SIDE ONLY."""
    if not database_configured():
        return None
    with session_scope() as db:
        vault = _get_vault(db, user_id)
        if vault is None:
            return None
        return _decrypt(vault.webhook_secret_encrypted)


# ---------------------------------------------------------------------------
# Status (frontend-safe, masked)
# ---------------------------------------------------------------------------
def _status_from_vault(vault: Optional[models.UserCredentialVault]) -> dict[str, Any]:
    if vault is None:
        return {
            "broker_name": "dhan",
            "has_dhan_client_id": False,
            # Client ID is not a secret, so the owner's own plaintext value is
            # returned here for autofill (unlike the access token, which never is).
            "dhan_client_id": None,
            "dhan_client_id_masked": None,
            "has_dhan_access_token": False,
            "has_dhan_api_secret": False,
            "has_webhook_secret": False,
            "mode": "paper",
            "dhan_token_saved_at": None,
            "connection_status": "NOT_CONFIGURED",
            "last_verified_at": None,
            "last_verification_error": None,
            "last_wallet_snapshot_at": None,
            "updated_at": None,
        }
    client_id_plain = _decrypt(vault.dhan_client_id_encrypted)
    return {
        "broker_name": vault.broker_name or "dhan",
        "has_dhan_client_id": bool(vault.dhan_client_id_encrypted),
        "dhan_client_id": client_id_plain,
        "dhan_client_id_masked": mask_client_id(client_id_plain),
        "has_dhan_access_token": bool(vault.dhan_access_token_encrypted),
        "has_dhan_api_secret": bool(vault.dhan_api_secret_encrypted),
        "has_webhook_secret": bool(vault.webhook_secret_encrypted),
        "mode": vault.mode or "paper",
        "dhan_token_saved_at": vault.dhan_token_saved_at.isoformat() if vault.dhan_token_saved_at else None,
        "connection_status": vault.connection_status or "NOT_CONFIGURED",
        "last_verified_at": vault.last_verified_at.isoformat() if vault.last_verified_at else None,
        "last_verification_error": vault.last_verification_error,
        "last_wallet_snapshot_at": (
            vault.last_wallet_snapshot_at.isoformat() if vault.last_wallet_snapshot_at else None
        ),
        "updated_at": vault.updated_at.isoformat() if vault.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Verification status
# ---------------------------------------------------------------------------
_ERROR_CATEGORY_TO_STATUS = {
    "TOKEN_EXPIRED": "TOKEN_EXPIRED",
    "AUTH": "INVALID_CREDENTIALS",
    "STATIC_IP": "BROKER_UNAVAILABLE",
    "RATE_LIMIT": "BROKER_UNAVAILABLE",
}


def connection_status_from_dhan_result(
    *, success: bool, status_code: int | None, raw_response: Any, message: str = ""
) -> tuple[str, str | None]:
    """Map a Dhan verification outcome to a persisted connection_status + safe message."""
    if success:
        return "CONNECTED", None
    if status_code is None:
        # No HTTP response at all: network/timeout, not a credential problem.
        return "BROKER_UNAVAILABLE", message or "Dhan did not respond."
    from app.services.dhan_error_interpreter import interpret_dhan_error

    interpreted = interpret_dhan_error(status_code, raw_response if raw_response is not None else message)
    category = str(interpreted.get("category") or "UNKNOWN")
    status = _ERROR_CATEGORY_TO_STATUS.get(category)
    if status is None:
        status = "BROKER_UNAVAILABLE" if status_code >= 500 else "ERROR"
    return status, str(interpreted.get("message") or message or None)


def record_verification_result(
    user_id: uuid.UUID, *, status: str, error: str | None = None, wallet_ok: bool = False
) -> dict[str, Any]:
    """Persist the outcome of a Dhan credential verification. Never raises."""
    if not database_configured():
        return _status_from_vault(None)
    with session_scope() as db:
        vault = _get_vault(db, user_id)
        if vault is None:
            return _status_from_vault(None)
        vault.connection_status = status
        vault.last_verified_at = datetime.now(timezone.utc)
        vault.last_verification_error = error
        if wallet_ok:
            vault.last_wallet_snapshot_at = datetime.now(timezone.utc)
        db.flush()
        return _status_from_vault(vault)


def user_credential_status(user_id: uuid.UUID) -> dict[str, Any]:
    if not database_configured():
        return _status_from_vault(None)
    with session_scope() as db:
        return _status_from_vault(_get_vault(db, user_id))


def has_user_credentials(user_id: uuid.UUID) -> bool:
    return get_user_dhan_credentials(user_id) is not None


def delete_user_credentials(user_id: uuid.UUID) -> None:
    if not database_configured():
        return
    with session_scope() as db:
        vault = _get_vault(db, user_id)
        if vault is not None:
            db.delete(vault)
