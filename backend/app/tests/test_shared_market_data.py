"""Tests for the shared market-data TOTP token service.

Deterministic and offline — no network calls. TOTP is checked against the
official RFC 6238 test vectors (SHA1, 6 digits, 30s period).
"""
from __future__ import annotations

import time

from app.services.credential_vault import DhanCredentials
from app.services import shared_market_data as smd

# RFC 6238 Appendix B seed "12345678901234567890" in base32.
_RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_totp_matches_rfc6238_vectors():
    # 8-digit reference values truncated to the 6 low-order digits.
    assert smd.generate_totp(_RFC_SECRET, at=59) == "287082"
    assert smd.generate_totp(_RFC_SECRET, at=1234567890) == "005924"
    assert smd.generate_totp(_RFC_SECRET, at=2000000000) == "279037"


def test_totp_handles_spaces_and_lowercase():
    spaced = "gezd gnbv gy3t qojq gezd gnbv gy3t qojq"
    assert smd.generate_totp(spaced, at=59) == "287082"


def test_not_configured_by_default(monkeypatch):
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_DATA_ENABLED", False, raising=False)
    assert smd.shared_market_data_configured() is False
    # With the shared feed off, no shared credentials are issued.
    assert smd.get_shared_market_credentials() is None
    assert smd.market_data_is_shared() is False


def test_configured_requires_all_secrets(monkeypatch):
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_DATA_ENABLED", True, raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_CLIENT_ID", "1000000001", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_PIN", "1234", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_TOTP_SECRET", "", raising=False)
    assert smd.shared_market_data_configured() is False

    monkeypatch.setattr(smd.settings, "DHAN_SHARED_TOTP_SECRET", _RFC_SECRET, raising=False)
    assert smd.shared_market_data_configured() is True


def test_status_is_masked_and_safe(monkeypatch):
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_DATA_ENABLED", True, raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_CLIENT_ID", "1000000001", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_PIN", "1234", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_TOTP_SECRET", _RFC_SECRET, raising=False)
    status = smd.shared_market_data_status()
    assert status["configured"] is True
    assert status["enabled"] is True
    # The raw PIN / TOTP secret must never appear in status output.
    assert "1234" not in str(status)
    assert _RFC_SECRET not in str(status)


def test_market_data_credentials_falls_back_when_shared_off(monkeypatch):
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_DATA_ENABLED", False, raising=False)
    captured = {}

    def fake_get_dhan_credentials():
        captured["called"] = True
        return None

    monkeypatch.setattr(
        "app.services.credential_vault.get_dhan_credentials",
        fake_get_dhan_credentials,
        raising=False,
    )
    result = smd.market_data_credentials()
    assert result is None
    assert captured.get("called") is True


def test_market_data_credentials_uses_shared_when_configured(monkeypatch):
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_DATA_ENABLED", True, raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_CLIENT_ID", "1000000001", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_PIN", "1234", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_TOTP_SECRET", _RFC_SECRET, raising=False)
    monkeypatch.setattr(
        smd,
        "get_shared_market_credentials",
        lambda: DhanCredentials("shared-client", "shared-token", "shared_market_data"),
    )
    monkeypatch.setattr(
        "app.services.credential_vault.get_dhan_credentials",
        lambda: (_ for _ in ()).throw(AssertionError("user credentials must not be read")),
        raising=False,
    )

    result = smd.market_data_credentials()

    assert result is not None
    assert result.client_id == "shared-client"
    assert result.access_token == "shared-token"
    assert result.source == "shared_market_data"


def test_market_data_credentials_does_not_fallback_when_shared_unavailable(monkeypatch):
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_DATA_ENABLED", True, raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_CLIENT_ID", "1000000001", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_PIN", "1234", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_TOTP_SECRET", _RFC_SECRET, raising=False)
    monkeypatch.setattr(smd, "get_shared_market_credentials", lambda: None)
    monkeypatch.setattr(
        "app.services.credential_vault.get_dhan_credentials",
        lambda: (_ for _ in ()).throw(AssertionError("stale user credentials must not be used")),
        raising=False,
    )

    assert smd.market_data_credentials() is None


def test_auth_failure_invalidates_and_forces_shared_refresh(monkeypatch):
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_DATA_ENABLED", True, raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_CLIENT_ID", "1000000001", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_PIN", "1234", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_TOTP_SECRET", _RFC_SECRET, raising=False)
    monkeypatch.setitem(smd._STATE, "access_token", "old-token")
    monkeypatch.setitem(smd._STATE, "expiry_epoch", 9999999999.0)
    calls: list[bool] = []

    def fake_generate() -> bool:
        calls.append(True)
        smd._STATE["access_token"] = "new-token"
        return True

    monkeypatch.setattr(smd, "_generate_token_locked", fake_generate)

    refreshed = smd.refresh_shared_token_after_auth_failure(status_code=401, message="Unauthorized")

    assert refreshed is True
    assert calls == [True]
    assert smd._STATE["access_token"] == "new-token"


def test_auth_failure_with_no_failed_token_still_invalidates(monkeypatch):
    """Backward-compat: callers that don't pass failed_token keep the old
    unconditional-invalidate behavior (e.g. any caller not yet updated)."""
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_DATA_ENABLED", True, raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_CLIENT_ID", "1000000001", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_PIN", "1234", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_TOTP_SECRET", _RFC_SECRET, raising=False)
    monkeypatch.setitem(smd._STATE, "access_token", "old-token")
    monkeypatch.setitem(smd._STATE, "expiry_epoch", 9999999999.0)
    calls: list[bool] = []
    monkeypatch.setattr(smd, "_generate_token_locked", lambda: (calls.append(True), True)[1])

    refreshed = smd.refresh_shared_token_after_auth_failure(
        status_code=401, message="Unauthorized", failed_token=None
    )

    assert refreshed is True
    assert calls == [True]


def test_auth_failure_skips_invalidate_when_token_already_replaced(monkeypatch):
    """The core race fix: a caller reporting a failure for a token that is no
    longer the cached one (someone else already refreshed it) must not
    invalidate the fresher token or burn a generation attempt."""
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_DATA_ENABLED", True, raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_CLIENT_ID", "1000000001", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_PIN", "1234", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_TOTP_SECRET", _RFC_SECRET, raising=False)
    # Cache already holds a fresh token that differs from what this caller
    # was using when it failed (another caller already fixed it).
    monkeypatch.setitem(smd._STATE, "access_token", "already-refreshed-token")
    monkeypatch.setitem(smd._STATE, "expiry_epoch", 9999999999.0)

    def fail_if_called() -> bool:
        raise AssertionError("must not invalidate/regenerate a token that already changed")

    monkeypatch.setattr(smd, "_generate_token_locked", fail_if_called)

    refreshed = smd.refresh_shared_token_after_auth_failure(
        status_code=401, message="Unauthorized", failed_token="stale-token-caller-was-using"
    )

    assert refreshed is True
    # The already-fresh token must survive untouched.
    assert smd._STATE["access_token"] == "already-refreshed-token"


def test_auth_failure_invalidates_when_failed_token_matches_current(monkeypatch):
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_DATA_ENABLED", True, raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_CLIENT_ID", "1000000001", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_PIN", "1234", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_TOTP_SECRET", _RFC_SECRET, raising=False)
    monkeypatch.setitem(smd._STATE, "access_token", "still-the-stale-token")
    monkeypatch.setitem(smd._STATE, "expiry_epoch", 9999999999.0)
    calls: list[bool] = []

    def fake_generate() -> bool:
        calls.append(True)
        smd._STATE["access_token"] = "regenerated-token"
        return True

    monkeypatch.setattr(smd, "_generate_token_locked", fake_generate)

    refreshed = smd.refresh_shared_token_after_auth_failure(
        status_code=401, message="Unauthorized", failed_token="still-the-stale-token"
    )

    assert refreshed is True
    assert calls == [True]
    assert smd._STATE["access_token"] == "regenerated-token"


def test_token_generation_respects_cooldown_after_recent_attempt(monkeypatch):
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_DATA_ENABLED", True, raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_CLIENT_ID", "1000000001", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_PIN", "1234", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_TOTP_SECRET", _RFC_SECRET, raising=False)
    monkeypatch.setitem(smd._STATE, "access_token", None)
    monkeypatch.setitem(smd._STATE, "expiry_epoch", 0.0)
    monkeypatch.setitem(smd._STATE, "last_error", None)
    # Simulate a failed attempt a few seconds ago — well within Dhan's 2-minute
    # rate limit, so a second attempt right now must not hit the network.
    monkeypatch.setitem(smd._STATE, "last_attempt_epoch", time.time() - 5.0)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("must not call Dhan again inside the cooldown window")

    monkeypatch.setattr("httpx.Client", fail_if_called)

    assert smd._generate_token_locked() is False
    assert smd._STATE["last_error"] == "token_generation_cooldown"


def test_token_generation_proceeds_once_cooldown_has_elapsed(monkeypatch):
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_DATA_ENABLED", True, raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_CLIENT_ID", "1000000001", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_PIN", "1234", raising=False)
    monkeypatch.setattr(smd.settings, "DHAN_SHARED_TOTP_SECRET", _RFC_SECRET, raising=False)
    monkeypatch.setitem(smd._STATE, "access_token", None)
    monkeypatch.setitem(smd._STATE, "expiry_epoch", 0.0)
    monkeypatch.setitem(smd._STATE, "last_error", None)
    # Well past the cooldown (and past the module's own interval), so this
    # attempt should proceed and actually reach the (mocked) HTTP call.
    monkeypatch.setitem(smd._STATE, "last_attempt_epoch", 0.0)

    calls: list[bool] = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"accessToken": "fresh-token", "dhanClientId": "1000000001", "expiryTime": None}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            calls.append(True)
            return FakeResponse()

    monkeypatch.setattr("httpx.Client", FakeClient)

    assert smd._generate_token_locked() is True
    assert calls == [True]
    assert smd._STATE["access_token"] == "fresh-token"
