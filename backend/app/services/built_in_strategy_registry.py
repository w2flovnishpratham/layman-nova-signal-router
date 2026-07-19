"""Server-owned definitions for NOVA's product strategy catalog.

This registry is presentation and setup authority only.  A strategy is marked
READY only when a real execution adapter exists in this repository.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def _standard_setup_schema() -> dict[str, Any]:
    return {
        "fields": [
            {
                "key": "direction",
                "type": "choice",
                "label": "Which signals should NOVA trade?",
                "options": ["CE", "PE", "BOTH"],
                "required": True,
                "default": "BOTH",
            },
            {
                "key": "lots",
                "type": "integer",
                "label": "How many lots should be used?",
                "minimum": 1,
                "maximum": 20,
                "required": True,
                "default": 1,
            },
            {
                "key": "stop_loss_percent",
                "type": "decimal",
                "label": "What stop loss percentage should be applied?",
                "minimum": 0,
                "maximum": 100,
                "required": True,
                "default": 10,
            },
            {
                "key": "take_profit_percent",
                "type": "decimal",
                "label": "What take profit percentage should be applied?",
                "minimum": 0,
                "maximum": 1000,
                "required": True,
                "default": 20,
            },
        ]
    }


_BUILT_INS: tuple[dict[str, Any], ...] = (
    {
        "strategy_key": "nova-supertrend",
        "catalog_code": "supertrend",
        "name": "Supertrend",
        "version": "1.0.0",
        "description": "NOVA built-in NIFTY Supertrend strategy.",
        "availability": "READY",
        "disabled_reason": None,
        "paper_eligible": True,
        "live_eligible": False,
        "execution_adapter": "strategy_webhook:supertrend",
        "setup_schema": _standard_setup_schema(),
    },
    {
        "strategy_key": "nova-orb",
        "catalog_code": "orb",
        "name": "ORB",
        "version": None,
        "description": "Opening Range Breakout strategy.",
        "availability": "COMING_SOON",
        "disabled_reason": "Missing execution adapter",
        "paper_eligible": False,
        "live_eligible": False,
        "execution_adapter": None,
        "setup_schema": {"fields": []},
    },
    {
        "strategy_key": "nova-vwap",
        "catalog_code": "vwap",
        "name": "VWAP",
        "version": None,
        "description": "Volume Weighted Average Price strategy.",
        "availability": "COMING_SOON",
        "disabled_reason": "Missing execution adapter",
        "paper_eligible": False,
        "live_eligible": False,
        "execution_adapter": None,
        "setup_schema": {"fields": []},
    },
    {
        "strategy_key": "nova-rsi",
        "catalog_code": "rsi",
        "name": "RSI",
        "version": None,
        "description": "Relative Strength Index strategy.",
        "availability": "COMING_SOON",
        "disabled_reason": "Missing execution adapter",
        "paper_eligible": False,
        "live_eligible": False,
        "execution_adapter": None,
        "setup_schema": {"fields": []},
    },
    {
        "strategy_key": "nova-scalper",
        "catalog_code": "scalper",
        "name": "Scalper",
        "version": None,
        "description": "NOVA intraday scalping strategy.",
        "availability": "COMING_SOON",
        "disabled_reason": "Missing execution adapter",
        "paper_eligible": False,
        "live_eligible": False,
        "execution_adapter": None,
        "setup_schema": {"fields": []},
    },
)


def list_built_ins() -> list[dict[str, Any]]:
    return deepcopy(list(_BUILT_INS))


def get_built_in(strategy_key: str) -> dict[str, Any] | None:
    normalized = str(strategy_key or "").strip().lower()
    return next(
        (deepcopy(item) for item in _BUILT_INS if item["strategy_key"] == normalized),
        None,
    )


def key_for_catalog_code(code: str | None) -> str | None:
    normalized = str(code or "").strip().lower()
    item = next((item for item in _BUILT_INS if item["catalog_code"] == normalized), None)
    return str(item["strategy_key"]) if item else None


def standard_setup_schema() -> dict[str, Any]:
    return _standard_setup_schema()
