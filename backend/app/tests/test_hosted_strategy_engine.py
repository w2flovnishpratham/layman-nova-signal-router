from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.hosted_strategy import Candle, Expression, StrategyIR
from app.services.hosted_strategy_engine import calculate_indicators, evaluate_expression, replay, validate_ir_document

FIXTURES = Path(__file__).parents[1] / "fixtures"


def document(name="hosted_ema_crossover_v1.json"):
    return json.loads((FIXTURES / name).read_text())


def candles(values, *, start=datetime(2026, 7, 13, 4, 0, tzinfo=timezone.utc), volume=100):
    return [Candle(close_timestamp=start + timedelta(minutes=i+1), open=v, high=v+1, low=v-1, close=v, volume=volume, finalized=True) for i, v in enumerate(values)]


@pytest.mark.parametrize("fixture", ["hosted_ema_crossover_v1.json", "hosted_rsi_trend_v1.json", "hosted_supertrend_v1.json"])
def test_reference_ir_fixtures_are_strict_and_eligible(fixture):
    ir, report = validate_ir_document(document(fixture))
    assert ir is not None
    assert report["eligible"] is True


@pytest.mark.parametrize("mutation,code", [
    (lambda d: d.update(extra_field=True), "SCHEMA_INVALID"),
    (lambda d: d.update(ir_version=2), "SCHEMA_INVALID"),
    (lambda d: d["indicators"][0].update(type="CUSTOM"), "SCHEMA_INVALID"),
    (lambda d: d["indicators"][0].update(period=-1), "SCHEMA_INVALID"),
    (lambda d: d["indicators"].append(copy.deepcopy(d["indicators"][0])), "DUPLICATE_INDICATOR_ID"),
    (lambda d: d["conditions"]["bullish"]["left"].update(indicator="missing"), "UNKNOWN_REFERENCE"),
    (lambda d: d.update(underlying="BANKNIFTY"), "SCHEMA_INVALID"),
    (lambda d: d.update(timeframe="5m"), "SCHEMA_INVALID"),
    (lambda d: d["actions"].__setitem__(slice(None), [a for a in d["actions"] if a["action"] != "EXIT"]), "MISSING_EXIT_COVERAGE"),
    (lambda d: d["actions"][3].update(priority=20), "CONFLICTING_ENTRY_PRIORITY"),
    (lambda d: d.update(quantity=65), "FORBIDDEN_AUTHORITY_FIELD"),
    (lambda d: d.update(description="exec('sentinel')"), "FORBIDDEN_CONTENT"),
    (lambda d: d.update(description="https://attacker.invalid/x"), "FORBIDDEN_CONTENT"),
])
def test_ir_rejects_invalid_or_privileged_content(mutation, code):
    data = document(); mutation(data)
    _, report = validate_ir_document(data)
    assert report["eligible"] is False
    assert code in {item["code"] for item in report["errors"]}


def test_expression_depth_and_division_by_zero_are_safe():
    node = {"op": "CONSTANT", "constant": 1}
    for _ in range(21): node = {"op": "ABS", "arg": node}
    data = document(); data["conditions"]["bullish"] = node
    _, report = validate_ir_document(data)
    assert "EXPRESSION_TOO_DEEP" in {item["code"] for item in report["errors"]}
    expr = Expression.model_validate({"op": "DIVIDE", "left": {"op": "CONSTANT", "constant": 1}, "right": {"op": "CONSTANT", "constant": 0}})
    with pytest.raises(ValueError, match="division by zero"):
        evaluate_expression(expr, index=0, candles=candles([100]), indicators={}, parameters={}, position="FLAT")


def test_fixed_indicator_outputs_and_missing_values():
    data = document("hosted_rsi_trend_v1.json")
    data["indicators"] = [
        {"id": "sma", "type": "SMA", "source": "close", "period": 3},
        {"id": "ema", "type": "EMA", "source": "close", "period": 3},
        {"id": "rsi", "type": "RSI", "source": "close", "period": 3},
        {"id": "atr", "type": "ATR", "source": "close", "period": 3},
        {"id": "vwap", "type": "VWAP", "source": "close"},
        {"id": "hi", "type": "HIGHEST", "source": "close", "period": 3},
        {"id": "lo", "type": "LOWEST", "source": "close", "period": 3}
    ]
    ir = StrategyIR.model_validate(data); output = calculate_indicators(ir, candles([10, 11, 12, 13]))
    assert output["sma"] == [None, None, pytest.approx(11), pytest.approx(12)]
    assert output["ema"] == [None, None, pytest.approx(11), pytest.approx(12)]
    assert output["rsi"][-1] == pytest.approx(100)
    assert output["atr"][:2] == [None, None]
    assert output["atr"][-1] == pytest.approx(2)
    assert output["vwap"][-1] == pytest.approx(11.5)
    assert output["hi"][-1] == 13 and output["lo"][-1] == 11


def test_crossing_previous_and_deterministic_replay():
    ir = StrategyIR.model_validate(document())
    series = candles([100+i*.1 for i in range(21)] + [95, 110, 112, 114])
    first = replay(ir, series); second = replay(ir, list(series))
    assert first == second
    assert first["purpose"] == "verification_not_profitability_certification"
    with pytest.raises(ValueError, match="DUPLICATE_CANDLES"):
        replay(ir, series + [series[-1]])


def test_in_progress_out_of_order_and_non_finite_candles_rejected():
    base = {"close_timestamp": "2026-07-13T09:21:00+05:30", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}
    with pytest.raises(ValidationError): Candle.model_validate({**base, "finalized": False})
    with pytest.raises(ValidationError): Candle.model_validate({**base, "close": float("nan"), "finalized": True})
    ir = StrategyIR.model_validate(document()); rows = candles(range(100, 125)); rows[-1], rows[-2] = rows[-2], rows[-1]
    with pytest.raises(ValueError, match="CANDLES_NOT_STRICTLY_ORDERED"): replay(ir, rows)
