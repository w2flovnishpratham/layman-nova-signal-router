"""Deterministic Pine feature detection; deliberately not a Pine compiler."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.domain.pine_capabilities import (
    CapabilityLevel,
    PineCapabilityRegistry,
    REGISTRY_PATH,
    SCHEMA_PATH,
    RegistryError,
    TemporalClass,
    load_registry,
    most_restrictive,
)


ANALYZER_VERSION = "nova.pine-semantic-preanalyzer.v1"


class AnalysisConfidence(StrEnum):
    HIGH_CONFIDENCE_MATCH = "HIGH_CONFIDENCE_MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    ANALYSIS_INDETERMINATE = "ANALYSIS_INDETERMINATE"


@dataclass(frozen=True, slots=True)
class PineSemanticAnalysisResult:
    analyzer_version: str
    registry_id: str
    registry_version: str
    registry_sha256: str
    source_sha256: str
    matched_capabilities: tuple[str, ...]
    effective_capability_level: CapabilityLevel
    temporal_classes: tuple[TemporalClass, ...]
    blocker_codes: tuple[str, ...]
    disclosure_codes: tuple[str, ...]
    admin_review_points: tuple[str, ...]
    confidence: AnalysisConfidence


_AUTHORITY_KEYS = {
    "user_id", "strategy_instance_id", "broker_account_id", "execution_mode",
    "lots", "quantity", "qty", "option_side", "strike", "expiry", "security_id",
    "order_type", "stop_loss", "take_profit", "sl", "tp", "credential",
}
_HOSTILE_INSTRUCTION = re.compile(
    r"\b(?:ignore|disregard)\s+(?:all\s+|any\s+)?(?:previous|prior|above|system|developer)\s+instructions\b"
    r"|\balways\s+emit\s+(?:buy_ce|buy_pe|exit)\b"
    r"|\bbypass\s+(?:nova|validation|safety)\b",
    re.IGNORECASE,
)
_API_CALL = re.compile(
    r"\b(?:strategy\.(?:entry|order|exit|close|close_all|cancel|cancel_all)"
    r"|request\.(?:security|security_lower_tf|footprint)"
    r"|ticker\.(?:heikinashi|renko|kagi|linebreak|pointfigure))\s*\("
)
_DECLARATION = re.compile(r"\b(?:indicator|strategy)\s*\(")


def _mask(source: str, *, strings: bool) -> str:
    output = list(source)
    index = 0
    quote: str | None = None
    block_comment = False
    line_comment = False
    escaped = False
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            else:
                output[index] = " "
            index += 1
            continue
        if block_comment:
            if char == "*" and following == "/":
                output[index] = output[index + 1] = " "
                block_comment = False
                index += 2
            else:
                if char != "\n":
                    output[index] = " "
                index += 1
            continue
        if quote:
            if strings and char != "\n":
                output[index] = " "
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and following == "/":
            output[index] = output[index + 1] = " "
            line_comment = True
            index += 2
            continue
        if char == "/" and following == "*":
            output[index] = output[index + 1] = " "
            block_comment = True
            index += 2
            continue
        if char in {'"', "'"}:
            quote = char
            if strings:
                output[index] = " "
        index += 1
    return "".join(output)


def _calls(source: str, api: str, *, locator: str | None = None) -> list[str]:
    starts = list(re.finditer(rf"\b{re.escape(api)}\s*\(", locator or source))
    calls: list[str] = []
    for start in starts:
        depth = 0
        quote: str | None = None
        escaped = False
        for index in range(start.end() - 1, len(source)):
            char = source[index]
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
                    calls.append(source[start.start():index + 1])
                    break
    return calls


def _first_arguments(call: str) -> tuple[str, ...]:
    body = call[call.find("(") + 1:-1]
    values: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(body):
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
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            values.append(body[start:index].strip())
            start = index + 1
    values.append(body[start:].strip())
    return tuple(values)


def _unbalanced(code: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for char in code:
        if char in "([{":
            stack.append(char)
        elif char in pairs and (not stack or stack.pop() != pairs[char]):
            return True
    return bool(stack)


def _detect(source: str) -> tuple[set[str], bool]:
    comments_masked = _mask(source, strings=False)
    code = _mask(source, strings=True)
    matched: set[str] = set()
    indeterminate = False

    if "NOVA_WORKFLOW_MARKER: PROTECTED_SOURCE_UNAVAILABLE" in source:
        matched.add("PROTECTED_OR_UNAVAILABLE_SOURCE")
    if _unbalanced(code) or not _DECLARATION.search(code):
        matched.add("MALFORMED_SOURCE")
    if _HOSTILE_INSTRUCTION.search(source):
        matched.add("PROMPT_INJECTION_LIKE_CONTENT")

    if re.search(r"\bindicator\s*\(", code):
        matched.add("BASIC_INDICATOR_BOOLEAN_SIGNAL")
    if re.search(r"\bbarstate\.isconfirmed\b|\balert\.freq_once_per_bar_close\b", code):
        matched.add("CONFIRMED_BAR_CLOSE_TIMING")
    if re.search(r"\bvarip\b", code):
        matched.add("INTRABAR_VARIP_STATE")

    strategy_declarations = _calls(comments_masked, "strategy", locator=code)
    declaration = strategy_declarations[0] if strategy_declarations else ""
    if re.search(r"\bcalc_on_every_tick\s*=\s*true\b", declaration):
        matched.add("CALC_ON_EVERY_TICK")
    if re.search(r"\bcalc_on_order_fills\s*=\s*true\b", declaration):
        matched.add("CALC_ON_ORDER_FILLS")
    if re.search(r"\bprocess_orders_on_close\s*=\s*true\b", declaration):
        matched.add("PROCESS_ORDERS_ON_CLOSE")

    entry_calls = _calls(comments_masked, "strategy.entry", locator=code)
    order_calls = _calls(comments_masked, "strategy.order", locator=code)
    if order_calls:
        matched.add("STRATEGY_ORDER_SEMANTICS")
    directions: list[str] = []
    entry_ids: set[str] = set()
    for call in entry_calls + order_calls:
        has_limit = bool(re.search(r"\blimit\s*=", call))
        has_stop = bool(re.search(r"\bstop\s*=", call))
        if has_limit and has_stop:
            matched.add("PENDING_STOP_LIMIT_ENTRY")
        elif has_limit:
            matched.add("PENDING_LIMIT_ENTRY")
        elif has_stop:
            matched.add("PENDING_STOP_ENTRY")
        elif call in entry_calls:
            matched.add("MARKET_DIRECTIONAL_ENTRY")
        if re.search(r"\bqty\s*=", call):
            matched.add("PINE_CONTROLLED_ENTRY_QUANTITY")
        args = _first_arguments(call)
        if args and re.fullmatch(r"[\"'][^\"']+[\"']", args[0]):
            entry_ids.add(args[0][1:-1])
        if "strategy.long" in call:
            directions.append("long")
        if "strategy.short" in call:
            directions.append("short")
    if {"long", "short"} <= set(directions):
        matched.add("OPPOSITE_DIRECTION_REVERSAL_NORMALIZATION")
    if len(entry_ids) > 1 and len(set(directions)) < 2:
        matched.add("MULTIPLE_ENTRY_IDS")
    if len(entry_calls) > 1 and len(set(directions)) == 1:
        matched.add("SAME_SIDE_STATE_NO_OP")

    close_present = bool(re.search(r"\bstrategy\.(?:close|close_all)\s*\(", code))
    if close_present:
        matched.add("EXPLICIT_FULL_FLATTEN")
    if close_present and re.search(r"\bstrategy\.position_size\s*==\s*0\b", code):
        matched.add("EXIT_WHILE_FLAT_NO_OP")
    if re.search(r"\bstrategy\.position_size\b", code) and not (
        close_present and re.search(r"\bstrategy\.position_size\s*==\s*0\b", code)
    ):
        matched.add("POSITION_STATE_REFERENCE")
    if re.search(r"\bstrategy\.(?:cancel|cancel_all)\s*\(", code):
        matched.add("PENDING_ORDER_CANCELLATION")

    exit_calls = _calls(comments_masked, "strategy.exit", locator=code)
    if exit_calls:
        matched.add("STRATEGY_EXIT_ORDER_SEMANTICS")
    if any(re.search(r"\b(?:stop|limit|loss|profit|trail_points|trail_offset)\s*=", call) for call in exit_calls):
        matched.add("BACKEND_MANAGED_BRACKET")
    if any(re.search(r"\b(?:qty|qty_percent)\s*=", call) for call in exit_calls):
        matched.add("PARTIAL_EXIT")
    if re.search(r"\bstrategy\.(?:position_avg_price|opentrades|closedtrades)\b", code):
        matched.add("FILL_DEPENDENT_STRATEGY_STATE")

    pyramiding = re.search(r"\bpyramiding\s*=\s*(\d+)\b", declaration)
    if pyramiding:
        matched.add("PYRAMIDING_LITERAL_CONFIGURATION")
        if int(pyramiding.group(1)) > 1:
            matched.add("PYRAMIDING_SCALE_IN")
    elif re.search(r"\bpyramiding\s*=", declaration) and not pyramiding:
        indeterminate = True

    security_calls = _calls(comments_masked, "request.security", locator=code)
    lower_calls = _calls(comments_masked, "request.security_lower_tf", locator=code)
    if lower_calls:
        matched.add("LOWER_TIMEFRAME_ARRAY")
    literal_symbols: set[str] = set()
    developing_security = False
    for call in security_calls + lower_calls:
        args = _first_arguments(call)
        if args:
            if re.fullmatch(r"[\"'][^\"']+[\"']", args[0]):
                literal_symbols.add(args[0][1:-1])
            elif args[0] != "syminfo.tickerid":
                matched.add("MULTI_SYMBOL_OR_DYNAMIC_REQUEST")
                indeterminate = True
        if len(args) > 1 and not (
            re.fullmatch(r"[\"'][^\"']+[\"']", args[1])
            or args[1] == "timeframe.period"
        ):
            matched.add("MULTI_SYMBOL_OR_DYNAMIC_REQUEST")
            indeterminate = True
        if "barmerge.lookahead_on" in call:
            if re.search(r"\[[1-9]\d*\]", call):
                matched.add("CONFIRMED_HTF_OFFSET_PATTERN")
            else:
                matched.add("UNSAFE_FUTURE_LOOKAHEAD")
        elif call in security_calls:
            developing_security = True
    if developing_security:
        matched.add("DEVELOPING_SECURITY_REQUEST")
    if len(literal_symbols) > 1:
        matched.add("MULTI_SYMBOL_OR_DYNAMIC_REQUEST")

    if re.search(r"\brequest\.footprint\s*\(|\b(?:bid|ask)\b", code):
        matched.add("EXTERNAL_MICROSTRUCTURE_DATA")
    if re.search(r"\bticker\.(?:heikinashi|renko|kagi|linebreak|pointfigure)\s*\(", code):
        matched.add("SYNTHETIC_CHART_PRICE_BASIS")
    if re.search(r"\b(?:confidence|probability|support|resistance)\b", code, re.IGNORECASE):
        matched.add("ADVISORY_CONFIDENCE_LEVELS")

    string_literals = [
        match.group(2)
        for match in re.finditer(r"([\"'])(.*?)(?<!\\)\1", comments_masked, re.DOTALL)
    ]
    if any(value.lstrip().startswith("{") and value.rstrip().endswith("}") for value in string_literals):
        matched.add("CUSTOM_ALERT_JSON")
    if any(
        "{" in value
        and any(re.search(rf"[\"']?{re.escape(key)}[\"']?\s*:", value, re.I) for key in _AUTHORITY_KEYS)
        for value in string_literals
    ):
        matched.add("AUTHORITY_BEARING_CUSTOM_WEBHOOK")

    # An API-like token that was not captured as a balanced call is ambiguous.
    for token in _API_CALL.findall(code):
        api = token.split("(", 1)[0].strip()
        if not _calls(comments_masked, api, locator=code):
            indeterminate = True
    return matched, indeterminate


def analyze_source(
    source: str,
    *,
    registry_path: Path = REGISTRY_PATH,
    schema_path: Path = SCHEMA_PATH,
) -> PineSemanticAnalysisResult:
    source_hash = hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()
    try:
        registry: PineCapabilityRegistry = load_registry(registry_path, schema_path)
    except RegistryError:
        return PineSemanticAnalysisResult(
            analyzer_version=ANALYZER_VERSION,
            registry_id="UNAVAILABLE",
            registry_version="UNAVAILABLE",
            registry_sha256="",
            source_sha256=source_hash,
            matched_capabilities=(),
            effective_capability_level=CapabilityLevel.L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE,
            temporal_classes=(TemporalClass.T9_EXTERNAL_OR_UNAVAILABLE_DATA,),
            blocker_codes=("BLK_REGISTRY_UNAVAILABLE",),
            disclosure_codes=(),
            admin_review_points=("Restore and validate the version-controlled registry.",),
            confidence=AnalysisConfidence.ANALYSIS_INDETERMINATE,
        )

    matched_ids, indeterminate = _detect(source)
    entries_by_id = registry.by_id()
    entries = tuple(entries_by_id[capability_id] for capability_id in sorted(matched_ids) if capability_id in entries_by_id)
    effective = most_restrictive(entries) or CapabilityLevel.L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE
    confidence = (
        AnalysisConfidence.ANALYSIS_INDETERMINATE
        if indeterminate or not entries
        else AnalysisConfidence.HIGH_CONFIDENCE_MATCH
    )
    return PineSemanticAnalysisResult(
        analyzer_version=ANALYZER_VERSION,
        registry_id=registry.registry_id,
        registry_version=registry.registry_version,
        registry_sha256=registry.sha256,
        source_sha256=source_hash,
        matched_capabilities=tuple(entry.capability_id for entry in entries),
        effective_capability_level=effective,
        temporal_classes=tuple(sorted({entry.temporal_class for entry in entries}, key=str)),
        blocker_codes=tuple(sorted({entry.blocker_code for entry in entries if entry.blocker_code})),
        disclosure_codes=tuple(sorted({code for entry in entries for code in entry.mandatory_disclosure})),
        admin_review_points=tuple(sorted({point for entry in entries for point in entry.admin_review_points})),
        confidence=confidence,
    )
