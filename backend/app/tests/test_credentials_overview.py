# ruff: noqa: F811
"""Credentials overview: masked only, owner scoped, no secret ever serialised."""
from __future__ import annotations

import json

from app.services import credentials_overview
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def test_an_unconnected_owner_is_reported_truthfully(mu_db):
    user = make_user("cred-empty@example.com")
    result = credentials_overview.build_credentials_overview(user.id)
    assert result["broker"]["connected"] is False
    assert result["broker"]["client_id_masked"] is None
    assert result["eligibility"]["paper"] is False
    assert result["eligibility"]["live"] is False


def test_the_builder_copies_named_fields_only(mu_db, monkeypatch):
    """A secret added upstream must not flow through to the browser.

    The status projection carries no plaintext today; this test plants one
    anyway, so a future change that starts returning it cannot leak silently.
    """
    user = make_user("cred-secret@example.com")

    monkeypatch.setattr(
        credentials_overview.vault,
        "user_credential_status",
        lambda _uid: {
            "broker_name": "dhan",
            "has_dhan_client_id": True,
            "dhan_client_id_masked": "••••1097",
            "has_dhan_access_token": True,
            "has_dhan_api_secret": True,
            "has_webhook_secret": True,
            "mode": "paper",
            "dhan_token_saved_at": "2026-07-24T09:00:00+00:00",
            "connection_status": "CONNECTED",
            "last_verified_at": "2026-07-24T09:00:00+00:00",
            "last_verification_error": None,
            "last_wallet_snapshot_at": "2026-07-24T09:00:00+00:00",
            "updated_at": "2026-07-24T09:00:00+00:00",
            # Planted: must not appear in the response.
            "dhan_access_token": "PLAINTEXT-TOKEN-SHOULD-NEVER-APPEAR",
            "webhook_secret": "PLAINTEXT-WEBHOOK-SECRET",
        },
    )
    result = credentials_overview.build_credentials_overview(user.id)
    assert result["broker"]["client_id_masked"] == "••••1097"
    assert result["broker"]["connected"] is True
    blob = json.dumps(result)
    assert "PLAINTEXT-TOKEN-SHOULD-NEVER-APPEAR" not in blob
    assert "PLAINTEXT-WEBHOOK-SECRET" not in blob


def test_a_static_ip_record_never_leaks_the_proxy_url(mu_db, monkeypatch):
    user = make_user("cred-egress@example.com")
    monkeypatch.setattr(
        "app.services.strategy_fanout.get_user_egress",
        lambda _uid: {
            "public_ip": "203.0.113.9",
            "proxy_url": "http://user:password@proxy.internal:8080",
            "verified": True,
            "last_verified_at": "2026-07-24T09:00:00+00:00",
        },
    )
    result = credentials_overview.build_credentials_overview(user.id)
    assert result["static_ip"]["status"] == "verified"
    assert result["static_ip"]["ip"] == "203.0.113.9"
    assert "proxy" not in json.dumps(result["static_ip"])
    assert "password" not in json.dumps(result)


def test_live_eligibility_states_every_blocker(mu_db, monkeypatch):
    user = make_user("cred-live@example.com")
    monkeypatch.setattr(credentials_overview.settings, "ENABLE_LIVE_ORDERS", False, raising=False)
    monkeypatch.setattr(credentials_overview.settings, "DHAN_MODE", "MOCK", raising=False)

    result = credentials_overview.build_credentials_overview(user.id)
    blockers = " ".join(result["eligibility"]["live_blockers"])
    assert result["eligibility"]["live"] is False
    assert "Live orders are disabled" in blockers
    assert "not REAL" in blockers


def test_token_expiry_is_reported_as_unknown_rather_than_invented(mu_db):
    user = make_user("cred-expiry@example.com")
    broker = credentials_overview.build_credentials_overview(user.id)["broker"]
    assert broker["token_expires_at"] is None
    assert broker["expiry_known"] is False


def test_only_dhan_is_advertised(mu_db):
    user = make_user("cred-brokers@example.com")
    result = credentials_overview.build_credentials_overview(user.id)
    assert result["supported_brokers"] == ["dhan"]
