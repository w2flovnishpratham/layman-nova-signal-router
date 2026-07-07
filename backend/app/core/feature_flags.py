from __future__ import annotations

import os
from collections.abc import Mapping


MULTI_STRATEGY_MODEL = "MULTI_STRATEGY_MODEL"
MULTI_STRATEGY_FANOUT = "MULTI_STRATEGY_FANOUT"
V2_PAPER_RUNNER_DEBUG = "V2_PAPER_RUNNER_DEBUG"
CUSTOM_WEBHOOKS = "CUSTOM_WEBHOOKS"
STRATEGY_CATALOG_UI = "STRATEGY_CATALOG_UI"

FEATURE_FLAGS = (
    MULTI_STRATEGY_MODEL,
    MULTI_STRATEGY_FANOUT,
    V2_PAPER_RUNNER_DEBUG,
    CUSTOM_WEBHOOKS,
    STRATEGY_CATALOG_UI,
)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def parse_feature_flag_value(value: str | None, *, default: bool = False) -> bool:
    """Parse a feature flag value and fail closed for unknown values."""
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return default


def is_feature_enabled(name: str, env: Mapping[str, str] | None = None) -> bool:
    if name not in FEATURE_FLAGS:
        raise ValueError(f"Unknown feature flag: {name}")
    source = os.environ if env is None else env
    return parse_feature_flag_value(source.get(name), default=False)


def feature_flag_states(env: Mapping[str, str] | None = None) -> dict[str, bool]:
    return {name: is_feature_enabled(name, env=env) for name in FEATURE_FLAGS}
