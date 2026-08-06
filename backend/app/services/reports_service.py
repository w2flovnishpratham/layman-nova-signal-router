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
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db import models
from app.db.engine import database_configured, session_scope

IST = ZoneInfo("Asia/Kolkata")

CSV_COLUMNS = [
    "trading_date_ist",
    "closed_at_ist",
    "origin",
    "strategy",
    "symbol",
    "option_side",
    "qty",
    "entry_price",
    "exit_price",
    "gross_pnl",
    "charges",
    "realized_pnl",
    "exit_trigger",
]


def _day_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Inclusive IST day range converted to instants."""
    return (
        datetime.combine(start, time.min, tzinfo=IST).astimezone(timezone.utc),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=IST).astimezone(timezone.utc),
    )


def _round(value: float) -> float:
    return round(float(value), 2)


_AUTOMATED_ORIGINS = ("BUILT_IN_STRATEGY", "USER_STRATEGY", "TRADINGVIEW_WEBHOOK")


def _fetch(
    user_id: uuid.UUID, start: date, end: date, mode: str, trade_origin: str | None = None
) -> list[models.PortfolioTrade]:
    if not database_configured():
        return []
    begin, finish = _day_bounds(start, end)
    with session_scope() as db:
        query = select(models.PortfolioTrade).where(
            models.PortfolioTrade.user_id == user_id,
            models.PortfolioTrade.mode == mode,
            models.PortfolioTrade.closed_at >= begin,
            models.PortfolioTrade.closed_at < finish,
        )
        selection = (trade_origin or "all").lower()
        if selection == "automated":
            query = query.where(models.PortfolioTrade.origin.in_(_AUTOMATED_ORIGINS))
        elif selection == "manual":
            query = query.where(models.PortfolioTrade.origin == "MANUAL")
        rows = list(db.scalars(query.order_by(models.PortfolioTrade.closed_at.asc())))
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
                origin=row.origin, exit_trigger=row.exit_trigger,
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


def _strategy_label(trade: models.PortfolioTrade) -> str:
    if trade.origin == "MANUAL":
        return "Manual Orders"
    return (trade.strategy_name or "Unattributed automated").strip()


def _trade_day(trade: models.PortfolioTrade) -> date:
    assert trade.closed_at is not None
    closed = trade.closed_at
    if closed.tzinfo is None:
        closed = closed.replace(tzinfo=timezone.utc)
    return closed.astimezone(IST).date()


def _win_rate(wins: int, losses: int) -> dict[str, Any]:
    decided = wins + losses
    return (
        {"value": round(wins / decided * 100.0, 1), "reason": None}
        if decided
        else {"value": None, "reason": "No non-zero closed trades in this period."}
    )


def _trade_row(trade: models.PortfolioTrade) -> dict[str, Any]:
    """Per-trade detail for a session's drill-down table.

    symbol is the full trading symbol (e.g. "NIFTY 11 AUG 24650 CALL"), so
    strike and expiry are carried there rather than as separate columns --
    PortfolioTrade has never stored them individually.

    Deliberately absent: the SL/TP levels that were armed on the trade. They
    live on the runtime position, not on the durable ledger row, so they are
    genuinely unavailable for closed trades and would have to be added as
    columns (plus a migration) before they could be shown.
    """
    entry = float(trade.entry_price or 0.0)
    exit_price = float(trade.exit_price or 0.0)
    entry_charges = float(trade.entry_charges or 0.0)
    exit_charges = float(trade.exit_charges or 0.0)
    realized = float(trade.realized_pnl or 0.0)
    invested = entry * int(trade.qty or 0)
    return {
        "id": str(trade.id),
        "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
        "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
        "symbol": trade.symbol,
        "option_side": trade.option_side,
        "qty": int(trade.qty or 0),
        "entry_price": _round(entry),
        "exit_price": _round(exit_price),
        "charges": _round(entry_charges + exit_charges),
        "gross_pnl": _round(float(trade.gross_pnl or 0.0)),
        "realized_pnl": _round(realized),
        "pnl_pct": _round(realized / invested * 100.0) if invested else None,
        "strategy": _strategy_label(trade),
        # Who opened it (MANUAL vs an automated source) and why it closed
        # (STOP_LOSS / TAKE_PROFIT / EOD_SQUAREOFF / MANUAL / ...).
        "origin": trade.origin,
        "exit_trigger": trade.exit_trigger,
        "result": "win" if realized > 0 else "loss" if realized < 0 else "flat",
    }


def _daily_sessions(trades: list[models.PortfolioTrade], mode: str) -> list[dict[str, Any]]:
    buckets: dict[date, list[models.PortfolioTrade]] = defaultdict(list)
    for trade in trades:
        buckets[_trade_day(trade)].append(trade)
    sessions: list[dict[str, Any]] = []
    for trade_date in sorted(buckets, reverse=True):
        rows = buckets[trade_date]
        wins = sum(float(row.realized_pnl or 0.0) > 0 for row in rows)
        losses = sum(float(row.realized_pnl or 0.0) < 0 for row in rows)
        strategies = list(dict.fromkeys(_strategy_label(row) for row in rows))
        sessions.append({
            "date": trade_date.isoformat(),
            "strategy_mix": strategies,
            "trades": len(rows),
            "wins": wins,
            "losses": losses,
            "win_rate": _win_rate(wins, losses),
            "max_drawdown": _max_drawdown(rows),
            "net_pnl": _round(sum(float(row.realized_pnl or 0.0) for row in rows)),
            "manual_orders": sum(row.origin == "MANUAL" for row in rows),
            "mode": mode,
            # Per-trade detail for the row's drill-down, newest first. Already
            # in memory here, so it costs no extra query -- the alternative was
            # a second endpoint refetching the same rows per day.
            "trades_detail": [
                _trade_row(row)
                for row in sorted(
                    rows,
                    key=lambda item: (item.closed_at is None, item.closed_at),
                    reverse=True,
                )
            ],
        })
    return sessions


def _strategy_breakdown(trades: list[models.PortfolioTrade]) -> list[dict[str, Any]]:
    buckets: dict[str, list[models.PortfolioTrade]] = defaultdict(list)
    for trade in trades:
        buckets[_strategy_label(trade)].append(trade)
    total_absolute = sum(abs(float(trade.realized_pnl or 0.0)) for trade in trades)
    result: list[dict[str, Any]] = []
    for label, rows in buckets.items():
        pnl = sum(float(row.realized_pnl or 0.0) for row in rows)
        wins = sum(float(row.realized_pnl or 0.0) > 0 for row in rows)
        losses = sum(float(row.realized_pnl or 0.0) < 0 for row in rows)
        result.append({
            "display_name": label,
            "realized_pnl": _round(pnl),
            "closed_trades": len(rows),
            "wins": wins,
            "losses": losses,
            "win_rate": _win_rate(wins, losses),
            "contribution_percentage": round(
                sum(abs(float(row.realized_pnl or 0.0)) for row in rows) / total_absolute * 100.0,
                1,
            ) if total_absolute else 0.0,
        })
    return sorted(result, key=lambda row: row["realized_pnl"], reverse=True)


def build_report(
    user_id: uuid.UUID,
    *,
    start: date,
    end: date,
    mode: str = "paper",
    trade_origin: str | None = None,
) -> dict[str, Any]:
    trades = _fetch(user_id, start, end, mode, trade_origin)
    wins = [t for t in trades if float(t.realized_pnl or 0.0) > 0]
    losses = [t for t in trades if float(t.realized_pnl or 0.0) < 0]
    scratches = len(trades) - len(wins) - len(losses)

    gross_profit = sum(float(t.realized_pnl or 0.0) for t in wins)
    gross_loss = abs(sum(float(t.realized_pnl or 0.0) for t in losses))
    net = sum(float(t.realized_pnl or 0.0) for t in trades)
    daily_sessions = _daily_sessions(trades, mode)

    return {
        "ok": True,
        "mode": mode,
        "trade_origin": (trade_origin or "all").lower(),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "timezone": "Asia/Kolkata"},
        "totals": {
            "trades": len(trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "scratch_trades": scratches,
            "gross_profit": _round(gross_profit),
            "gross_loss": _round(gross_loss),
            "net_pnl": _round(net),
            "sessions": len(daily_sessions),
        },
        "win_rate": (
            _win_rate(len(wins), len(losses))
            if trades
            else {"value": None, "reason": "No closed trades in this period."}
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
        "daily_sessions": daily_sessions,
        "by_strategy": _strategy_breakdown(trades),
        "trades": [
            {
                "trading_date_ist": _trade_day(t).isoformat(),
                "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                "origin": t.origin,
                "strategy": _strategy_label(t),
                "symbol": t.symbol,
                "option_side": t.option_side,
                "qty": int(t.qty or 0),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "charges": _round(float(t.entry_charges or 0.0) + float(t.exit_charges or 0.0)),
                "gross_pnl": _round(float(t.gross_pnl or 0.0)),
                "realized_pnl": _round(float(t.realized_pnl or 0.0)),
                "exit_trigger": t.exit_trigger,
            }
            for t in trades
        ],
    }


def report_csv(report: dict[str, Any]) -> str:
    """Filter-scoped summary, sessions, strategy totals and closed trades."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["NOVA REPORT"])
    writer.writerow(["mode", report["mode"]])
    writer.writerow(["trade_origin", report["trade_origin"]])
    writer.writerow(["period", report["period"]["start"], report["period"]["end"], report["period"]["timezone"]])
    writer.writerow([])
    writer.writerow(["SUMMARY"])
    writer.writerow(["sessions", "closed_trades", "wins", "losses", "net_pnl", "profit_factor", "max_drawdown"])
    writer.writerow([
        report["totals"]["sessions"],
        report["totals"]["trades"],
        report["totals"]["winning_trades"],
        report["totals"]["losing_trades"],
        report["totals"]["net_pnl"],
        report["profit_factor"]["value"] if report["profit_factor"]["value"] is not None else "",
        report["max_drawdown"]["value"] if report["max_drawdown"]["value"] is not None else "",
    ])
    writer.writerow([])
    writer.writerow(["DAILY SESSIONS"])
    writer.writerow(["date", "strategy_mix", "closed_trades", "wins", "losses", "win_rate", "max_drawdown", "net_pnl", "mode"])
    for session in report["daily_sessions"]:
        writer.writerow([
            session["date"],
            " · ".join(session["strategy_mix"]),
            session["trades"],
            session["wins"],
            session["losses"],
            session["win_rate"]["value"] if session["win_rate"]["value"] is not None else "",
            session["max_drawdown"]["value"] if session["max_drawdown"]["value"] is not None else "",
            session["net_pnl"],
            session["mode"],
        ])
    writer.writerow([])
    writer.writerow(["BY STRATEGY"])
    writer.writerow(["category", "realized_pnl", "closed_trades", "wins", "losses", "win_rate", "contribution_percentage"])
    for strategy in report["by_strategy"]:
        writer.writerow([
            strategy["display_name"],
            strategy["realized_pnl"],
            strategy["closed_trades"],
            strategy["wins"],
            strategy["losses"],
            strategy["win_rate"]["value"] if strategy["win_rate"]["value"] is not None else "",
            strategy["contribution_percentage"],
        ])
    writer.writerow([])
    writer.writerow(["CLOSED TRADES"])
    writer.writerow(CSV_COLUMNS)
    for trade in report["trades"]:
        closed = trade["closed_at"]
        closed_ist = (
            datetime.fromisoformat(closed).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
            if closed
            else ""
        )
        writer.writerow([
            trade["trading_date_ist"],
            closed_ist,
            trade["origin"] or "",
            trade["strategy"] or "",
            trade["symbol"] or "",
            trade["option_side"] or "",
            trade["qty"],
            trade["entry_price"] if trade["entry_price"] is not None else "",
            trade["exit_price"] if trade["exit_price"] is not None else "",
            trade["gross_pnl"],
            trade["charges"],
            trade["realized_pnl"],
            trade["exit_trigger"] or "",
        ])
    return buffer.getvalue()


def report_pdf(report: dict[str, Any]) -> bytes:
    """Compact PDF export; ReportLab is used only for this actual PDF surface."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"NOVA {report['mode']} report",
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("NOVA Performance Report", styles["Title"]),
        Paragraph(
            f"{report['mode'].title()} · {report['trade_origin'].title()} · "
            f"{report['period']['start']} to {report['period']['end']} · Asia/Kolkata",
            styles["BodyText"],
        ),
        Spacer(1, 6 * mm),
    ]
    totals = report["totals"]
    summary = [
        ["Sessions", "Closed trades", "Wins", "Losses", "Net P&L", "Profit factor", "Max drawdown"],
        [
            totals["sessions"], totals["trades"], totals["winning_trades"], totals["losing_trades"],
            f"Rs {totals['net_pnl']:,.2f}",
            report["profit_factor"]["value"] if report["profit_factor"]["value"] is not None else "N/A",
            f"Rs {report['max_drawdown']['value']:,.2f}" if report["max_drawdown"]["value"] is not None else "N/A",
        ],
    ]
    story.append(Table(summary, repeatRows=1))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Daily sessions", styles["Heading2"]))
    daily = [["Date", "Strategy mix", "Trades", "Win rate", "Max DD", "Net P&L"]]
    for row in report["daily_sessions"]:
        daily.append([
            row["date"],
            ", ".join(row["strategy_mix"]),
            row["trades"],
            f"{row['win_rate']['value']}%" if row["win_rate"]["value"] is not None else "N/A",
            f"Rs {row['max_drawdown']['value']:,.2f}" if row["max_drawdown"]["value"] is not None else "N/A",
            f"Rs {row['net_pnl']:,.2f}",
        ])
    table = Table(daily, repeatRows=1, colWidths=[24 * mm, 58 * mm, 15 * mm, 20 * mm, 23 * mm, 27 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F6BED")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(table)
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("By strategy", styles["Heading2"]))
    strategy_rows = [["Category", "Realized P&L", "Closed trades", "Wins", "Losses", "Win rate"]]
    for row in report["by_strategy"]:
        strategy_rows.append([
            row["display_name"],
            f"Rs {row['realized_pnl']:,.2f}",
            row["closed_trades"],
            row["wins"],
            row["losses"],
            f"{row['win_rate']['value']}%" if row["win_rate"]["value"] is not None else "N/A",
        ])
    story.append(Table(strategy_rows, repeatRows=1))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Detailed closed trades", styles["Heading2"]))
    trade_rows = [["Date", "Origin", "Strategy", "Instrument", "Qty", "Charges", "Net P&L"]]
    for row in report["trades"]:
        trade_rows.append([
            row["trading_date_ist"],
            "Manual" if row["origin"] == "MANUAL" else "Automated",
            row["strategy"],
            f"{row['symbol'] or ''} {row['option_side'] or ''}".strip(),
            row["qty"],
            f"Rs {row['charges']:,.2f}",
            f"Rs {row['realized_pnl']:,.2f}",
        ])
    trade_table = Table(
        trade_rows,
        repeatRows=1,
        colWidths=[22 * mm, 20 * mm, 38 * mm, 42 * mm, 12 * mm, 22 * mm, 24 * mm],
    )
    trade_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F6BED")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(trade_table)
    document.build(story)
    return output.getvalue()
