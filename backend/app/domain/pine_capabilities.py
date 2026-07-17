"""Immutable loader for the version-controlled Pine capability registry."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "pine_capabilities" / "registry.v1.json"
SCHEMA_PATH = ROOT / "pine_capabilities" / "registry.schema.json"
FIXTURE_ID_PATTERN = re.compile(r"^F(?:0[1-9]|[1-5][0-9]|6[0-6])$")


class CapabilityLevel(StrEnum):
    L0_DIRECTLY_SUPPORTED = "L0_DIRECTLY_SUPPORTED"
    L1_NORMALIZED_WITHOUT_MATERIAL_CHANGE = "L1_NORMALIZED_WITHOUT_MATERIAL_CHANGE"
    L2_SUPPORTED_WITH_DISCLOSED_CHANGE = "L2_SUPPORTED_WITH_DISCLOSED_CHANGE"
    L3_REQUIRES_BACKEND_CAPABILITY = "L3_REQUIRES_BACKEND_CAPABILITY"
    L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE = "L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE"


class TemporalClass(StrEnum):
    T0_CONFIRMED_BAR_CLOSE_DETERMINISTIC = "T0_CONFIRMED_BAR_CLOSE_DETERMINISTIC"
    T1_REALTIME_FLUCTUATION_NORMALIZED_TO_CLOSE = "T1_REALTIME_FLUCTUATION_NORMALIZED_TO_CLOSE"
    T2_HTF_DEVELOPING_SERIES = "T2_HTF_DEVELOPING_SERIES"
    T3_HTF_CONFIRMED_OFFSET_SERIES = "T3_HTF_CONFIRMED_OFFSET_SERIES"
    T4_FUTURE_LEAKAGE_LOOKAHEAD = "T4_FUTURE_LEAKAGE_LOOKAHEAD"
    T5_LOWER_TIMEFRAME_ARRAY_AGGREGATION = "T5_LOWER_TIMEFRAME_ARRAY_AGGREGATION"
    T6_FILL_DEPENDENT_RECALCULATION = "T6_FILL_DEPENDENT_RECALCULATION"
    T7_SYNTHETIC_CHART_PRICE_BASIS = "T7_SYNTHETIC_CHART_PRICE_BASIS"
    T8_INTRABAR_NONREPRODUCIBLE_STATE = "T8_INTRABAR_NONREPRODUCIBLE_STATE"
    T9_EXTERNAL_OR_UNAVAILABLE_DATA = "T9_EXTERNAL_OR_UNAVAILABLE_DATA"


_LEVEL_RANK = {level: index for index, level in enumerate(CapabilityLevel)}


class RegistryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PineCapability:
    capability_id: str
    family: str
    pine_patterns: tuple[str, ...]
    pine_apis: tuple[str, ...]
    minimum_pine_version: int
    maximum_tested_pine_version: int
    capability_level: CapabilityLevel
    temporal_class: TemporalClass
    required_nova_backend_capability: str
    conversion_policy: str
    allowed_normalization: str
    mandatory_disclosure: tuple[str, ...]
    blocker_code: str | None
    admin_review_points: tuple[str, ...]
    user_facing_message: str
    fixtures: tuple[str, ...]
    effective_version: str
    deprecated_version: str | None
    priority: int


@dataclass(frozen=True, slots=True)
class PineCapabilityRegistry:
    registry_id: str
    registry_version: str
    effective_date: str
    capabilities: tuple[PineCapability, ...]
    sha256: str

    def by_id(self) -> dict[str, PineCapability]:
        return {entry.capability_id: entry for entry in self.capabilities}


def canonical_registry_json(value: dict[str, Any]) -> str:
    normalized = dict(value)
    normalized["capabilities"] = sorted(
        (dict(item) for item in value.get("capabilities", [])),
        key=lambda item: item.get("capability_id", ""),
    )
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def most_restrictive(entries: tuple[PineCapability, ...]) -> CapabilityLevel | None:
    if not entries:
        return None
    return max((entry.capability_level for entry in entries), key=_LEVEL_RANK.__getitem__)


def load_registry(
    registry_path: Path = REGISTRY_PATH,
    schema_path: Path = SCHEMA_PATH,
) -> PineCapabilityRegistry:
    try:
        value = json.loads(registry_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistryError("Pine capability registry is unavailable or malformed.") from exc
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.path))
    if errors:
        raise RegistryError(f"Pine capability registry schema error: {errors[0].message}")
    ids = [item["capability_id"] for item in value["capabilities"]]
    if len(ids) != len(set(ids)):
        raise RegistryError("Duplicate Pine capability ID.")
    entries: list[PineCapability] = []
    for item in value["capabilities"]:
        try:
            level = CapabilityLevel(item["capability_level"])
            temporal = TemporalClass(item["temporal_class"])
        except ValueError as exc:
            raise RegistryError("Unknown capability level or temporal class.") from exc
        if level is CapabilityLevel.L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE and not item["blocker_code"]:
            raise RegistryError(f"L4 capability {item['capability_id']} requires a blocker code.")
        if any(not FIXTURE_ID_PATTERN.fullmatch(fixture) for fixture in item["fixtures"]):
            raise RegistryError(f"Invalid fixture reference for {item['capability_id']}.")
        if "ENABLE" in item["conversion_policy"].upper() or item["required_nova_backend_capability"].upper().startswith("ENABLE_"):
            raise RegistryError("Capability registry cannot enable execution features.")
        entries.append(PineCapability(
            capability_id=item["capability_id"],
            family=item["family"],
            pine_patterns=tuple(item["pine_patterns"]),
            pine_apis=tuple(item["pine_apis"]),
            minimum_pine_version=item["minimum_pine_version"],
            maximum_tested_pine_version=item["maximum_tested_pine_version"],
            capability_level=level,
            temporal_class=temporal,
            required_nova_backend_capability=item["required_nova_backend_capability"],
            conversion_policy=item["conversion_policy"],
            allowed_normalization=item["allowed_normalization"],
            mandatory_disclosure=tuple(item["mandatory_disclosure"]),
            blocker_code=item["blocker_code"],
            admin_review_points=tuple(item["admin_review_points"]),
            user_facing_message=item["user_facing_message"],
            fixtures=tuple(item["fixtures"]),
            effective_version=item["effective_version"],
            deprecated_version=item["deprecated_version"],
            priority=item["priority"],
        ))
    canonical = canonical_registry_json(value)
    return PineCapabilityRegistry(
        registry_id=value["registry_id"],
        registry_version=value["registry_version"],
        effective_date=value["effective_date"],
        capabilities=tuple(sorted(entries, key=lambda entry: (-entry.priority, entry.capability_id))),
        sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
