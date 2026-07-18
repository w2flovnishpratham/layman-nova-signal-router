from __future__ import annotations

import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.schemas.pine_conversion import ClaudePineConversionOutput
from app.services import pine_conversion_provider
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401

SOURCE = """//@version=6\r
indicator("C1 NIFTY source", overlay=true)\r
fast = ta.ema(close, 5)\r
slow = ta.ema(close, 13)\r
longCondition = ta.crossover(fast, slow)\r
shortCondition = ta.crossunder(fast, slow)\r
"""

LAYER = """//@version=6
indicator("C1 NIFTY converted", overlay=true)
fast = ta.ema(close, 5)
slow = ta.ema(close, 13)
bool novaBuyCeSignal = ta.crossover(fast, slow)
bool novaBuyPeSignal = ta.crossunder(fast, slow)
bool novaExitSignal = false
plot(fast)
plot(slow)
"""


def _client(user_model):
    from app.auth.dependencies import get_current_user
    from app.routers import pine_conversion
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    app.include_router(pine_conversion.admin_router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(user_model)
    return TestClient(app)


def _enable(monkeypatch):
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-only-anthropic-key")
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MODEL", "claude-test-model")
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MAX_REPAIRS", 1)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MAX_INPUT_TOKENS", 10_000)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_DAILY_ADMIN_LIMIT", 10)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_DAILY_GLOBAL_LIMIT", 50)


def _submit(client: TestClient, source: str = SOURCE, name: str = "C1 Test"):
    response = client.post("/api/admin/pine-conversions", json={
        "strategy_name": name,
        "source": source,
        "original_filename": "exact-source.pine",
        "internal_notes": "Internal review only",
        "options": {
            "requested_setup_type": "USER_MANAGED_TRADINGVIEW",
            "intended_symbol": "NIFTY",
            "intended_timeframe": "5",
        },
    })
    assert response.status_code == 200, response.text
    return response.json()["conversion"]


def _output(conversion: dict, *, layer: str = LAYER, **changes):
    matched = conversion["analysis"]["matched_capabilities"]
    value = {
        "schema_version": "nova.claude-pine-conversion.v1",
        "source_sha256": conversion["source_sha256"],
        "status": "CONVERTED",
        "strategy_layer": layer,
        "signal_mapping": {
            "buy_ce_source": "ta.crossover(fast, slow)",
            "buy_pe_source": "ta.crossunder(fast, slow)",
            "exit_source": "false",
        },
        "behavior_preservation": {"logic_changed": False, "change_summary": []},
        "capabilities": {"handled": matched, "unsupported": [], "manual_review": []},
        "user_summary": "Preserved EMA crossover direction.",
        "admin_review_points": [],
    }
    value.update(changes)
    return ClaudePineConversionOutput.model_validate(value)


def test_admin_submission_preserves_exact_source_and_normal_user_is_denied(mu_db, monkeypatch):
    _enable(monkeypatch)
    admin = _client(make_user("c1-admin@example.com", is_admin=True))
    normal = _client(make_user("c1-user@example.com"))
    assert normal.post("/api/admin/pine-conversions", json={
        "strategy_name": "No", "source": SOURCE, "original_filename": "x.pine",
    }).status_code == 403
    conversion = _submit(admin)
    expected = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
    assert conversion["source_sha256"] == expected
    detail = admin.get(f"/api/admin/pine-conversions/{conversion['id']}").json()["conversion"]
    assert detail["original_source"] == SOURCE
    assert detail["analysis"]["source_sha256"] == expected
    assert detail["conversion_status"] == "READY_FOR_CONVERSION"
    assert normal.get(f"/api/admin/pine-conversions/{conversion['id']}").status_code == 403


def test_binary_oversized_and_unsafe_schemas_fail_closed(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-input-admin@example.com", is_admin=True))
    bad = client.post("/api/admin/pine-conversions", json={
        "strategy_name": "Binary", "source": SOURCE + "\x00", "original_filename": "x.pine",
    })
    assert bad.status_code == 422 and bad.json()["reason"] == "BINARY_SOURCE"
    monkeypatch.setattr(settings, "PERSONAL_PINE_MAX_SOURCE_BYTES", 20)
    large = client.post("/api/admin/pine-conversions", json={
        "strategy_name": "Large", "source": SOURCE, "original_filename": "x.pine",
    })
    assert large.status_code == 413 and large.json()["reason"] == "SOURCE_TOO_LARGE"
    unsafe = client.post("/api/admin/pine-conversions", json={
        "strategy_name": "Unsafe", "source": SOURCE, "original_filename": "x.pine", "model": "attacker",
    })
    assert unsafe.status_code == 422


def test_hard_block_disabled_and_missing_key_make_zero_provider_calls(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-block-admin@example.com", is_admin=True))
    blocked = _submit(client, SOURCE + "varip int tickState = 0\n", "Blocked")
    fake = pine_conversion_provider.FakePineConversionProvider(_output(blocked))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)
    response = client.post(f"/api/admin/pine-conversions/{blocked['id']}/convert")
    assert response.status_code == 409
    assert fake.count_calls == fake.convert_calls == fake.repair_calls == 0

    ready = _submit(client, name="Disabled")
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_ENABLED", False)
    disabled = client.post(f"/api/admin/pine-conversions/{ready['id']}/convert").json()["conversion"]
    assert disabled["conversion_status"] == "MANUAL_CONVERSION_REQUIRED"
    assert disabled["safe_error_code"] == "AI_DISABLED"
    assert fake.count_calls == 0

    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    missing = _submit(client, SOURCE + "\n// distinct\n", "Missing key")
    result = client.post(f"/api/admin/pine-conversions/{missing['id']}/convert").json()["conversion"]
    assert result["safe_error_code"] == "PROVIDER_NOT_CONFIGURED"
    assert fake.count_calls == 0


def test_success_appends_transport_validates_and_approves_exact_hashes(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-success-admin@example.com", is_admin=True))
    conversion = _submit(client)
    fake = pine_conversion_provider.FakePineConversionProvider(_output(conversion))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)
    result = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert")
    assert result.status_code == 200, result.text
    detail = result.json()["conversion"]
    assert detail["conversion_status"] == "READY_FOR_ADMIN_REVIEW"
    assert detail["validation"]["eligible_for_review"] is True
    assert detail["strategy_layer"] == LAYER
    assert "NOVA FROZEN TRANSPORT BEGIN: pine_transport_v2" not in detail["strategy_layer"]
    assert detail["final_candidate"].count("NOVA FROZEN TRANSPORT BEGIN: pine_transport_v2") == 1
    assert detail["final_candidate"].count("NOVA FROZEN TRANSPORT END: pine_transport_v2") == 1
    assert detail["candidate_sha256"] == hashlib.sha256(detail["final_candidate"].encode()).hexdigest()
    assert detail["provenance"]["structured_output_valid"] is True
    assert fake.count_calls == fake.convert_calls == 1 and fake.repair_calls == 0
    approved = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/approve",
        json={"reason": "Reviewed for TradingView compile only"},
    ).json()["conversion"]
    assert approved["conversion_status"] == "APPROVED_FOR_TRADINGVIEW_COMPILE"
    assert approved["review_status"] == "APPROVED_FOR_TRADINGVIEW_COMPILE"
    assert approved["approval_integrity"] is True


def test_model_transport_source_sha_and_logic_change_are_rejected(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-untrusted-admin@example.com", is_admin=True))
    cases = [
        LAYER + '\n// === NOVA FROZEN TRANSPORT BEGIN: pine_transport_v2 ===\n',
        LAYER,
        LAYER,
    ]
    for index, layer in enumerate(cases):
        conversion = _submit(client, SOURCE + f"\n// case {index}\n", f"Unsafe {index}")
        if index == 0:
            output = _output(conversion, layer=layer)
        elif index == 1:
            output = _output(conversion)
            output = output.model_copy(update={"source_sha256": "0" * 64})
        else:
            value = _output(conversion).model_dump()
            value["behavior_preservation"] = {"logic_changed": True, "change_summary": ["changed entry"]}
            output = ClaudePineConversionOutput.model_validate(value)
        fake = pine_conversion_provider.FakePineConversionProvider(output)
        monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda fake=fake: fake)
        result = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert").json()["conversion"]
        assert result["conversion_status"] == "MANUAL_CONVERSION_REQUIRED"
        assert result["candidate_version_id"] is None


def test_exact_cache_hit_and_model_change_miss(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-cache-admin@example.com", is_admin=True))
    first = _submit(client, name="First")
    first_provider = pine_conversion_provider.FakePineConversionProvider(_output(first))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: first_provider)
    assert client.post(f"/api/admin/pine-conversions/{first['id']}/convert").json()["conversion"]["conversion_status"] == "READY_FOR_ADMIN_REVIEW"

    second = _submit(client, name="Second")
    unused = pine_conversion_provider.FakePineConversionProvider(_output(second))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: unused)
    cached = client.post(f"/api/admin/pine-conversions/{second['id']}/convert").json()["conversion"]
    assert cached["provider_mode"] == "CLAUDE_API_CACHE"
    assert cached["provenance"]["cache_status"] == "HIT"
    assert unused.count_calls == unused.convert_calls == 0

    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MODEL", "different-model")
    third = _submit(client, name="Third")
    miss = pine_conversion_provider.FakePineConversionProvider(_output(third))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: miss)
    client.post(f"/api/admin/pine-conversions/{third['id']}/convert")
    assert miss.count_calls == miss.convert_calls == 1


def test_token_quota_timeout_and_one_repair_are_bounded(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-limits-admin@example.com", is_admin=True))
    too_large = _submit(client, SOURCE + "\n// token\n", "Token")
    provider = pine_conversion_provider.FakePineConversionProvider(_output(too_large), input_tokens=101)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MAX_INPUT_TOKENS", 100)
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: provider)
    result = client.post(f"/api/admin/pine-conversions/{too_large['id']}/convert").json()["conversion"]
    assert result["safe_error_code"] == "INPUT_TOO_LARGE"
    assert provider.count_calls == 1 and provider.convert_calls == 0

    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MAX_INPUT_TOKENS", 10_000)
    repair = _submit(client, SOURCE + "\n// repair\n", "Repair")
    broken = _output(repair, layer=LAYER.replace("bool novaExitSignal = false\n", ""))
    repaired = _output(repair)
    repair_provider = pine_conversion_provider.FakePineConversionProvider(broken, repair_output=repaired)
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: repair_provider)
    fixed = client.post(f"/api/admin/pine-conversions/{repair['id']}/convert").json()["conversion"]
    assert fixed["conversion_status"] == "READY_FOR_ADMIN_REVIEW"
    assert fixed["provenance"]["repair_count"] == 1
    assert repair_provider.repair_calls == 1

    quota = _submit(client, SOURCE + "\n// quota\n", "Quota")
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_DAILY_ADMIN_LIMIT", 0)
    limited = client.post(f"/api/admin/pine-conversions/{quota['id']}/convert")
    assert limited.status_code == 429 and limited.json()["reason"] == "QUOTA_EXCEEDED"


def test_provider_timeout_is_sanitized_and_retryable(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-timeout-admin@example.com", is_admin=True))
    conversion = _submit(client, SOURCE + "\n// timeout\n", "Timeout")

    class TimeoutProvider:
        def count_tokens(self, request):
            return 100

        def convert(self, request):
            raise pine_conversion_provider.ProviderError("PROVIDER_TIMEOUT")

        def repair(self, request):
            raise AssertionError("Timeout must not enter the semantic repair path")

    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: TimeoutProvider())
    response = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert")
    assert response.status_code == 200
    detail = response.json()["conversion"]
    assert detail["conversion_status"] == "AI_FAILED_RETRYABLE"
    assert detail["safe_error_code"] == "PROVIDER_TIMEOUT"
    assert "TimeoutProvider" not in response.text


def test_manual_fallback_same_validation_rejection_and_no_execution_rows(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-manual-admin@example.com", is_admin=True))
    conversion = _submit(client)
    package = client.post(f"/api/admin/pine-conversions/{conversion['id']}/manual-package").json()
    assert conversion["source_sha256"] in package["package"]
    assert SOURCE in package["package"]
    assert "test-only-anthropic-key" not in package["package"]
    invalid = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/manual-response",
        json={"response_json": '{"unknown":true}'},
    )
    assert invalid.status_code == 422 and invalid.json()["reason"] == "INVALID_MANUAL_RESPONSE"
    manual = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/manual-response",
        json={"response_json": json.dumps(_output(conversion).model_dump())},
    )
    assert manual.status_code == 200, manual.text
    detail = manual.json()["conversion"]
    assert detail["provider_mode"] == "MANUAL_ADMIN_COPY_PASTE"
    assert detail["validation"]["eligible_for_review"] is True
    rejected = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/reject",
        json={"reason": "TradingView review found a mismatch"},
    ).json()["conversion"]
    assert rejected["conversion_status"] == "REJECTED"

    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db:
        assert db.query(models.StrategyInstance).count() == 0
        assert db.query(models.StrategyInstanceWebhookCredential).count() == 0
        assert db.query(models.StrategyExecutionJob).count() == 0


def test_candidate_mutation_invalidates_approval_binding(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-binding-admin@example.com", is_admin=True))
    conversion = _submit(client)
    fake = pine_conversion_provider.FakePineConversionProvider(_output(conversion))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)
    converted = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert").json()["conversion"]
    client.post(f"/api/admin/pine-conversions/{conversion['id']}/approve", json={})
    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db:
        artifact = db.query(models.StrategySourceArtifact).filter_by(
            strategy_version_id=converted["candidate_version_id"], artifact_type="pine_script"
        ).one()
        artifact.content += "\n// mutation"
    detail = client.get(f"/api/admin/pine-conversions/{conversion['id']}").json()["conversion"]
    assert detail["approval_integrity"] is False
