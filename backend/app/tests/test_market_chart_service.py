from __future__ import annotations

from datetime import datetime, timedelta

from app.services import market_chart_service as charts
from app.services import shared_market_data as smd
from app.services.credential_vault import DhanCredentials


def test_chart_auth_failure_does_not_refresh_shared_market_token(monkeypatch):
    calls = {"refresh": 0}

    class FakeDhanClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_intraday_candles(self, **kwargs):
            return {"success": False, "status_code": 401, "error": "Unauthorized"}

    def fake_refresh(**kwargs):
        calls["refresh"] += 1
        raise AssertionError("chart fetch must not refresh the shared token")

    monkeypatch.setattr(charts, "RealDhanClient", FakeDhanClient)
    monkeypatch.setattr(charts, "market_data_credentials", lambda: DhanCredentials("client", "token", "shared_market_data"))
    monkeypatch.setattr(charts, "shared_market_data_configured", lambda: True)
    monkeypatch.setattr(smd, "refresh_shared_token_after_auth_failure", fake_refresh)

    payload = charts._fetch_candles("5", "5m", datetime.now(charts._IST).date() - timedelta(days=1))

    assert payload["status"] == "unavailable"
    assert payload["reason"] == "market_data_chart_auth_failed"
    assert calls["refresh"] == 0
