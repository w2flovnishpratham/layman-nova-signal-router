from __future__ import annotations

from datetime import date
from pathlib import Path


def test_one_minute_candles_survive_cache_restarts_and_only_changed_rows_update(tmp_path, monkeypatch):
    from app.config import settings
    from app.db import engine as db_engine
    from app.db.models import Base
    from app.services import market_candle_store as store

    db_path = tmp_path / "market-candles.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    db_engine.reset_engine_for_tests()
    trading_date = date(2026, 8, 7)
    candle = {"time": 1_754_538_300, "open": 24000, "high": 24005, "low": 23995, "close": 24002, "volume": 0}
    try:
        Base.metadata.create_all(db_engine.get_engine())
        assert store.upsert_session(trading_date, [candle]) is True
        assert store.upsert_session(trading_date, [candle]) is False
        loaded = store.load_session(trading_date)
        assert loaded and loaded["candles"] == [{**candle, "open": 24000.0, "high": 24005.0, "low": 23995.0, "close": 24002.0, "volume": 0.0}]
        assert store.load_latest_session(date(2026, 8, 9))[0] == trading_date

        changed = {**candle, "close": 24004}
        assert store.upsert_session(trading_date, [changed]) is True
        assert store.load_session(trading_date)["candles"][0]["close"] == 24004.0
    finally:
        db_engine.reset_engine_for_tests()


def test_market_candle_migration_upgrades_and_rolls_back(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from app.config import settings
    from app.db import engine as db_engine

    db_path = tmp_path / "market-candle-migration.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    db_engine.reset_engine_for_tests()
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    try:
        command.upgrade(config, "head")
        engine = create_engine(f"sqlite:///{db_path}")
        assert "market_candles_1m" in inspect(engine).get_table_names()
        assert {"symbol", "candle_time", "trading_date", "open", "high", "low", "close", "volume"}.issubset(
            {column["name"] for column in inspect(engine).get_columns("market_candles_1m")}
        )
        command.downgrade(config, "0024_strategy_webhook_provider")
        assert "market_candles_1m" not in inspect(engine).get_table_names()
        engine.dispose()
    finally:
        db_engine.reset_engine_for_tests()


def test_writing_one_minute_does_not_read_back_the_whole_session(tmp_path, monkeypatch):
    """Persisting a tick must cost one row, not the whole trading day.

    upsert_session runs on every tick batch (~0.35s). It used to SELECT every
    candle for the trading date just to update the current minute, so each
    tick dragged a growing slice of the session across the wire -- ~190 rows
    on average, ~64k times a session. Neon bills on data transfer, and that
    read amplification was a plausible cause of the quota outage that took
    production down.
    """
    from sqlalchemy import event

    from app.config import settings
    from app.db import engine as db_engine
    from app.db.models import Base
    from app.services import market_candle_store as store

    db_path = tmp_path / "amplification.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    db_engine.reset_engine_for_tests()
    trading_date = date(2026, 8, 7)
    session_open = 1_754_538_300

    try:
        engine = db_engine.get_engine()
        Base.metadata.create_all(engine)

        # A full session's worth of candles already stored.
        full_day = [
            {
                "time": session_open + minute * 60,
                "open": 24000 + minute,
                "high": 24005 + minute,
                "low": 23995 + minute,
                "close": 24002 + minute,
                "volume": 0,
            }
            for minute in range(375)
        ]
        assert store.upsert_session(trading_date, full_day) is True

        # cursor.rowcount is -1 for SELECT on SQLite, so count the ORM rows
        # actually materialised instead -- that is the traffic Neon bills for.
        from sqlalchemy.orm import Session as OrmSession

        rows_read = 0

        def _count(session, instance):
            nonlocal rows_read
            rows_read += 1

        event.listen(OrmSession, "loaded_as_persistent", _count)

        # One minute ticks up, exactly as a tick batch would write it.
        latest = {**full_day[-1], "close": 24999}
        assert store.upsert_session(trading_date, [latest]) is True

        event.remove(OrmSession, "loaded_as_persistent", _count)

        # Well under the 375 the old full-day query pulled back.
        assert rows_read <= 5, f"writing one candle read {rows_read} rows back"
        assert store.load_session(trading_date)["candles"][-1]["close"] == 24999.0
        # Nothing else was disturbed.
        assert len(store.load_session(trading_date)["candles"]) == 375
    finally:
        db_engine.reset_engine_for_tests()
