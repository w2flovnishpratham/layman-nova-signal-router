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
from typing import Any

from sqlalchemy import select

from app.db import models
from app.db.engine import database_configured, session_scope
from app.services import strategy_risk


def _utilisation(used: int, limit: int) -> dict[str, Any]:
    """A limit of 0 means unlimited, so percentage is undefined."""
    if limit <= 0:
        return {"used": int(used), "limit": 0, "unlimited": True, "pct": None}
    pct = round(min(max((used / limit) * 100.0, 0.0), 100.0), 1)
    return {"used": int(used), "limit": int(limit), "unlimited": False, "pct": pct}


def build_risk_overview(user_id: uuid.UUID) -> dict[str, Any]:
    trade_date = strategy_risk.trade_date_ist()
    payload: dict[str, Any] = {
        "ok": True,
        "available": True,
        "trade_date_ist": trade_date.isoformat(),
        "scope": "strategy_fanout",
        "user": strategy_risk._public_user_control(None),
        "strategies": [],
    }

    if not database_configured():
        payload["available"] = False
        return payload

    with session_scope() as db:
        user_row = db.scalar(
            select(models.UserRiskControl).where(models.UserRiskControl.user_id == user_id)
        )
        payload["user"] = strategy_risk._public_user_control(user_row)

        override_names = set(
            db.scalars(
                select(models.UserStrategyRiskControl.strategy_name).where(
                    models.UserStrategyRiskControl.user_id == user_id
                )
            )
        )
        counters = {
            row.strategy_name: row
            for row in db.scalars(
                select(models.UserStrategyDailyRiskCounter).where(
                    models.UserStrategyDailyRiskCounter.user_id == user_id,
                    models.UserStrategyDailyRiskCounter.trade_date_ist == trade_date,
                )
            )
        }

    names = sorted(override_names | set(counters))
    strategies = []
    for name in names:
        # Delegate precedence to the enforcing code path.
        controls = strategy_risk.get_effective_controls(user_id, name)
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
    return payload
