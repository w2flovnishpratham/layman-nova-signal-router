from __future__ import annotations

import logging


def test_shared_market_data_status_does_not_leak_token_generation_inputs(monkeypatch, caplog):
    from app.services import shared_market_data as smd

    monkeypatch.setattr(smd.settings, "DHAN_SHARED_DATA_ENABLED", True, raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_CLIENT_ID", "1000000001", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_PIN", "4321", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_TOTP_SECRET", "GEZDGNBVGY3TQOJQ", raising=False)
    monkeypatch.setitem(smd._STATE, "access_token", None)
    monkeypatch.setitem(smd._STATE, "client_id", None)
    monkeypatch.setitem(smd._STATE, "expiry_epoch", 0.0)
    monkeypatch.setitem(smd._STATE, "last_error", None)

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            raise RuntimeError(
                "boom https://auth.dhan.co/app/generateAccessToken?"
                "dhanClientId=1000000001&pin=4321&totp=123456"
            )

    monkeypatch.setattr("httpx.Client", FailingClient)

    with caplog.at_level(logging.WARNING):
        assert smd.refresh_shared_token(force=True) is False

    status_text = str(smd.shared_market_data_status())
    log_text = caplog.text
    for secret in ("1000000001", "4321", "123456", "GEZDGNBVGY3TQOJQ", "generateAccessToken"):
        assert secret not in status_text
        assert secret not in log_text
    assert smd.shared_market_data_status()["last_error"] == "token_request_failed"


def test_public_vault_status_does_not_disclose_path_or_encryption_type(monkeypatch, tmp_path):
    from app.config import settings
    from app.services import credential_vault

    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "invalid-key", raising=False)
    monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", tmp_path / "credentials.enc.json")

    status = credential_vault.vault_status()
    status_text = str(status)

    assert status["ready"] is False
    assert "Fernet" not in status_text
    assert "fernet" not in status_text
    assert "TOKEN_ENCRYPTION_KEY" not in status_text
    assert str(tmp_path) not in status_text
    assert "path" not in status
