"""Portfolio analytics for the user dashboard — LIVE money only.

This dashboard intentionally ignores paper trading. The paper wallet can be
reset with a fresh virtual balance at any time, so its P&L history is not a
meaningful record. Live trades are real money: persistent and worth tracking.

We reconstruct the round-trips NOVA actually executed with real orders by
pairing filled ENTRY/EXIT legs from the durable order event log, and we use the
real Dhan fund snapshot (never the paper wallet) as the capital base. Everything
is derived server-side so the frontend just renders.

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
from app.services.state_store import get_engine_mode

logger = logging.getLogger("nova_signal_router.portfolio")

_IST = ZoneInfo("Asia/Kolkata")
_ORDER_LOG_LIMIT = 5000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_LEGACY_POSITION_GROUP = "__legacy__"
_STRATEGY_METADATA_FIELDS = (
    "strategy_code",
    "instance_id",
    "strategy_version_id",
    "execution_mode",
    "v2_job_id",
    "source_signal_id",
)


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


def _position_group_key(ev: dict) -> str:
    instance_id = str(ev.get("instance_id") or "").strip()
    return instance_id or _LEGACY_POSITION_GROUP


def _strategy_metadata(entry: dict, exit_event: dict | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    exit_event = exit_event or {}
    for key in _STRATEGY_METADATA_FIELDS:
        value = exit_event.get(key)
        if value in (None, ""):
            value = entry.get(key)
        if value not in (None, ""):
            metadata[key] = value
    return metadata


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
    """Walk filled LIVE order legs chronologically and pair ENTRY/EXIT legs.

    Legacy records without instance_id continue to use the single global stream.
    Instance-aware records are paired only inside their own instance bucket, so
    an EXIT for one strategy instance cannot close another instance's ENTRY.
    """
    legs = [ev for ev in events if _is_filled(ev) and _is_live(ev)]
    legs.sort(key=lambda ev: _parse_ts(ev.get("timestamp")))
    legs = _dedupe_legs_by_order_id(legs)

    trades: list[dict[str, Any]] = []
    open_entries: dict[str, dict] = {}

    for ev in legs:
        action = (_action(ev) or "").upper()
        group_key = _position_group_key(ev)
        if action == "ENTRY":
            open_entries[group_key] = ev
        elif action == "EXIT" and group_key in open_entries:
            entry = open_entries.pop(group_key)
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
                    "opened_at": entry.get("timestamp"),
                    "closed_at": ev.get("timestamp"),
                    "hold_minutes": _round(hold_minutes, 1),
                    "result": "win" if realized > 0 else "loss" if realized < 0 else "flat",
                    **_strategy_metadata(entry, ev),
                }
            )

    open_entry = open_entries.get(_LEGACY_POSITION_GROUP)
    if open_entry is None and open_entries:
        open_entry = min(open_entries.values(), key=lambda ev: _parse_ts(ev.get("timestamp")))
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


def _live_wallet_snapshot() -> dict[str, Any]:
    """Real Dhan funds only — never the resettable paper wallet."""
    try:
        from app.services.state_store import get_wallet_snapshot
        from app.services.wallet_service import refresh_wallet_snapshot

        if (get_engine_mode(legacy_fallback=False) or "") == "live":
            snap = refresh_wallet_snapshot(force=False, log_event=False)
        else:
            # Not live right now: use the last real funds snapshot on record.
            snap = get_wallet_snapshot()
        return snap if isinstance(snap, dict) else {}
    except Exception:
        return {}


def _persist_best_effort(trades: list[dict[str, Any]], wallet: dict[str, Any], kpis: dict[str, Any]) -> None:
    """Upsert closed live trades + an equity snapshot for the bound user. Never raises."""
    try:
        from app.db.engine import database_configured, session_scope
        from app.db.models import PortfolioSnapshot, PortfolioTrade
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
                        opened_at=_parse_ts(t.get("opened_at")) if t.get("opened_at") else None,
                        closed_at=_parse_ts(t.get("closed_at")) if t.get("closed_at") else None,
                    )
                )
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
                )
            )
    except Exception as exc:  # pragma: no cover - persistence is best effort
        logger.debug("Portfolio persistence skipped: %s", exc)


def build_portfolio_analytics(persist: bool = True) -> dict[str, Any]:
    # LIVE-only: paper trades and the resettable paper wallet are excluded.
    events = read_jsonl("order", limit=_ORDER_LOG_LIMIT)
    trades, open_entry = _pair_round_trips(events)

    # Real Dhan funds as the capital base.
    snap = _live_wallet_snapshot()
    available = _num(snap.get("available_balance"))
    utilized = _num(snap.get("utilized_amount"))
    sod_limit = _num(snap.get("sod_limit"))
    funds_connected = bool(snap.get("success")) if snap else False
    current_equity = None
    if available is not None or utilized is not None:
        current_equity = (available or 0.0) + (utilized or 0.0)

    realized_total = sum(float(t["realized_pnl"] or 0.0) for t in trades)

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
    }

    pct_base = starting_balance if starting_balance and starting_balance > 0 else (current_equity or 0.0)
    kpis = _compute_kpis(trades, pct_base)

    open_position = None
    if open_entry is not None:
        open_position = {
            "symbol": open_entry.get("trading_symbol"),
            "option_side": _side(open_entry),
            "qty": _qty(open_entry),
            "entry_price": _round(_num(open_entry.get("avg_price"))),
            "opened_at": open_entry.get("timestamp"),
            "entry_order_id": open_entry.get("order_id"),
            **_strategy_metadata(open_entry),
        }

    payload = {
        "mode": "live",
        "currency": "INR",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "funds_connected": funds_connected,
        "wallet": wallet,
        "kpis": kpis,
        "equity_curve": _equity_curve(trades, starting_balance),
        "daily_pnl": _daily_pnl(trades),
        "side_breakdown": _side_breakdown(trades),
        "symbol_breakdown": _symbol_breakdown(trades),
        "open_position": open_position,
        "trades": list(reversed(trades)),
    }

    if persist:
        _persist_best_effort(trades, wallet, kpis)

    return payload
