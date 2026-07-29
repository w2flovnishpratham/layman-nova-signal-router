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


def test_client_id_is_returned_in_plaintext_for_autofill(mu_db):
    """Client ID is not a secret: unlike the access token, the owner's own
    plaintext value is returned so the Credentials page can autofill it."""
    from app.services import user_credential_vault as vault

    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id=CLIENT_ID, dhan_access_token=TOKEN)
    status = vault.user_credential_status(user.id)
    assert status["dhan_client_id"] == CLIENT_ID
    assert TOKEN not in str(status)  # the token itself never leaves the server


def test_blank_access_token_preserves_the_existing_token(mu_db):
    from app.services import user_credential_vault as vault

    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id=CLIENT_ID, dhan_access_token=TOKEN)
    # Blank/whitespace-only submissions must not clear the stored token.
    vault.save_user_credentials(user.id, dhan_client_id=CLIENT_ID, dhan_access_token="   ")
    creds = vault.get_user_dhan_credentials(user.id)
    assert creds is not None
    assert creds.access_token == TOKEN


def test_new_access_token_replaces_the_previous_one(mu_db):
    from app.services import user_credential_vault as vault

    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id=CLIENT_ID, dhan_access_token=TOKEN)
    new_token = TOKEN + "-ROTATED"
    vault.save_user_credentials(user.id, dhan_access_token=new_token)
    creds = vault.get_user_dhan_credentials(user.id)
    assert creds is not None
    assert creds.access_token == new_token
    assert creds.client_id == CLIENT_ID  # untouched field is preserved


def test_connection_status_defaults_to_not_configured(mu_db):
    from app.services import user_credential_vault as vault

    user = make_user("alice@gmail.com")
    assert vault.user_credential_status(user.id)["connection_status"] == "NOT_CONFIGURED"
    vault.save_user_credentials(user.id, dhan_client_id=CLIENT_ID, dhan_access_token=TOKEN)
    assert vault.user_credential_status(user.id)["connection_status"] == "NOT_CONFIGURED"


def test_record_verification_result_persists_status(mu_db):
    from app.services import user_credential_vault as vault

    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id=CLIENT_ID, dhan_access_token=TOKEN)
    status = vault.record_verification_result(user.id, status="CONNECTED", wallet_ok=True)
    assert status["connection_status"] == "CONNECTED"
    assert status["last_verified_at"] is not None
    assert status["last_wallet_snapshot_at"] is not None


def test_saving_credentials_resets_connection_status(mu_db):
    """A stale CONNECTED must never survive a credential swap without
    re-verification — otherwise a bad new token would still read as connected."""
    from app.services import user_credential_vault as vault

    user = make_user("alice@gmail.com")
    vault.save_user_credentials(user.id, dhan_client_id=CLIENT_ID, dhan_access_token=TOKEN)
    vault.record_verification_result(user.id, status="CONNECTED")
    assert vault.user_credential_status(user.id)["connection_status"] == "CONNECTED"

    vault.save_user_credentials(user.id, dhan_access_token=TOKEN + "-NEW")
    assert vault.user_credential_status(user.id)["connection_status"] == "NOT_CONFIGURED"


def test_another_user_cannot_read_or_update_the_connection(mu_db):
    from app.services import user_credential_vault as vault

    owner = make_user("owner@gmail.com")
    other = make_user("other@gmail.com")
    vault.save_user_credentials(owner.id, dhan_client_id=CLIENT_ID, dhan_access_token=TOKEN)

    assert vault.user_credential_status(other.id)["has_dhan_client_id"] is False
    assert vault.user_credential_status(other.id)["dhan_client_id"] is None
    assert vault.get_user_dhan_credentials(other.id) is None

    vault.delete_user_credentials(other.id)  # no-op for an owner with nothing saved
    assert vault.has_user_credentials(owner.id) is True  # owner's row untouched


def test_connection_status_classifier_maps_dhan_outcomes():
    from app.services.user_credential_vault import connection_status_from_dhan_result

    assert connection_status_from_dhan_result(success=True, status_code=200, raw_response={}) == ("CONNECTED", None)

    status, _ = connection_status_from_dhan_result(success=False, status_code=None, raw_response=None, message="timed out")
    assert status == "BROKER_UNAVAILABLE"

    status, _ = connection_status_from_dhan_result(
        success=False, status_code=401, raw_response={"errorMessage": "Invalid token or token expired"}
    )
    assert status == "TOKEN_EXPIRED"

    status, _ = connection_status_from_dhan_result(
        success=False, status_code=403, raw_response={"errorMessage": "Unauthorized IP. Static IP not whitelisted."}
    )
    assert status == "BROKER_UNAVAILABLE"

    status, _ = connection_status_from_dhan_result(
        success=False, status_code=502, raw_response={"errorMessage": "upstream failure"}
    )
    assert status == "BROKER_UNAVAILABLE"
