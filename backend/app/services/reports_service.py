"""Owner-scoped performance reports computed from closed trades.

Everything here is derived from ``PortfolioTrade`` - one row per round trip the
engine actually tracked end to end. No reports table exists and none is needed:
the metrics are simple aggregates over rows that are already indexed by
``(user_id, closed_at)``.

Truthfulness rules:
* A metric with a zero denominator is ``None`` with a stated reason, never 0.
  Win rate over no trades is unknown; profit factor with no losing trade is
  undefined (not "infinite" and not some large number).
* Max drawdown is measured on **closed-trade equity** - the running sum of
  realised P&L in close order. It is not an intraday equity curve, and the
  payload says so, because NOVA does not store one.
* Money is in rupees, matching ``PortfolioTrade.realized_pnl``.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db import models
from app.db.engine import database_configured, session_scope

IST = ZoneInfo("Asia/Kolkata")

CSV_COLUMNS = [
    "closed_at_ist",
    "strategy",
    "symbol",
    "option_side",
    "qty",
    "entry_price",
    "exit_price",
    "gross_pnl",
    "charges",
    "realized_pnl",
]


def _day_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Inclusive IST day range converted to instants."""
    return (
        datetime.combine(start, time.min, tzinfo=IST),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=IST),
    )


def _round(value: float) -> float:
    return round(float(value), 2)


def _fetch(user_id: uuid.UUID, start: date, end: date, mode: str) -> list[models.PortfolioTrade]:
    if not database_configured():
        return []
    begin, finish = _day_bounds(start, end)
    with session_scope() as db:
        rows = list(db.scalars(
            select(models.PortfolioTrade)
            .where(
                models.PortfolioTrade.user_id == user_id,
                models.PortfolioTrade.mode == mode,
                models.PortfolioTrade.closed_at >= begin,
                models.PortfolioTrade.closed_at < finish,
            )
            .order_by(models.PortfolioTrade.closed_at.asc())
        ))
        # Detach a plain snapshot: the session closes with the scope.
        return [
            models.PortfolioTrade(
                id=row.id, user_id=row.user_id, mode=row.mode, strategy_name=row.strategy_name,
                symbol=row.symbol, option_side=row.option_side, qty=row.qty,
                entry_price=row.entry_price, exit_price=row.exit_price,
                entry_charges=row.entry_charges, exit_charges=row.exit_charges,
                gross_pnl=row.gross_pnl, realized_pnl=row.realized_pnl,
                entry_order_id=row.entry_order_id, exit_order_id=row.exit_order_id,
                signal_id=row.signal_id, opened_at=row.opened_at, closed_at=row.closed_at,
            )
            for row in rows
        ]


def _max_drawdown(trades: list[models.PortfolioTrade]) -> dict[str, Any]:
    """Deepest fall from a peak on the running closed-trade equity."""
    if not trades:
        return {"value": None, "basis": "closed_trade_equity", "reason": "No closed trades in this period."}
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for trade in trades:
        equity += float(trade.realized_pnl or 0.0)
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return {
        "value": _round(abs(worst)),
        "basis": "closed_trade_equity",
        "reason": None,
    }


def build_report(
    user_id: uuid.UUID,
    *,
    start: date,
    end: date,
    mode: str = "paper",
) -> dict[str, Any]:
    trades = _fetch(user_id, start, end, mode)
    wins = [t for t in trades if float(t.realized_pnl or 0.0) > 0]
    losses = [t for t in trades if float(t.realized_pnl or 0.0) < 0]
    scratches = len(trades) - len(wins) - len(losses)

    gross_profit = sum(float(t.realized_pnl or 0.0) for t in wins)
    gross_loss = abs(sum(float(t.realized_pnl or 0.0) for t in losses))
    net = sum(float(t.realized_pnl or 0.0) for t in trades)

    return {
        "ok": True,
        "mode": mode,
        "period": {"start": start.isoformat(), "end": end.isoformat(), "timezone": "Asia/Kolkata"},
        "totals": {
            "trades": len(trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "scratch_trades": scratches,
            "gross_profit": _round(gross_profit),
            "gross_loss": _round(gross_loss),
            "net_pnl": _round(net),
        },
        "win_rate": (
            {"value": None, "reason": "No closed trades in this period."}
            if not trades
            else {"value": round(len(wins) / len(trades) * 100.0, 1), "reason": None}
        ),
        "average_winner": (
            {"value": _round(gross_profit / len(wins)), "reason": None}
            if wins
            else {"value": None, "reason": "No winning trades in this period."}
        ),
        "average_loser": (
            {"value": _round(-gross_loss / len(losses)), "reason": None}
            if losses
            else {"value": None, "reason": "No losing trades in this period."}
        ),
        "profit_factor": (
            {"value": round(gross_profit / gross_loss, 2), "reason": None}
            if gross_loss > 0
            else {"value": None, "reason": "Undefined without a losing trade."}
        ),
        "max_drawdown": _max_drawdown(trades),
        "trades": [
            {
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                "strategy": t.strategy_name,
                "symbol": t.symbol,
                "option_side": t.option_side,
                "qty": int(t.qty or 0),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "charges": _round(float(t.entry_charges or 0.0) + float(t.exit_charges or 0.0)),
                "gross_pnl": _round(float(t.gross_pnl or 0.0)),
                "realized_pnl": _round(float(t.realized_pnl or 0.0)),
            }
            for t in trades
        ],
    }


def report_csv(report: dict[str, Any]) -> str:
    """CSV of the report's trades. Carries no credential or secret field."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for trade in report["trades"]:
        closed = trade["closed_at"]
        closed_ist = (
            datetime.fromisoformat(closed).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
            if closed
            else ""
        )
        writer.writerow([
            closed_ist,
            trade["strategy"] or "",
            trade["symbol"] or "",
            trade["option_side"] or "",
            trade["qty"],
            trade["entry_price"] if trade["entry_price"] is not None else "",
            trade["exit_price"] if trade["exit_price"] is not None else "",
            trade["gross_pnl"],
            trade["charges"],
            trade["realized_pnl"],
        ])
    return buffer.getvalue()
