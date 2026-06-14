from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.auth.db import session_scope
from app.auth.models import StrategySignal
from app.services.strategy_catalog_service import get_active_version, get_strategy_by_code
from app.services.trading_security import stable_json_hash


SENSITIVE_SIGNAL_KEYS = {
    "access_token",
    "access-token",
    "authorization",
    "cookie",
    "dhan_client_id",
    "secret",
    "token",
    "webhook_secret",
}


class StrategySignalError(ValueError):
    status_code = 400


class StrategySignalNotFound(StrategySignalError):
    status_code = 404


class DuplicateStrategySignal(StrategySignalError):
    status_code = 409


def accept_strategy_signal(payload: dict[str, Any]) -> StrategySignal:
    normalized = _normalized_signal_payload(payload)
    with session_scope() as session:
        strategy = get_strategy_by_code(session, normalized["strategy_code"])
        if strategy is None or not strategy.is_active:
            raise StrategySignalNotFound("Strategy not found or inactive.")
        if not strategy.is_paper_allowed:
            raise StrategySignalError("Strategy is not enabled for Paper signals.")
        version = get_active_version(session, int(strategy.id))
        if version is None:
            raise StrategySignalError("Strategy has no active version.")

        safe_payload = redact_signal_payload(payload)
        record = StrategySignal(
            strategy_id=int(strategy.id),
            strategy_version_id=int(version.id),
            strategy_code=strategy.strategy_code,
            signal_id=normalized["signal_id"],
            payload_hash=stable_json_hash(normalized | {"raw_payload": safe_payload}),
            symbol=normalized["symbol"],
            action=normalized["action"],
            option_type=normalized["option_type"],
            strike=normalized["strike"],
            expiry=normalized["expiry"],
            timeframe=normalized["timeframe"] or strategy.timeframe,
            price=normalized["price"],
            raw_payload_redacted_json=safe_payload,
            source=normalized["source"],
            status="received",
        )
        session.add(record)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            existing = session.exec(
                select(StrategySignal).where(
                    StrategySignal.strategy_id == strategy.id,
                    StrategySignal.strategy_version_id == version.id,
                    StrategySignal.signal_id == normalized["signal_id"],
                )
            ).first()
            if existing is None:
                raise
            raise DuplicateStrategySignal("Duplicate strategy signal_id was rejected.") from exc
        session.refresh(record)
        session.expunge(record)
        return record


def redact_signal_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if str(key).strip().lower() in SENSITIVE_SIGNAL_KEYS
                else redact_signal_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_signal_payload(item) for item in value]
    return value


def _normalized_signal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    strategy_code = str(payload.get("strategy_code") or "").strip().upper()
    signal_id = str(payload.get("signal_id") or "").strip()
    symbol = str(payload.get("symbol") or "").strip().upper()
    action = str(payload.get("action") or "").strip().upper()
    option_type = str(payload.get("option_type") or "").strip().upper() or None
    if not strategy_code or not signal_id or not symbol:
        raise StrategySignalError("strategy_code, signal_id, and symbol are required.")
    if action not in {"BUY", "SELL", "EXIT", "CLOSE"}:
        raise StrategySignalError("action must be BUY, SELL, EXIT, or CLOSE.")
    if option_type not in {None, "CE", "PE"}:
        raise StrategySignalError("option_type must be CE or PE.")
    return {
        "strategy_code": strategy_code,
        "signal_id": signal_id,
        "symbol": symbol,
        "action": action,
        "option_type": option_type,
        "strike": _optional_float(payload.get("strike")),
        "expiry": str(payload.get("expiry") or "").strip() or None,
        "timeframe": str(payload.get("timeframe") or "").strip() or None,
        "price": _optional_float(payload.get("price")),
        "source": str(payload.get("source") or "internal").strip().lower(),
    }


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise StrategySignalError("Numeric signal fields must contain valid numbers.") from exc
