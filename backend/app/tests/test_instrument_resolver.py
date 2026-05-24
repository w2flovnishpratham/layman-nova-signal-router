from pathlib import Path

from app.schemas.signal import NormalizedSignal
from app.services import instrument_resolver


def make_signal() -> NormalizedSignal:
    return NormalizedSignal(
        payload_format="PINE_MULTI_LEG",
        secret="test-secret",
        signal_id="test-signal",
        strategy_code="TRADINGVIEW_NIFTY_V1",
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        instrument_type="OPT",
        exchange_segment="NSE_FNO",
        security_id=None,
        trading_symbol="NIFTY 2026-05-26 23800 CE",
        option_side="CE",
        strike=23800.0,
        expiry="2026-05-26",
        qty=1,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload={"test": True},
    )


def test_resolve_option_security_id_from_cached_scrip_master(tmp_path, monkeypatch):
    csv_file = tmp_path / "scrip.csv"
    csv_file.write_text(
        "\n".join(
            [
                "SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_SEGMENT,SEM_TRADING_SYMBOL,UNDERLYING_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_LOT_UNITS",
                "999999,OPTIDX,D,NIFTY 26 MAY 23800 CALL,NIFTY,2026-05-26,23800.000000,CE,75",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(instrument_resolver, "_INDEX", None)
    monkeypatch.setattr(instrument_resolver, "_INDEX_MTIME", None)
    monkeypatch.setattr(instrument_resolver, "_ensure_scrip_master", lambda **_kwargs: csv_file)

    match = instrument_resolver.resolve_option_security_id(make_signal())

    assert match is not None
    assert match.security_id == "999999"
    assert match.trading_symbol == "NIFTY 26 MAY 23800 CALL"
    assert match.lot_size == 75


def test_resolve_option_security_id_returns_none_without_match(tmp_path, monkeypatch):
    csv_file = tmp_path / "scrip.csv"
    csv_file.write_text(
        "\n".join(
            [
                "SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_SEGMENT,SEM_TRADING_SYMBOL,UNDERLYING_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE",
                "999999,OPTIDX,D,NIFTY 26 MAY 23750 CALL,NIFTY,2026-05-26,23750.000000,CE",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(instrument_resolver, "_INDEX", None)
    monkeypatch.setattr(instrument_resolver, "_INDEX_MTIME", None)
    monkeypatch.setattr(instrument_resolver, "_ensure_scrip_master", lambda **_kwargs: csv_file)

    assert instrument_resolver.resolve_option_security_id(make_signal()) is None
