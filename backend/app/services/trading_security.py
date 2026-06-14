from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.auth.db import session_scope
from app.auth.models import OrderRouteAttempt, WebhookNonceReceipt, WebhookSignalReceipt, utc_now_dt
from app.schemas.signal import NormalizedSignal


LOCAL_PRINCIPAL_ID = "__local_operator__"


@dataclass(frozen=True)
class ClaimResult:
    claimed: bool
    suspicious: bool
    record_id: int | None
    status: str
    message: str


def request_principal_id(user_id: str | None) -> str:
    return str(user_id or LOCAL_PRINCIPAL_ID)


def stable_json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def signal_payload_hash(signal: NormalizedSignal) -> str:
    def without_secrets(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): (
                    "[REDACTED]"
                    if str(key).strip().lower() in {"secret", "webhook_secret", "access_token", "authorization"}
                    else without_secrets(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [without_secrets(item) for item in value]
        return value

    return stable_json_hash(
        {
            "signal_id": signal.signal_id,
            "strategy_code": signal.strategy_code,
            "action": signal.action,
            "side": signal.side,
            "symbol": signal.symbol,
            "instrument_type": signal.instrument_type,
            "exchange_segment": signal.exchange_segment,
            "security_id": signal.security_id,
            "trading_symbol": signal.trading_symbol,
            "option_side": signal.option_side,
            "strike": signal.strike,
            "expiry": signal.expiry,
            "qty": signal.qty,
            "order_type": signal.order_type,
            "product_type": signal.product_type,
            "raw_payload": without_secrets(signal.raw_payload),
        }
    )


def claim_webhook_signal(user_id: str, signal: NormalizedSignal) -> ClaimResult:
    payload_hash = signal_payload_hash(signal)
    now = utc_now_dt()
    with session_scope() as session:
        receipt = WebhookSignalReceipt(
            user_id=user_id,
            signal_id=signal.signal_id,
            strategy_code=signal.strategy_code,
            payload_hash=payload_hash,
            first_seen_at=now,
            last_seen_at=now,
            status="received",
        )
        session.add(receipt)
        try:
            session.commit()
            session.refresh(receipt)
            return ClaimResult(True, False, receipt.id, receipt.status, "Signal receipt claimed.")
        except IntegrityError:
            session.rollback()
            existing = session.exec(
                select(WebhookSignalReceipt).where(
                    WebhookSignalReceipt.user_id == user_id,
                    WebhookSignalReceipt.signal_id == signal.signal_id,
                )
            ).first()
            if existing is None:
                raise
            suspicious = existing.payload_hash != payload_hash
            existing.last_seen_at = now
            if suspicious:
                existing.status = "suspicious_duplicate"
                existing.message = "Signal id was reused with a different payload."
            session.add(existing)
            session.commit()
            return ClaimResult(
                False,
                suspicious,
                existing.id,
                existing.status,
                existing.message or "Duplicate signal receipt.",
            )


def complete_webhook_signal(
    record_id: int | None,
    *,
    status: str,
    message: str | None = None,
    correlation_id: str | None = None,
) -> None:
    if record_id is None:
        return
    with session_scope() as session:
        receipt = session.get(WebhookSignalReceipt, record_id)
        if receipt is None:
            return
        receipt.status = status
        receipt.message = message
        receipt.correlation_id = correlation_id
        receipt.last_seen_at = utc_now_dt()
        session.add(receipt)
        session.commit()


def claim_webhook_nonce(user_id: str, nonce: str, request_timestamp: int) -> ClaimResult:
    now = utc_now_dt()
    with session_scope() as session:
        receipt = WebhookNonceReceipt(
            user_id=user_id,
            nonce=nonce,
            request_timestamp=request_timestamp,
            first_seen_at=now,
        )
        session.add(receipt)
        try:
            session.commit()
            session.refresh(receipt)
            return ClaimResult(True, False, receipt.id, "received", "Nonce claimed.")
        except IntegrityError:
            session.rollback()
            existing = session.exec(
                select(WebhookNonceReceipt).where(
                    WebhookNonceReceipt.user_id == user_id,
                    WebhookNonceReceipt.nonce == nonce,
                )
            ).first()
            return ClaimResult(
                False,
                False,
                existing.id if existing else None,
                "duplicate",
                "Nonce was already used.",
            )


def order_attempt_payload_hash(payload: dict[str, Any]) -> str:
    return stable_json_hash(payload)


def claim_order_route_attempt(
    *,
    user_id: str,
    correlation_id: str,
    signal: NormalizedSignal,
    instrument_identity: str,
    payload_hash: str,
) -> ClaimResult:
    now = utc_now_dt()
    with session_scope() as session:
        attempt = OrderRouteAttempt(
            user_id=user_id,
            correlation_id=correlation_id,
            signal_id=signal.signal_id,
            strategy_code=signal.strategy_code,
            action=signal.action,
            instrument_identity=instrument_identity,
            payload_hash=payload_hash,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        session.add(attempt)
        try:
            session.commit()
            session.refresh(attempt)
            return ClaimResult(True, False, attempt.id, attempt.status, "Order route attempt claimed.")
        except IntegrityError:
            session.rollback()
            existing = session.exec(
                select(OrderRouteAttempt).where(
                    OrderRouteAttempt.user_id == user_id,
                    OrderRouteAttempt.correlation_id == correlation_id,
                )
            ).first()
            if existing is None:
                raise
            suspicious = existing.payload_hash != payload_hash
            return ClaimResult(
                False,
                suspicious,
                existing.id,
                existing.status,
                (
                    "Correlation id was reused with a different order payload."
                    if suspicious
                    else "Order route attempt already exists."
                ),
            )


def complete_order_route_attempt(
    record_id: int | None,
    *,
    status: str,
    order_id: str | None = None,
    message: str | None = None,
) -> None:
    if record_id is None:
        return
    with session_scope() as session:
        attempt = session.get(OrderRouteAttempt, record_id)
        if attempt is None:
            return
        attempt.status = status
        attempt.order_id = order_id
        attempt.message = message
        attempt.updated_at = utc_now_dt()
        session.add(attempt)
        session.commit()
