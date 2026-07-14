"""Pure deterministic Strategy IR validation, evaluation, and replay."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import time
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.schemas.hosted_strategy import (
    MAX_EXPRESSION_DEPTH, VALIDATOR_VERSION, ActionRule, Candle, Expression, StrategyIR,
)


def canonical_ir(ir: StrategyIR) -> tuple[dict[str, Any], str]:
    data = ir.model_dump(mode="json", exclude_none=True)
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return data, hashlib.sha256(raw.encode()).hexdigest()


def _depth(node: Expression) -> int:
    children = [child for child in (node.left, node.right, node.arg) if child]
    children.extend(node.args or [])
    return 1 + max((_depth(child) for child in children), default=0)


def minimum_warmup(ir: StrategyIR) -> int:
    periods = []
    for item in ir.indicators:
        period = item.atr_period if item.type == "SUPERTREND" else item.period
        periods.append(int(period or 1) + (1 if item.type in {"RSI", "ATR", "ROC", "SUPERTREND"} else 0))
    return max(periods, default=1)


def validate_ir_document(document: dict[str, Any]) -> tuple[StrategyIR | None, dict[str, Any]]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    forbidden_keys = {"user_id", "position_id", "lots", "quantity", "qty", "security_id", "strike", "expiry", "trading_symbol", "order_type", "product_type", "live_orders", "broker_id", "credential", "url", "python", "pine", "javascript", "sql", "shell", "code"}
    suspicious = re.compile(r"https?://|\b(?:eval|exec|compile|subprocess|__import__)\s*\(|\b(?:select|insert|update|delete|drop)\s+\w+|\{\{|<script|#!/bin/", re.IGNORECASE)

    def scan(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                if str(key).lower() in forbidden_keys:
                    errors.append({"code": "FORBIDDEN_AUTHORITY_FIELD", "path": child_path, "message": "Execution-authority or executable-code fields are forbidden."})
                scan(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value): scan(child, f"{path}.{index}")
        elif isinstance(value, str) and suspicious.search(value):
            errors.append({"code": "FORBIDDEN_CONTENT", "path": path, "message": "Executable, URL, template, or query content is forbidden."})

    scan(document)
    try:
        if len(json.dumps(document, allow_nan=False).encode()) > 262_144:
            errors.append({"code": "IR_TOO_LARGE", "path": "", "message": "IR exceeds 262144 bytes."})
    except (TypeError, ValueError):
        errors.append({"code": "NON_FINITE_OR_INVALID_JSON", "path": "", "message": "IR must be finite JSON data."})
    try:
        ir = StrategyIR.model_validate(document)
    except ValidationError as exc:
        for finding in exc.errors(include_url=False):
            errors.append({
                "code": "SCHEMA_INVALID",
                "path": ".".join(map(str, finding["loc"])),
                "message": finding["msg"],
            })
        return None, _report(None, errors, warnings)

    indicator_ids = [item.id for item in ir.indicators]
    if len(indicator_ids) != len(set(indicator_ids)):
        errors.append({"code": "DUPLICATE_INDICATOR_ID", "path": "indicators", "message": "Indicator IDs must be unique."})
    condition_ids = set(ir.conditions)
    for name, expression in ir.conditions.items():
        if _depth(expression) > MAX_EXPRESSION_DEPTH:
            errors.append({"code": "EXPRESSION_TOO_DEEP", "path": f"conditions.{name}", "message": f"Expression depth exceeds {MAX_EXPRESSION_DEPTH}."})
        for reference_type, reference in _references(expression):
            allowed = set(indicator_ids) if reference_type == "indicator" else set(ir.parameters)
            if reference not in allowed:
                errors.append({"code": "UNKNOWN_REFERENCE", "path": f"conditions.{name}", "message": f"Unknown {reference_type} '{reference}'."})
    for index, action in enumerate(ir.actions):
        if action.when not in condition_ids:
            errors.append({"code": "UNKNOWN_CONDITION", "path": f"actions.{index}.when", "message": f"Unknown condition '{action.when}'."})
    if not any(action.action == "EXIT" for action in ir.actions):
        errors.append({"code": "MISSING_EXIT_COVERAGE", "path": "actions", "message": "At least one EXIT rule is required."})
    entries: dict[int, set[str]] = {}
    for action in ir.actions:
        if action.action in {"BUY_CE", "BUY_PE"}:
            entries.setdefault(action.priority, set()).add(action.action)
    if any(len(actions) > 1 for actions in entries.values()):
        errors.append({"code": "CONFLICTING_ENTRY_PRIORITY", "path": "actions", "message": "BUY_CE and BUY_PE must not share a priority."})
    required = minimum_warmup(ir)
    if ir.warmup_bars < required:
        errors.append({"code": "WARMUP_INSUFFICIENT", "path": "warmup_bars", "message": f"At least {required} warmup bars are required."})
    elif ir.warmup_bars > required * 4:
        warnings.append({"code": "WARMUP_LARGE", "path": "warmup_bars", "message": "Warmup is materially larger than indicator requirements."})
    return ir, _report(ir, errors, warnings)


def _references(node: Expression):
    if node.indicator:
        yield "indicator", node.indicator
    if node.parameter:
        yield "parameter", node.parameter
    for child in [child for child in (node.left, node.right, node.arg) if child] + (node.args or []):
        yield from _references(child)


def _report(ir: StrategyIR | None, errors: list[dict[str, str]], warnings: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "validator_version": VALIDATOR_VERSION,
        "ir_contract_version": ir.ir_version if ir else None,
        "eligible": not errors,
        "errors": errors,
        "warnings": warnings,
        "minimum_warmup_bars": minimum_warmup(ir) if ir else None,
    }


def _sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period: total -= values[i - period]
        if i >= period - 1: out[i] = total / period
    return out


def _ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period: return out
    out[period - 1] = sum(values[:period]) / period
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        out[i] = values[i] * alpha + float(out[i - 1]) * (1 - alpha)
    return out


def _wilder(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period: return out
    out[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        out[i] = (float(out[i - 1]) * (period - 1) + values[i]) / period
    return out


def _atr(candles: list[Candle], period: int) -> list[float | None]:
    tr = [c.high - c.low if i == 0 else max(c.high - c.low, abs(c.high - candles[i-1].close), abs(c.low - candles[i-1].close)) for i, c in enumerate(candles)]
    return _wilder(tr, period)


def calculate_indicators(ir: StrategyIR, candles: list[Candle]) -> dict[str, list[float | None]]:
    result: dict[str, list[float | None]] = {}
    for item in ir.indicators:
        values = [float(getattr(candle, item.source)) for candle in candles]
        period = int(item.period or 1)
        if item.type == "SMA": series = _sma(values, period)
        elif item.type == "EMA": series = _ema(values, period)
        elif item.type == "RSI":
            changes = [0.0] + [values[i] - values[i-1] for i in range(1, len(values))]
            gains, losses = _wilder([max(v, 0) for v in changes], period), _wilder([max(-v, 0) for v in changes], period)
            series = [None if g is None or l is None else 100.0 if l == 0 else 100 - 100 / (1 + g/l) for g, l in zip(gains, losses)]
        elif item.type == "ATR": series = _atr(candles, period)
        elif item.type == "VWAP":
            total_pv = total_v = 0.0; series = []
            for candle in candles:
                typical = (candle.high + candle.low + candle.close) / 3
                total_pv += typical * candle.volume; total_v += candle.volume
                series.append(total_pv / total_v if total_v else None)
        elif item.type in {"HIGHEST", "LOWEST"}:
            fn = max if item.type == "HIGHEST" else min
            series = [None if i < period-1 else fn(values[i-period+1:i+1]) for i in range(len(values))]
        elif item.type == "ROC":
            series = [None if i < period or values[i-period] == 0 else (values[i]/values[i-period]-1)*100 for i in range(len(values))]
        else:
            atr = _atr(candles, int(item.atr_period or 10)); multiplier = float(item.multiplier or 3)
            upper: list[float | None] = [None] * len(candles); lower = upper.copy(); series = upper.copy()
            for i, candle in enumerate(candles):
                if atr[i] is None: continue
                mid = (candle.high + candle.low) / 2; basic_up = mid + multiplier*float(atr[i]); basic_lo = mid - multiplier*float(atr[i])
                upper[i] = basic_up if i == 0 or upper[i-1] is None or basic_up < float(upper[i-1]) or candles[i-1].close > float(upper[i-1]) else upper[i-1]
                lower[i] = basic_lo if i == 0 or lower[i-1] is None or basic_lo > float(lower[i-1]) or candles[i-1].close < float(lower[i-1]) else lower[i-1]
                if i == 0 or series[i-1] is None: series[i] = upper[i]
                elif series[i-1] == upper[i-1]: series[i] = upper[i] if candle.close <= float(upper[i]) else lower[i]
                else: series[i] = lower[i] if candle.close >= float(lower[i]) else upper[i]
        result[item.id] = series
    return result


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("expression requires a finite number")
    return float(value)


def evaluate_expression(node: Expression, *, index: int, candles: list[Candle], indicators: dict[str, list[float | None]], parameters: dict[str, Any], position: str) -> Any:
    def ev(child: Expression, at: int = index):
        return evaluate_expression(child, index=at, candles=candles, indicators=indicators, parameters=parameters, position=position)
    if index < 0: return None
    if node.op == "CANDLE_FIELD": return getattr(candles[index], str(node.field))
    if node.op == "INDICATOR_VALUE": return indicators[str(node.indicator)][index]
    if node.op == "PARAMETER_VALUE": return parameters[str(node.parameter)]
    if node.op == "CONSTANT": return node.constant
    if node.op == "POSITION_STATE": return position == node.state
    if node.op == "PREVIOUS_VALUE": return ev(node.arg, index-int(node.bars or 1))
    if node.op in {"AND", "OR"}:
        values = [bool(ev(arg)) for arg in node.args or []]
        return all(values) if node.op == "AND" else any(values)
    if node.op == "NOT": return not bool(ev(node.arg))
    if node.op == "ABS": return abs(_finite_number(ev(node.arg)))
    if node.op in {"MIN", "MAX"}:
        values = [_finite_number(ev(arg)) for arg in node.args or []]
        return (min if node.op == "MIN" else max)(values)
    left, right = ev(node.left), ev(node.right)
    if node.op in {"CROSSES_ABOVE", "CROSSES_BELOW"}:
        if index == 0 or left is None or right is None: return False
        previous_left, previous_right = ev(node.left, index-1), ev(node.right, index-1)
        if previous_left is None or previous_right is None: return False
        return (previous_left <= previous_right and left > right) if node.op == "CROSSES_ABOVE" else (previous_left >= previous_right and left < right)
    if node.op in {"EQ", "NE"}: return (left == right) if node.op == "EQ" else (left != right)
    if left is None or right is None: return False
    if node.op == "GT": return left > right
    if node.op == "GTE": return left >= right
    if node.op == "LT": return left < right
    if node.op == "LTE": return left <= right
    a, b = _finite_number(left), _finite_number(right)
    if node.op == "ADD": return a+b
    if node.op == "SUBTRACT": return a-b
    if node.op == "MULTIPLY": return a*b
    if node.op == "DIVIDE":
        if b == 0: raise ValueError("division by zero")
        return a/b
    raise ValueError("unsupported expression operation")


@dataclass(frozen=True)
class EvaluationResult:
    action: str
    rule: str | None
    indicator_fingerprint: str
    result_fingerprint: str


def evaluate(ir: StrategyIR, candles: list[Candle], *, position: str = "FLAT", last_action_index: dict[str, int] | None = None) -> EvaluationResult:
    if len(candles) < ir.warmup_bars:
        raise ValueError("WARMUP_INCOMPLETE")
    if any(candles[i].close_timestamp >= candles[i+1].close_timestamp for i in range(len(candles)-1)):
        raise ValueError("CANDLES_NOT_STRICTLY_ORDERED")
    indicators = calculate_indicators(ir, candles); index = len(candles)-1
    local = candles[index].close_timestamp.astimezone(ZoneInfo(ir.session.timezone)).time()
    local_date = candles[index].close_timestamp.astimezone(ZoneInfo(ir.session.timezone)).date()
    entry_start, entry_end, force_exit = map(time.fromisoformat, (ir.session.entry_start, ir.session.entry_end, ir.session.force_exit_time))
    candidates: list[ActionRule] = []
    for action in ir.actions:
        if action.position_states and position not in action.position_states: continue
        if action.action.startswith("BUY_") and (position != "FLAT" or not entry_start <= local <= entry_end or (ir.session.skip_expiry_day and local_date.weekday() == 3)): continue
        if action.action == "EXIT" and position == "FLAT": continue
        if action.cooldown_bars and index - (last_action_index or {}).get(action.action, -10**9) <= action.cooldown_bars: continue
        if bool(evaluate_expression(ir.conditions[action.when], index=index, candles=candles, indicators=indicators, parameters=ir.parameters, position=position)):
            candidates.append(action)
    if position != "FLAT" and local >= force_exit:
        candidates.append(ActionRule(action="EXIT", when=next(iter(ir.conditions)), priority=1000, audit_label="SESSION_FORCE_EXIT"))
    chosen = max(candidates, key=lambda item: (item.priority, item.action == "EXIT", item.action), default=None)
    action = chosen.action if chosen else "HOLD"
    indicator_tail = {key: values[-2:] for key, values in sorted(indicators.items())}
    indicator_hash = hashlib.sha256(json.dumps(indicator_tail, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result_hash = hashlib.sha256(f"{candles[-1].close_timestamp.isoformat()}|{position}|{action}|{chosen.when if chosen else ''}|{indicator_hash}".encode()).hexdigest()
    return EvaluationResult(action, chosen.audit_label or chosen.when if chosen else None, indicator_hash, result_hash)


def replay(ir: StrategyIR, candles: list[Candle], *, starting_position: str = "FLAT") -> dict[str, Any]:
    ordered = list(candles)
    if len({c.close_timestamp for c in ordered}) != len(ordered):
        raise ValueError("DUPLICATE_CANDLES")
    if any(ordered[i].close_timestamp >= ordered[i+1].close_timestamp for i in range(len(ordered)-1)):
        raise ValueError("CANDLES_NOT_STRICTLY_ORDERED")
    position = starting_position; timeline = []; last_actions: dict[str, int] = {}
    for index in range(ir.warmup_bars-1, len(ordered)):
        result = evaluate(ir, ordered[:index+1], position=position, last_action_index=last_actions)
        if result.action != "HOLD":
            timeline.append({"candle_close_timestamp": ordered[index].close_timestamp.isoformat(), "action": result.action, "rule": result.rule})
            last_actions[result.action] = index
            if result.action == "BUY_CE": position = "LONG_CE"
            elif result.action == "BUY_PE": position = "LONG_PE"
            elif result.action == "EXIT": position = "FLAT"
    fingerprint = hashlib.sha256(json.dumps(timeline, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"purpose": "verification_not_profitability_certification", "timeline": timeline, "fingerprint": fingerprint, "final_position": position}
