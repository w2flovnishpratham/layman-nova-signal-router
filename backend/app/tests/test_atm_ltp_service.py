from app.services import atm_ltp_service, quote_service
from app.services.credential_vault import DhanCredentials
from app.services.dhan_client import DhanLtpResult
from app.services.dhan_marketfeed_ws import MarketFeedLtpResult
from app.services.security_id_resolver import OptionContractSuggestion


def test_atm_snapshot_rounds_spot_to_nearest_strike(monkeypatch):
    def fake_ltp(exchange_segment, security_id, **_kwargs):
        if security_id == "13":
            return {
                "status": "websocket",
                "source": "dhan_marketfeed_ws",
                "ltp": 24083.1,
                "receivedAt": "2026-06-17T09:10:00Z",
                "ageSeconds": 0.2,
            }
        return {
            "status": "websocket",
            "source": "dhan_marketfeed_ws",
            "ltp": 155.5 if security_id == "CE24100" else 162.25,
            "receivedAt": "2026-06-17T09:10:00Z",
            "ageSeconds": 0.1,
        }

    def fake_suggest(symbol, option_side, exchange_segment, reference_price, strike, **_kwargs):
        assert symbol == "NIFTY"
        assert exchange_segment == "NSE_FNO"
        assert reference_price == 24083.1
        assert strike == 24100.0
        security_id = "CE24100" if option_side == "CE" else "PE24100"
        return OptionContractSuggestion(
            symbol="NIFTY",
            expiry="2026-06-23",
            strike=24100.0,
            option_side=option_side,
            security_id=security_id,
            trading_symbol=f"NIFTY 23 JUN 24100 {option_side}",
            lot_size=65,
        )

    monkeypatch.setattr(atm_ltp_service, "_ltp_with_prefer_ws", fake_ltp)
    monkeypatch.setattr(
        quote_service,
        "get_quote_snapshots",
        lambda requests, **_kwargs: {
            (item.exchange_segment, item.security_id): {
                "status": "FRESH",
                "source": "DHAN_WEBSOCKET",
                "ltp": fake_ltp(item.exchange_segment, item.security_id)["ltp"],
                "received_at": "2026-06-17T09:10:00Z",
                "age_seconds": 0.1,
                "stale": False,
            }
            for item in requests
        },
    )
    monkeypatch.setattr(atm_ltp_service, "_market_is_open", lambda: True)
    monkeypatch.setattr(atm_ltp_service, "suggest_option_contract", fake_suggest)
    monkeypatch.setattr(atm_ltp_service, "marketfeed_ws_status", lambda: {"connected": True})
    monkeypatch.setattr(
        atm_ltp_service,
        "get_runtime_settings",
        lambda: {"option_ws_stale_seconds": 5, "option_rest_fallback_enabled": True, "marketfeed_ws_enabled": True},
    )

    snapshot = atm_ltp_service.get_atm_option_snapshot(option_side="BOTH", lots=2)

    assert snapshot["ok"] is True
    assert snapshot["niftySpot"] == 24083.1
    assert snapshot["niftySpotSource"] == "dhan_marketfeed_ws"
    assert snapshot["atmStrike"] == 24100
    assert snapshot["options"]["CE"]["securityId"] == "CE24100"
    assert snapshot["options"]["CE"]["ltp"] == 155.5
    assert snapshot["options"]["CE"]["qty"] == 130
    assert snapshot["options"]["PE"]["securityId"] == "PE24100"
    assert snapshot["options"]["PE"]["ltpSource"] == "dhan_marketfeed_ws"


def test_atm_snapshot_waits_instead_of_guessing_without_spot(monkeypatch):
    calls = []

    def fake_ltp(**_kwargs):
        return {"status": "ws_waiting", "source": "dhan_marketfeed_ws", "ltp": None}

    def fake_suggest(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(atm_ltp_service, "_ltp_with_prefer_ws", fake_ltp)
    monkeypatch.setattr(atm_ltp_service, "_market_is_open", lambda: True)
    monkeypatch.setattr(atm_ltp_service, "suggest_option_contract", fake_suggest)
    monkeypatch.setattr(atm_ltp_service, "_latest_signal_nifty_price", lambda: None)
    monkeypatch.setattr(atm_ltp_service, "marketfeed_ws_status", lambda: {"connected": False})
    monkeypatch.setattr(atm_ltp_service, "get_runtime_settings", lambda: {})

    snapshot = atm_ltp_service.get_atm_option_snapshot(option_side="CE", lots=1, allow_rest_fallback=False)

    assert snapshot["ok"] is False
    assert snapshot["niftySpot"] is None
    assert snapshot["atmStrike"] is None
    assert snapshot["options"]["CE"]["status"] == "contract_missing"
    assert calls == []


def test_ltp_cache_reuses_recent_websocket_tick(monkeypatch):
    atm_ltp_service._LTP_CACHE.clear()
    calls = []

    def fake_marketfeed_ltp(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return MarketFeedLtpResult(
                success=True,
                message="fresh",
                ltp=134.35,
                exchange_segment=kwargs["exchange_segment"],
                security_id=kwargs["security_id"],
                received_at="2026-06-17T09:10:00Z",
                age_seconds=0.1,
            )
        return MarketFeedLtpResult(
            success=False,
            message="waiting",
            ltp=None,
            exchange_segment=kwargs["exchange_segment"],
            security_id=kwargs["security_id"],
            error="ws_waiting",
        )

    monkeypatch.setattr(quote_service, "ensure_marketfeed_subscription", lambda **_kwargs: None)
    monkeypatch.setattr(quote_service, "_market_is_open", lambda: True)
    monkeypatch.setattr(quote_service, "get_marketfeed_ltp", fake_marketfeed_ltp)
    monkeypatch.setattr(
        quote_service,
        "get_runtime_settings",
        lambda: {"marketfeed_ws_enabled": True, "option_ltp_source": "AUTO", "option_ltp_cache_seconds": 15},
    )
    monkeypatch.setattr(
        quote_service,
        "market_data_credentials",
        lambda: (_ for _ in ()).throw(AssertionError("REST fallback should not be used when cache is fresh")),
    )

    first = atm_ltp_service._ltp_with_prefer_ws(
        exchange_segment="NSE_FNO",
        security_id="56376",
        max_age_seconds=5,
        allow_rest_fallback=True,
    )
    second = atm_ltp_service._ltp_with_prefer_ws(
        exchange_segment="NSE_FNO",
        security_id="56376",
        max_age_seconds=5,
        allow_rest_fallback=True,
    )

    assert first["status"] == "websocket"
    assert second["ltp"] == 134.35
    assert second["status"] == "cache:websocket"


def test_rest_ltp_refreshes_shared_token_after_auth_failure(monkeypatch):
    quote_service.reset_quote_state_for_tests()
    with quote_service.dhan_quote_rate_limiter._lock:
        quote_service.dhan_quote_rate_limiter._blocked_until = 0.0
        quote_service.dhan_quote_rate_limiter._requests.clear()
    tokens_used = []
    refresh_calls = []
    old_creds = DhanCredentials("shared-client", "old-shared-token", "shared_market_data")
    new_creds = DhanCredentials("shared-client", "new-shared-token", "shared_market_data")
    credential_reads = 0

    def fake_market_data_credentials():
        nonlocal credential_reads
        credential_reads += 1
        return old_creds if credential_reads == 1 else new_creds

    class RetryClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_ltp_batch(self, **kwargs):
            token = kwargs["access_token"]
            tokens_used.append(token)
            key = tuple(kwargs["instruments"][0])
            if token == "old-shared-token":
                return {key: DhanLtpResult(
                    success=False,
                    message="Dhan LTP request failed: Unauthorized",
                    ltp=None,
                    status_code=401,
                    error="Unauthorized",
                )}
            return {key: DhanLtpResult(success=True, message="quote", ltp=101.25)}

    def fake_refresh(**kwargs):
        refresh_calls.append(kwargs)
        return True

    monkeypatch.setattr(quote_service, "_market_is_open", lambda: True)
    monkeypatch.setattr(quote_service, "get_runtime_settings", lambda: {"marketfeed_ws_enabled": False})
    monkeypatch.setattr(quote_service, "market_data_credentials", fake_market_data_credentials)
    monkeypatch.setattr(quote_service, "RealDhanClient", RetryClient)
    monkeypatch.setattr(quote_service, "refresh_shared_token_after_auth_failure", fake_refresh)

    result = atm_ltp_service._ltp_with_prefer_ws(
        exchange_segment="NSE_FNO",
        security_id="57046",
        max_age_seconds=5,
        allow_rest_fallback=True,
    )

    assert result["status"] == "rest"
    assert result["ltp"] == 101.25
    assert tokens_used == ["old-shared-token", "new-shared-token"]
    assert refresh_calls[0]["status_code"] == 401


def test_atm_snapshot_does_not_fetch_when_market_is_closed(monkeypatch):
    cleared = {"called": False}

    def forbidden_fetch(**_kwargs):
        raise AssertionError("closed market must not fetch Dhan market data")

    monkeypatch.setattr(atm_ltp_service, "_market_is_open", lambda: False)
    monkeypatch.setattr(atm_ltp_service, "clear_marketfeed_subscription", lambda: cleared.update(called=True))
    monkeypatch.setattr(atm_ltp_service, "_ltp_with_prefer_ws", forbidden_fetch)
    monkeypatch.setattr(atm_ltp_service, "suggest_option_contract", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not resolve contracts")))
    monkeypatch.setattr(atm_ltp_service, "marketfeed_ws_status", lambda: {"connected": False})

    snapshot = atm_ltp_service.get_atm_option_snapshot(option_side="BOTH", lots=1)

    assert cleared["called"] is True
    assert snapshot["ok"] is False
    assert snapshot["marketOpen"] is False
    assert snapshot["niftySpotSource"] == "market_closed"
    assert snapshot["options"]["CE"]["ltpStatus"] == "market_closed"
