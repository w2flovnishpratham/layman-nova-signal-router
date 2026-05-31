from __future__ import annotations

import base64
import json
import math
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import RUNTIME_STATE_DIR, settings
from app.services.state_store import utc_now

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:  # pragma: no cover - exercised only when dependency is absent.
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment]


CREDENTIALS_FILE = RUNTIME_STATE_DIR / "credentials.enc.json"
_LOCK = threading.RLock()
_LOCAL_MEMORY_PAYLOAD: dict[str, Any] = {"version": 1, "dhan": None, "webhook_secret": None}
WEBHOOK_SECRET_MIN_LENGTH = 16
WEBHOOK_SECRET_MIN_ENTROPY_BITS = 60.0
WEBHOOK_SECRET_STRENGTH_MESSAGE = (
    "Webhook secret is too weak. Use at least 16 random characters with mixed letters, numbers, or symbols."
)


class VaultError(RuntimeError):
    pass


@dataclass(frozen=True)
class DhanCredentials:
    client_id: str
    access_token: str
    source: str = "vault"


def mask_secret(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if len(text) <= 8:
        return "****"
    return f"{text[:4]}{'*' * (len(text) - 8)}{text[-4:]}"


def mask_client_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if len(text) <= 4:
        return "****"
    return f"{'*' * max(len(text) - 4, 0)}{text[-4:]}"


def _encryption_key() -> str:
    return settings.TOKEN_ENCRYPTION_KEY.strip()


def _fernet() -> Any:
    key = _encryption_key()
    if not key:
        raise VaultError("TOKEN_ENCRYPTION_KEY is missing.")
    if Fernet is None:
        raise VaultError("cryptography is not installed; encrypted credential vault is unavailable.")
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, base64.binascii.Error) as exc:
        raise VaultError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key.") from exc


def vault_ready() -> bool:
    try:
        _fernet()
        return True
    except VaultError:
        return False


def vault_status() -> dict[str, Any]:
    try:
        _fernet()
        ready = True
        error = None
    except VaultError as exc:
        ready = False
        error = str(exc)
    return {
        "ready": ready,
        "local_mock_allowed": local_mock_without_key_allowed(),
        "file_exists": CREDENTIALS_FILE.exists(),
        "path": str(CREDENTIALS_FILE),
        "error": error,
    }


def local_mock_without_key_allowed() -> bool:
    return settings.APP_ENV.lower() != "production" and settings.DHAN_MODE.upper() == "MOCK"


def generate_fernet_key() -> str:
    if Fernet is None:
        raise VaultError("cryptography is not installed; cannot generate a Fernet key.")
    return Fernet.generate_key().decode("utf-8")


def _empty_payload() -> dict[str, Any]:
    return {"version": 1, "dhan": None, "webhook_secret": None}


def _memory_payload() -> dict[str, Any]:
    base = _empty_payload()
    base.update(deepcopy(_LOCAL_MEMORY_PAYLOAD))
    return base


def _read_payload() -> dict[str, Any]:
    with _LOCK:
        if local_mock_without_key_allowed() and not _encryption_key():
            return _memory_payload()
        if not CREDENTIALS_FILE.exists():
            return _empty_payload()
        try:
            envelope = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
            token = envelope.get("fernet")
            if not token:
                return _empty_payload()
            decrypted = _fernet().decrypt(str(token).encode("utf-8"))
            payload = json.loads(decrypted.decode("utf-8"))
            if not isinstance(payload, dict):
                return _empty_payload()
            base = _empty_payload()
            base.update(payload)
            return base
        except InvalidToken as exc:
            raise VaultError("Credential vault could not be decrypted with TOKEN_ENCRYPTION_KEY.") from exc
        except json.JSONDecodeError as exc:
            raise VaultError("Credential vault file is not valid JSON.") from exc


def _write_payload(payload: dict[str, Any]) -> None:
    with _LOCK:
        if local_mock_without_key_allowed() and not _encryption_key():
            _LOCAL_MEMORY_PAYLOAD.clear()
            _LOCAL_MEMORY_PAYLOAD.update(deepcopy(payload))
            _LOCAL_MEMORY_PAYLOAD["version"] = 1
            _LOCAL_MEMORY_PAYLOAD["updated_at"] = utc_now()
            return
        CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = deepcopy(payload)
        payload["version"] = 1
        payload["updated_at"] = utc_now()
        ciphertext = _fernet().encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        envelope = {
            "version": 1,
            "updated_at": payload["updated_at"],
            "fernet": ciphertext.decode("utf-8"),
        }
        tmp = CREDENTIALS_FILE.with_suffix(CREDENTIALS_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
        tmp.replace(CREDENTIALS_FILE)


def get_dhan_credentials() -> DhanCredentials | None:
    try:
        payload = _read_payload()
    except VaultError:
        payload = _empty_payload()
    dhan = payload.get("dhan")
    if isinstance(dhan, dict):
        client_id = str(dhan.get("client_id") or "").strip()
        access_token = str(dhan.get("access_token") or "").strip()
        if client_id and access_token:
            return DhanCredentials(client_id=client_id, access_token=access_token, source="vault")

    return None


def save_dhan_credentials(client_id: str, access_token: str) -> dict[str, Any]:
    client_id = client_id.strip()
    access_token = access_token.strip()
    if not client_id:
        raise VaultError("Dhan Client ID is required.")
    if not access_token:
        raise VaultError("Dhan Access Token is required.")
    payload = _read_payload()
    payload["dhan"] = {
        "client_id": client_id,
        "access_token": access_token,
        "connected_at": utc_now(),
    }
    _write_payload(payload)
    return dhan_metadata()


def clear_dhan_credentials() -> None:
    payload = _read_payload()
    payload["dhan"] = None
    _write_payload(payload)


def dhan_token_age_metadata() -> dict[str, Any]:
    """
    Returns token age metadata — never exposes the actual token.
    Dhan access tokens are treated as ~24-hour / daily tokens.
    """
    from datetime import datetime, timezone, timedelta
    from app.config import settings as _settings

    try:
        payload = _read_payload()
    except VaultError:
        payload = _empty_payload()

    dhan_entry = payload.get("dhan") or {}
    saved_at_raw = dhan_entry.get("connected_at") if isinstance(dhan_entry, dict) else None

    if not saved_at_raw:
        return {
            "token_saved_at": None,
            "token_age_minutes": None,
            "token_estimated_expiry_at": None,
            "token_expired": None,
            "token_warn": None,
            "token_max_age_hours": _settings.TOKEN_MAX_AGE_HOURS,
            "token_warn_age_hours": _settings.TOKEN_WARN_AGE_HOURS,
        }

    try:
        saved_at = datetime.fromisoformat(saved_at_raw)
        now = datetime.now(timezone.utc)
        if saved_at.tzinfo is None:
            saved_at = saved_at.replace(tzinfo=timezone.utc)
        age_minutes = int((now - saved_at).total_seconds() / 60)
        age_hours = age_minutes / 60.0
        estimated_expiry = saved_at + timedelta(hours=_settings.TOKEN_MAX_AGE_HOURS)
        return {
            "token_saved_at": saved_at.isoformat(),
            "token_age_minutes": age_minutes,
            "token_estimated_expiry_at": estimated_expiry.isoformat(),
            "token_expired": age_hours >= _settings.TOKEN_MAX_AGE_HOURS,
            "token_warn": age_hours >= _settings.TOKEN_WARN_AGE_HOURS,
            "token_max_age_hours": _settings.TOKEN_MAX_AGE_HOURS,
            "token_warn_age_hours": _settings.TOKEN_WARN_AGE_HOURS,
        }
    except (ValueError, TypeError):
        return {
            "token_saved_at": saved_at_raw,
            "token_age_minutes": None,
            "token_estimated_expiry_at": None,
            "token_expired": None,
            "token_warn": None,
            "token_max_age_hours": _settings.TOKEN_MAX_AGE_HOURS,
            "token_warn_age_hours": _settings.TOKEN_WARN_AGE_HOURS,
        }


def dhan_metadata() -> dict[str, Any]:
    creds = get_dhan_credentials()
    token_meta = dhan_token_age_metadata()
    return {
        "connected": creds is not None,
        "source": creds.source if creds else None,
        "client_id_masked": mask_client_id(creds.client_id) if creds else None,
        "access_token_present": bool(creds and creds.access_token),
        "access_token_masked": mask_secret(creds.access_token) if creds else None,
        **token_meta,
    }


def get_webhook_secret() -> str | None:
    try:
        payload = _read_payload()
    except VaultError:
        payload = _empty_payload()
    secret = payload.get("webhook_secret")
    if isinstance(secret, str) and secret.strip():
        return secret.strip()
    return None


def _webhook_secret_entropy_bits(secret: str) -> float:
    if not secret:
        return 0.0
    counts = {char: secret.count(char) for char in set(secret)}
    entropy_per_char = 0.0
    for count in counts.values():
        probability = count / len(secret)
        entropy_per_char -= probability * math.log2(probability)
    return entropy_per_char * len(secret)


def _contains_long_sequence(secret: str, *, length: int = 8) -> bool:
    normalized = "".join(char.lower() for char in secret if char.isalnum())
    if len(normalized) < length:
        return False
    sequences = ("0123456789", "abcdefghijklmnopqrstuvwxyz")
    for sequence in sequences:
        for source in (sequence, sequence[::-1]):
            for index in range(0, len(source) - length + 1):
                if source[index : index + length] in normalized:
                    return True
    return False


def webhook_secret_strength_error(webhook_secret: str | None) -> str | None:
    secret = (webhook_secret or "").strip()
    if len(secret) < WEBHOOK_SECRET_MIN_LENGTH:
        return f"Webhook secret must be at least {WEBHOOK_SECRET_MIN_LENGTH} characters."

    normalized = "".join(char.lower() for char in secret if char.isalnum())
    weak_terms = (
        "password",
        "passcode",
        "changeme",
        "default",
        "letmein",
        "qwerty",
        "admin",
        "testsecret",
        "tradingviewsecret",
    )
    if len(set(secret)) < 8 or any(term in normalized for term in weak_terms) or _contains_long_sequence(secret):
        return WEBHOOK_SECRET_STRENGTH_MESSAGE

    if _webhook_secret_entropy_bits(secret) < WEBHOOK_SECRET_MIN_ENTROPY_BITS:
        return WEBHOOK_SECRET_STRENGTH_MESSAGE

    return None


def save_webhook_secret(webhook_secret: str) -> dict[str, Any]:
    webhook_secret = webhook_secret.strip()
    strength_error = webhook_secret_strength_error(webhook_secret)
    if strength_error:
        raise VaultError(strength_error)
    payload = _read_payload()
    payload["webhook_secret"] = webhook_secret
    payload["webhook_secret_updated_at"] = utc_now()
    _write_payload(payload)
    return webhook_secret_metadata()


def webhook_secret_metadata() -> dict[str, Any]:
    secret = get_webhook_secret()
    return {
        "set": bool(secret),
        "masked": mask_secret(secret),
        "source": "vault" if _vault_has_webhook_secret() else None,
    }


def _vault_has_webhook_secret() -> bool:
    try:
        payload = _read_payload()
    except VaultError:
        return False
    secret = payload.get("webhook_secret")
    return isinstance(secret, str) and bool(secret.strip())


def clear_all_credentials() -> None:
    _write_payload(_empty_payload())


def credentials_file_path() -> Path:
    return CREDENTIALS_FILE
