"""Portfolio analytics for the user dashboard, selectable by mode=live|paper.

Live: reconstructs the round-trips NOVA actually executed with real orders by
pairing filled ENTRY/EXIT legs from the durable order event log, using the
real Dhan fund snapshot (never the paper wallet) as the capital base.

Paper: reads the virtual wallet's closed_trades/open_trade directly (paper_portfolio.py)
so the paper book never mixes with real money. The paper wallet can be reset at
any time by the user, so its history is a session record, not permanent capital.

Everything is derived server-side so the frontend just renders.

When a database is configured and a real (non-dev) user is bound to the request,
closed trades and an equity snapshot are persisted best-effort so history
survives beyond the rolling log window. Persistence never blocks the response.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.audit_logger import read_jsonl
from app.services.signal_parser import AUTOMATED_ORIGINS, origin_from_source
from app.services.state_store import get_engine_mode

logger = logging.getLogger("nova_signal_router.portfolio")

_IST = ZoneInfo("Asia/Kolkata")
_ORDER_LOG_LIMIT = 5000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_ts(value: Any) -> datetime:
    if not value:
        return _EPOCH
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return _EPOCH


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None, ndigits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), ndigits)


def _action(ev: dict) -> str | None:
    return ev.get("normalized_action") or ev.get("action")


def _qty(ev: dict) -> int:
    return int(_num(ev.get("normalized_qty")) or _num(ev.get("qty")) or 0)


def _side(ev: dict) -> str | None:
    return ev.get("normalized_option_side") or ev.get("option_side")


def _is_filled(ev: dict) -> bool:
    if ev.get("phase") != "after_response":
        return False
    if ev.get("success") is False:
        return False
    status = str(ev.get("status") or "").upper()
    if status in {"REJECTED", "CANCELLED", "CANCELED", "FAILED"}:
        return False
    return ev.get("avg_price") is not None or ev.get("success") is True


def _is_live(ev: dict) -> bool:
    """True only for real-money legs. Paper fills (virtual wallet the user can
    reset at will) are excluded so the dashboard reflects actual capital."""
    order_id = str(ev.get("order_id") or "")
    if order_id.startswith("PAPER-"):
        return False
    if str(ev.get("mode") or "").lower() == "live":
        return True
    # Older records without an explicit mode: trust the live-order flags.
    if ev.get("live_orders_enabled") and str(ev.get("dhan_mode") or "").upper() == "REAL":
        return True
    return False


def _dedupe_legs_by_order_id(legs: list[dict]) -> list[dict]:
    """One leg per broker order.

    A single order can produce two after_response events: the immediate
    placement echo (avg_price null for MARKET orders in TRANSIT) and the
    post-poll fill update (avg_price set). Keep one record per order_id,
    preferring the one that carries a fill price, at its ORIGINAL position in
    the chronological sequence so ENTRY/EXIT pairing is unaffected.
    Legs without an order_id are kept as-is.
    """
    slot_by_order: dict[str, int] = {}
    deduped: list[dict] = []
    for ev in legs:
        order_id = str(ev.get("order_id") or "").strip()
        if not order_id:
            deduped.append(ev)
            continue
        slot = slot_by_order.get(order_id)
        if slot is None:
            slot_by_order[order_id] = len(deduped)
            deduped.append(ev)
            continue
        kept = deduped[slot]
        # Later events win when they add a fill price (or the kept one has none).
        if ev.get("avg_price") is not None or kept.get("avg_price") is None:
            deduped[slot] = ev
    return deduped


def _pair_round_trips(events: list[dict]) -> tuple[list[dict[str, Any]], dict | None]:
    """Walk filled LIVE order legs chronologically and pair each ENTRY with the
    next EXIT. The engine holds at most one position at a time, so a stack of
    depth one is correct and robust."""
    legs = [ev for ev in events if _is_filled(ev) and _is_live(ev)]
    legs.sort(key=lambda ev: _parse_ts(ev.get("timestamp")))
    legs = _dedupe_legs_by_order_id(legs)

    trades: list[dict[str, Any]] = []
    open_entry: dict | None = None

    for ev in legs:
        action = (_action(ev) or "").upper()
        if action == "ENTRY":
            open_entry = ev
        elif action == "EXIT" and open_entry is not None:
            entry = open_entry
            open_entry = None
            qty = _qty(entry) or _qty(ev)
            entry_price = _num(entry.get("avg_price"))
            exit_price = _num(ev.get("avg_price"))
            gross = None
            if entry_price is not None and exit_price is not None:
                gross = (exit_price - entry_price) * qty
            realized = gross if gross is not None else 0.0

            opened_at = _parse_ts(entry.get("timestamp"))
            closed_at = _parse_ts(ev.get("timestamp"))
            hold_minutes = None
            if opened_at > _EPOCH and closed_at > _EPOCH:
                hold_minutes = max(0.0, (closed_at - opened_at).total_seconds() / 60.0)

            pnl_pct = None
            invested = (entry_price or 0) * qty
            if invested:
                pnl_pct = realized / invested * 100.0

            trades.append(
                {
                    "symbol": ev.get("trading_symbol") or entry.get("trading_symbol"),
                    "option_side": _side(entry) or _side(ev),
                    "qty": qty,
                    "entry_price": _round(entry_price),
                    "exit_price": _round(exit_price),
                    "entry_charges": 0.0,
                    "exit_charges": 0.0,
                    "gross_pnl": _round(gross if gross is not None else realized),
                    "realized_pnl": _round(realized),
                    "pnl_pct": _round(pnl_pct),
                    "entry_order_id": entry.get("order_id"),
                    "exit_order_id": ev.get("order_id"),
                    "signal_id": ev.get("signal_id") or entry.get("signal_id"),
                    # Position keeps the origin of whoever opened it, not the exit leg.
                    "origin": origin_from_source(entry.get("source")),
                    "opened_at": entry.get("timestamp"),
                    "closed_at": ev.get("timestamp"),
                    "hold_minutes": _round(hold_minutes, 1),
                    "result": "win" if realized > 0 else "loss" if realized < 0 else "flat",
                }
            )

    return trades, open_entry


def _empty_kpis() -> dict[str, Any]:
    return {
        "realized_pnl": 0.0,
        "realized_pnl_pct": 0.0,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "win_rate": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "avg_trade": 0.0,
        "profit_factor": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "max_drawdown": 0.0,
        "max_drawdown_pct": 0.0,
        "total_charges": 0.0,
        "avg_hold_minutes": 0.0,
        "current_streak": {"type": "flat", "count": 0},
    }


def _compute_kpis(trades: list[dict[str, Any]], capital_base: float) -> dict[str, Any]:
    if not trades:
        return _empty_kpis()

    base = capital_base if capital_base and capital_base > 0 else 1.0
    pnls = [float(t["realized_pnl"] or 0.0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    breakeven = len([p for p in pnls if p == 0])
    realized = sum(pnls)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    total_charges = sum(float(t.get("entry_charges") or 0) + float(t.get("exit_charges") or 0) for t in trades)
    holds = [float(t["hold_minutes"]) for t in trades if t.get("hold_minutes") is not None]

    peak = base
    equity = base
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    streak_type = "flat"
    streak_count = 0
    for p in reversed(pnls):
        kind = "win" if p > 0 else "loss" if p < 0 else "flat"
        if streak_count == 0:
            streak_type = kind
            streak_count = 1
        elif kind == streak_type:
            streak_count += 1
        else:
            break

    return {
        "realized_pnl": _round(realized),
        "realized_pnl_pct": _round(realized / base * 100.0),
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": breakeven,
        "win_rate": _round(len(wins) / len(trades) * 100.0),
        "avg_win": _round(gross_win / len(wins) if wins else 0.0),
        "avg_loss": _round(sum(losses) / len(losses) if losses else 0.0),
        "avg_trade": _round(realized / len(trades)),
        "profit_factor": _round(gross_win / gross_loss if gross_loss else (gross_win if gross_win else 0.0)),
        "best_trade": _round(max(pnls)),
        "worst_trade": _round(min(pnls)),
        "max_drawdown": _round(max_dd),
        "max_drawdown_pct": _round(max_dd / peak * 100.0 if peak else 0.0),
        "total_charges": _round(total_charges),
        "avg_hold_minutes": _round(sum(holds) / len(holds) if holds else 0.0, 1),
        "current_streak": {"type": streak_type, "count": streak_count},
    }


def _equity_curve(trades: list[dict[str, Any]], starting_balance: float) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = [
        {
            "index": 0,
            "t": trades[0]["opened_at"] if trades else None,
            "equity": _round(starting_balance),
            "cumulative_pnl": 0.0,
        }
    ]
    cumulative = 0.0
    equity = starting_balance
    for i, t in enumerate(trades, start=1):
        pnl = float(t["realized_pnl"] or 0.0)
        cumulative += pnl
        equity += pnl
        points.append(
            {
                "index": i,
                "t": t["closed_at"],
                "equity": _round(equity),
                "cumulative_pnl": _round(cumulative),
            }
        )
    return points


def _daily_pnl(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for t in trades:
        closed = _parse_ts(t.get("closed_at"))
        if closed <= _EPOCH:
            continue
        day = closed.astimezone(_IST).date().isoformat()
        bucket = buckets.setdefault(day, {"date": day, "pnl": 0.0, "trades": 0, "wins": 0})
        bucket["pnl"] += float(t["realized_pnl"] or 0.0)
        bucket["trades"] += 1
        if float(t["realized_pnl"] or 0.0) > 0:
            bucket["wins"] += 1
    rows = sorted(buckets.values(), key=lambda b: b["date"])
    for row in rows:
        row["pnl"] = _round(row["pnl"])
    return rows


def _side_breakdown(trades: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for side in ("CE", "PE"):
        side_trades = [t for t in trades if (t.get("option_side") or "").upper() == side]
        pnl = sum(float(t["realized_pnl"] or 0.0) for t in side_trades)
        wins = len([t for t in side_trades if float(t["realized_pnl"] or 0.0) > 0])
        out[side] = {
            "trades": len(side_trades),
            "pnl": _round(pnl),
            "win_rate": _round(wins / len(side_trades) * 100.0 if side_trades else 0.0),
        }
    return out


def _symbol_breakdown(trades: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for t in trades:
        symbol = t.get("symbol") or "—"
        bucket = buckets.setdefault(symbol, {"symbol": symbol, "trades": 0, "pnl": 0.0})
        bucket["trades"] += 1
        bucket["pnl"] += float(t["realized_pnl"] or 0.0)
    rows = sorted(buckets.values(), key=lambda b: abs(b["pnl"]), reverse=True)[:limit]
    for row in rows:
        row["pnl"] = _round(row["pnl"])
    return rows


def _live_wallet_snapshot(*, force: bool = False) -> dict[str, Any]:
    """Real Dhan funds only — never the resettable paper wallet."""
    try:
        from app.services.state_store import get_wallet_snapshot
        from app.services.wallet_service import refresh_wallet_snapshot

        if (get_engine_mode(legacy_fallback=False) or "") == "live":
            snap = refresh_wallet_snapshot(force=force, log_event=False)
        else:
            # Not live right now: use the last real funds snapshot on record.
            snap = get_wallet_snapshot()
        return snap if isinstance(snap, dict) else {}
    except Exception:
        return {}


def _persist_trades_best_effort(trades: list[dict[str, Any]]) -> None:
    """Upsert closed live trades for the bound user, idempotent on exit_order_id.
    Never raises. Split out from snapshot persistence so a single confirmed
    EXIT fill can persist its realized P&L without needing a fresh wallet
    snapshot (see log_order_event's live-exit hook in audit_logger.py)."""
    try:
        from app.db.engine import database_configured, session_scope
        from app.db.models import PortfolioTrade
        from app.services.execution_context import current_execution_user
    except Exception:
        return
    if not database_configured():
        return
    user = current_execution_user()
    if user is None or user.is_dev:
        return
    try:
        with session_scope() as session:
            existing = {
                row[0]
                for row in session.query(PortfolioTrade.exit_order_id)
                .filter(PortfolioTrade.user_id == user.id)
                .all()
                if row[0]
            }
            for t in trades:
                exit_id = t.get("exit_order_id")
                if not exit_id or str(exit_id) in existing:
                    continue
                session.add(
                    PortfolioTrade(
                        user_id=user.id,
                        mode="live",
                        strategy_name=(
                            None if t.get("origin") == "MANUAL"
                            else t.get("strategy_name") or t.get("strategy")
                        ),
                        symbol=t.get("symbol"),
                        option_side=t.get("option_side"),
                        qty=int(t.get("qty") or 0),
                        entry_price=t.get("entry_price"),
                        exit_price=t.get("exit_price"),
                        entry_charges=float(t.get("entry_charges") or 0.0),
                        exit_charges=float(t.get("exit_charges") or 0.0),
                        gross_pnl=float(t.get("gross_pnl") or 0.0),
                        realized_pnl=float(t.get("realized_pnl") or 0.0),
                        entry_order_id=t.get("entry_order_id"),
                        exit_order_id=str(exit_id),
                        signal_id=t.get("signal_id"),
                        origin=t.get("origin"),
                        exit_trigger=t.get("exit_trigger"),
                        opened_at=_parse_ts(t.get("opened_at")) if t.get("opened_at") else None,
                        closed_at=_parse_ts(t.get("closed_at")) if t.get("closed_at") else None,
                    )
                )
    except Exception as exc:  # pragma: no cover - persistence is best effort
        logger.debug("Portfolio trade persistence skipped: %s", exc)


def _persist_snapshot_best_effort(wallet: dict[str, Any], kpis: dict[str, Any]) -> None:
    """Add one equity snapshot for the bound user. Never raises."""
    try:
        from app.db.engine import database_configured, session_scope
        from app.db.models import PortfolioSnapshot
        from app.services.execution_context import current_execution_user
    except Exception:
        return
    if not database_configured():
        return
    user = current_execution_user()
    if user is None or user.is_dev:
        return
    try:
        with session_scope() as session:
            session.add(
                PortfolioSnapshot(
                    user_id=user.id,
                    mode="live",
                    starting_balance=_num(wallet.get("starting_balance")),
                    available_balance=_num(wallet.get("available_balance")),
                    utilized_amount=_num(wallet.get("utilized_amount")),
                    equity=_num(wallet.get("equity")),
                    realized_pnl=kpis.get("realized_pnl"),
                    trade_count=kpis.get("total_trades") or 0,
                    fetch_status="ok" if wallet.get("funds_connected") else "error",
                )
            )
    except Exception as exc:  # pragma: no cover - persistence is best effort
        logger.debug("Portfolio snapshot persistence skipped: %s", exc)


def _last_known_live_snapshot(user_id: Any) -> dict[str, Any] | None:
    """Most recent successful Live wallet reading on record for this user, for
    display when the current Dhan fetch is stale/failed/unavailable. Never raises."""
    try:
        from app.db.engine import database_configured, session_scope
        from app.db.models import PortfolioSnapshot
    except Exception:
        return None
    if not database_configured():
        return None
    try:
        with session_scope() as session:
            row = (
                session.query(PortfolioSnapshot)
                .filter(
                    PortfolioSnapshot.user_id == user_id,
                    PortfolioSnapshot.mode == "live",
                    PortfolioSnapshot.fetch_status == "ok",
                )
                .order_by(PortfolioSnapshot.captured_at.desc())
                .first()
            )
            if row is None:
                return None
            return {
                "starting_balance": row.starting_balance,
                "available_balance": row.available_balance,
                "utilized_amount": row.utilized_amount,
                "equity": row.equity,
                "captured_at": row.captured_at.isoformat() if row.captured_at else None,
            }
    except Exception as exc:  # pragma: no cover - read is best effort
        logger.debug("Last-known live snapshot lookup skipped: %s", exc)
        return None


def _persist_best_effort(trades: list[dict[str, Any]], wallet: dict[str, Any], kpis: dict[str, Any]) -> None:
    """Upsert closed live trades + an equity snapshot for the bound user. Never raises."""
    _persist_trades_best_effort(trades)
    _persist_snapshot_best_effort(wallet, kpis)


def persist_live_trades_from_log() -> None:
    """Best-effort: re-pair the live order-event log and upsert any newly
    closed trades. Cheap (no live Dhan API call, unlike full analytics) and
    idempotent, so it's safe to call right when an EXIT fill is logged rather
    than waiting for someone to next open the dashboard."""
    try:
        events = read_jsonl("order", limit=_ORDER_LOG_LIMIT)
        trades, _ = _pair_round_trips(events)
        _persist_trades_best_effort(trades)
    except Exception as exc:  # pragma: no cover - persistence is best effort
        logger.debug("Live trade persistence trigger skipped: %s", exc)


def _resolve_strategy_display_name(session: Any, closed_trade: dict[str, Any]) -> str | None:
    """apply_paper_entry now tags every AUTOMATED paper trade with the
    catalog code that triggered it (see execution_router._tag_paper_origin);
    resolve it to the human-readable name shown elsewhere (e.g. user_runs),
    so Reports stops falling back to "Unattributed automated" for every
    single automated trade -- which it always did, for every strategy,
    since apply_paper_entry never captured any strategy identity before."""
    if closed_trade.get("origin") == "MANUAL":
        return None
    code = closed_trade.get("strategy_code")
    if code:
        from app.db import models

        display_name = session.query(models.StrategyCatalog.display_name).filter(
            models.StrategyCatalog.code == code
        ).scalar()
        if display_name:
            return display_name
        return str(code)
    return closed_trade.get("strategy_name") or closed_trade.get("strategy")


def persist_paper_trade(closed_trade: dict[str, Any]) -> None:
    """Best-effort durable record of one closed Paper trade, so /api/reports
    (mode=paper) reflects real history instead of always reading back empty.
    Mirrors _persist_best_effort's live path; idempotent on exit_order_id;
    never raises.
    """
    try:
        from app.db.engine import database_configured, session_scope
        from app.db.models import PortfolioTrade
        from app.services.execution_context import current_execution_user
    except Exception:
        return
    if not database_configured():
        return
    user = current_execution_user()
    if user is None or user.is_dev:
        return
    exit_id = closed_trade.get("exit_order_id")
    if not exit_id:
        return
    try:
        with session_scope() as session:
            exists = (
                session.query(PortfolioTrade.id)
                .filter(
                    PortfolioTrade.user_id == user.id,
                    PortfolioTrade.exit_order_id == str(exit_id),
                )
                .first()
            )
            if exists:
                return
            symbol = str(closed_trade.get("symbol") or "") or None
            option_side = (
                "CE" if symbol and symbol.endswith("CE") else "PE" if symbol and symbol.endswith("PE") else None
            )
            entry_charges = float(closed_trade.get("entry_charges") or 0.0)
            exit_charges = float(closed_trade.get("exit_charges") or 0.0)
            realized = float(closed_trade.get("realized_pnl") or 0.0)
            session.add(
                PortfolioTrade(
                    user_id=user.id,
                    mode="paper",
                    strategy_name=_resolve_strategy_display_name(session, closed_trade),
                    symbol=symbol,
                    option_side=option_side,
                    qty=int(closed_trade.get("qty") or 0),
                    entry_price=closed_trade.get("entry_price"),
                    exit_price=closed_trade.get("exit_price"),
                    entry_charges=entry_charges,
                    exit_charges=exit_charges,
                    gross_pnl=round(realized + entry_charges + exit_charges, 2),
                    realized_pnl=realized,
                    entry_order_id=closed_trade.get("entry_order_id"),
                    exit_order_id=str(exit_id),
                    origin=closed_trade.get("origin"),
                    exit_trigger=closed_trade.get("exit_trigger"),
                    opened_at=_parse_ts(closed_trade.get("opened_at")) if closed_trade.get("opened_at") else None,
                    closed_at=_parse_ts(closed_trade.get("closed_at")) if closed_trade.get("closed_at") else None,
                )
            )
    except Exception as exc:  # pragma: no cover - persistence is best effort
        logger.debug("Paper trade persistence skipped: %s", exc)


def _paper_trade_row(trade: dict[str, Any]) -> dict[str, Any]:
    symbol = trade.get("symbol")
    upper = str(symbol).upper() if symbol else ""
    option_side = "CE" if upper.endswith("CE") else "PE" if upper.endswith("PE") else None
    opened = _parse_ts(trade.get("opened_at"))
    closed = _parse_ts(trade.get("closed_at"))
    hold_minutes = (
        round((closed - opened).total_seconds() / 60.0, 1)
        if opened > _EPOCH and closed > _EPOCH
        else None
    )
    entry_price = _num(trade.get("entry_price"))
    exit_price = _num(trade.get("exit_price"))
    entry_charges = _num(trade.get("entry_charges")) or 0.0
    exit_charges = _num(trade.get("exit_charges")) or 0.0
    realized = _num(trade.get("realized_pnl")) or 0.0
    gross = realized + entry_charges + exit_charges
    qty = int(trade.get("qty") or 0)
    invested = (entry_price or 0) * qty
    pnl_pct = (realized / invested * 100.0) if invested else None
    return {
        "symbol": symbol,
        "option_side": option_side,
        "qty": qty,
        "entry_price": _round(entry_price),
        "exit_price": _round(exit_price),
        "entry_charges": _round(entry_charges),
        "exit_charges": _round(exit_charges),
        "gross_pnl": _round(gross),
        "realized_pnl": _round(realized),
        "pnl_pct": _round(pnl_pct),
        "entry_order_id": trade.get("entry_order_id"),
        "exit_order_id": trade.get("exit_order_id"),
        "signal_id": trade.get("signal_id"),
        "origin": trade.get("origin"),
        "exit_trigger": trade.get("exit_trigger"),
        "opened_at": trade.get("opened_at"),
        "closed_at": trade.get("closed_at"),
        "hold_minutes": hold_minutes,
        "result": "win" if realized > 0 else "loss" if realized < 0 else "flat",
    }


def _filter_trades_by_origin(trades: list[dict[str, Any]], trade_origin: str | None) -> list[dict[str, Any]]:
    """Reporting-only filter — never touches the stored trade/wallet ledger."""
    selection = (trade_origin or "all").lower()
    if selection == "automated":
        return [t for t in trades if t.get("origin") in AUTOMATED_ORIGINS]
    if selection == "manual":
        return [t for t in trades if t.get("origin") == "MANUAL"]
    return trades


def _build_paper_portfolio_analytics(trade_origin: str | None = None) -> dict[str, Any]:
    from app.services.paper_portfolio import get_paper_portfolio

    portfolio = get_paper_portfolio()
    all_trades = [_paper_trade_row(t) for t in portfolio.closed_trades]
    trades = _filter_trades_by_origin(all_trades, trade_origin)

    starting_balance = float(portfolio.session_start_balance or portfolio.starting_balance or 0.0)
    current_equity = round(float(portfolio.available_balance) + float(portfolio.utilized_amount), 2)

    wallet = {
        # Real account state — always the true totals, never filtered.
        "starting_balance": _round(starting_balance),
        "available_balance": _round(portfolio.available_balance),
        "utilized_amount": _round(portfolio.utilized_amount),
        "sod_limit": None,
        "realized_pnl": _round(portfolio.realized_pnl),
        "session_pnl": _round(portfolio.session_pnl),
        "equity": _round(current_equity),
        "funds_connected": True,
    }

    pct_base = starting_balance if starting_balance > 0 else (current_equity or 0.0)
    kpis = _compute_kpis(trades, pct_base)

    open_position = None
    open_trade = portfolio.open_trade
    if isinstance(open_trade, dict) and open_trade:
        symbol = open_trade.get("symbol")
        upper = str(symbol).upper() if symbol else ""
        open_position = {
            "symbol": symbol,
            "option_side": "CE" if upper.endswith("CE") else "PE" if upper.endswith("PE") else None,
            "qty": int(open_trade.get("qty") or 0),
            "entry_price": _round(_num(open_trade.get("entry_price"))),
            "opened_at": open_trade.get("opened_at"),
            "entry_order_id": open_trade.get("entry_order_id"),
        }

    return {
        "mode": "paper",
        "currency": "INR",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "funds_connected": True,
        "trade_origin": (trade_origin or "all").lower(),
        "wallet": wallet,
        "kpis": kpis,
        "equity_curve": _equity_curve(trades, starting_balance),
        "daily_pnl": _daily_pnl(trades),
        "side_breakdown": _side_breakdown(trades),
        "symbol_breakdown": _symbol_breakdown(trades),
        "open_position": open_position,
        "trades": list(reversed(trades)),
    }


def build_portfolio_analytics(
    persist: bool = True, mode: str | None = None, force: bool = False, trade_origin: str | None = None
) -> dict[str, Any]:
    if (mode or "live").lower() == "paper":
        return _build_paper_portfolio_analytics(trade_origin=trade_origin)

    # LIVE-only: paper trades and the resettable paper wallet are excluded.
    events = read_jsonl("order", limit=_ORDER_LOG_LIMIT)
    trades, open_entry = _pair_round_trips(events)

    # Real Dhan funds as the capital base.
    snap = _live_wallet_snapshot(force=force)
    available = _num(snap.get("available_balance"))
    utilized = _num(snap.get("utilized_amount"))
    sod_limit = _num(snap.get("sod_limit"))
    funds_connected = bool(snap.get("success")) if snap else False
    current_equity = None
    if available is not None or utilized is not None:
        current_equity = (available or 0.0) + (utilized or 0.0)

    realized_total = sum(float(t["realized_pnl"] or 0.0) for t in trades)

    balance_source = "current"
    last_known_at = None
    if not funds_connected:
        # Current fetch failed/stale/disconnected: fall back to the most recent
        # successful reading on record rather than showing nothing or 0.
        from app.services.execution_context import current_execution_user

        user = current_execution_user()
        last_known = _last_known_live_snapshot(user.id) if user is not None and not user.is_dev else None
        if last_known is not None:
            available = last_known["available_balance"]
            utilized = last_known["utilized_amount"]
            current_equity = last_known["equity"]
            balance_source = "last_known"
            last_known_at = last_known["captured_at"]
        else:
            balance_source = "none"

    # Infer the capital base before these trades so the equity curve ends at the
    # real account value. Fall back to start-of-day funds, then to 0.
    if current_equity is not None:
        starting_balance = current_equity - realized_total
    elif sod_limit is not None:
        starting_balance = sod_limit
    else:
        starting_balance = 0.0

    wallet = {
        "starting_balance": _round(starting_balance) if starting_balance else None,
        "available_balance": _round(available),
        "utilized_amount": _round(utilized),
        "sod_limit": _round(sod_limit),
        "realized_pnl": _round(realized_total),
        "session_pnl": _round(_num(snap.get("session_pnl"))),
        "equity": _round(current_equity),
        "funds_connected": funds_connected,
        "balance_source": balance_source,
        "last_known_at": last_known_at,
    }

    pct_base = starting_balance if starting_balance and starting_balance > 0 else (current_equity or 0.0)
    # kpis here is computed from the FULL trade set and is what gets persisted
    # below — the ground-truth equity-curve history must never be filter-dependent.
    kpis = _compute_kpis(trades, pct_base)
    display_trades = _filter_trades_by_origin(trades, trade_origin)
    display_kpis = _compute_kpis(display_trades, pct_base) if trade_origin and trade_origin != "all" else kpis

    open_position = None
    if open_entry is not None:
        open_position = {
            "symbol": open_entry.get("trading_symbol"),
            "option_side": _side(open_entry),
            "qty": _qty(open_entry),
            "entry_price": _round(_num(open_entry.get("avg_price"))),
            "opened_at": open_entry.get("timestamp"),
            "entry_order_id": open_entry.get("order_id"),
        }

    payload = {
        "mode": "live",
        "currency": "INR",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "funds_connected": funds_connected,
        "trade_origin": (trade_origin or "all").lower(),
        "wallet": wallet,
        "kpis": display_kpis,
        "equity_curve": _equity_curve(display_trades, starting_balance),
        "daily_pnl": _daily_pnl(display_trades),
        "side_breakdown": _side_breakdown(display_trades),
        "symbol_breakdown": _symbol_breakdown(display_trades),
        "open_position": open_position,
        "trades": list(reversed(display_trades)),
    }

    if persist:
        # Always persisted from the FULL, unfiltered trade set (see kpis above).
        _persist_best_effort(trades, wallet, kpis)

    return payload
