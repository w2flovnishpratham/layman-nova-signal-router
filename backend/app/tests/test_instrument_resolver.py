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


def test_scrip_master_write_is_atomic_for_concurrent_readers(tmp_path):
    """The instrument master maps strike/expiry to Dhan security IDs and now has
    two reader processes (engine + webhook intake). A reader must never observe
    a half-written file, so the write goes to a temp file and is renamed into
    place rather than truncating the live one."""
    target = tmp_path / "nested" / "scrip.csv"
    instrument_resolver.write_scrip_master_atomically(target, b"first,row\n")
    assert target.read_bytes() == b"first,row\n"

    # Overwriting an existing file replaces it wholesale, leaving no temp debris.
    instrument_resolver.write_scrip_master_atomically(target, b"second,row,longer\n")
    assert target.read_bytes() == b"second,row,longer\n"
    assert list(target.parent.iterdir()) == [target]


def test_scrip_master_write_leaves_previous_file_intact_on_failure(tmp_path, monkeypatch):
    """A failed write must not destroy the instrument master that order routing
    depends on -- the old file has to survive intact."""
    target = tmp_path / "scrip.csv"
    instrument_resolver.write_scrip_master_atomically(target, b"good,data\n")

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(instrument_resolver.os, "replace", _boom)
    try:
        instrument_resolver.write_scrip_master_atomically(target, b"partial")
    except OSError:
        pass

    assert target.read_bytes() == b"good,data\n"
    assert list(target.parent.iterdir()) == [target]
