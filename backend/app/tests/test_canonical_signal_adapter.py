from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.domain.secret_taint import (
    GENERIC_SAFE_DETAIL,
    SecretTaintClass,
    build_safe_metadata,
    is_credential_shaped,
    project_safe_signal_id,
    redact,
    validate_safe_detail,
)
from app.domain.canonical_signal import (
    CanonicalEventType,
    CanonicalIntentReason,
    DesiredPositionState,
)
from app.domain.legacy_signal_adapter import adapt_legacy_action, canonical_to_normalized_signal
from app.services.private_webhook_service import PrivateWebhookPayload, build_normalized_signal


NOW = datetime(2026, 7, 18, 9, 30, tzinfo=timezone.utc)
AUTH = {"instance_id": "11111111-1111-1111-1111-111111111111"}
BACKEND_DIR = Path(__file__).resolve().parents[2]


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


def test_adapter_import_does_not_load_configuration():
    script = """
import sys
import app.domain.legacy_signal_adapter
assert "app.config" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_adapter_import_does_not_construct_settings():
    script = """
import sys
import types
constructed = False
module = types.ModuleType("app.config")
class Settings:
    def __init__(self):
        global constructed
        constructed = True
        raise AssertionError("Settings construction is forbidden")
module.Settings = Settings
sys.modules["app.config"] = module
import app.domain.legacy_signal_adapter
assert not constructed
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_existing_config_export_remains_compatible():
    from app.config import DEFAULT_EXCHANGE_SEGMENT

    assert DEFAULT_EXCHANGE_SEGMENT == "NSE_FNO"


def test_secret_taint_class_is_closed():
    assert {item.value for item in SecretTaintClass} == {
        "SERVER_TRUSTED",
        "USER_IDENTIFIER",
        "USER_METADATA",
        "SECRET",
        "DERIVED_SAFE",
    }


@pytest.mark.parametrize(
    "value",
    ["nwk_token123", "NWK_TOKEN123", "NwK_ToKeN123", "prefix nwk_token123 suffix"],
)
def test_credential_shaped_values_are_detected_case_insensitively(value):
    assert is_credential_shaped(value)


def test_request_local_known_secret_and_ordinary_text_classification():
    assert is_credential_shaped("opaque-value", {"opaque-value"})
    assert not is_credential_shaped("ordinary-text", {"different-secret"})


def test_credential_text_is_redacted_from_diagnostics_and_exceptions():
    secret = "nwk_never_return_this"
    diagnostic = redact(secret)
    detail = validate_safe_detail(secret)
    assert diagnostic == "[REDACTED]"
    assert secret not in diagnostic
    assert detail == GENERIC_SAFE_DETAIL
    try:
        raise ValueError(detail)
    except ValueError as exc:
        assert secret not in str(exc)


def test_safe_metadata_accepts_only_valid_closed_values():
    source = {
        "strategy_version": "legend-v3.1",
        "timeframe": "15m",
        "reference_price": 25000.25,
        "comment": "never copied",
        "signal_id": "never-copied",
        "quantity": 65,
        "nested": {"credential": "nwk_nested_secret"},
    }
    original = {
        "strategy_version": "legend-v3.1",
        "timeframe": "15m",
        "reference_price": 25000.25,
        "comment": "never copied",
        "signal_id": "never-copied",
        "quantity": 65,
        "nested": {"credential": "nwk_nested_secret"},
    }

    metadata = build_safe_metadata(source)

    assert dict(metadata) == {
        "strategy_version": "legend-v3.1",
        "timeframe": "15m",
        "reference_price": 25000.25,
    }
    assert source == original
    assert len(json.dumps(dict(metadata)).encode("utf-8")) <= 2 * 1024
    with pytest.raises(TypeError):
        metadata["timeframe"] = "1h"


@pytest.mark.parametrize(
    "source",
    [
        {"strategy_version": "x" * 41},
        {"strategy_version": "bad value"},
        {"strategy_version": "nwk_secret123"},
        {"timeframe": "x" * 13},
        {"timeframe": "bad value"},
        {"timeframe": "NWK_SECRET123"},
        {"reference_price": float("nan")},
        {"reference_price": float("inf")},
        {"reference_price": 0},
        {"reference_price": -1},
        {"reference_price": True},
    ],
)
def test_invalid_or_credential_shaped_metadata_values_are_omitted(source):
    assert not build_safe_metadata(source)


def test_safe_signal_id_projection_is_closed_and_secret_safe():
    assert project_safe_signal_id("signal-123") == "signal-123"
    assert project_safe_signal_id("nwk_signal_secret") == "credential-shaped-id"
    assert project_safe_signal_id("opaque-secret", {"opaque-secret"}) == "credential-shaped-id"
    assert project_safe_signal_id("line\nbreak") == "invalid-signal-id"


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("canonical-evidence-rejected", "canonical-evidence-rejected"),
        ("unknown-detail", GENERIC_SAFE_DETAIL),
        ("line\nbreak", GENERIC_SAFE_DETAIL),
        ("x" * 161, GENERIC_SAFE_DETAIL),
        ("nwk_secret123", GENERIC_SAFE_DETAIL),
    ],
)
def test_safe_detail_validation_is_closed(detail, expected):
    assert validate_safe_detail(detail) == expected
