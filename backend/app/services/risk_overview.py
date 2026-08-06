"""Owner-scoped read model for the Risk page.

These are the **strategy fan-out** risk gates enforced server-side before an
order intent is accepted (``UserRiskControl`` / ``UserStrategyRiskControl``), and
the durable IST-day counters (``UserStrategyDailyRiskCounter``). They are a
different subsystem from the engine's runtime settings, so the page must not
present them as one and the same.

Precedence (strategy override > user > env default) is NOT reimplemented here -
it is delegated to ``strategy_risk.get_effective_controls`` so the numbers shown
can never drift from the numbers actually enforced.

Truthfulness rules baked in:
* A limit of ``0`` means **no limit**, not "zero allowed". Utilisation is then
  ``None`` and ``unlimited`` is true - never 0% or 100%.
* Loss utilisation only counts *negative* realised P&L; a profitable day uses
  none of the daily loss budget.
* Money is stored and returned in **paise**.

Read-only. No writes, no migration.
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db import models
from app.db.engine import database_configured, readonly_session_scope
from app.services import risk_configuration, strategy_risk

IST = ZoneInfo("Asia/Kolkata")


def _utilisation(used: int, limit: int) -> dict[str, Any]:
    """A limit of 0 means unlimited, so percentage is undefined."""
    if limit <= 0:
        return {"used": int(used), "limit": 0, "unlimited": True, "pct": None}
    pct = round(min(max((used / limit) * 100.0, 0.0), 100.0), 1)
    return {"used": int(used), "limit": int(limit), "unlimited": False, "pct": pct}


def _fetch_user_risk_control(user_id: uuid.UUID):
    with readonly_session_scope() as db:
        return db.scalar(
            select(models.UserRiskControl).where(models.UserRiskControl.user_id == user_id)
        )


def _fetch_override_names(user_id: uuid.UUID) -> set[str]:
    with readonly_session_scope() as db:
        return set(
            db.scalars(
                select(models.UserStrategyRiskControl.strategy_name).where(
                    models.UserStrategyRiskControl.user_id == user_id
                )
            )
        )


def _fetch_counters(user_id: uuid.UUID, trade_date) -> dict[str, Any]:
    with readonly_session_scope() as db:
        return {
            row.strategy_name: row
            for row in db.scalars(
                select(models.UserStrategyDailyRiskCounter).where(
                    models.UserStrategyDailyRiskCounter.user_id == user_id,
                    models.UserStrategyDailyRiskCounter.trade_date_ist == trade_date,
                )
            )
        }


def _fetch_trades(user_id: uuid.UUID, normalized_mode: str, begin, finish) -> list[Any]:
    with readonly_session_scope() as db:
        return list(
            db.scalars(
                select(models.PortfolioTrade).where(
                    models.PortfolioTrade.user_id == user_id,
                    models.PortfolioTrade.mode == normalized_mode,
                    models.PortfolioTrade.closed_at >= begin,
                    models.PortfolioTrade.closed_at < finish,
                )
            )
        )


def _fetch_open_positions(user_id: uuid.UUID, normalized_mode: str) -> list[Any]:
    with readonly_session_scope() as db:
        return list(
            db.scalars(
                select(models.StrategyInstancePosition).where(
                    models.StrategyInstancePosition.user_id == user_id,
                    models.StrategyInstancePosition.execution_mode == normalized_mode,
                    models.StrategyInstancePosition.position_state.in_(models.ACTIVE_POSITION_STATES),
                )
            )
        )


def _fetch_audit_rows(user_id: uuid.UUID) -> list[Any]:
    with readonly_session_scope() as db:
        return list(
            db.scalars(
                select(models.AuditLog)
                .where(
                    models.AuditLog.user_id == user_id,
                    models.AuditLog.action.in_(
                        ["RISK_BREAKER_TRIGGERED", "RISK_KILL_SWITCH", "RISK_CONFIGURATION_SAVED"]
                    ),
                )
                .order_by(models.AuditLog.created_at.desc())
                .limit(8)
            )
        )


def build_risk_overview(user_id: uuid.UUID, mode: str = "paper") -> dict[str, Any]:
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"paper", "live"}:
        raise ValueError("mode must be paper or live")
    trade_date = strategy_risk.trade_date_ist()
    active = risk_configuration.configuration(user_id, normalized_mode)
    active_values = active["values"]
    payload: dict[str, Any] = {
        "ok": True,
        "available": True,
        "trade_date_ist": trade_date.isoformat(),
        "scope": "strategy_fanout",
        "mode": normalized_mode,
        "configuration": active,
        "user": strategy_risk._public_user_control(None),
        "strategies": [],
        "today_usage": {
            "daily_loss": _utilisation(0, int(float(active_values["daily_loss_cap"]) * 100)),
            "trades": _utilisation(0, int(active_values["max_trades_per_day"])),
            "open_positions": _utilisation(0, int(active_values["max_open_positions"])),
            "margin_exposure": _utilisation(
                0,
                int(float(active_values["margin_exposure_cap"] or 0) * 100),
            ),
        },
        "breaker_history": [],
    }

    if not database_configured():
        payload["available"] = False
        return payload

    begin = datetime.combine(trade_date, time.min, tzinfo=IST).astimezone(timezone.utc)
    finish = begin + timedelta(days=1)

    # Six independent, read-only, single-table lookups -- none needs another's
    # result. Sequentially this was 6 separate Neon round-trips; run them
    # concurrently instead. Each gets its own session (SQLAlchemy sessions
    # aren't thread-safe to share across threads).
    with ThreadPoolExecutor(max_workers=6) as pool:
        user_row_future = pool.submit(_fetch_user_risk_control, user_id)
        override_names_future = pool.submit(_fetch_override_names, user_id)
        counters_future = pool.submit(_fetch_counters, user_id, trade_date)
        trades_future = pool.submit(_fetch_trades, user_id, normalized_mode, begin, finish)
        open_positions_future = pool.submit(_fetch_open_positions, user_id, normalized_mode)
        audit_rows_future = pool.submit(_fetch_audit_rows, user_id)

        payload["user"] = strategy_risk._public_user_control(user_row_future.result())
        override_names = override_names_future.result()
        counters = counters_future.result()
        trades = trades_future.result()
        open_positions = open_positions_future.result()
        audit_rows = audit_rows_future.result()

    names = sorted(override_names | set(counters))
    # Each get_effective_controls call is independent (a different strategy
    # name) and read-only -- same concurrency treatment as above.
    with ThreadPoolExecutor(max_workers=max(len(names), 1)) as pool:
        controls_by_name = dict(
            zip(names, pool.map(lambda name: strategy_risk.get_effective_controls(user_id, name), names))
        )
    strategies = []
    for name in names:
        controls = controls_by_name[name]
        effective = controls["effective"]
        counter = counters.get(name)
        orders_used = int(counter.orders_count) if counter else 0
        notional_used = int(counter.notional_used_paise) if counter else 0
        realized = int(counter.realized_pnl_paise) if counter else 0
        # Only a negative realised P&L consumes the daily loss budget.
        loss_used = -realized if realized < 0 else 0

        strategies.append({
            "strategy_name": name,
            "kill_switch": bool(effective.get("strategy_kill_switch")),
            "effective": effective,
            "usage": {
                "orders_count": orders_used,
                "notional_used_paise": notional_used,
                "realized_pnl_paise": realized,
                "loss_used_paise": loss_used,
            },
            "utilisation": {
                "orders": _utilisation(orders_used, int(effective.get("max_orders_per_day") or 0)),
                "notional": _utilisation(notional_used, int(effective.get("max_notional_per_trade_paise") or 0)),
                "loss": _utilisation(loss_used, int(effective.get("max_loss_per_day_paise") or 0)),
            },
        })

    payload["strategies"] = strategies
    loss_used_paise = int(round(max(0.0, -sum(float(row.realized_pnl or 0) for row in trades)) * 100))
    margin_used_paise = 0
    for position in open_positions:
        snapshot = position.raw_snapshot if isinstance(position.raw_snapshot, dict) else {}
        margin_used_paise += int(
            round(float(snapshot.get("entry_price") or 0) * int(snapshot.get("qty") or 0) * 100)
        )
    payload["today_usage"] = {
        "daily_loss": _utilisation(loss_used_paise, int(float(active_values["daily_loss_cap"]) * 100)),
        "trades": _utilisation(
            len(trades) + len(open_positions),
            int(active_values["max_trades_per_day"]),
        ),
        "open_positions": _utilisation(
            len(open_positions),
            int(active_values["max_open_positions"]),
        ),
        "margin_exposure": _utilisation(
            margin_used_paise,
            int(float(active_values["margin_exposure_cap"] or 0) * 100),
        ),
    }
    payload["breaker_history"] = [
        {
            "timestamp": row.created_at.isoformat(),
            "event": (row.audit_metadata or {}).get("message")
            or row.action.replace("_", " ").title(),
            "mode": (row.audit_metadata or {}).get("mode", normalized_mode).upper(),
            "engine": (row.audit_metadata or {}).get("engine"),
            "trigger_type": (row.audit_metadata or {}).get("trigger_type", row.action),
            "outcome": (row.audit_metadata or {}).get("outcome", "Recorded"),
        }
        for row in audit_rows
        if (row.audit_metadata or {}).get("mode", normalized_mode) == normalized_mode
    ]
    return payload
