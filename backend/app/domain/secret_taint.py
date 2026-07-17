"""Pure secret-taint and safe evidence-projection helpers."""
from __future__ import annotations

import json
import math
import re
import secrets
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Iterable, Mapping


class SecretTaintClass(StrEnum):
    SERVER_TRUSTED = "SERVER_TRUSTED"
    USER_IDENTIFIER = "USER_IDENTIFIER"
    USER_METADATA = "USER_METADATA"
    SECRET = "SECRET"
    DERIVED_SAFE = "DERIVED_SAFE"


GENERIC_SAFE_DETAIL = "canonical-evidence-unavailable"
MAX_SAFE_METADATA_BYTES = 2 * 1024

_CREDENTIAL_PATTERN = re.compile(r"(?i)(?<![A-Za-z0-9_])nwk_[A-Za-z0-9_-]+")
_STRATEGY_VERSION_PATTERN = re.compile(r"[A-Za-z0-9._:+-]{1,40}\Z")
_TIMEFRAME_PATTERN = re.compile(r"[A-Za-z0-9._:+/-]{1,12}\Z")
_SAFE_DETAIL_PATTERN = re.compile(r"[a-z0-9][a-z0-9._:-]{0,159}\Z")
_SAFE_DETAIL_CODES = frozenset(
    {
        GENERIC_SAFE_DETAIL,
        "canonical-evidence-rejected",
        "registry-unavailable",
        "semantic-analysis-unavailable",
    }
)


def is_credential_shaped(value: object, known_secrets: Iterable[str] = ()) -> bool:
    """Return whether a value contains credential-shaped or request-known secret text."""
    if not isinstance(value, str):
        return False
    for known_secret in known_secrets:
        if (
            isinstance(known_secret, str)
            and secrets.compare_digest(value.encode("utf-8"), known_secret.encode("utf-8"))
        ):
            return True
    return _CREDENTIAL_PATTERN.search(value) is not None


def validate_bounded_safe_string(
    value: object,
    *,
    maximum_length: int,
    pattern: re.Pattern[str],
    known_secrets: Iterable[str] = (),
) -> str | None:
    """Return a closed, bounded string or omit it without echoing unsafe input."""
    if not isinstance(value, str) or not 0 < len(value) <= maximum_length:
        return None
    if is_credential_shaped(value, known_secrets):
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    return value if pattern.fullmatch(value) else None


def project_safe_signal_id(value: object, known_secrets: Iterable[str] = ()) -> str:
    """Project a signal identifier without copying secret-shaped content."""
    if is_credential_shaped(value, known_secrets):
        return "credential-shaped-id"
    if not isinstance(value, str) or not value or len(value) > 128:
        return "invalid-signal-id"
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return "invalid-signal-id"
    return value


def build_safe_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    known_secrets: Iterable[str] = (),
) -> Mapping[str, str | float]:
    """Build the immutable, closed metadata projection allowed for future evidence."""
    source = dict(metadata or {})
    local_secrets = tuple(known_secrets)
    safe: dict[str, str | float] = {}

    strategy_version = validate_bounded_safe_string(
        source.get("strategy_version"),
        maximum_length=40,
        pattern=_STRATEGY_VERSION_PATTERN,
        known_secrets=local_secrets,
    )
    if strategy_version is not None:
        safe["strategy_version"] = strategy_version

    timeframe = validate_bounded_safe_string(
        source.get("timeframe"),
        maximum_length=12,
        pattern=_TIMEFRAME_PATTERN,
        known_secrets=local_secrets,
    )
    if timeframe is not None:
        safe["timeframe"] = timeframe

    reference_price = source.get("reference_price")
    if (
        isinstance(reference_price, (int, float))
        and not isinstance(reference_price, bool)
        and math.isfinite(reference_price)
        and reference_price > 0
    ):
        safe["reference_price"] = float(reference_price)

    encoded = json.dumps(safe, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_SAFE_METADATA_BYTES:
        return MappingProxyType({})
    return MappingProxyType(safe)


def validate_safe_detail(
    detail: object,
    *,
    known_secrets: Iterable[str] = (),
) -> str:
    """Return only a closed server-authored detail code, otherwise a generic code."""
    if (
        isinstance(detail, str)
        and len(detail) <= 160
        and detail in _SAFE_DETAIL_CODES
        and _SAFE_DETAIL_PATTERN.fullmatch(detail)
        and not is_credential_shaped(detail, known_secrets)
    ):
        return detail
    return GENERIC_SAFE_DETAIL


def redact(value: object, known_secrets: Iterable[str] = ()) -> str:
    """Render a diagnostic value without exposing credential-shaped text."""
    if is_credential_shaped(value, known_secrets):
        return "[REDACTED]"
    return str(value)
