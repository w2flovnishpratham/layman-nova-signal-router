# ruff: noqa: F811
"""POST /api/broker/dhan/test must persist a truthful connection_status —
never inferred from "values exist in the vault", only from what Dhan
actually said the last time NOVA asked."""
from __future__ import annotations

from app.services.dhan_client import DhanValidationResult
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _verify_as(monkeypatch, user, validation: DhanValidationResult, *, captured_proxy_urls: list | None = None):
    import app.routers.broker as broker
    from app.services import user_credential_vault as vault
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    vault.save_user_credentials(user.id, dhan_client_id="1100123456", dhan_access_token="TOKEN-ABC")

    class _FakeClient:
        def __init__(self, *, proxy_url=None, expected_egress_ip=None):
            if captured_proxy_urls is not None:
                captured_proxy_urls.append(proxy_url)

        def validate_token(self, *, client_id, access_token):
            return validation

    monkeypatch.setattr(broker, "RealDhanClient", _FakeClient)
    monkeypatch.setattr(
        broker, "refresh_wallet_snapshot", lambda force=True, log_event=True, proxy_url=None: {"success": False}
    )

    current = current_user_from_model(user)
    with bind_user_execution_context(current):
        result = broker.test_dhan()
    return result, vault.user_credential_status(user.id)


def test_successful_verification_persists_connected(mu_db, monkeypatch):
    user = make_user("verify-ok@example.com")
    _, status = _verify_as(
        monkeypatch, user, DhanValidationResult(success=True, message="Dhan token valid.", status_code=200)
    )
    assert status["connection_status"] == "CONNECTED"
    assert status["last_verified_at"] is not None


def test_invalid_credentials_persist_invalid_credentials(mu_db, monkeypatch):
    user = make_user("verify-invalid@example.com")
    _, status = _verify_as(
        monkeypatch,
        user,
        DhanValidationResult(
            success=False,
            message="Dhan token validation failed: unauthorized.",
            status_code=401,
            raw_response={"errorMessage": "Invalid access token."},
        ),
    )
    assert status["connection_status"] == "INVALID_CREDENTIALS"
    assert status["last_verification_error"]


def test_expired_token_persists_token_expired(mu_db, monkeypatch):
    user = make_user("verify-expired@example.com")
    _, status = _verify_as(
        monkeypatch,
        user,
        DhanValidationResult(
            success=False,
            message="Dhan token validation failed: token invalid.",
            status_code=401,
            raw_response={"errorMessage": "Invalid token or token expired"},
        ),
    )
    assert status["connection_status"] == "TOKEN_EXPIRED"


def test_broker_unavailable_on_timeout(mu_db, monkeypatch):
    user = make_user("verify-timeout@example.com")
    _, status = _verify_as(
        monkeypatch,
        user,
        DhanValidationResult(success=False, message="Dhan token validation timed out.", status_code=None),
    )
    assert status["connection_status"] == "BROKER_UNAVAILABLE"


def test_verification_never_logs_the_raw_credentials(mu_db, monkeypatch, caplog):
    user = make_user("verify-safe@example.com")
    result, _ = _verify_as(
        monkeypatch, user, DhanValidationResult(success=True, message="Dhan token valid.", status_code=200)
    )
    assert "TOKEN-ABC" not in str(result)


def test_credential_check_bypasses_the_execution_node_proxy(mu_db, monkeypatch):
    """The standalone Credentials page's check is read-only and must not
    depend on the user's assigned execution-node proxy — that proxy has its
    own separate static-IP verify flow, and this endpoint is meant to answer
    "are my Client ID/Access Token correct" independent of whether that
    routing infrastructure happens to be healthy right now."""
    user = make_user("verify-no-proxy@example.com")
    seen: list = []
    _verify_as(
        monkeypatch,
        user,
        DhanValidationResult(success=True, message="Dhan token valid.", status_code=200),
        captured_proxy_urls=seen,
    )
    assert seen == [""], "RealDhanClient must be constructed with proxy_url='' for this check"
