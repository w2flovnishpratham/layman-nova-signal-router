from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.pine_capabilities import CapabilityLevel, TemporalClass
from app.services.pine_semantic_preanalyzer import AnalysisConfidence, analyze_source


FIXTURES = Path(__file__).parent / "pine_fixtures" / "r1a"


def test_every_r1a_fixture_matches_its_expected_classification():
    pine_files = sorted(FIXTURES.glob("*.pine"))
    assert len(pine_files) == 28
    for pine_path in pine_files:
        expected = json.loads(pine_path.with_suffix(".expected.json").read_text(encoding="utf-8"))
        result = analyze_source(pine_path.read_text(encoding="utf-8"))
        assert set(expected["required_capabilities"]) <= set(result.matched_capabilities), pine_path.name
        assert result.effective_capability_level.value == expected["effective_capability_level"], pine_path.name
        assert result.confidence.value == expected["confidence"], pine_path.name
        assert set(expected.get("temporal_classes", [])) <= {item.value for item in result.temporal_classes}, pine_path.name
        assert set(expected.get("blocker_codes", [])) <= set(result.blocker_codes), pine_path.name
        assert set(expected.get("disclosure_codes", [])) <= set(result.disclosure_codes), pine_path.name
        assert result.registry_sha256 and result.source_sha256


def test_comments_strings_and_similar_variable_names_do_not_fake_api_detection():
    source = '''//@version=6
indicator("negative")
// strategy.entry("Long", strategy.long)
message = "strategy.exit('x', stop=1)"
strategy_entry_count = 1
plot(strategy_entry_count)
'''
    result = analyze_source(source)
    assert "MARKET_DIRECTIONAL_ENTRY" not in result.matched_capabilities
    assert "BACKEND_MANAGED_BRACKET" not in result.matched_capabilities


def test_safe_htf_offset_is_t3_not_t4_and_unsafe_is_t4():
    safe = analyze_source('''//@version=6
indicator("safe")
x = request.security(syminfo.tickerid, "60", close[1], lookahead=barmerge.lookahead_on)
''')
    unsafe = analyze_source('''//@version=6
indicator("unsafe")
x = request.security(syminfo.tickerid, "60", close, lookahead=barmerge.lookahead_on)
''')
    assert TemporalClass.T3_HTF_CONFIRMED_OFFSET_SERIES in safe.temporal_classes
    assert TemporalClass.T4_FUTURE_LEAKAGE_LOOKAHEAD not in safe.temporal_classes
    assert TemporalClass.T4_FUTURE_LEAKAGE_LOOKAHEAD in unsafe.temporal_classes
    assert unsafe.effective_capability_level is CapabilityLevel.L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE


def test_ambiguous_dynamic_request_is_indeterminate():
    result = analyze_source('''//@version=6
indicator("dynamic")
symbol = input.symbol("NSE:NIFTY")
tf = input.timeframe("60")
x = request.security(symbol, tf, close)
''')
    assert "MULTI_SYMBOL_OR_DYNAMIC_REQUEST" in result.matched_capabilities
    assert result.confidence is AnalysisConfidence.ANALYSIS_INDETERMINATE


@pytest.mark.parametrize(
    ("statement", "capability"),
    [
        ('strategy("x", process_orders_on_close=true)', "PROCESS_ORDERS_ON_CLOSE"),
        ('strategy("x")\nstrategy.order("x", strategy.long)', "STRATEGY_ORDER_SEMANTICS"),
        ('strategy("x")\nstrategy.entry("x", strategy.long, qty=2)', "PINE_CONTROLLED_ENTRY_QUANTITY"),
        ('strategy("x")\nplot(strategy.position_size)', "POSITION_STATE_REFERENCE"),
        ('strategy("x")\nstrategy.cancel_all()', "PENDING_ORDER_CANCELLATION"),
        ('strategy("x")\nstrategy.exit("x", "entry")', "STRATEGY_EXIT_ORDER_SEMANTICS"),
        ('strategy("x", pyramiding=1)', "PYRAMIDING_LITERAL_CONFIGURATION"),
        (
            'indicator("x")\nx=request.security(syminfo.tickerid, "60", close)',
            "DEVELOPING_SECURITY_REQUEST",
        ),
        ('indicator("x")\nalert(\'{"action":"BUY_CE"}\')', "CUSTOM_ALERT_JSON"),
    ],
)
def test_required_high_confidence_constructs_are_classified(statement, capability):
    result = analyze_source(f"//@version=6\n{statement}\n")
    assert capability in result.matched_capabilities
    assert result.confidence is AnalysisConfidence.HIGH_CONFIDENCE_MATCH


def test_invalid_registry_returns_indeterminate_failure_result(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    result = analyze_source("//@version=6\nindicator(\"x\")", registry_path=bad)
    assert result.confidence is AnalysisConfidence.ANALYSIS_INDETERMINATE
    assert result.blocker_codes == ("BLK_REGISTRY_UNAVAILABLE",)
    assert result.effective_capability_level is CapabilityLevel.L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE
