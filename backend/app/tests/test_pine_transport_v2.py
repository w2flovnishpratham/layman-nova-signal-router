"""Frozen Transport V2 HOLD-isolation and registration checks."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.services import pine_conversion_service as conversion
from app.services import pine_validation


ROOT = Path(__file__).resolve().parents[3]
V1 = ROOT / "backend/app/prompts/pine_transport_v1.txt"
V2 = ROOT / "backend/app/prompts/pine_transport_v2.txt"
V1_SHA256 = "b72f2efcf839e693c83773e40c2324009065ded7a2ddfcbdb31a1f110efdc611"
V2_SHA256 = "18a3247c93c0c17e2bb70847a635c721bacf6e231d8d14c14db7871da56ef96f"


def _dispatch(*, ready=True, hold=False, hold_sent=False, exit=False, bullish=False, bearish=False):
    """Deterministic model of the registered V2 alert branch."""
    if not hold:
        hold_sent = False
    actions = []
    if ready:
        if hold:
            if not hold_sent:
                actions.append("HOLD")
                hold_sent = True
        elif exit:
            actions.append("EXIT")
        elif bullish:
            actions.append("BUY_CE")
        elif bearish:
            actions.append("BUY_PE")
    return actions, hold_sent


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        ({}, ["HOLD"]),
        ({"bullish": True}, ["HOLD"]),
        ({"bearish": True}, ["HOLD"]),
        ({"exit": True}, ["HOLD"]),
        ({"bullish": True, "bearish": True, "exit": True}, ["HOLD"]),
    ],
)
def test_hold_mode_emits_only_one_hold(signals, expected):
    actions, sent = _dispatch(hold=True, **signals)
    assert actions == expected and sent is True
    actions, sent = _dispatch(hold=True, hold_sent=sent, **signals)
    assert actions == [] and sent is True


def test_disable_then_reenable_allows_one_new_hold():
    assert _dispatch(hold=True) == (["HOLD"], True)
    assert _dispatch(hold=False, hold_sent=True) == ([], False)
    assert _dispatch(hold=True, hold_sent=False) == (["HOLD"], True)


@pytest.mark.parametrize(
    ("signal", "action"),
    [("bullish", "BUY_CE"), ("bearish", "BUY_PE"), ("exit", "EXIT")],
)
def test_hold_off_preserves_normal_priority(signal, action):
    actions, sent = _dispatch(hold=False, **{signal: True})
    assert actions == [action] and sent is False


def test_placeholder_or_unconfirmed_state_suppresses_hold_and_actions():
    assert _dispatch(ready=False, hold=True, bullish=True, bearish=True, exit=True) == ([], False)
    assert _dispatch(ready=False, hold=False, bullish=True, bearish=True, exit=True) == ([], False)


def test_transport_files_are_independent_registered_artifacts():
    assert hashlib.sha256(V1.read_bytes()).hexdigest() == conversion.TRANSPORT_V1_SHA256 == V1_SHA256
    assert hashlib.sha256(V2.read_bytes()).hexdigest() == conversion.TRANSPORT_V2_SHA256 == V2_SHA256
    assert conversion.QUALIFICATION_PACKAGES["v3"][1:] == (
        "pine_transport_v1", conversion.TRANSPORT_PATH, V1_SHA256,
    )
    assert conversion.QUALIFICATION_PACKAGES["v3.1"][1:] == (
        "pine_transport_v2", conversion.TRANSPORT_V2_PATH, V2_SHA256,
    )
    assert pine_validation.FROZEN_TRANSPORTS == {
        "pine_transport_v1": V1,
        "pine_transport_v2": V2,
    }


def test_v2_template_has_one_ready_gated_exclusive_alert_branch():
    text = V2.read_text(encoding="utf-8")
    assert text.count("if novaAlertReady") == 1
    assert "if novaAlertReady\n    if novaSendHoldTest\n        if not novaHoldSent" in text
    assert "    else\n        if novaExitSignal" in text
    assert text.count('alert(novaWebhookPayload("HOLD")') == 1
    assert text.count('alert(novaWebhookPayload("BUY_CE")') == 1
    assert text.count('alert(novaWebhookPayload("BUY_PE")') == 1
    assert text.count('alert(novaWebhookPayload("EXIT")') == 1
