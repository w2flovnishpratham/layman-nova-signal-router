# ruff: noqa: FLY002
from types import SimpleNamespace

from app.config import settings
from app.schemas.signal import NormalizedSignal
from app.services import execution_router, security_id_resolver
from app.services.dhan_debugger import validate_dhan_payload
from app.services.execution_router import _build_dhan_payload_and_resolution
from app.services.security_id_resolver import (
    resolve_security_id,
    suggest_option_contract,
)


def make_signal(security_id=None) -> NormalizedSignal:
    return NormalizedSignal(
        payload_format="PINE_MULTI_LEG",
        secret="test-secret",
        signal_id="security-id-test",
        strategy_code="TRADINGVIEW_NIFTY_V1",
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        instrument_type="OPT",
        exchange_segment="NSE_FNO",
        security_id=security_id,
        trading_symbol="NIFTY 2026-05-28 22500 CE",
        option_side="CE",
        strike=22500.0,
        expiry="2026-05-28",
        qty=1,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload={"test": True},
    )


def disable_fallbacks(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False)
    monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "")
    monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)


def test_provided_security_id_passes(monkeypatch):
    disable_fallbacks(monkeypatch)

    result = resolve_security_id(make_signal(security_id="123456"))

    assert result.ok is True
    assert result.security_id == "123456"
    assert result.method == "PROVIDED_IN_SIGNAL"


def test_missing_security_id_blocks(monkeypatch):
    disable_fallbacks(monkeypatch)

    result = resolve_security_id(make_signal())
    payload, resolution = _build_dhan_payload_and_resolution(make_signal(), 1, "ENTRY")
    validation = validate_dhan_payload(payload)

    assert result.ok is False
    assert result.method == "NOT_FOUND"
    assert result.reason == "securityId not resolved for NIFTY 2026-05-28 22500 CE"
    assert resolution["ok"] is False
    assert validation["ok"] is False
    assert "securityId" in validation["missing_fields"]


def test_default_security_id_works_only_when_allowed(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
    monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "999999")
    monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)

    result = resolve_security_id(make_signal())

    assert result.ok is True
    assert result.security_id == "999999"
    assert result.method == "DEFAULT_ENV"


def test_default_security_id_ignored_when_not_allowed(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False)
    monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "999999")
    monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)

    result = resolve_security_id(make_signal())

    assert result.ok is False
    assert result.security_id is None
    assert result.method == "NOT_FOUND"


def test_dhan_payload_validation_passes_after_security_id_resolution(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
    monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "999999")
    monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
    monkeypatch.setattr(
        execution_router,
        "get_dhan_credentials",
        lambda: SimpleNamespace(client_id="test-client"),
    )

    payload, resolution = _build_dhan_payload_and_resolution(make_signal(), 1, "ENTRY")
    validation = validate_dhan_payload(payload)

    assert resolution["ok"] is True
    assert payload["securityId"] == "999999"
    assert validation["ok"] is True


def test_suggest_option_contract_selects_nearest_future_strike(tmp_path, monkeypatch):
    csv_path = tmp_path / "scrip.csv"
    csv_path.write_text(
        "\n".join(
            [
                "EXCH_ID,SEGMENT,SECURITY_ID,INSTRUMENT,UNDERLYING_SYMBOL,SYMBOL_NAME,DISPLAY_NAME,LOT_SIZE,SM_EXPIRY_DATE,STRIKE_PRICE,OPTION_TYPE",
                "NSE,D,111,OPTIDX,NIFTY,NIFTY-Dec2099-23000-CE,NIFTY 31 DEC 23000 CALL,65,2099-12-31,23000.00000,CE",
                "NSE,D,222,OPTIDX,NIFTY,NIFTY-Dec2099-23100-CE,NIFTY 31 DEC 23100 CALL,65,2099-12-31,23100.00000,CE",
                "NSE,D,333,OPTIDX,NIFTY,NIFTY-Dec2099-23200-PE,NIFTY 31 DEC 23200 PUT,65,2099-12-31,23200.00000,PE",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(security_id_resolver, "_candidate_scrip_master_paths", lambda: [csv_path])
    monkeypatch.setattr(security_id_resolver, "_suggest_option_contract_from_index", lambda **_kwargs: None)

    result = suggest_option_contract(option_side="CE", reference_price=23080)

    assert result is not None
    assert result.security_id == "222"
    assert result.expiry == "2099-12-31"
    assert result.strike == 23100.0
    assert result.trading_symbol == "NIFTY-Dec2099-23100-CE"
