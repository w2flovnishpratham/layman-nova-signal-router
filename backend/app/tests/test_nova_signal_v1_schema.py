from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.schemas.nova_signal_v1 import NovaSignalV1


def valid_payload() -> dict:
    return {
        "version": "nova.v1",
        "secret": "webhook-secret",
        "signal_id": "signal-001",
        "strategy_code": "SUPERTREND_NIFTY",
        "action": "ENTRY",
        "intent": "BULLISH",
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "option_side": "AUTO",
        "strike_mode": "ATM",
        "expiry_mode": "NEXT_WEEKLY",
        "qty_mode": "LOTS",
        "lots": 1,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
        "source": "tradingview",
        "timestamp": "2026-07-03T09:15:00+05:30",
    }


def test_valid_nova_v1_payload_parses():
    payload = NovaSignalV1.model_validate(valid_payload())

    assert payload.version == "nova.v1"
    assert isinstance(payload.timestamp, datetime)
    assert payload.metadata == {}


def test_valid_nova_v1_manual_payload_parses_required_values():
    data = {
        **valid_payload(),
        "strike_mode": "MANUAL",
        "strike": 24500,
        "expiry_mode": "MANUAL",
        "expiry": "2026-07-09",
        "metadata": {"alert": "supertrend"},
    }

    payload = NovaSignalV1.model_validate(data)

    assert payload.strike == 24500
    assert payload.expiry == date(2026, 7, 9)
    assert payload.metadata == {"alert": "supertrend"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "v1"),
        ("action", "BUY"),
        ("intent", "LONG"),
        ("symbol", "FINNIFTY"),
        ("instrument_type", "EQ"),
        ("option_side", "CALL"),
        ("strike_mode", "SPOT"),
        ("expiry_mode", "WEEKLY"),
        ("qty_mode", "UNITS"),
        ("order_type", "SL"),
        ("product_type", "CNC"),
        ("source", "external"),
    ],
)
def test_unknown_enum_values_fail_validation(field, value):
    data = valid_payload()
    data[field] = value

    with pytest.raises(ValidationError):
        NovaSignalV1.model_validate(data)


def test_manual_strike_mode_requires_strike():
    data = {**valid_payload(), "strike_mode": "MANUAL"}

    with pytest.raises(ValidationError, match="strike is required"):
        NovaSignalV1.model_validate(data)


def test_manual_expiry_mode_requires_expiry():
    data = {**valid_payload(), "expiry_mode": "MANUAL"}

    with pytest.raises(ValidationError, match="expiry is required"):
        NovaSignalV1.model_validate(data)


@pytest.mark.parametrize("lots", [0, -1])
def test_lots_must_be_at_least_one(lots):
    data = {**valid_payload(), "lots": lots}

    with pytest.raises(ValidationError):
        NovaSignalV1.model_validate(data)


def test_timestamp_must_parse_as_datetime():
    data = {**valid_payload(), "timestamp": "not-a-date"}

    with pytest.raises(ValidationError):
        NovaSignalV1.model_validate(data)
