"""Durable storage for the global authoritative NIFTY 1m series."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from app.db.engine import database_configured, readonly_session_scope, session_scope
from app.db.models import MarketCandle1m, utcnow

SYMBOL = "NIFTY"
SOURCE = "dhan_authoritative_1m"


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def load_session(trading_date: date) -> dict[str, Any] | None:
    if not database_configured():
        return None
    with readonly_session_scope() as session:
        rows = session.scalars(
            select(MarketCandle1m)
            .where(
                MarketCandle1m.symbol == SYMBOL,
                MarketCandle1m.trading_date == trading_date,
            )
            .order_by(MarketCandle1m.candle_time)
        ).all()
    if not rows:
        return None
    return {
        "candles": [
            {
                "time": int(_aware(row.candle_time).timestamp()),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume),
            }
            for row in rows
        ],
        "updated_at": max(_aware(row.updated_at) for row in rows).isoformat(),
    }


def load_latest_session(on_or_before: date) -> tuple[date, dict[str, Any]] | None:
    if not database_configured():
        return None
    with readonly_session_scope() as session:
        latest = session.scalar(
            select(func.max(MarketCandle1m.trading_date)).where(
                MarketCandle1m.symbol == SYMBOL,
                MarketCandle1m.trading_date <= on_or_before,
            )
        )
    if latest is None:
        return None
    loaded = load_session(latest)
    return (latest, loaded) if loaded else None


def upsert_session(trading_date: date, candles: list[dict[str, Any]]) -> bool:
    """Insert/update only changed OHLCV rows; return whether storage changed."""
    if not database_configured() or not candles:
        return False
    changed = False
    # Scoped to the candles actually being written, not the whole trading day.
    # This runs on every tick batch (~0.35s), and selecting the full day pulled
    # a growing slice of the session -- ~190 rows on average, ~64k times a day --
    # across the wire to update the one minute that changed. Neon bills on data
    # transfer, and that read amplification was large enough to matter.
    # (symbol, candle_time) is the primary key, so the IN hits it directly.
    wanted = [int(candle["time"]) for candle in candles]
    with session_scope() as session:
        existing = {
            int(_aware(row.candle_time).timestamp()): row
            for row in session.scalars(
                select(MarketCandle1m).where(
                    MarketCandle1m.symbol == SYMBOL,
                    MarketCandle1m.candle_time.in_(
                        [datetime.fromtimestamp(stamp, tz=timezone.utc) for stamp in wanted]
                    ),
                )
            ).all()
        }
        now = utcnow()
        for candle in candles:
            stamp = int(candle["time"])
            values = {
                "open": _decimal(candle["open"]),
                "high": _decimal(candle["high"]),
                "low": _decimal(candle["low"]),
                "close": _decimal(candle["close"]),
                "volume": _decimal(candle.get("volume") or 0),
            }
            row = existing.get(stamp)
            if row is None:
                session.add(MarketCandle1m(
                    symbol=SYMBOL,
                    candle_time=datetime.fromtimestamp(stamp, tz=timezone.utc),
                    trading_date=trading_date,
                    source=SOURCE,
                    updated_at=now,
                    **values,
                ))
                changed = True
                continue
            if all(getattr(row, key) == value for key, value in values.items()):
                continue
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = now
            changed = True
    return changed
