from __future__ import annotations

import pytest

from app.services.option_intent_mapper import (
    OPTION_SIDE_BLOCKED,
    OptionIntentMapper,
)


@pytest.mark.parametrize(
    ("action", "intent", "expected_side"),
    [
        ("ENTRY", "BULLISH", "CE"),
        ("ENTRY", "BEARISH", "PE"),
        ("EXIT", "FLAT", "NONE"),
        ("REVERSE", "BULLISH", "CE"),
        ("REVERSE", "BEARISH", "PE"),
        ("HEARTBEAT", "FLAT", "NONE"),
    ],
)
def test_option_intent_mapping_rules(action, intent, expected_side):
    result = OptionIntentMapper().map(action=action, intent=intent)

    assert result.ok is True
    assert result.option_side == expected_side


def test_ce_only_instance_blocks_bearish_pe_entry():
    result = OptionIntentMapper().map(
        action="ENTRY",
        intent="BEARISH",
        side_preference="CE",
    )

    assert result.ok is False
    assert result.error_code == OPTION_SIDE_BLOCKED
    assert result.option_side is None


def test_pe_only_instance_blocks_bullish_ce_entry():
    result = OptionIntentMapper().map(
        action="ENTRY",
        intent="BULLISH",
        side_preference="PE",
    )

    assert result.ok is False
    assert result.error_code == OPTION_SIDE_BLOCKED
    assert result.option_side is None


def test_both_side_preference_allows_ce_and_pe_entries():
    mapper = OptionIntentMapper()

    assert mapper.map(action="ENTRY", intent="BULLISH", side_preference="BOTH").option_side == "CE"
    assert mapper.map(action="ENTRY", intent="BEARISH", side_preference="BOTH").option_side == "PE"
