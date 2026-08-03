from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.pine_capabilities import (
    CapabilityLevel,
    REGISTRY_PATH,
    SCHEMA_PATH,
    RegistryError,
    canonical_registry_json,
    load_registry,
    most_restrictive,
)
from app.services.pine_semantic_preanalyzer import analyze_source


FIXTURES = Path(__file__).parent / "pine_fixtures" / "r1a"


def _documents():
    return (
        json.loads(REGISTRY_PATH.read_text(encoding="utf-8")),
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
    )


def _write(tmp_path, value, schema):
    registry = tmp_path / "registry.json"
    schema_path = tmp_path / "schema.json"
    registry.write_text(json.dumps(value), encoding="utf-8")
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    return registry, schema_path


def test_registry_schema_types_hash_and_fixture_references_are_valid():
    registry = load_registry()
    assert registry.registry_id == "nova.pine-capabilities"
    assert registry.registry_version == "v1"
    assert len(registry.sha256) == 64
    assert len(registry.capabilities) >= 25
    assert all(entry.fixtures for entry in registry.capabilities)


def test_canonical_hash_ignores_json_key_and_capability_entry_order(tmp_path):
    value, schema = _documents()
    reversed_value = {
        "capabilities": list(reversed(value["capabilities"])),
        "effective_date": value["effective_date"],
        "registry_version": value["registry_version"],
        "registry_id": value["registry_id"],
    }
    assert canonical_registry_json(value) == canonical_registry_json(reversed_value)
    registry_path, schema_path = _write(tmp_path, reversed_value, schema)
    assert load_registry(registry_path, schema_path).sha256 == load_registry().sha256


def test_every_registry_fixture_resolves_and_proves_the_cited_capability():
    fixtures = {
        path.name.split("_", 1)[0]: path
        for path in sorted(FIXTURES.glob("*.pine"))
    }
    registry = load_registry()
    analyses = {
        fixture_id: analyze_source(path.read_text(encoding="utf-8"))
        for fixture_id, path in fixtures.items()
    }
    for entry in registry.capabilities:
        for fixture_id in entry.fixtures:
            assert fixture_id in fixtures, (entry.capability_id, fixture_id)
            assert entry.capability_id in analyses[fixture_id].matched_capabilities, (
                entry.capability_id,
                fixture_id,
            )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["capabilities"].append(dict(value["capabilities"][0])),
        lambda value: value["capabilities"][0].update(capability_level="UNKNOWN"),
        lambda value: value["capabilities"][0].update(temporal_class="UNKNOWN"),
        lambda value: value["capabilities"][-1].update(blocker_code=None),
        lambda value: value["capabilities"][0].update(fixtures=["F99"]),
        lambda value: value["capabilities"][0].update(conversion_policy="ENABLE_LIVE_EXECUTION"),
    ],
)
def test_invalid_registry_fails_closed(tmp_path, mutate):
    value, schema = _documents()
    mutate(value)
    registry, schema_path = _write(tmp_path, value, schema)
    with pytest.raises(RegistryError):
        load_registry(registry, schema_path)


def test_most_restrictive_match_wins():
    registry = load_registry()
    entries = registry.by_id()
    assert most_restrictive((
        entries["MARKET_DIRECTIONAL_ENTRY"],
        entries["UNSAFE_FUTURE_LOOKAHEAD"],
        entries["BACKEND_MANAGED_BRACKET"],
    )) is CapabilityLevel.L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE
