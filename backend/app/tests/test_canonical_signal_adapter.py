from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from app.domain.canonical_signal import (
    CanonicalEventType,
    CanonicalIntentReason,
    DesiredPositionState,
)
from app.domain.legacy_signal_adapter import adapt_legacy_action, canonical_to_normalized_signal
from app.services.private_webhook_service import PrivateWebhookPayload, build_normalized_signal


NOW = datetime(2026, 7, 18, 9, 30, tzinfo=timezone.utc)
AUTH = {"instance_id": "11111111-1111-1111-1111-111111111111"}


def _event(action="BUY_CE", metadata=None):
    return adapt_legacy_action(
        action,
        signal_id="signal-1",
        signal_time=NOW,
        strategy_version="v1",
        timeframe="5",
        metadata=metadata or {"reference_price": 25000.0, "comment": "safe"},
    )


@pytest.mark.parametrize(
    ("action", "event_type", "state", "reason"),
    [
        ("BUY_CE", CanonicalEventType.STRATEGY_SIGNAL, DesiredPositionState.BULLISH, CanonicalIntentReason.DIRECTIONAL_SIGNAL),
        ("BUY_PE", CanonicalEventType.STRATEGY_SIGNAL, DesiredPositionState.BEARISH, CanonicalIntentReason.DIRECTIONAL_SIGNAL),
        ("EXIT", CanonicalEventType.STRATEGY_SIGNAL, DesiredPositionState.FLAT, CanonicalIntentReason.EXPLICIT_EXIT),
        ("HOLD", CanonicalEventType.CONNECTIVITY_TEST, DesiredPositionState.NONE, CanonicalIntentReason.CONNECTIVITY_TEST),
    ],
)
def test_closed_legacy_mapping(action, event_type, state, reason):
    event = _event(action)
    assert (event.event_type, event.desired_state, event.intent_reason) == (event_type, state, reason)


@pytest.mark.parametrize("action", ["buy_ce", " BUY_CE", "LONG", "WAIT", ""])
def test_unknown_or_unnormalized_actions_fail_closed(action):
    with pytest.raises(ValueError):
        _event(action)


@pytest.mark.parametrize(
    "metadata",
    [
        {"event_type": "CONNECTIVITY_TEST"},
        {"desired_state": "FLAT"},
        {"quantity": 50},
        {"option_side": "PE"},
        {"execution_mode": "real_orders"},
        {"nested": {"credential": "secret"}},
        {"comment": "nwk_plaintext_should_not_survive"},
    ],
)
def test_metadata_cannot_override_authority_or_contain_credential(metadata):
    with pytest.raises(ValueError):
        _event(metadata=metadata)


def test_canonical_event_and_nested_metadata_are_immutable_and_have_no_credential_field():
    event = _event(metadata={"safe": {"values": [1, 2]}})
    with pytest.raises(FrozenInstanceError):
        event.signal_id = "changed"
    with pytest.raises(TypeError):
        event.metadata["safe"] = {}
    with pytest.raises(TypeError):
        event.metadata["safe"]["values"] = ()
    assert "credential" not in event.__dataclass_fields__
    assert "nwk_" not in repr(event)


@pytest.mark.parametrize("action", ["BUY_CE", "BUY_PE", "EXIT"])
def test_compatibility_signal_is_field_equivalent_to_authoritative_mapping(action):
    payload = PrivateWebhookPayload(
        action=action,
        signal_id="signal-1",
        signal_time=NOW,
        strategy_version="v1",
        timeframe="5",
        reference_price=25000.0,
        comment="safe",
    )
    event = _event(action)
    compatibility = canonical_to_normalized_signal(
        event,
        strategy_instance_id=AUTH["instance_id"],
        received_at=NOW,
    )
    authoritative = build_normalized_signal(AUTH, payload, action, received_at=NOW)
    assert compatibility is not None
    assert compatibility.model_dump(mode="json") == authoritative.model_dump(mode="json")


def test_hold_has_no_compatibility_signal():
    assert canonical_to_normalized_signal(
        _event("HOLD"),
        strategy_instance_id=AUTH["instance_id"],
        received_at=NOW,
    ) is None
