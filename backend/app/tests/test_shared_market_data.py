"""Tests for the shared market-data TOTP token service.

Deterministic and offline — no network calls. TOTP is checked against the
official RFC 6238 test vectors (SHA1, 6 digits, 30s period).
"""
from __future__ import annotations

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
