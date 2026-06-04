from __future__ import annotations

from app.config import settings
from app.routers import setup as setup_router
from app.services import credential_vault, state_store, wallet_service
from app.services.dhan_client import DhanFundsResult, DhanValidationResult


class FakeReadOnlyDhanClient:
    def validate_token(self, *, client_id: str, access_token: str) -> DhanValidationResult:
        return DhanValidationResult(success=True, message="Dhan token valid.", status_code=200)

    def get_fund_limit(self, *, client_id: str, access_token: str) -> DhanFundsResult:
        return DhanFundsResult(
            success=True,
            message="Dhan fund limit fetched.",
            status_code=200,
            client_id=client_id,
            available_balance=19660.39,
            utilized_amount=73.0,
        )


def test_mock_order_mode_still_uses_real_read_only_account_data(monkeypatch):
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", True)
    monkeypatch.setattr(setup_router, "RealDhanClient", FakeReadOnlyDhanClient)

    ok, _message, funds, details = setup_router.validate_dhan_credentials("1000000001", "token")

    assert ok is True
    assert funds is not None
    assert funds.available_balance == 19660.39
    assert details["read_only_real_data"] is True


def test_wallet_refresh_uses_real_read_only_data_in_mock_order_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "local")
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", True)
    monkeypatch.setattr(wallet_service, "RealDhanClient", FakeReadOnlyDhanClient)
    monkeypatch.setattr(state_store, "APP_STATE_FILE", tmp_path / "app_state.json")
    monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", tmp_path / "credentials.enc.json")
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    credential_vault._LOCAL_MEMORY_PAYLOAD.clear()
    credential_vault._LOCAL_MEMORY_PAYLOAD.update(
        {
            "version": 1,
            "dhan": {
                "client_id": "1000000001",
                "access_token": "token",
                "connected_at": state_store.utc_now(),
            },
            "webhook_secret": None,
        }
    )

    snapshot = wallet_service.refresh_wallet_snapshot(force=True)

    assert snapshot["success"] is True
    assert snapshot["available_balance"] == 19660.39
    assert snapshot["utilized_amount"] == 73.0
