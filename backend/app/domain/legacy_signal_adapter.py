"""Pure adapter from the frozen four-action wire contract to canonical intent."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.domain.canonical_signal import (
    CanonicalContractVersion,
    CanonicalEventType,
    CanonicalIntentReason,
    CanonicalSignalEvent,
    DesiredPositionState,
)
from app.domain.trading_constants import DEFAULT_EXCHANGE_SEGMENT
from app.schemas.signal import NormalizedSignal


LEGACY_ADAPTER_VERSION = "nova.legacy-signal-adapter.v1"

_LEGACY_ACTIONS = {
    "BUY_CE": (
        CanonicalEventType.STRATEGY_SIGNAL,
        DesiredPositionState.BULLISH,
        CanonicalIntentReason.DIRECTIONAL_SIGNAL,
    ),
    "BUY_PE": (
        CanonicalEventType.STRATEGY_SIGNAL,
        DesiredPositionState.BEARISH,
        CanonicalIntentReason.DIRECTIONAL_SIGNAL,
    ),
    "EXIT": (
        CanonicalEventType.STRATEGY_SIGNAL,
        DesiredPositionState.FLAT,
        CanonicalIntentReason.EXPLICIT_EXIT,
    ),
    "HOLD": (
        CanonicalEventType.CONNECTIVITY_TEST,
        DesiredPositionState.NONE,
        CanonicalIntentReason.CONNECTIVITY_TEST,
    ),
}


def adapt_legacy_action(
    action: str,
    *,
    signal_id: str,
    signal_time: datetime,
    strategy_version: str | None = None,
    timeframe: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> CanonicalSignalEvent:
    try:
        event_type, desired_state, reason = _LEGACY_ACTIONS[action]
    except KeyError:
        raise ValueError(f"Unsupported normalized legacy action: {action}") from None
    return CanonicalSignalEvent(
        contract_version=CanonicalContractVersion.V1,
        event_type=event_type,
        desired_state=desired_state,
        intent_reason=reason,
        signal_id=signal_id,
        signal_time=signal_time,
        strategy_version=strategy_version,
        timeframe=timeframe,
        metadata=metadata or {},
    )


def canonical_to_normalized_signal(
    event: CanonicalSignalEvent,
    *,
    strategy_instance_id: str,
    received_at: datetime,
) -> NormalizedSignal | None:
    if event.event_type is CanonicalEventType.CONNECTIVITY_TEST:
        return None
    mapping = {
        DesiredPositionState.BULLISH: ("BUY_CE", "ENTRY", "BUY", "CE"),
        DesiredPositionState.BEARISH: ("BUY_PE", "ENTRY", "BUY", "PE"),
        DesiredPositionState.FLAT: ("EXIT", "EXIT", "SELL", None),
    }
    try:
        wire_action, action, side, option_side = mapping[event.desired_state]
    except KeyError:
        raise ValueError(f"Unsupported canonical desired state: {event.desired_state}") from None
    raw_payload = {
        "private_instance_webhook": True,
        "strategy_instance_id": strategy_instance_id,
        "normalized_action": wire_action,
        "signal_id": event.signal_id,
        "signal_time": event.signal_time.astimezone(timezone.utc).isoformat(),
        "received_at": received_at.isoformat(),
        "strategy_version": event.strategy_version,
        "timeframe": event.timeframe,
        "reference_price": event.metadata.get("reference_price"),
        "comment": event.metadata.get("comment"),
    }
    return NormalizedSignal(
        payload_format="NOVA",
        secret="",
        signal_id=f"PWH-{strategy_instance_id}-{event.signal_id}",
        strategy_code=f"instance:{strategy_instance_id}",
        action=action,
        side=side,
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment=DEFAULT_EXCHANGE_SEGMENT,
        option_side=option_side,
        security_id=None,
        strike=None,
        expiry=None,
        qty=1,
        order_type="MARKET",
        product_type="INTRADAY",
        source="private_tradingview_webhook",
        raw_payload=raw_payload,
    )
