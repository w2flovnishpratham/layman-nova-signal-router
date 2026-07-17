"""Deterministic, non-executing NOVA Pine compatibility validation."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from app.config import settings

CONTRACT_VERSION = 1
VALIDATOR_VERSION = "1.0.0"
VALIDATION_ENGINE = "nova-pine-static"
MAX_LINE_CHARS = 4096
MAX_FINDINGS = 100
SUPPORTED_ACTIONS = {"BUY_CE", "BUY_PE", "EXIT", "HOLD"}
FROZEN_TRANSPORT_VERSION = "pine_transport_v1"
FROZEN_TRANSPORT_MARKER = f"NOVA FROZEN TRANSPORT BEGIN: {FROZEN_TRANSPORT_VERSION}"
FROZEN_TRANSPORT_END_MARKER = f"NOVA FROZEN TRANSPORT END: {FROZEN_TRANSPORT_VERSION}"
FROZEN_TRANSPORT_PATH = Path(__file__).resolve().parents[1] / "prompts/pine_transport_v1.txt"
FROZEN_TRANSPORTS = {
    FROZEN_TRANSPORT_VERSION: FROZEN_TRANSPORT_PATH,
    "pine_transport_v2": Path(__file__).resolve().parents[1] / "prompts/pine_transport_v2.txt",
}
MANIFEST_SCHEMA = "nova.pine-conversion-manifest.v1"
MANIFEST_KEYS = {
    "schema", "status", "strategy_code", "source_pine_version", "target_pine_version",
    "actions", "explicit_exit_present", "reversal_mode", "signal_timing", "timeframe_policy",
    "hold_test_included", "repainting_classification", "behavior_classification", "behavior_changes",
    "unsupported_constructs", "admin_review_points", "blocked_reasons", "frozen_transport_version",
}
UNSUPPORTED_ACTIONS = {
    "BUY", "SELL", "LONG", "SHORT", "CALL", "PUT", "CLOSE_ALL", "MARKET_BUY", "MARKET_SELL"
}
SERVER_FIELDS = {
    "user_id", "strategy_instance_id", "broker_account_id", "execution_mode", "lots",
    "quantity", "qty", "strike", "expiry", "security_id", "trading_symbol", "position_id",
    "event_source", "exit_reason", "manual", "eod", "order_type", "product_type",
}
UNSUPPORTED_UNDERLYINGS = {
    "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX", "BTCUSD", "ETHUSD",
}
SECRET_PATTERNS = (
    re.compile(r"\bnwk_[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb)://[^\s\"']+", re.I),
    re.compile(r"\b(?:access[_ -]?token|client[_ -]?secret|totp[_ -]?(?:secret|seed)|authorization)\b", re.I),
)


def contains_credential_like_text(source: str) -> bool:
    return any(pattern.search(source) for pattern in SECRET_PATTERNS)


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    title: str
    explanation: str
    remediation: str
    blocks_review: bool
    line: int | None = None
    column: int | None = None
    excerpt: str | None = None


def canonicalize_source(source: str) -> str:
    if not isinstance(source, str):
        raise ValueError("Pine source must be UTF-8 text.")
    return source.replace("\r\n", "\n").replace("\r", "\n")


def validate_admin_manifest(value: str | dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    try:
        manifest = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return {"valid": False, "errors": ["INVALID_JSON"], "manifest": None}
    if not isinstance(manifest, dict):
        return {"valid": False, "errors": ["MANIFEST_NOT_OBJECT"], "manifest": None}
    missing, extra = MANIFEST_KEYS - manifest.keys(), manifest.keys() - MANIFEST_KEYS
    if missing:
        errors.append("MISSING_KEYS:" + ",".join(sorted(missing)))
    if extra:
        errors.append("UNKNOWN_KEYS:" + ",".join(sorted(extra)))
    if manifest.get("schema") != MANIFEST_SCHEMA:
        errors.append("INVALID_SCHEMA")
    status = manifest.get("status")
    if status not in {"READY_FOR_STATIC_VALIDATION", "READY_WITH_DISCLOSED_CHANGES", "BLOCKED"}:
        errors.append("INVALID_STATUS")
    if not isinstance(manifest.get("strategy_code"), str) or not re.fullmatch(r"[A-Z0-9_]+", manifest.get("strategy_code", "")):
        errors.append("INVALID_STRATEGY_CODE")
    if not isinstance(manifest.get("source_pine_version"), str) or manifest.get("target_pine_version") != "6":
        errors.append("INVALID_PINE_VERSION")
    actions = manifest.get("actions")
    if not isinstance(actions, list) or any(not isinstance(action, str) or action not in SUPPORTED_ACTIONS for action in actions) or len(actions or []) != len(set(actions or [])):
        errors.append("INVALID_ACTIONS")
    if manifest.get("reversal_mode") != "BACKEND_EXIT_FIRST":
        errors.append("INVALID_REVERSAL_MODE")
    if manifest.get("signal_timing") not in {"CONFIRMED_BAR_CLOSE", "INTRABAR_APPROXIMATION", "BLOCKED"}:
        errors.append("INVALID_SIGNAL_TIMING")
    if manifest.get("timeframe_policy") not in {"CHART_TIMEFRAME", "SOURCE_FIXED_TIMEFRAME", "BLOCKED"}:
        errors.append("INVALID_TIMEFRAME_POLICY")
    if manifest.get("frozen_transport_version") not in FROZEN_TRANSPORTS:
        errors.append("INVALID_TRANSPORT_VERSION")
    for key in ("explicit_exit_present", "hold_test_included"):
        if not isinstance(manifest.get(key), bool):
            errors.append("INVALID_" + key.upper())
    for key in ("repainting_classification", "behavior_classification"):
        if not isinstance(manifest.get(key), str) or not manifest.get(key):
            errors.append("INVALID_" + key.upper())
    for key in ("behavior_changes", "unsupported_constructs", "admin_review_points", "blocked_reasons"):
        if not isinstance(manifest.get(key), list) or any(not isinstance(item, str) for item in manifest.get(key, [])):
            errors.append("INVALID_" + key.upper())
    if status == "BLOCKED":
        if not manifest.get("blocked_reasons"):
            errors.append("BLOCKED_REASON_REQUIRED")
    elif manifest.get("blocked_reasons") or manifest.get("hold_test_included") is not True or "HOLD" not in (actions or []):
        errors.append("SUPPORTED_MANIFEST_SAFETY_INVALID")
    return {"valid": not errors, "errors": errors, "manifest": manifest if not errors else None}


def _frozen_transport_matches(source: str, version: str) -> bool:
    start_marker = f"NOVA FROZEN TRANSPORT BEGIN: {version}"
    end_marker = f"NOVA FROZEN TRANSPORT END: {version}"
    start, end = source.find(start_marker), source.find(end_marker)
    if start < 0 or end < start:
        return False
    start = source.rfind("//", 0, start)
    end = source.find("\n", end)
    block = source[start:end if end >= 0 else len(source)].strip()
    block = re.sub(r'(?m)^const string novaStrategyCode = "[A-Z0-9_]+"$', 'const string novaStrategyCode = "{{STRATEGY_CODE}}"', block)
    block = re.sub(r'(?m)^const string novaStrategyVersion = "[A-Z0-9_]+"$', 'const string novaStrategyVersion = "{{STRATEGY_VERSION}}"', block)
    return block == FROZEN_TRANSPORTS[version].read_text(encoding="utf-8").strip()


def _without_comments(source: str) -> str:
    """Remove // comments while preserving strings, newlines and columns."""
    output: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(source):
        char = source[index]
        if quote:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            output.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < len(source) and source[index + 1] == "/":
            while index < len(source) and source[index] != "\n":
                output.append(" ")
                index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _call_spans(code: str, names: tuple[str, ...]) -> list[tuple[int, str]]:
    starts = re.compile(rf"\b(?:{'|'.join(map(re.escape, names))})\s*\(")
    spans: list[tuple[int, str]] = []
    for match in starts.finditer(code):
        depth = 0
        quote: str | None = None
        escaped = False
        for index in range(match.end() - 1, len(code)):
            char = code[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in {'"', "'"}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    spans.append((match.start(), code[match.start():index + 1]))
                    break
    return spans


def _location(source: str, offset: int) -> tuple[int, int, str]:
    line = source.count("\n", 0, offset) + 1
    start = source.rfind("\n", 0, offset) + 1
    end = source.find("\n", offset)
    if end < 0:
        end = len(source)
    excerpt = source[start:end].strip()
    return line, offset - start + 1, excerpt[:120]


def validate_source(source: str) -> dict[str, Any]:
    started = time.perf_counter()
    source = canonicalize_source(source)
    findings: list[Finding] = []

    def add(
        code: str,
        severity: str,
        title: str,
        explanation: str,
        remediation: str,
        *,
        offset: int | None = None,
        secret: bool = False,
    ) -> None:
        if len(findings) >= MAX_FINDINGS:
            return
        line = column = None
        excerpt = None
        if offset is not None:
            line, column, excerpt = _location(source, offset)
            if secret:
                excerpt = "[redacted]"
        findings.append(Finding(
            code, severity, title, explanation, remediation, severity == "ERROR",
            line, column, excerpt,
        ))

    try:
        encoded = source.encode("utf-8")
    except UnicodeEncodeError:
        encoded = b""
        add(
            "INVALID_UTF8",
            "ERROR",
            "Source is not valid UTF-8",
            "The submitted text contains an invalid Unicode surrogate.",
            "Save the script as UTF-8 and upload it again.",
        )
    if not source.strip():
        add("SOURCE_EMPTY", "ERROR", "Source is empty", "No Pine source was provided.", "Paste or upload one Pine script.")
    if len(encoded) > max(int(settings.PERSONAL_PINE_MAX_SOURCE_BYTES), 1):
        add("SOURCE_TOO_LARGE", "ERROR", "Source is too large", "The source exceeds NOVA's configured byte limit.", "Reduce the file size and submit one script only.")
    if "\x00" in source:
        add("SOURCE_NULL_BYTE", "ERROR", "Null byte detected", "Pine source must be plain UTF-8 text.", "Remove binary or null-byte content.", offset=source.index("\x00"))
    for match in re.finditer(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", source):
        add("SOURCE_CONTROL_CHARACTER", "ERROR", "Unsupported control character", "The source contains non-text control data.", "Upload plain UTF-8 Pine text.", offset=match.start())
    for number, line_text in enumerate(source.split("\n"), 1):
        if len(line_text) > MAX_LINE_CHARS:
            offset = sum(len(line) + 1 for line in source.split("\n")[:number - 1])
            add("SOURCE_LINE_TOO_LONG", "ERROR", "Line is too long", "A source line exceeds the safe rendering limit.", "Split the expression across lines.", offset=offset)

    for pattern in SECRET_PATTERNS:
        match = pattern.search(source)
        if match:
            add("CREDENTIAL_IN_SOURCE", "ERROR", "Credential-like value detected", "Private source must not contain credentials, tokens, database URLs or authentication headers.", "Remove the secret and rotate it if it may have been exposed.", offset=match.start(), secret=True)

    code = _without_comments(source)
    frozen_versions = [version for version in FROZEN_TRANSPORTS if f"NOVA FROZEN TRANSPORT BEGIN: {version}" in source]
    frozen_transport_version = frozen_versions[0] if len(frozen_versions) == 1 else None
    has_frozen_transport = frozen_transport_version is not None
    version_match = re.search(r"(?m)^\s*//@version\s*=\s*(\d+)\s*$", source)
    pine_version = int(version_match.group(1)) if version_match else None
    if pine_version is None:
        add("PINE_VERSION_MISSING", "ERROR", "Pine version is missing", "A Pine version directive is required.", "Add //@version=6 as the first source directive.")
    elif pine_version < 5:
        add("PINE_VERSION_UNSUPPORTED", "ERROR", "Pine version is unsupported", f"Pine v{pine_version} is outside this contract.", "Upgrade the script manually to Pine v5 or v6.", offset=version_match.start())
    elif pine_version == 5:
        add("PINE_V5_UPGRADE_RECOMMENDED", "WARNING", "Pine v5 accepted with warning", "Static checks can accept Pine v5 but do not prove Pine v6 compatibility.", "Compile and test an explicit Pine v6 upgrade in TradingView.", offset=version_match.start())
    elif pine_version > 6:
        add("PINE_VERSION_UNSUPPORTED", "ERROR", "Pine version is unsupported", f"Pine v{pine_version} is not supported by contract v1.", "Use Pine v6.", offset=version_match.start())

    declarations = list(re.finditer(r"\b(?:indicator|strategy)\s*\(", code))
    if not declarations:
        add("DECLARATION_MISSING", "ERROR", "Script declaration is missing", "Exactly one indicator() or strategy() declaration is required.", "Add one Pine declaration with a title.")
    elif len(declarations) > 1:
        add("DECLARATION_MULTIPLE", "ERROR", "Multiple script declarations", "A Pine file may define only one supported script declaration.", "Keep exactly one indicator() or strategy() call.", offset=declarations[1].start())
    else:
        declaration = _call_spans(code[declarations[0].start():], ("indicator", "strategy"))
        declaration_text = declaration[0][1] if declaration else ""
        if not re.search(r"\(\s*[\"'][^\"']+[\"']", declaration_text):
            add("DECLARATION_TITLE_MISSING", "ERROR", "Script title is missing", "The declaration needs a non-empty title.", "Add a quoted title as the first declaration argument.", offset=declarations[0].start())
        if not re.search(r"\boverlay\s*=\s*true\b", declaration_text, re.I):
            add("OVERLAY_UNCONFIRMED", "WARNING", "Overlay mode is not confirmed", "NOVA chart setup normally expects overlay=true.", "Confirm the intended chart behavior and set overlay=true when appropriate.", offset=declarations[0].start())

    alert_spans = _call_spans(code, ("alert", "alertcondition"))
    emitted: set[str] = set()
    for offset, call in alert_spans:
        emitted.update(action for action in SUPPORTED_ACTIONS if re.search(rf"(?<![A-Z0-9_]){action}(?![A-Z0-9_])", call))
        for action in UNSUPPORTED_ACTIONS:
            if re.search(rf"[\"']{action}[\"']", call):
                add("ACTION_UNSUPPORTED", "ERROR", "Unsupported emitted action", f"{action} is not a NOVA contract action.", "Emit BUY_CE, BUY_PE, EXIT or optional HOLD.", offset=offset)
        for field in SERVER_FIELDS:
            if re.search(rf"[\"']{re.escape(field)}[\"']\s*:", call, re.I):
                add("SERVER_AUTHORITY_FIELD", "ERROR", "Server-controlled field emitted", f"The alert attempts to control '{field}'.", "Remove the field; NOVA resolves it server-side.", offset=offset)

    if not emitted.intersection({"BUY_CE", "BUY_PE"}):
        add("ENTRY_ACTION_MISSING", "ERROR", "Entry action is missing", "No emitted BUY_CE or BUY_PE action was found in an alert call.", "Emit at least one supported entry action from alert() or alertcondition().")
    if "EXIT" not in emitted:
        add("EXIT_ACTION_MISSING", "ERROR", "EXIT action is missing", "Entry-capable scripts must expose an EXIT path.", "Emit EXIT for the current NOVA position.")
    if "HOLD" not in emitted:
        if has_frozen_transport:
            add("HOLD_REQUIRED", "ERROR", "HOLD facility is missing", "Prompt v3 candidates require the canonical one-time HOLD facility.", f"Restore the frozen {frozen_transport_version} block without changes.")
        else:
            add("HOLD_OPTIONAL", "INFO", "HOLD is optional", "No audit-only HOLD action was found.", "No change is required unless you want explicit no-op alerts.")

    if has_frozen_transport:
        if not _frozen_transport_matches(source, frozen_transport_version):
            add("TRANSPORT_HASH_MISMATCH", "ERROR", "Frozen transport was changed", f"The complete {frozen_transport_version} block does not match the registered template after constant substitution.", "Restore the exact frozen transport block.")
        required_transport = {
            "TRANSPORT_END_MISSING": f"NOVA FROZEN TRANSPORT END: {frozen_transport_version}",
            "TRANSPORT_VERSION_MISSING": f'novaTransportVersion = "{frozen_transport_version}"',
            "CREDENTIAL_INPUT_INVALID": 'input.string(novaCredentialPlaceholder, "NOVA private credential"',
            "CREDENTIAL_PLACEHOLDER_INVALID": 'novaCredentialPlaceholder = "REPLACE_WITH_PRIVATE_CREDENTIAL"',
            "SIGNAL_ID_INVALID": 'novaStrategyCode + ":" + syminfo.ticker + ":" + str.tostring(time_close) + ":" + action',
            "SIGNAL_TIME_INVALID": 'str.format_time(time_close, "yyyy-MM-dd\'T\'HH:mm:ss\'Z\'", "UTC")',
            "BAR_CLOSE_GATE_INVALID": "barstate.isrealtime and barstate.isconfirmed and novaCredentialReady",
            "HOLD_INPUT_INVALID": 'input.bool(false, "Send one HOLD connectivity test"',
            "HOLD_ONCE_INVALID": "not novaHoldSent",
        }
        for finding_code, fragment in required_transport.items():
            if fragment not in source:
                add(finding_code, "ERROR", "Frozen transport is incomplete", f"Required {frozen_transport_version} element {finding_code} was not found.", "Restore the exact frozen transport block.")
        payload_fields = set(re.findall(r'"([a-z_]+)"\s*:', code))
        allowed_fields = {"credential", "action", "signal_id", "signal_time", "strategy_version", "timeframe", "reference_price", "comment"}
        if payload_fields - allowed_fields:
            add("PAYLOAD_FIELD_UNSUPPORTED", "ERROR", "Unsupported webhook field", "The v3 payload contains a field owned by NOVA.", "Keep only canonical required fields and permitted metadata.")
        emitted_literals = set(re.findall(r'novaWebhookPayload\(\s*"([A-Z_]+)"', code))
        if emitted_literals - SUPPORTED_ACTIONS:
            add("ACTION_UNSUPPORTED", "ERROR", "Unsupported emitted action", "The frozen transport contains a non-contract action.", "Use only BUY_CE, BUY_PE, EXIT, and HOLD.")
        for offset, call in alert_spans:
            if not re.match(r'alert\s*\(\s*novaWebhookPayload\(\s*"(?:BUY_CE|BUY_PE|EXIT|HOLD)"\s*\)', call):
                add("NONCANONICAL_ALERT", "ERROR", "Non-canonical alert detected", "Prompt v3 candidates may emit alerts only through the frozen NOVA transport.", "Remove strategy-layer alert calls and keep only canonical booleans.", offset=offset)
        forbidden_input = re.search(r'input\.\w+\([^\n]*"[^"\n]*(?:strategy code|strategy version|timezone|webhook url|quantity|lots|strike|expiry|broker|execution mode|order type|product type)[^"\n]*"', code, re.I)
        if forbidden_input:
            add("TRANSPORT_AUTHORITY_INPUT", "ERROR", "Editable transport authority detected", "A normal-user input controls NOVA transport or execution authority.", "Remove the input and keep transport identity as constants.", offset=forbidden_input.start())

    upper_code = code.upper()
    for underlying in UNSUPPORTED_UNDERLYINGS:
        match = re.search(rf"\b{underlying}\b", upper_code)
        if match:
            add("UNDERLYING_UNSUPPORTED", "ERROR", "Unsupported underlying detected", f"{underlying} is outside the NIFTY-only contract.", "Use a NIFTY chart and remove unsupported symbol execution.", offset=match.start())
    if "NIFTY" not in upper_code:
        if "SYMINF" in upper_code:
            add("UNDERLYING_GENERIC", "WARNING", "Chart symbol is generic", "Static validation cannot prove the script will run only on NIFTY.", "Run it only on a NIFTY chart and verify TradingView alert setup.")
        else:
            add("UNDERLYING_UNCONFIRMED", "WARNING", "NIFTY compatibility is unconfirmed", "No explicit NIFTY or generic chart-symbol reference was found.", "Confirm the script is intended only for NIFTY.")

    pyramiding = re.search(r"\bpyramiding\s*=\s*(\d+)", code)
    if pyramiding and int(pyramiding.group(1)) > 1:
        add("PYRAMIDING_UNSUPPORTED", "ERROR", "Pyramiding is unsupported", "NOVA currently supports one position without scale-in.", "Set pyramiding to 0 or 1 and remove scale-in behavior.", offset=pyramiding.start())
    if len(re.findall(r"\bstrategy\.entry\s*\(", code)) > 2:
        add("MULTIPLE_ENTRY_PATHS", "WARNING", "Multiple entry paths detected", "Multiple strategy.entry calls may imply scaling or independent legs.", "Confirm all paths normalize to one current position.")
    if re.search(r"\b(?:martingale|strategy\.opentrades|strategy\.order)\b", code, re.I):
        add("POSITION_MODEL_UNSUPPORTED", "ERROR", "Unsupported position management", "The source appears to manage scaling, orders or multiple positions directly.", "Use one-position BUY_CE/BUY_PE/EXIT intent only.")
    if re.search(r"\brequest\.security\s*\(", code) and len(re.findall(r"\brequest\.security\s*\(", code)) > 1:
        add("MULTI_SYMBOL_RISK", "WARNING", "Multiple security requests detected", "The source may depend on multiple symbols.", "Keep execution intent NIFTY-only and review every requested series.")

    repaint = re.search(r"\bbarmerge\.lookahead_on\b|\blookahead\s*=\s*barmerge\.lookahead_on\b", code)
    if repaint:
        add("POTENTIAL_REPAINTING", "WARNING", "Potential repainting risk detected", "Lookahead may use future data and repaint signals.", "Disable lookahead and confirm signals on closed bars.", offset=repaint.start())
    if alert_spans and "BARSTATE.ISCONFIRMED" not in upper_code and "FREQ_ONCE_PER_BAR_CLOSE" not in upper_code:
        add("BAR_CONFIRMATION_MISSING", "WARNING", "Bar-close confirmation is not visible", "Alerts may repeat intrabar or repaint before close.", "Gate signals with barstate.isconfirmed or once-per-bar-close alert frequency.")
    if not re.search(r"\b(?:eod|session|hour|minute|time_close)\b", code, re.I):
        add("EOD_HANDLING_UNCONFIRMED", "WARNING", "Script EOD handling is unconfirmed", "Static validation found no visible intraday session exit logic. NOVA still applies mandatory EOD protection.", "Add an EXIT alert before market close or document reliance on NOVA EOD protection.")

    errors = sum(item.severity == "ERROR" for item in findings)
    warnings = sum(item.severity == "WARNING" for item in findings)
    infos = sum(item.severity == "INFO" for item in findings)
    return {
        "validator_version": VALIDATOR_VERSION,
        "contract_version": CONTRACT_VERSION,
        "validation_engine": VALIDATION_ENGINE,
        "status": "FAILED" if errors else "PASSED_WITH_WARNINGS" if warnings else "PASSED",
        "error_count": errors,
        "warning_count": warnings,
        "info_count": infos,
        "eligible_for_review": errors == 0,
        "pine_version": pine_version,
        "emitted_actions": sorted(emitted),
        "findings": [asdict(item) for item in findings],
        "duration_ms": max(0, round((time.perf_counter() - started) * 1000)),
    }
