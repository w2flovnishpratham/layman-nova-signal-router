"""Closed, immutable internal strategy-signal contract."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


class CanonicalEventType(StrEnum):
    STRATEGY_SIGNAL = "STRATEGY_SIGNAL"
    CONNECTIVITY_TEST = "CONNECTIVITY_TEST"


class DesiredPositionState(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    FLAT = "FLAT"
    NONE = "NONE"


class CanonicalIntentReason(StrEnum):
    DIRECTIONAL_SIGNAL = "DIRECTIONAL_SIGNAL"
    EXPLICIT_EXIT = "EXPLICIT_EXIT"
    CONNECTIVITY_TEST = "CONNECTIVITY_TEST"


class CanonicalRoutingResult(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    COMPATIBILITY_ENTRY = "COMPATIBILITY_ENTRY"
    COMPATIBILITY_EXIT = "COMPATIBILITY_EXIT"
    CONNECTIVITY_NO_JOB = "CONNECTIVITY_NO_JOB"


class CanonicalContractVersion(StrEnum):
    V1 = "nova.canonical-signal.v1"


_FORBIDDEN_METADATA_KEYS = {
    "event_type",
    "desired_state",
    "intent_reason",
    "reason",
    "user",
    "user_id",
    "strategy_instance",
    "strategy_instance_id",
    "broker",
    "broker_account_id",
    "execution_mode",
    "lots",
    "quantity",
    "qty",
    "option_side",
    "strike",
    "expiry",
    "security_id",
    "order_type",
    "sl",
    "stop_loss",
    "tp",
    "take_profit",
    "credential",
    "secret",
    "token",
}


def _freeze_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in deepcopy(dict(value)).items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_METADATA_KEYS:
                raise ValueError(f"Canonical metadata key is forbidden: {key}")
            frozen[str(key)] = _freeze_metadata(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_metadata(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_metadata(item) for item in value)
    if isinstance(value, str) and value.startswith("nwk_"):
        raise ValueError("Credential-like plaintext is forbidden in canonical metadata.")
    return value


@dataclass(frozen=True, slots=True)
class CanonicalSignalEvent:
    contract_version: CanonicalContractVersion
    event_type: CanonicalEventType
    desired_state: DesiredPositionState
    intent_reason: CanonicalIntentReason
    signal_id: str
    signal_time: datetime
    strategy_version: str | None = None
    timeframe: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not self.signal_id or not self.signal_id.strip():
            raise ValueError("signal_id is required.")
        if self.signal_time.tzinfo is None:
            raise ValueError("signal_time must be timezone-aware.")
        if self.event_type is CanonicalEventType.CONNECTIVITY_TEST:
            if self.desired_state is not DesiredPositionState.NONE:
                raise ValueError("Connectivity events must have desired_state=NONE.")
            if self.intent_reason is not CanonicalIntentReason.CONNECTIVITY_TEST:
                raise ValueError("Connectivity events must use CONNECTIVITY_TEST reason.")
        elif self.desired_state is DesiredPositionState.NONE:
            raise ValueError("Strategy signals cannot have desired_state=NONE.")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))
