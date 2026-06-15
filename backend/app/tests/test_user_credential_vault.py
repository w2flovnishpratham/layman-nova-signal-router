"""Per-user encrypted credential vault."""
from __future__ import annotations

import sqlalchemy as sa

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


TOKEN = "eyJ0eXAabcdefGHIJ1234567890_TOKEN"
CLIENT_ID = "1100123456"
WEBHOOK_SECRET = "Zx9$kLmnPq72! vWeRt8sUaB"


def test_save_returns_masked_status_only(mu_db):
    from app.services import user_credential_vault as vault

    user = make_user("alice@gmail.com")
    status = vault.save_user_credentials(
        user.id, dhan_client_id=CLIENT_ID, dhan_access_token=TOKEN, webhook_secret=WEBHOOK_SECRET
    )
    assert status["has_dhan_client_id"] is True
    assert status["has_dhan_access_token"] is True
    assert status["has_webhook_secret"] is True
    # Masked client id ends with the last 4 digits and contains no full value.
    assert status["dhan_client_id_masked"].endswith("3456")
    assert CLIENT_ID not in (status["dhan_client_id_masked"] or "")
    # No plaintext secret of any kind in the response.
    assert TOKEN not in str(status)
    assert WEBHOOK_SECRET not in str(status)


def test_database_stores_ciphertext_not_plaintext(mu_db):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import user_credential_vault as vault

    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id=CLIENT_ID, dhan_access_token=TOKEN, webhook_secret=WEBHOOK_SECRET)

    with session_scope() as db:
        row = db.scalar(sa.select(models.UserCredentialVault).where(models.UserCredentialVault.user_id == user.id))
        blob = "".join(
            filter(
                None,
                [
                    row.dhan_client_id_encrypted,
                    row.dhan_access_token_encrypted,
                    row.webhook_secret_encrypted,
                ],
            )
        )
    assert TOKEN not in blob
    assert CLIENT_ID not in blob
    assert WEBHOOK_SECRET not in blob
    assert row.dhan_access_token_encrypted.startswith("gAAAA")  # Fernet token prefix


def test_server_side_decrypt_roundtrip(mu_db):
    from app.services import user_credential_vault as vault

    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id=CLIENT_ID, dhan_access_token=TOKEN)
    creds = vault.get_user_dhan_credentials(user.id)
    assert creds is not None
    assert creds.client_id == CLIENT_ID
    assert creds.access_token == TOKEN
    assert vault.get_user_webhook_secret(user.id) is None  # not set


def test_delete_removes_credentials(mu_db):
    from app.services import user_credential_vault as vault

    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id=CLIENT_ID, dhan_access_token=TOKEN)
    assert vault.has_user_credentials(user.id) is True
    vault.delete_user_credentials(user.id)
    assert vault.has_user_credentials(user.id) is False
    assert vault.user_credential_status(user.id)["has_dhan_access_token"] is False


def test_weak_webhook_secret_rejected(mu_db):
    from app.services import user_credential_vault as vault

    user = make_user("alice@gmail.com")
    try:
        vault.save_user_credentials(user.id, webhook_secret="password123")
        assert False, "weak secret should be rejected"
    except vault.UserVaultError:
        pass
