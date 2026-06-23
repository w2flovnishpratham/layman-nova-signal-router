"""Durable server-side risk controls for strategy fan-out jobs."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.db import crud, models
from app.db.engine import database_configured, get_engine, session_scope
from app.schemas.signal import NormalizedSignal


IST = ZoneInfo("Asia/Kolkata")
MONEY_QUANT = Decimal("0.01")
PRICE_KEYS = (
    "ltp",
    "price",
    "entry_price",
    "option_ltp",
    "optionLtp",
    "last_price",
    "lastPrice",
    "close",
)
PNL_KEYS = (
    "realized_pnl",
    "realizedPnl",
    "net_pnl",
    "netPnl",
    "paper_pnl",
    "paperPnl",
    "pnl",
)


@dataclass
class StrategyRiskDecision:
    allowed: bool
    reason: str
    code: str = "allowed"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "code": self.code,
            "metadata": self.metadata,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def trade_date_ist(now: datetime | None = None) -> date:
    value = now or _now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST).date()


def _canonical_strategy_name(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("_", "-")
    if normalized in {"tradingview-nifty-v1", "supertrend"}:
        return "supertrend"
    return normalized


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def money_to_paise(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        rupees = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return 0
    paise = (rupees * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(paise)


def paise_to_rupees(paise: int) -> str:
    value = (Decimal(int(paise)) / Decimal("100")).quantize(MONEY_QUANT)
    return format(value, "f")


def _extract_price(signal: NormalizedSignal) -> Decimal | None:
    raw_payload = signal.raw_payload if isinstance(signal.raw_payload, dict) else {}
    candidates: list[Any] = []
    for key in PRICE_KEYS:
        if key in raw_payload:
            candidates.append(raw_payload.get(key))
    for key in PRICE_KEYS:
        if hasattr(signal, key):
            candidates.append(getattr(signal, key))
    for candidate in candidates:
        if candidate in (None, ""):
            continue
        try:
            price = Decimal(str(candidate))
        except (InvalidOperation, ValueError):
            continue
        if price > 0:
            return price
    return None


def _notional_paise(signal: NormalizedSignal, qty: int) -> int | None:
    price = _extract_price(signal)
    if price is None:
        return None
    notional = (price * Decimal(max(int(qty), 0)) * Decimal("100")).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )
    return int(notional)


def _limit_value(strategy_value: int, user_value: int, default_value: int) -> int:
    if strategy_value > 0:
        return strategy_value
    if user_value > 0:
        return user_value
    return max(default_value, 0)


def _public_user_control(row: models.UserRiskControl | None) -> dict[str, Any]:
    if row is None:
        return {
            "kill_switch": False,
            "max_lots_per_order": 0,
            "max_notional_per_trade_paise": 0,
            "max_orders_per_day": 0,
            "max_loss_per_day_paise": 0,
        }
    return {
        "kill_switch": bool(row.kill_switch),
        "max_lots_per_order": int(row.max_lots_per_order or 0),
        "max_notional_per_trade_paise": int(row.max_notional_per_trade_paise or 0),
        "max_orders_per_day": int(row.max_orders_per_day or 0),
        "max_loss_per_day_paise": int(row.max_loss_per_day_paise or 0),
    }


def _public_strategy_control(row: models.UserStrategyRiskControl | None) -> dict[str, Any]:
    base = _public_user_control(None)
    if row is None:
        return {**base, "strategy_name": None}
    return {
        **base,
        "strategy_name": row.strategy_name,
        "kill_switch": bool(row.kill_switch),
        "max_lots_per_order": int(row.max_lots_per_order or 0),
        "max_notional_per_trade_paise": int(row.max_notional_per_trade_paise or 0),
        "max_orders_per_day": int(row.max_orders_per_day or 0),
        "max_loss_per_day_paise": int(row.max_loss_per_day_paise or 0),
    }


def get_effective_controls(user_id: uuid.UUID | str, strategy_name: str) -> dict[str, Any]:
    if not database_configured():
        return {
            "user": _public_user_control(None),
            "strategy": _public_strategy_control(None),
            "effective": {
                "kill_switch": False,
                "strategy_kill_switch": False,
                "max_lots_per_order": _positive_int(settings.STRATEGY_MAX_LOTS_PER_ORDER),
                "max_notional_per_trade_paise": _positive_int(settings.STRATEGY_MAX_NOTIONAL_PER_TRADE_PAISE),
                "max_orders_per_day": _positive_int(settings.STRATEGY_MAX_ORDERS_PER_DAY),
                "max_loss_per_day_paise": _positive_int(settings.STRATEGY_MAX_LOSS_PER_DAY_PAISE),
            },
        }
    user_uuid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    strategy = _canonical_strategy_name(strategy_name)
    with session_scope() as db:
        user_row = db.scalar(select(models.UserRiskControl).where(models.UserRiskControl.user_id == user_uuid))
        strategy_row = db.scalar(
            select(models.UserStrategyRiskControl).where(
                models.UserStrategyRiskControl.user_id == user_uuid,
                models.UserStrategyRiskControl.strategy_name == strategy,
            )
        )
        user_control = _public_user_control(user_row)
        strategy_control = _public_strategy_control(strategy_row)
        effective = {
            "kill_switch": bool(user_control["kill_switch"]),
            "strategy_kill_switch": bool(strategy_control["kill_switch"]),
            "max_lots_per_order": _limit_value(
                strategy_control["max_lots_per_order"],
                user_control["max_lots_per_order"],
                _positive_int(settings.STRATEGY_MAX_LOTS_PER_ORDER),
            ),
            "max_notional_per_trade_paise": _limit_value(
                strategy_control["max_notional_per_trade_paise"],
                user_control["max_notional_per_trade_paise"],
                _positive_int(settings.STRATEGY_MAX_NOTIONAL_PER_TRADE_PAISE),
            ),
            "max_orders_per_day": _limit_value(
                strategy_control["max_orders_per_day"],
                user_control["max_orders_per_day"],
                _positive_int(settings.STRATEGY_MAX_ORDERS_PER_DAY),
            ),
            "max_loss_per_day_paise": _limit_value(
                strategy_control["max_loss_per_day_paise"],
                user_control["max_loss_per_day_paise"],
                _positive_int(settings.STRATEGY_MAX_LOSS_PER_DAY_PAISE),
            ),
        }
    return {"user": user_control, "strategy": strategy_control, "effective": effective}


def set_user_risk_control(user_id: uuid.UUID | str, **changes: Any) -> dict[str, Any]:
    user_uuid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    now = _now()
    with session_scope() as db:
        row = db.scalar(select(models.UserRiskControl).where(models.UserRiskControl.user_id == user_uuid))
        if row is None:
            row = models.UserRiskControl(user_id=user_uuid, created_at=now, updated_at=now)
            db.add(row)
        _apply_control_changes(row, changes)
        row.updated_at = now
        db.flush()
        return _public_user_control(row)


def set_user_strategy_risk_control(user_id: uuid.UUID | str, strategy_name: str, **changes: Any) -> dict[str, Any]:
    user_uuid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    strategy = _canonical_strategy_name(strategy_name)
    if not strategy:
        raise ValueError("strategy_name is required.")
    now = _now()
    with session_scope() as db:
        row = db.scalar(
            select(models.UserStrategyRiskControl).where(
                models.UserStrategyRiskControl.user_id == user_uuid,
                models.UserStrategyRiskControl.strategy_name == strategy,
            )
        )
        if row is None:
            row = models.UserStrategyRiskControl(
                user_id=user_uuid,
                strategy_name=strategy,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        _apply_control_changes(row, changes)
        row.updated_at = now
        db.flush()
        return _public_strategy_control(row)


def _apply_control_changes(row: Any, changes: dict[str, Any]) -> None:
    if "kill_switch" in changes and changes["kill_switch"] is not None:
        row.kill_switch = bool(changes["kill_switch"])
    for field_name in (
        "max_lots_per_order",
        "max_notional_per_trade_paise",
        "max_orders_per_day",
        "max_loss_per_day_paise",
    ):
        if field_name in changes and changes[field_name] is not None:
            setattr(row, field_name, _positive_int(changes[field_name]))


def evaluate_and_reserve_order(
    *,
    user_id: uuid.UUID | str,
    strategy_name: str,
    lots: int,
    qty: int,
    signal: NormalizedSignal,
    execution_mode: str,
) -> StrategyRiskDecision:
    """Fail-closed risk check and durable daily order reservation."""
    if execution_mode == "signal_only" or not database_configured():
        return StrategyRiskDecision(True, "Risk gates skipped for signal-only mode.")

    user_uuid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    strategy = _canonical_strategy_name(strategy_name)
    controls = get_effective_controls(user_uuid, strategy)["effective"]
    metadata: dict[str, Any] = {
        "strategy_name": strategy,
        "execution_mode": execution_mode,
        "lots": int(lots),
        "qty": int(qty),
        "signal_id": signal.signal_id,
    }

    if controls["kill_switch"]:
        return _blocked(user_uuid, strategy, "per_user_kill_switch", "Trade blocked: per-user kill switch is active.", metadata)
    if controls["strategy_kill_switch"]:
        return _blocked(user_uuid, strategy, "per_strategy_kill_switch", "Trade blocked: per-strategy kill switch is active.", metadata)

    max_lots = int(controls["max_lots_per_order"])
    if max_lots > 0 and int(lots) > max_lots:
        return _blocked(
            user_uuid,
            strategy,
            "max_lots_per_order",
            f"Trade blocked: lots {lots} exceeds max_lots_per_order {max_lots}.",
            {**metadata, "max_lots_per_order": max_lots},
        )

    notional_paise: int | None = None
    max_notional = int(controls["max_notional_per_trade_paise"])
    if max_notional > 0:
        notional_paise = _notional_paise(signal, qty)
        if notional_paise is None:
            return _blocked(
                user_uuid,
                strategy,
                "notional_unavailable",
                "Trade blocked: max_notional_per_trade is configured but LTP is unavailable.",
                {**metadata, "max_notional_per_trade_paise": max_notional},
            )
        metadata["notional_paise"] = notional_paise
        if notional_paise > max_notional:
            return _blocked(
                user_uuid,
                strategy,
                "max_notional_per_trade",
                (
                    "Trade blocked: notional "
                    f"Rs.{paise_to_rupees(notional_paise)} exceeds max_notional_per_trade "
                    f"Rs.{paise_to_rupees(max_notional)}."
                ),
                {**metadata, "max_notional_per_trade_paise": max_notional},
            )

    today = trade_date_ist()
    max_orders = int(controls["max_orders_per_day"])
    max_loss = int(controls["max_loss_per_day_paise"])
    with session_scope() as db:
        statement = select(models.UserStrategyDailyRiskCounter).where(
            models.UserStrategyDailyRiskCounter.user_id == user_uuid,
            models.UserStrategyDailyRiskCounter.strategy_name == strategy,
            models.UserStrategyDailyRiskCounter.trade_date_ist == today,
        )
        if get_engine().dialect.name == "postgresql":
            statement = statement.with_for_update()
        counter = db.scalar(statement)
        if counter is None:
            counter = models.UserStrategyDailyRiskCounter(
                user_id=user_uuid,
                strategy_name=strategy,
                trade_date_ist=today,
                orders_count=0,
                notional_used_paise=0,
                realized_pnl_paise=0,
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(counter)
            db.flush()

        metadata.update(
            {
                "trade_date_ist": today.isoformat(),
                "orders_count": int(counter.orders_count or 0),
                "realized_pnl_paise": int(counter.realized_pnl_paise or 0),
            }
        )
        if max_orders > 0 and int(counter.orders_count or 0) >= max_orders:
            decision = StrategyRiskDecision(
                False,
                f"Trade blocked: max_orders_per_day {max_orders} reached.",
                "max_orders_per_day",
                {**metadata, "max_orders_per_day": max_orders},
            )
            _audit_risk_block(db, user_uuid, strategy, decision)
            return decision
        if max_loss > 0 and int(counter.realized_pnl_paise or 0) <= -max_loss:
            decision = StrategyRiskDecision(
                False,
                f"Trade blocked: max_loss_per_day Rs.{paise_to_rupees(max_loss)} reached.",
                "max_loss_per_day",
                {**metadata, "max_loss_per_day_paise": max_loss},
            )
            _audit_risk_block(db, user_uuid, strategy, decision)
            return decision

        counter.orders_count = int(counter.orders_count or 0) + 1
        counter.notional_used_paise = int(counter.notional_used_paise or 0) + int(notional_paise or 0)
        counter.updated_at = _now()
        crud.add_audit_log(
            db,
            user_id=user_uuid,
            action="STRATEGY_RISK_RESERVED",
            metadata={
                **metadata,
                "orders_count": counter.orders_count,
                "notional_used_paise": counter.notional_used_paise,
            },
        )

    return StrategyRiskDecision(True, "Server-side strategy risk checks passed.", metadata=metadata)


def _blocked(
    user_id: uuid.UUID,
    strategy_name: str,
    code: str,
    reason: str,
    metadata: dict[str, Any],
) -> StrategyRiskDecision:
    decision = StrategyRiskDecision(False, reason, code, metadata)
    with session_scope() as db:
        _audit_risk_block(db, user_id, strategy_name, decision)
    return decision


def _audit_risk_block(db, user_id: uuid.UUID, strategy_name: str, decision: StrategyRiskDecision) -> None:
    crud.add_audit_log(
        db,
        user_id=user_id,
        action="STRATEGY_RISK_BLOCKED",
        metadata={
            "strategy_name": strategy_name,
            "code": decision.code,
            "reason": decision.reason,
            **decision.metadata,
        },
    )


def record_strategy_risk_result(
    *,
    user_id: uuid.UUID | str,
    strategy_name: str,
    execution_result: dict[str, Any],
) -> None:
    pnl_paise = _extract_pnl_paise(execution_result)
    if pnl_paise is None or not database_configured():
        return
    user_uuid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    strategy = _canonical_strategy_name(strategy_name)
    today = trade_date_ist()
    with session_scope() as db:
        counter = db.scalar(
            select(models.UserStrategyDailyRiskCounter).where(
                models.UserStrategyDailyRiskCounter.user_id == user_uuid,
                models.UserStrategyDailyRiskCounter.strategy_name == strategy,
                models.UserStrategyDailyRiskCounter.trade_date_ist == today,
            )
        )
        if counter is None:
            counter = models.UserStrategyDailyRiskCounter(
                user_id=user_uuid,
                strategy_name=strategy,
                trade_date_ist=today,
                orders_count=0,
                notional_used_paise=0,
                realized_pnl_paise=0,
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(counter)
            db.flush()
        counter.realized_pnl_paise = int(counter.realized_pnl_paise or 0) + pnl_paise
        counter.updated_at = _now()


def _extract_pnl_paise(value: Any) -> int | None:
    if isinstance(value, dict):
        for key in PNL_KEYS:
            if key in value:
                return money_to_paise(value[key])
        for item in value.values():
            found = _extract_pnl_paise(item)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _extract_pnl_paise(item)
            if found is not None:
                return found
    return None
