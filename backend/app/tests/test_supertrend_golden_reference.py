"""Static-only checks for the Supertrend v2 Pine golden-reference candidate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.services.pine_validation import validate_source


ROOT = Path(__file__).resolve().parents[3]
PINE_PATH = ROOT / "docs" / "pine" / "golden-references" / "nova_supertrend_v2.pine"
QUALIFICATION_PATH = ROOT / "docs" / "pine" / "golden-references" / "nova_supertrend_v2_qualification.json"
OLD_PATH = ROOT / "nova_indicator_v2.pine"
PROMPT_PATH = ROOT / "backend" / "app" / "prompts" / "pine_conversion_v1.txt"


def source() -> str:
    return PINE_PATH.read_text(encoding="utf-8")


def test_golden_reference_matches_private_webhook_contract():
    pine = source()
    report = validate_source(pine)
    assert report["eligible_for_review"] is True, report["findings"]
    assert report["emitted_actions"] == ["BUY_CE", "BUY_PE", "EXIT", "HOLD"]
    assert '"credential"' in pine
    assert "barstate.isrealtime and barstate.isconfirmed" in pine
    assert "alert.freq_once_per_bar_close" in pine
    assert '"NOVA-ST-V2:" + syminfo.ticker + ":" + str.tostring(time_close) + ":" + action' in pine
    assert "credential != credentialPlaceholder" in pine


def test_golden_reference_has_no_client_execution_authority():
    pine = source()
    forbidden = (
        "atmStrike", "activeStrike", "activeOptionSide", "activeExpiry", "hasLocalTrade",
        '"strike":', '"expiry":', '"qty":', '"quantity":', '"lots":', '"security_id":',
        '"broker_account":', '"client_id":', '"instrument_type":', '"exchange_segment":',
        '"order_type":', '"product_type":', '"paper_mode":', '"live_mode":',
        '"access_token":', '"totp":', '"secret":', "math.round(close / 50)",
    )
    assert not [value for value in forbidden if value in pine]


def test_extensions_are_safe_defaults_and_eod_is_deduplicated():
    pine = source()
    assert 'enableEodExit = input.bool(false' in pine
    assert 'resetTrendDaily = input.bool(false' in pine
    assert 'freshEntryAfterDailyReset = input.bool(false' in pine
    assert "lastEodExitDate != closeDateIst" in pine
    assert "hasLocalTrade" not in pine


def test_qualification_manifest_pins_candidate_and_old_source():
    record = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
    candidate_hash = hashlib.sha256(PINE_PATH.read_bytes()).hexdigest()
    old_hash = hashlib.sha256(OLD_PATH.read_bytes()).hexdigest()
    prompt_hash = hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()
    assert record["candidate_source_sha256"] == candidate_hash
    assert record["old_nova_source_sha256"] == old_hash
    assert record["master_prompt_content_sha256"] == prompt_hash
    assert record["master_prompt_version"] == "v1"
    assert record["qualification_status"] == "QUALIFICATION"
    assert record["static_validation_status"] == "PASSED_WITH_WARNINGS"
    assert record["static_validation_findings"] == ["UNDERLYING_GENERIC"]
    assert record["original_source_sha256"] is None
    assert record["original_source_blocker"] == "SOURCE_NOT_SUPPLIED"
    assert record["tradingview_compile_status"] == "BLOCKED"
    assert record["real_tradingview_alert_status"] == "BLOCKED"
    assert record["paper_entry_status"] == "BLOCKED"
    assert record["paper_exit_status"] == "BLOCKED"
    assert record["reversal_status"] == "BLOCKED"


def test_old_nova_fails_current_contract_checks_corrected_candidate_passes():
    old_report = validate_source(OLD_PATH.read_text(encoding="utf-8"))
    corrected_report = validate_source(source())
    old_codes = {finding["code"] for finding in old_report["findings"]}
    corrected_codes = {finding["code"] for finding in corrected_report["findings"]}
    assert {"ENTRY_ACTION_MISSING", "EXIT_ACTION_MISSING"} <= old_codes
    assert old_report["emitted_actions"] == []
    assert old_report["eligible_for_review"] is False
    assert "SERVER_AUTHORITY_FIELD" not in corrected_codes
    assert corrected_codes == {"UNDERLYING_GENERIC"}
    assert corrected_report["eligible_for_review"] is True
