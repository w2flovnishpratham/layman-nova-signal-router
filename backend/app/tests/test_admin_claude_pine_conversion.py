from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.schemas.pine_conversion import ClaudePineConversionOutput
from app.services import (
    admin_pine_conversion_service,
    pine_conversion_provider,
    pine_conversion_service,
)

pytest_plugins = ("app.tests.conftest_multiuser",)

SOURCE = """//@version=6\r
indicator("C1 NIFTY source", overlay=true)\r
fast = ta.ema(close, 5)\r
slow = ta.ema(close, 13)\r
longCondition = ta.crossover(fast, slow)\r
shortCondition = ta.crossunder(fast, slow)\r
"""


def make_user(*args, **kwargs):
    from app.tests.conftest_multiuser import make_user as create_user

    return create_user(*args, **kwargs)

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

BACKTEST_LAYER = """//@version=6
strategy("C1 NIFTY backtest", overlay=true, default_qty_type=strategy.fixed, default_qty_value=1)
fast = ta.ema(close, 5)
slow = ta.ema(close, 13)
bool novaBuyCeSignal = ta.crossover(fast, slow)
bool novaBuyPeSignal = ta.crossunder(fast, slow)
bool novaExitSignal = false
if novaBuyCeSignal
    strategy.entry("CE", strategy.long)
if novaBuyPeSignal
    strategy.entry("PE", strategy.short)
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


def _output(conversion: dict, *, layer: str = LAYER, backtest_layer: str | None = BACKTEST_LAYER, **changes):
    matched = conversion["analysis"]["matched_capabilities"]
    value = {
        "schema_version": "nova.claude-pine-conversion.v1",
        "source_sha256": conversion["source_sha256"],
        "status": "CONVERTED",
        "strategy_layer": layer,
        "backtest_layer": backtest_layer,
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


def _assert_no_candidate_evidence(conversion: dict):
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        row = db.get(models.PineConversionRequest, uuid.UUID(conversion["id"]))
        assert row.status == "manual_conversion_required"
        assert row.safe_error_code == "SOURCE_ARTIFACT_INTEGRITY_MISMATCH"
        assert row.candidate_version_id is None
        assert row.validation_report_id is None
        assert db.query(models.StrategyVersion).filter_by(strategy_id=row.strategy_id).count() == 1
        assert db.query(models.StrategyValidationReport).join(
            models.StrategyVersion,
            models.StrategyValidationReport.strategy_version_id == models.StrategyVersion.id,
        ).filter(models.StrategyVersion.strategy_id == row.strategy_id).count() == 0
        assert db.query(models.StrategyAdminReview).join(
            models.StrategyVersion,
            models.StrategyAdminReview.strategy_version_id == models.StrategyVersion.id,
        ).filter(models.StrategyVersion.strategy_id == row.strategy_id).count() == 0
        assert db.query(models.StrategyInstance).count() == 0
        assert db.query(models.StrategyInstanceWebhookCredential).count() == 0
        assert db.query(models.StrategyExecutionJob).count() == 0


def _assert_source_integrity_failure(client: TestClient, conversion: dict, *, response_sha: str | None = None):
    output = _output(conversion)
    if response_sha is not None:
        output = output.model_copy(update={"source_sha256": response_sha})
    response = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/manual-response",
        json={"response_json": json.dumps(output.model_dump())},
    )
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == "SOURCE_ARTIFACT_INTEGRITY_MISMATCH"
    detail_response = client.get(f"/api/admin/pine-conversions/{conversion['id']}")
    if detail_response.status_code == 200:
        detail = detail_response.json()["conversion"]
        assert detail["conversion_status"] == "MANUAL_CONVERSION_REQUIRED"
        assert detail["safe_error_code"] == "SOURCE_ARTIFACT_INTEGRITY_MISMATCH"
        assert detail["candidate_version_id"] is None
        assert detail["validation"] is None
    else:
        assert detail_response.status_code == 404
        assert detail_response.json()["reason"] == "SOURCE_NOT_FOUND"
    _assert_no_candidate_evidence(conversion)


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


def test_capability_analysis_is_advisory_only_and_does_not_block_conversion(mu_db, monkeypatch):
    """Pre-conversion capability analysis is context for Claude, never a gate:
    a source matching a normally-unsupported mechanism (here, non-reproducible
    intrabar `varip` state) must still reach the provider, not 409."""
    _enable(monkeypatch)
    client = _client(make_user("c1-advisory-admin@example.com", is_admin=True))
    advisory = _submit(client, SOURCE + "varip int tickState = 0\n", "Advisory")
    assert advisory["conversion_status"] == "READY_FOR_CONVERSION"
    assert advisory["analysis_status"] == "ANALYZED"
    assert advisory["analysis"]["blockers"]
    assert advisory["conversion_guidance"]["blockers"] == advisory["analysis"]["blockers"]
    fake = pine_conversion_provider.FakePineConversionProvider(_output(advisory))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)
    response = client.post(f"/api/admin/pine-conversions/{advisory['id']}/convert")
    assert response.status_code == 200, response.text
    assert fake.count_calls == fake.convert_calls == 1


def test_hard_block_disabled_and_missing_key_make_zero_provider_calls(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-block-admin@example.com", is_admin=True))
    fake = pine_conversion_provider.FakePineConversionProvider(_output(_submit(client, name="Unused")))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)
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


def test_approved_candidate_can_be_published_and_appears_ready_in_registry(mu_db, monkeypatch):
    from app.services import built_in_strategy_registry, signal_validator
    from app.schemas.signal import NormalizedSignal

    _enable(monkeypatch)
    client = _client(make_user("c1-publish-admin@example.com", is_admin=True))
    conversion = _submit(client, name="Bollinger Squeeze")
    fake = pine_conversion_provider.FakePineConversionProvider(_output(conversion))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)
    client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert")
    client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/approve",
        json={"reason": "Reviewed for TradingView compile only"},
    )

    # Not selectable under its randomly generated private code before publish.
    assert built_in_strategy_registry.get_built_in("nova-bollinger-squeeze") is None

    published = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/publish",
        json={"catalog_code": "bollinger-squeeze", "display_name": "Bollinger Squeeze"},
    )
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["ok"] is True
    assert body["catalog_code"] == "bollinger-squeeze"
    assert body["webhook_path"] == "/api/webhook/strategy/bollinger-squeeze"

    # Transport swap: the private per-user credential shape is gone, replaced
    # by the single-admin-secret broadcast shape, and the layer's canonical
    # booleans/webhook function are still wired up so it compiles unchanged.
    broadcast_pine = body["broadcast_pine"]
    assert "REPLACE_WITH_NOVA_MANAGED_SECRET" in broadcast_pine
    assert '"secret":"' in broadcast_pine
    assert "REPLACE_WITH_PRIVATE_CREDENTIAL" not in broadcast_pine
    assert "nwk_" not in broadcast_pine
    assert '"credential":"' not in broadcast_pine
    assert "novaBuyCeSignal" in broadcast_pine
    assert "novaWebhookPayload" in broadcast_pine

    entry = built_in_strategy_registry.get_built_in("nova-bollinger-squeeze")
    assert entry is not None
    assert entry["availability"] == "READY"
    assert entry["execution_adapter"] == "strategy_webhook:bollinger-squeeze"

    # The webhook URL must survive a plain re-fetch, not just the one-time
    # publish response -- an admin reopening this conversion later (or after
    # the publish toast disappeared) still needs to find it.
    refetched = client.get(f"/api/admin/pine-conversions/{conversion['id']}").json()["conversion"]
    assert refetched["catalog_code"] == "bollinger-squeeze"
    assert refetched["webhook_path"] == "/api/webhook/strategy/bollinger-squeeze"

    # Same for the actual broadcast script -- an admin who reopens the
    # conversion (not just the tab that had the fresh publish response) must
    # still be able to get the correct, secret-swapped Pine, not fall back to
    # copying the pre-swap "Exact original source" panel which still has
    # REPLACE_WITH_PRIVATE_CREDENTIAL in it.
    assert refetched["broadcast_pine"] == broadcast_pine

    # The shared webhook validator now accepts this newly published code too.
    signal = NormalizedSignal(
        payload_format="NOVA",
        secret="",
        signal_id="publish-test-1",
        strategy_code="bollinger-squeeze",
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        qty=1,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload={},
    )
    ok, error = signal_validator.validate_signal(signal)
    assert ok, error

    # Publishing twice with a different code the second time re-codes the
    # same underlying strategy rather than erroring.
    republish = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/publish",
        json={"catalog_code": "bollinger-squeeze-v2"},
    )
    assert republish.status_code == 200, republish.text
    assert built_in_strategy_registry.get_built_in("nova-bollinger-squeeze") is None
    assert built_in_strategy_registry.get_built_in("nova-bollinger-squeeze-v2") is not None


def test_unpublish_archives_the_strategy_and_deactivates_subscribers_and_is_reversible(mu_db, monkeypatch):
    from app.services import built_in_strategy_registry, strategy_fanout
    from app.db import models
    from app.db.engine import session_scope
    from sqlalchemy import select

    _enable(monkeypatch)
    client = _client(make_user("c1-unpublish-admin@example.com", is_admin=True))
    conversion = _submit(client, name="Unpublish Target")
    fake = pine_conversion_provider.FakePineConversionProvider(_output(conversion))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)
    client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert")
    client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/approve",
        json={"reason": "Reviewed for TradingView compile only"},
    )
    published = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/publish",
        json={"catalog_code": "unpublish-target"},
    )
    assert published.status_code == 200, published.text
    assert built_in_strategy_registry.get_built_in("nova-unpublish-target") is not None

    subscriber = make_user("c1-unpublish-subscriber@example.com")
    strategy_fanout.subscribe_user(subscriber.id, "unpublish-target", lots=1, execution_mode="signal_only")
    with session_scope() as db:
        sub = db.scalar(select(models.StrategySubscription).where(
            models.StrategySubscription.user_id == subscriber.id,
            models.StrategySubscription.strategy_name == "unpublish-target",
        ))
        assert sub is not None and sub.active is True

    # Only the submitting admin can unpublish -- same isolation as everything else.
    foreign_admin = _client(make_user("c1-unpublish-foreign@example.com", is_admin=True))
    assert foreign_admin.post(f"/api/admin/pine-conversions/{conversion['id']}/unpublish").status_code == 404

    unpublished = client.post(f"/api/admin/pine-conversions/{conversion['id']}/unpublish")
    assert unpublished.status_code == 200, unpublished.text
    assert unpublished.json()["catalog_code"] == "unpublish-target"
    assert unpublished.json()["deactivated_subscriptions"] == 1
    assert built_in_strategy_registry.get_built_in("nova-unpublish-target") is None

    with session_scope() as db:
        sub = db.scalar(select(models.StrategySubscription).where(
            models.StrategySubscription.user_id == subscriber.id,
            models.StrategySubscription.strategy_name == "unpublish-target",
        ))
        assert sub is not None and sub.active is False

    refetched = client.get(f"/api/admin/pine-conversions/{conversion['id']}").json()["conversion"]
    assert refetched["strategy_published"] is False
    assert refetched["catalog_code"] == "unpublish-target"  # still surfaced for reference

    again = client.post(f"/api/admin/pine-conversions/{conversion['id']}/unpublish")
    assert again.status_code == 409
    assert again.json()["reason"] == "ALREADY_UNPUBLISHED"

    # Reversible: publishing the same approved candidate again reactivates it.
    republished = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/publish",
        json={"catalog_code": "unpublish-target"},
    )
    assert republished.status_code == 200, republished.text
    assert built_in_strategy_registry.get_built_in("nova-unpublish-target") is not None


def test_publish_rejects_unapproved_or_taken_code(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-publish-guard-admin@example.com", is_admin=True))
    conversion = _submit(client, name="Not Yet Approved")

    pending = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/publish",
        json={"catalog_code": "not-yet"},
    )
    assert pending.status_code == 409
    assert pending.json()["reason"] == "NOT_APPROVED"

    invalid_code = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/publish",
        json={"catalog_code": "Not_Valid!"},
    )
    assert invalid_code.status_code == 422


def test_source_sha_mismatch_and_logic_changed_reach_admin_review_as_info(mu_db, monkeypatch):
    """No in-between checkup: a source_sha256 mismatch or an inconsistent
    logic_changed/status pairing in Claude's own response is no longer a
    backend block. It reaches admin review as-is; the admin reads Claude's
    self-reported fields and approves/rejects/requests changes."""
    _enable(monkeypatch)
    client = _client(make_user("c1-untrusted-admin@example.com", is_admin=True))
    cases = [
        lambda conversion: _output(conversion).model_copy(update={"source_sha256": "0" * 64}),
        lambda conversion: ClaudePineConversionOutput.model_validate({
            **_output(conversion).model_dump(),
            "behavior_preservation": {"logic_changed": True, "change_summary": ["changed entry"]},
        }),
    ]
    for index, make_output in enumerate(cases):
        conversion = _submit(client, SOURCE + f"\n// case {index}\n", f"Case {index}")
        output = make_output(conversion)
        fake = pine_conversion_provider.FakePineConversionProvider(output)
        monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda fake=fake: fake)
        result = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert").json()["conversion"]
        assert result["conversion_status"] == "READY_FOR_ADMIN_REVIEW"
        assert result["candidate_version_id"] is not None
        assert fake.repair_calls == 0


def _logic_changed(conversion, *, status="CONVERTED"):
    return _output(
        conversion,
        status=status,
        behavior_preservation={"logic_changed": True, "change_summary": ["normalized reversal"]},
    )


def test_api_prompt_states_logic_preservation_contract_and_v31_is_unchanged():
    from types import SimpleNamespace

    row = SimpleNamespace(
        input_source_sha256="a" * 64,
        usage_summary={"analysis": {"matched_capabilities": []}},
        options={},
        model="claude-test-model",
    )
    prompt = admin_pine_conversion_service._build_request(
        row, '//@version=6\nindicator("x")\nplot(close)\n'
    ).prompt
    assert "behavior_preservation.logic_changed=false" in prompt
    assert "status must be MANUAL_REVIEW_REQUIRED" in prompt
    assert "invalid and will be rejected" in prompt
    assert "alert() or alertcondition() call" in prompt
    # Prompt V3.1 and Transport V2 files stay hash-pinned (ownership unchanged).
    pine_conversion_service._read_canonical(
        pine_conversion_service.prompt_path("v3.1"), pine_conversion_service.PROMPT_V31_SHA256
    )
    pine_conversion_service._read_canonical(
        pine_conversion_service.TRANSPORT_V2_PATH, pine_conversion_service.TRANSPORT_V2_SHA256
    )


def test_logic_changed_converted_reaches_admin_review_without_a_repair_call(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-logic-repair-admin@example.com", is_admin=True))
    conversion = _submit(client)
    inconsistent = _logic_changed(conversion)   # logic_changed=true + CONVERTED (invalid pairing)
    fake = pine_conversion_provider.FakePineConversionProvider(inconsistent)
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)
    detail = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert").json()["conversion"]
    assert detail["conversion_status"] == "READY_FOR_ADMIN_REVIEW"
    assert detail["candidate_version_id"] is not None
    assert fake.convert_calls == 1 and fake.repair_calls == 0


def test_logic_changed_with_manual_review_required_reaches_admin_review_as_info(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-logic-manual-admin@example.com", is_admin=True))
    conversion = _submit(client)
    manual = _logic_changed(conversion, status="MANUAL_REVIEW_REQUIRED")
    fake = pine_conversion_provider.FakePineConversionProvider(manual)  # no repair_output
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)
    detail = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert").json()["conversion"]
    assert detail["conversion_status"] == "READY_FOR_ADMIN_REVIEW"
    assert detail["candidate_version_id"] is not None
    assert fake.repair_calls == 0


# alert(...) -> STRATEGY_LAYER_ALERT_FORBIDDEN (layer level); alertcondition(...) ->
# NONCANONICAL_ALERT (after transport assembly). Both are strategy-layer alerts.
@pytest.mark.parametrize(
    "alert_line",
    ['alert("BUY_CE")', 'alertcondition(novaBuyCeSignal, "BUY_CE")'],
)
def test_strategy_layer_alert_reaches_admin_review_without_a_repair_call(mu_db, monkeypatch, alert_line):
    _enable(monkeypatch)
    client = _client(make_user("c1-alert-repair-admin@example.com", is_admin=True))
    conversion = _submit(client)
    dirty = _output(conversion, layer=LAYER + "\n" + alert_line + "\n")  # strategy-layer alert
    fake = pine_conversion_provider.FakePineConversionProvider(dirty)
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)
    detail = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert").json()["conversion"]
    assert detail["conversion_status"] == "READY_FOR_ADMIN_REVIEW"
    assert detail["candidate_version_id"] is not None
    assert fake.repair_calls == 0
    # The deterministic validator still runs and its verdict is still visible
    # to the admin -- it just no longer blocks the path to review.
    assert detail["validation"]["eligible_for_review"] is False


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


def test_quota_is_bounded(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-limits-admin@example.com", is_admin=True))
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
    assert package["package"].count(SOURCE) == 1
    assert "test-only-anthropic-key" not in package["package"]
    assert "nova.claude-pine-conversion.v1" in package["package"]
    packaged_schema = json.loads(
        package["package"]
        .split("AUTHORITATIVE C1 RESPONSE JSON SCHEMA\n", 1)[1]
        .split("\n\nAPPROVED CONVERSION OPTIONS", 1)[0]
    )
    assert packaged_schema == ClaudePineConversionOutput.model_json_schema(mode="validation")
    for field in ClaudePineConversionOutput.model_fields:
        assert f'"{field}"' in package["package"]
    assert '"CONVERTED"' in package["package"]
    assert '"MANUAL_REVIEW_REQUIRED"' in package["package"]
    assert '"BLOCKED"' in package["package"]
    assert "exactly one raw JSON object" in package["package"]
    assert "Return exactly three artifacts" not in package["package"]
    assert "ARTIFACT_1_FINAL_NOVA_PINE" not in package["package"]
    assert "BEGIN_FROZEN_NOVA_TRANSPORT" not in package["package"]
    assert "NOVA FROZEN TRANSPORT BEGIN" not in package["package"]
    assert "{{TRANSPORT}}" not in package["package"]
    assert pine_conversion_service.TRANSPORT_V2_PATH.read_text(encoding="utf-8") not in package["package"]
    assert "Claude must not generate transport" in package["package"]
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
    assert detail["provenance"]["structured_output_valid"] is True
    assert detail["validation"]["eligible_for_review"] is True
    assert detail["strategy_layer"] == LAYER
    assert "NOVA FROZEN TRANSPORT BEGIN: pine_transport_v2" not in detail["strategy_layer"]
    assert detail["final_candidate"].count("NOVA FROZEN TRANSPORT BEGIN: pine_transport_v2") == 1
    assert detail["candidate_sha256"] == hashlib.sha256(detail["final_candidate"].encode()).hexdigest()
    approved = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/approve",
        json={"reason": "Manual candidate reviewed for compile"},
    ).json()["conversion"]
    assert approved["conversion_status"] == "APPROVED_FOR_TRADINGVIEW_COMPILE"
    assert approved["approval_integrity"] is True
    assert approved["candidate_sha256"] == detail["candidate_sha256"]

    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db:
        assert db.query(models.StrategyInstance).count() == 0
        assert db.query(models.StrategyInstanceWebhookCredential).count() == 0
        assert db.query(models.StrategyExecutionJob).count() == 0


def test_manual_response_contract_fails_closed_for_invalid_shapes(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-manual-invalid-admin@example.com", is_admin=True))

    cases = [
        ("wrong-sha", lambda conversion: _output(conversion).model_copy(update={"source_sha256": "0" * 64}).model_dump()),
        ("missing-source-sha", lambda conversion: {
            key: value for key, value in _output(conversion).model_dump().items() if key != "source_sha256"
        }),
        ("missing-field", lambda conversion: {
            key: value for key, value in _output(conversion).model_dump().items() if key != "signal_mapping"
        }),
        ("unknown-field", lambda conversion: {**_output(conversion).model_dump(), "tools": ["bash"]}),
        ("transport", lambda conversion: _output(
            conversion,
            layer=LAYER + "\n// === NOVA FROZEN TRANSPORT BEGIN: pine_transport_v2 ===\n",
        ).model_dump()),
        ("empty-layer", lambda conversion: {**_output(conversion).model_dump(), "strategy_layer": ""}),
        ("wrong-schema", lambda conversion: {
            **_output(conversion).model_dump(), "schema_version": "nova.claude-pine-conversion.v2"
        }),
    ]
    for index, (name, build) in enumerate(cases):
        conversion = _submit(client, SOURCE + f"\n// manual invalid {index}\n", f"Manual {name}")
        response = client.post(
            f"/api/admin/pine-conversions/{conversion['id']}/manual-response",
            json={"response_json": json.dumps(build(conversion))},
        )
        assert response.status_code == 422, (name, response.text)
        assert response.json()["reason"] in {
            "INVALID_MANUAL_RESPONSE",
            "SOURCE_SHA_MISMATCH",
            "MODEL_TRANSPORT_FORBIDDEN",
        }
        detail = client.get(f"/api/admin/pine-conversions/{conversion['id']}").json()["conversion"]
        assert detail["candidate_version_id"] is None

    prose = _submit(client, SOURCE + "\n// legacy prose\n", "Legacy package response")
    response = client.post(
        f"/api/admin/pine-conversions/{prose['id']}/manual-response",
        json={"response_json": "ARTIFACT_1_FINAL_NOVA_PINE\n```pine\n//@version=6\n```"},
    )
    assert response.status_code == 422
    assert response.json()["reason"] == "INVALID_MANUAL_RESPONSE"


def test_manual_source_mutations_fail_before_candidate_creation(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-source-mutation-admin@example.com", is_admin=True))
    cases = (
        "content-only",
        "stored-sha-only",
        "content-and-stored-sha",
        "response-uses-new-sha",
    )
    for index, case in enumerate(cases):
        conversion = _submit(
            client,
            SOURCE + f"\n// source-integrity-{index}\n",
            f"Source integrity {case}",
        )
        package = client.post(
            f"/api/admin/pine-conversions/{conversion['id']}/manual-package"
        )
        assert package.status_code == 200
        response_sha = None
        from app.db.engine import session_scope
        with session_scope() as db:
            from app.db import models

            row = db.get(models.PineConversionRequest, uuid.UUID(conversion["id"]))
            artifact = db.query(models.StrategySourceArtifact).filter_by(
                strategy_version_id=row.input_version_id,
                artifact_type="pine_script",
            ).one()
            if case == "content-only":
                artifact.content += "\n// changed after package"
            elif case == "stored-sha-only":
                artifact.content_sha256 = "0" * 64
            else:
                artifact.content += "\n// retarget attempt"
                new_sha = hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
                artifact.content_sha256 = new_sha
                if case == "response-uses-new-sha":
                    response_sha = new_sha
        _assert_source_integrity_failure(
            client,
            conversion,
            response_sha=response_sha,
        )


def test_manual_toctou_source_mutation_fails_in_persistence_transaction(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-manual-toctou-admin@example.com", is_admin=True))
    conversion = _submit(client, SOURCE + "\n// manual-toctou\n", "Manual TOCTOU")
    client.post(f"/api/admin/pine-conversions/{conversion['id']}/manual-package")
    original_persist = admin_pine_conversion_service._persist_candidate
    mutated = False

    def mutate_then_persist(*args, **kwargs):
        nonlocal mutated
        if not mutated:
            from app.db import models
            from app.db.engine import session_scope

            with session_scope() as db:
                row = db.get(models.PineConversionRequest, uuid.UUID(conversion["id"]))
                artifact = db.query(models.StrategySourceArtifact).filter_by(
                    strategy_version_id=row.input_version_id,
                    artifact_type="pine_script",
                ).one()
                artifact.content += "\n// changed between checks"
            mutated = True
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(admin_pine_conversion_service, "_persist_candidate", mutate_then_persist)
    _assert_source_integrity_failure(client, conversion)
    assert mutated is True


def test_api_toctou_source_mutation_fails_in_shared_persistence(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-api-toctou-admin@example.com", is_admin=True))
    conversion = _submit(client, SOURCE + "\n// api-toctou\n", "API TOCTOU")
    provider = pine_conversion_provider.FakePineConversionProvider(_output(conversion))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: provider)
    original_persist = admin_pine_conversion_service._persist_candidate

    def mutate_then_persist(*args, **kwargs):
        from app.db import models
        from app.db.engine import session_scope

        with session_scope() as db:
            row = db.get(models.PineConversionRequest, uuid.UUID(conversion["id"]))
            artifact = db.query(models.StrategySourceArtifact).filter_by(
                strategy_version_id=row.input_version_id,
                artifact_type="pine_script",
            ).one()
            artifact.content += "\n// changed after provider validation"
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(admin_pine_conversion_service, "_persist_candidate", mutate_then_persist)
    response = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert")
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == "SOURCE_ARTIFACT_INTEGRITY_MISMATCH"
    assert provider.count_calls == provider.convert_calls == 1
    detail = client.get(f"/api/admin/pine-conversions/{conversion['id']}").json()["conversion"]
    assert detail["conversion_status"] == "MANUAL_CONVERSION_REQUIRED"
    assert detail["candidate_version_id"] is None
    assert detail["validation"] is None
    _assert_no_candidate_evidence(conversion)


def test_api_source_mutation_fails_before_provider_use(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-api-precheck-admin@example.com", is_admin=True))
    conversion = _submit(client, SOURCE + "\n// api-precheck\n", "API precheck")
    provider = pine_conversion_provider.FakePineConversionProvider(_output(conversion))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: provider)
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        row = db.get(models.PineConversionRequest, uuid.UUID(conversion["id"]))
        artifact = db.query(models.StrategySourceArtifact).filter_by(
            strategy_version_id=row.input_version_id,
            artifact_type="pine_script",
        ).one()
        artifact.content += "\n// changed before API conversion"
    response = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert")
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == "SOURCE_ARTIFACT_INTEGRITY_MISMATCH"
    assert provider.count_calls == provider.convert_calls == provider.repair_calls == 0
    with session_scope() as db:
        row = db.get(models.PineConversionRequest, uuid.UUID(conversion["id"]))
        assert row.status == "ready_for_conversion"
        assert row.candidate_version_id is None


def test_source_artifact_owner_and_reference_mismatches_fail_closed(mu_db, monkeypatch):
    _enable(monkeypatch)
    owner = make_user("c1-reference-owner@example.com", is_admin=True)
    other_owner = make_user("c1-reference-foreign@example.com", is_admin=True)
    client = _client(owner)
    other_client = _client(other_owner)

    for index, case in enumerate(("foreign-conversion", "foreign-owner", "missing-artifact", "request-version-mismatch")):
        conversion = _submit(client, SOURCE + f"\n// reference-{index}\n", f"Reference {case}")
        donor = (
            _submit(other_client, SOURCE + f"\n// foreign-{index}\n", f"Foreign {case}")
            if case == "foreign-owner"
            else _submit(client, SOURCE + f"\n// donor-{index}\n", f"Donor {case}")
        )
        from app.db import models
        from app.db.engine import session_scope
        with session_scope() as db:
            row = db.get(models.PineConversionRequest, uuid.UUID(conversion["id"]))
            donor_row = db.get(models.PineConversionRequest, uuid.UUID(donor["id"]))
            if case in {"foreign-conversion", "foreign-owner"}:
                row.input_version_id = donor_row.input_version_id
            elif case == "missing-artifact":
                artifact = db.query(models.StrategySourceArtifact).filter_by(
                    strategy_version_id=row.input_version_id,
                    artifact_type="pine_script",
                ).one()
                db.delete(artifact)
            else:
                row.strategy_id = donor_row.strategy_id
        _assert_source_integrity_failure(client, conversion)


BYTE_EXACT_SOURCES = (
    pytest.param(
        '//@version=6\nindicator("LF source", overlay=true)\nbool sourceSignal = close > open\n',
        id="lf",
    ),
    pytest.param(
        '//@version=6\r\nindicator("CRLF source", overlay=true)\r\nbool sourceSignal = close > open\r\n',
        id="crlf",
    ),
    pytest.param(
        '//@version=6\nindicator("No trailing newline", overlay=true)\nbool sourceSignal = close > open',
        id="no-trailing-newline",
    ),
    pytest.param(
        '//@version=6\nindicator("Two trailing newlines", overlay=true)\nbool sourceSignal = close > open\n\n',
        id="trailing-newlines",
    ),
    pytest.param(
        '//@version=6\nindicator("Unicode Ω संकेत", overlay=true)\nbool sourceSignal = close > open\n',
        id="unicode",
    ),
)


@pytest.mark.parametrize("source", BYTE_EXACT_SOURCES)
def test_valid_manual_source_byte_variants_remain_exact(mu_db, monkeypatch, source):
    _enable(monkeypatch)
    client = _client(make_user(f"c1-bytes-{hashlib.sha256(source.encode()).hexdigest()[:8]}@example.com", is_admin=True))
    conversion = _submit(client, source, "Exact byte variant")
    expected_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert conversion["source_sha256"] == expected_sha
    package = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/manual-package"
    ).json()["package"]
    assert package.split("BEGIN_UNTRUSTED_PINE_SOURCE\n", 1)[1].split(
        "\nEND_UNTRUSTED_PINE_SOURCE",
        1,
    )[0] == source
    response = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/manual-response",
        json={"response_json": json.dumps(_output(conversion).model_dump())},
    )
    assert response.status_code == 200, response.text
    detail = response.json()["conversion"]
    assert detail["original_source"] == source
    assert detail["conversion_status"] == "READY_FOR_ADMIN_REVIEW"


def test_logic_changed_manual_response_cannot_become_review_ready(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("c1-manual-logic-admin@example.com", is_admin=True))
    conversion = _submit(client, SOURCE + "\n// logic changed\n", "Logic changed")
    raw = _output(conversion).model_dump()
    raw["status"] = "MANUAL_REVIEW_REQUIRED"
    raw["backtest_layer"] = None  # only ever set alongside status=CONVERTED
    raw["behavior_preservation"] = {
        "logic_changed": True,
        "change_summary": ["Source behavior could not be preserved."],
    }
    response = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/manual-response",
        json={"response_json": json.dumps(raw)},
    )
    assert response.status_code == 422
    assert response.json()["reason"] == "LOGIC_CHANGED_REQUIRES_MANUAL_CORRECTION"
    detail = client.get(f"/api/admin/pine-conversions/{conversion['id']}").json()["conversion"]
    assert detail["conversion_status"] == "MANUAL_CONVERSION_REQUIRED"
    assert detail["candidate_version_id"] is None


def test_normal_user_cannot_generate_or_view_manual_package(mu_db, monkeypatch):
    _enable(monkeypatch)
    admin = _client(make_user("c1-manual-owner@example.com", is_admin=True))
    normal = _client(make_user("c1-manual-normal@example.com"))
    conversion = _submit(admin, SOURCE + "\n// auth\n", "Manual auth")
    assert normal.post(f"/api/admin/pine-conversions/{conversion['id']}/manual-package").status_code == 403
    assert normal.get(f"/api/admin/pine-conversions/{conversion['id']}").status_code == 403


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
