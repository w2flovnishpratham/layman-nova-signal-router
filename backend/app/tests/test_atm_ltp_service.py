from app.services import atm_ltp_service
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

    monkeypatch.setattr(atm_ltp_service, "ensure_marketfeed_subscription", lambda **_kwargs: None)
    monkeypatch.setattr(atm_ltp_service, "_market_is_open", lambda: True)
    monkeypatch.setattr(atm_ltp_service, "get_marketfeed_ltp", fake_marketfeed_ltp)
    monkeypatch.setattr(
        atm_ltp_service,
        "get_runtime_settings",
        lambda: {"marketfeed_ws_enabled": True, "option_ltp_source": "AUTO", "option_ltp_cache_seconds": 15},
    )
    monkeypatch.setattr(
        atm_ltp_service,
        "get_dhan_credentials",
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
