from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.domain.pine_capabilities import CapabilityLevel, TemporalClass
from app.services.pine_semantic_preanalyzer import AnalysisConfidence, analyze_source


FIXTURES = Path(__file__).parent / "pine_fixtures" / "r1a"
EXPECTED_KEYS = {
    "fixture_id",
    "source_sha256",
    "analyzer_version",
    "registry_id",
    "registry_version",
    "registry_sha256",
    "matched_capabilities_exact",
    "effective_capability_level",
    "temporal_classes_exact",
    "blocker_codes_exact",
    "disclosure_codes_exact",
    "admin_review_points_exact",
    "confidence",
}
EXACT_ARRAY_KEYS = (
    "matched_capabilities_exact",
    "temporal_classes_exact",
    "blocker_codes_exact",
    "disclosure_codes_exact",
    "admin_review_points_exact",
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def _validate_expected(expected: dict[str, object], pine_path: Path) -> None:
    assert set(expected) == EXPECTED_KEYS
    fixture_id = pine_path.name.split("_", 1)[0]
    assert expected["fixture_id"] == fixture_id
    assert SHA256_PATTERN.fullmatch(str(expected["source_sha256"]))
    assert SHA256_PATTERN.fullmatch(str(expected["registry_sha256"]))
    for key in EXACT_ARRAY_KEYS:
        values = expected[key]
        assert isinstance(values, list)
        assert values == sorted(set(values))


def _actual_document(pine_path: Path) -> dict[str, object]:
    result = analyze_source(pine_path.read_text(encoding="utf-8"))
    return {
        "fixture_id": pine_path.name.split("_", 1)[0],
        "source_sha256": result.source_sha256,
        "analyzer_version": result.analyzer_version,
        "registry_id": result.registry_id,
        "registry_version": result.registry_version,
        "registry_sha256": result.registry_sha256,
        "matched_capabilities_exact": list(result.matched_capabilities),
        "effective_capability_level": result.effective_capability_level.value,
        "temporal_classes_exact": [item.value for item in result.temporal_classes],
        "blocker_codes_exact": list(result.blocker_codes),
        "disclosure_codes_exact": list(result.disclosure_codes),
        "admin_review_points_exact": list(result.admin_review_points),
        "confidence": result.confidence.value,
    }


def _assert_expected_matches(pine_path: Path, expected: dict[str, object]) -> None:
    _validate_expected(expected, pine_path)
    assert expected == _actual_document(pine_path)


def _fixture_pairs(paths: list[Path]) -> dict[str, Path]:
    fixtures: dict[str, Path] = {}
    for path in paths:
        fixture_id = path.name.split("_", 1)[0]
        assert fixture_id not in fixtures
        fixtures[fixture_id] = path
    return fixtures


def test_every_r1a_fixture_matches_its_expected_classification():
    pine_files = sorted(FIXTURES.glob("*.pine"))
    assert len(pine_files) == 33
    assert len(list(FIXTURES.glob("*.expected.json"))) == 33
    _fixture_pairs(pine_files)
    for pine_path in pine_files:
        expected = json.loads(pine_path.with_suffix(".expected.json").read_text(encoding="utf-8"))
        _assert_expected_matches(pine_path, expected)


def test_expected_schema_rejects_missing_and_unknown_keys():
    pine_path = sorted(FIXTURES.glob("*.pine"))[0]
    expected = _actual_document(pine_path)
    missing = dict(expected)
    missing.pop("confidence")
    unknown = dict(expected, unknown_key=True)
    with pytest.raises(AssertionError):
        _validate_expected(missing, pine_path)
    with pytest.raises(AssertionError):
        _validate_expected(unknown, pine_path)


def test_fixture_filename_and_id_mismatch_fails():
    pine_path = sorted(FIXTURES.glob("*.pine"))[0]
    expected = _actual_document(pine_path)
    expected["fixture_id"] = "F66"
    with pytest.raises(AssertionError):
        _validate_expected(expected, pine_path)


def test_duplicate_fixture_ids_fail(tmp_path):
    first = tmp_path / "F01_one.pine"
    second = tmp_path / "F01_two.pine"
    first.touch()
    second.touch()
    with pytest.raises(AssertionError):
        _fixture_pairs([first, second])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(source_sha256="0" * 64),
        lambda value: value.update(registry_sha256="0" * 64),
        lambda value: value["matched_capabilities_exact"].append("ZZZ_EXTRA"),
        lambda value: value["matched_capabilities_exact"].pop(),
        lambda value: value["temporal_classes_exact"].append("T9_EXTERNAL_OR_UNAVAILABLE_DATA"),
        lambda value: value["blocker_codes_exact"].pop(),
        lambda value: value["disclosure_codes_exact"].append("DISC_EXTRA"),
        lambda value: value.update(confidence="ANALYSIS_INDETERMINATE"),
        lambda value: value["admin_review_points_exact"].pop(),
    ],
)
def test_any_exact_expectation_mismatch_fails(mutate):
    pine_path = FIXTURES / "F44_unsafe_future_lookahead.pine"
    expected = _actual_document(pine_path)
    mutate(expected)
    with pytest.raises(AssertionError):
        _assert_expected_matches(pine_path, expected)


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
    assert result.confidence is AnalysisConfidence.HIGH_CONFIDENCE_MATCH
    assert result.effective_capability_level is CapabilityLevel.L0_DIRECTLY_SUPPORTED


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


def test_dynamic_request_with_unresolved_context_is_partial_and_remains_l4():
    result = analyze_source('''//@version=6
indicator("dynamic")
symbol = input.symbol("NSE:NIFTY")
tf = input.timeframe("60")
x = request.security(symbol, tf, close)
''')
    assert "MULTI_SYMBOL_OR_DYNAMIC_REQUEST" in result.matched_capabilities
    assert result.confidence is AnalysisConfidence.PARTIAL_MATCH
    assert result.effective_capability_level is CapabilityLevel.L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE


@pytest.mark.parametrize(
    ("source", "capability", "level"),
    [
        (
            '''//@version=6
strategy("computed entry")
entry_limit = close - ta.atr(14)
strategy.entry("L", strategy.long, limit=entry_limit)
''',
            "PENDING_LIMIT_ENTRY",
            CapabilityLevel.L3_REQUIRES_BACKEND_CAPABILITY,
        ),
        (
            '''//@version=6
strategy("computed exit")
portion = math.max(10, strategy.position_size)
strategy.exit("X", "L", qty_percent=portion)
''',
            "PARTIAL_EXIT",
            CapabilityLevel.L3_REQUIRES_BACKEND_CAPABILITY,
        ),
        (
            '''//@version=6
indicator("partial JSON")
action = close > open ? "BUY_CE" : "BUY_PE"
payload = '{"action":"' + action + '"}'
alert(payload)
''',
            "CUSTOM_ALERT_JSON",
            CapabilityLevel.L2_SUPPORTED_WITH_DISCLOSED_CHANGE,
        ),
        (
            '''//@version=6
strategy("fill state")
index = strategy.opentrades - 1
price = strategy.opentrades.entry_price(index)
plot(price)
''',
            "FILL_DEPENDENT_STRATEGY_STATE",
            CapabilityLevel.L3_REQUIRES_BACKEND_CAPABILITY,
        ),
    ],
)
def test_meaningful_incomplete_constructs_are_partial_without_reducing_severity(
    source,
    capability,
    level,
):
    result = analyze_source(source)
    assert capability in result.matched_capabilities
    assert result.confidence is AnalysisConfidence.PARTIAL_MATCH
    assert result.effective_capability_level is level


def test_partial_confidence_cannot_cancel_a_high_confidence_blocker():
    result = analyze_source('''//@version=6
indicator("mixed")
unsafe = request.security(syminfo.tickerid, "60", close, lookahead=barmerge.lookahead_on)
payload = '{"action":"' + (close > open ? "BUY_CE" : "BUY_PE") + '"}'
alert(payload)
''')
    assert "UNSAFE_FUTURE_LOOKAHEAD" in result.matched_capabilities
    assert "CUSTOM_ALERT_JSON" in result.matched_capabilities
    assert result.confidence is AnalysisConfidence.PARTIAL_MATCH
    assert result.effective_capability_level is CapabilityLevel.L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE


def test_indeterminate_analysis_cannot_promote_a_safe_match():
    result = analyze_source('''//@version=6
indicator("broken")
plot(close
''')
    assert "BASIC_INDICATOR_BOOLEAN_SIGNAL" in result.matched_capabilities
    assert "MALFORMED_SOURCE" in result.matched_capabilities
    assert result.confidence is AnalysisConfidence.ANALYSIS_INDETERMINATE
    assert result.effective_capability_level is CapabilityLevel.L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE


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
