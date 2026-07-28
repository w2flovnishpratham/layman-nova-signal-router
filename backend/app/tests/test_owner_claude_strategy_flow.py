from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import models
from app.db.engine import session_scope
from app.schemas.pine_conversion import ClaudePineConversionOutput
from app.services import pine_conversion_provider, pine_semantic_preanalyzer
from app.tests.conftest_multiuser import make_user

pytest_plugins = ("app.tests.conftest_multiuser",)


SOURCE = """//@version=6
strategy("Owner Pine", overlay=true)
fast = ta.ema(close, 5)
slow = ta.ema(close, 13)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""

LAYER = """//@version=6
strategy("Owner Pine", overlay=true)
fast = ta.ema(close, 5)
slow = ta.ema(close, 13)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, alert_message=novaWebhookPayload("BUY_CE", "Long"))
if ta.crossunder(fast, slow)
    strategy.close("Long", alert_message=novaWebhookPayload("EXIT", "Long"))
"""


@pytest.fixture
def owner_flow(mu_db, monkeypatch):
    from app.auth.dependencies import get_current_user
    from app.routers import c2_tradingview, personal_pine, pine_conversion
    from app.services.user_context import current_user_from_model

    owner = make_user("owner-claude@example.com")
    other = make_user("other-claude@example.com")
    admin = make_user("admin-claude@example.com", is_admin=True)
    current = {"user": owner}
    app = FastAPI()
    app.include_router(personal_pine.router)
    app.include_router(pine_conversion.router)
    app.include_router(pine_conversion.admin_router)
    app.include_router(c2_tradingview.router)
    app.include_router(c2_tradingview.admin_router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(
        current["user"]
    )
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-only-never-sent")
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MODEL", "claude-test")
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MAX_REPAIRS", 1)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MAX_INPUT_TOKENS", 10_000)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_DAILY_ADMIN_LIMIT", 10)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_DAILY_GLOBAL_LIMIT", 50)
    monkeypatch.setattr(settings, "C2_TRADINGVIEW_INSTALLATION_ENABLED", True)

    analysis = pine_semantic_preanalyzer.analyze_source(SOURCE)
    output = ClaudePineConversionOutput.model_validate({
        "schema_version": "nova.claude-pine-conversion.v1",
        "source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
        "status": "CONVERTED",
        "strategy_layer": LAYER,
        "signal_mapping": {
            "buy_ce_source": "ta.crossover(fast, slow)",
            "buy_pe_source": "ta.crossunder(fast, slow)",
            "exit_source": "false",
        },
        "behavior_preservation": {
            "logic_changed": False,
            "change_summary": [],
        },
        "capabilities": {
            "handled": list(analysis.matched_capabilities),
            "unsupported": [],
            "manual_review": [],
        },
        "user_summary": "Owner strategy behavior was preserved.",
        "admin_review_points": [],
    })
    provider = pine_conversion_provider.FakePineConversionProvider(output)
    monkeypatch.setattr(
        pine_conversion_provider, "get_claude_provider", lambda: provider
    )
    return TestClient(app), current, owner, other, admin, provider


BOLLINGER_SOURCE = """//@version=6
indicator("Bollinger Bands strategy", overlay=true)
[middle, upper, lower] = ta.bb(close, 20, 2)
if ta.crossunder(close, lower)
    strategy.entry("Long", strategy.long, stop=lower)
if ta.crossover(close, upper)
    strategy.entry("Short", strategy.short, stop=upper)
strategy.cancel_all()
"""

BOLLINGER_LAYER = """//@version=6
indicator("Bollinger Bands converted", overlay=true)
[middle, upper, lower] = ta.bb(close, 20, 2)
bool novaBuyCeSignal = ta.crossunder(close, lower)
bool novaBuyPeSignal = ta.crossover(close, upper)
bool novaExitSignal = ta.crossover(close, upper) or ta.crossunder(close, lower)
"""


def test_owner_bollinger_pending_orders_reach_claude_automatically_not_unsupported(
    mu_db, monkeypatch
):
    """Regression for the exact reported bug: a user uploads a Pine strategy
    with pending stop entries + strategy.cancel_all + an opposite-direction
    reversal, clicks "Convert and send for admin review", and it used to get
    stuck at UNSUPPORTED_STRATEGY / Validation: NOT_RUN forever. It must now
    reach Claude automatically and land in the admin review queue."""
    import hashlib as _hashlib

    from app.auth.dependencies import get_current_user
    from app.routers import personal_pine, pine_conversion
    from app.services.user_context import current_user_from_model

    owner = make_user("bollinger-owner@example.com")
    current = {"user": owner}
    app = FastAPI()
    app.include_router(personal_pine.router)
    app.include_router(pine_conversion.router)
    app.include_router(pine_conversion.admin_router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(
        current["user"]
    )
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-only-never-sent")
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MODEL", "claude-test")
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MAX_REPAIRS", 1)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MAX_INPUT_TOKENS", 10_000)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_DAILY_ADMIN_LIMIT", 10)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_DAILY_GLOBAL_LIMIT", 50)
    client = TestClient(app)

    created = client.post(
        "/api/personal-pine-strategies",
        json={
            "name": "Bollinger Bands strategy",
            "source": BOLLINGER_SOURCE,
            "filename": "bollinger.pine",
        },
    )
    assert created.status_code == 200, created.text
    strategy = created.json()["strategy"]
    version = created.json()["version"]

    analysis = pine_semantic_preanalyzer.analyze_source(BOLLINGER_SOURCE)
    output = ClaudePineConversionOutput.model_validate({
        "schema_version": "nova.claude-pine-conversion.v1",
        "source_sha256": _hashlib.sha256(BOLLINGER_SOURCE.encode()).hexdigest(),
        "status": "CONVERTED",
        "strategy_layer": BOLLINGER_LAYER,
        "signal_mapping": {
            "buy_ce_source": "ta.crossunder(close, lower)",
            "buy_pe_source": "ta.crossover(close, upper)",
            "exit_source": "ta.crossover(close, upper) or ta.crossunder(close, lower)",
        },
        "behavior_preservation": {"logic_changed": False, "change_summary": []},
        "capabilities": {
            "handled": list(analysis.matched_capabilities),
            "unsupported": [],
            "manual_review": [],
        },
        "user_summary": "Normalized pending stop entries and OCA cancellation to confirmed signals.",
        "admin_review_points": [],
    })
    provider = pine_conversion_provider.FakePineConversionProvider(output)
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: provider)

    response = client.post(
        (
            f"/api/personal-pine-strategies/{strategy['id']}/versions/"
            f"{version['id']}/claude-conversion"
        ),
        json={
            "consent": True,
            "options": {
                "requested_setup_type": "USER_MANAGED_TRADINGVIEW",
                "intended_symbol": "NIFTY",
                "intended_timeframe": "5",
            },
        },
    )
    assert response.status_code == 202, response.text
    conversion = response.json()["conversion"]

    # The old bug: this used to stop here as UNSUPPORTED_STRATEGY /
    # Validation: NOT_RUN, with Claude never called.
    assert conversion["conversion_status"] == "READY_FOR_ADMIN_REVIEW"
    assert conversion["validation_status"] == "PASSED"
    assert provider.convert_calls == 1

    # The blocker finding is still visible -- advisory, not hidden.
    assert conversion["analysis"]["blockers"] == ["BLK_PENDING_ENGINE"]
    assert conversion["conversion_guidance"]["blockers"] == ["BLK_PENDING_ENGINE"]

    detail = client.get(
        f"/api/personal-pine-claude-conversions/{conversion['id']}"
    ).json()["conversion"]
    assert "novaBuyCeSignal" in detail["strategy_layer"]
    assert "novaBuyPeSignal" in detail["strategy_layer"]
    assert "novaExitSignal" in detail["strategy_layer"]
    assert "strategy.entry" not in detail["final_candidate"]
    assert "stop=" not in detail["final_candidate"]
    assert "strategy.cancel" not in detail["final_candidate"]


def test_owner_bollinger_realistic_claude_response_is_not_rejected_as_invalid(
    mu_db, monkeypatch
):
    """Regression for the actual production failure after the advisory-flow
    fix shipped: a real Claude response describes what it normalized in
    free-text sentences (not the analyzer's bare capability_id tokens) and
    reports CONVERTED with a non-empty `unsupported` list for the mechanism
    it dropped (pending stop entries / OCA cancellation) in favor of a
    disclosed, supported equivalent. The old `_validate_layer` manifest/status
    checks rejected exactly this shape -- every real Bollinger-style strategy
    landed at manual_conversion_required with CAPABILITY_MANIFEST_INCOMPLETE /
    CAPABILITY_MANIFEST_UNKNOWN / UNSUPPORTED_CAPABILITY_STATUS_INVALID even
    though the conversion was correct. The prior test in this file passed
    only because its fake fixture echoed exact capability_id tokens into
    `handled`, which no real Claude response does."""
    import hashlib as _hashlib

    from app.auth.dependencies import get_current_user
    from app.routers import personal_pine, pine_conversion
    from app.services.user_context import current_user_from_model

    owner = make_user("bollinger-realistic-owner@example.com")
    current = {"user": owner}
    app = FastAPI()
    app.include_router(personal_pine.router)
    app.include_router(pine_conversion.router)
    app.include_router(pine_conversion.admin_router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(
        current["user"]
    )
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-only-never-sent")
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MODEL", "claude-test")
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MAX_REPAIRS", 1)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MAX_INPUT_TOKENS", 10_000)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_DAILY_ADMIN_LIMIT", 10)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_DAILY_GLOBAL_LIMIT", 50)
    client = TestClient(app)

    created = client.post(
        "/api/personal-pine-strategies",
        json={
            "name": "Bollinger Bands strategy",
            "source": BOLLINGER_SOURCE,
            "filename": "bollinger.pine",
        },
    )
    assert created.status_code == 200, created.text
    strategy = created.json()["strategy"]
    version = created.json()["version"]

    output = ClaudePineConversionOutput.model_validate({
        "schema_version": "nova.claude-pine-conversion.v1",
        "source_sha256": _hashlib.sha256(BOLLINGER_SOURCE.encode()).hexdigest(),
        "status": "CONVERTED",
        "strategy_layer": BOLLINGER_LAYER,
        "signal_mapping": {
            "buy_ce_source": "ta.crossunder(close, lower)",
            "buy_pe_source": "ta.crossover(close, upper)",
            "exit_source": "ta.crossover(close, upper) or ta.crossunder(close, lower)",
        },
        "behavior_preservation": {"logic_changed": False, "change_summary": []},
        "capabilities": {
            "handled": ["Bollinger band basis/upper/lower calculation preserved unchanged"],
            "unsupported": [
                "Pending stop-entry order placement -- normalized to confirmed-bar market intent",
                "OCA group cancellation lifecycle -- not tracked; exit-first reversal used instead",
            ],
            "manual_review": [],
        },
        "user_summary": "Normalized pending stop entries and OCA cancellation to confirmed signals.",
        "admin_review_points": ["Confirm source does not require atomic broker reversal."],
    })
    provider = pine_conversion_provider.FakePineConversionProvider(output)
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: provider)

    response = client.post(
        (
            f"/api/personal-pine-strategies/{strategy['id']}/versions/"
            f"{version['id']}/claude-conversion"
        ),
        json={
            "consent": True,
            "options": {
                "requested_setup_type": "USER_MANAGED_TRADINGVIEW",
                "intended_symbol": "NIFTY",
                "intended_timeframe": "5",
            },
        },
    )
    assert response.status_code == 202, response.text
    conversion = response.json()["conversion"]
    assert conversion["conversion_status"] == "READY_FOR_ADMIN_REVIEW", conversion
    assert conversion["validation_status"] == "PASSED"
    assert provider.convert_calls == 1


def test_owner_resubmit_after_stuck_status_gets_next_attempt_not_500(owner_flow):
    """Regression: `existing` reuse lookup deliberately excludes rows left in
    rejected/unsupported_strategy status so a user can retry. But identity_sha256
    is deterministic for the same owner+strategy+version+options, so a naive
    retry re-inserts (identity_sha256, attempt=1) and collides with the old
    stuck row on uq_pine_conversion_identity_attempt -- surfacing as a 500 to
    the user instead of a fresh conversion."""
    client, current, owner, _other, _admin, provider = owner_flow

    created = client.post(
        "/api/personal-pine-strategies",
        json={"name": "Owner Pine", "source": SOURCE, "filename": "owner.pine"},
    )
    assert created.status_code == 200, created.text
    strategy = created.json()["strategy"]
    version = created.json()["version"]
    payload = {
        "consent": True,
        "options": {
            "requested_setup_type": "USER_MANAGED_TRADINGVIEW",
            "intended_symbol": "NIFTY",
            "intended_timeframe": "5",
        },
    }
    convert_url = (
        f"/api/personal-pine-strategies/{strategy['id']}/versions/"
        f"{version['id']}/claude-conversion"
    )

    first = client.post(convert_url, json=payload)
    assert first.status_code == 202, first.text
    first_id = first.json()["conversion"]["id"]

    # Simulate the old pre-fix stuck state (or a since-rejected candidate):
    # a row for this exact identity left in a status the reuse check excludes.
    with session_scope() as db:
        row = db.get(models.PineConversionRequest, uuid.UUID(first_id))
        row.status = "unsupported_strategy"
        db.add(row)

    second = client.post(convert_url, json=payload)
    assert second.status_code == 202, second.text
    assert second.json()["reused"] is False
    assert second.json()["conversion"]["id"] != first_id


def test_owner_pine_claude_admin_compile_installs_only_for_origin_owner(owner_flow):
    client, current, owner, other, admin, provider = owner_flow
    created = client.post(
        "/api/personal-pine-strategies",
        json={
            "name": "My Owner Strategy",
            "source": SOURCE,
            "filename": "owner.pine",
        },
    )
    assert created.status_code == 200, created.text
    strategy = created.json()["strategy"]
    version = created.json()["version"]

    conversion_response = client.post(
        (
            f"/api/personal-pine-strategies/{strategy['id']}/versions/"
            f"{version['id']}/claude-conversion"
        ),
        json={
            "consent": True,
            "options": {
                "requested_setup_type": "USER_MANAGED_TRADINGVIEW",
                "intended_symbol": "NIFTY",
                "intended_timeframe": "5",
            },
        },
    )
    assert conversion_response.status_code == 202, conversion_response.text
    conversion = conversion_response.json()["conversion"]
    assert conversion["owner_user_id"] == str(owner.id)
    assert conversion["conversion_status"] == "READY_FOR_ADMIN_REVIEW"
    assert provider.convert_calls == 1

    current["user"] = other
    hidden = client.get(
        f"/api/personal-pine-claude-conversions/{conversion['id']}"
    )
    assert hidden.status_code == 404

    current["user"] = admin
    queue = client.get("/api/admin/pine-conversions")
    assert queue.status_code == 200
    assert conversion["id"] in {
        item["id"] for item in queue.json()["conversions"]
    }
    approved_response = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/approve",
        json={"reason": "Exact owner candidate reviewed"},
    )
    assert approved_response.status_code == 200, approved_response.text
    assert (
        approved_response.json()["conversion"]["conversion_status"]
        == "APPROVED_FOR_TRADINGVIEW_COMPILE"
    )

    compile_response = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/compile-success",
        json={"setup_notes": "Compiled exactly in TradingView"},
    )
    assert compile_response.status_code == 200, compile_response.text
    installation = compile_response.json()["installation"]
    assert installation["owner_user_id"] == str(owner.id)
    assert installation["strategy_id"] == strategy["id"]
    assert installation["mode"] == "SELF"
    assert installation["live_eligible"] is False

    redirected = client.post(
        "/api/admin/strategy-installations",
        json={
            "conversion_id": conversion["id"],
            "owner_user_id": str(other.id),
            "mode": "SELF",
            "instance_label": "Wrong owner",
        },
    )
    assert redirected.status_code == 409
    assert redirected.json()["reason"] == "OWNER_BINDING_INVALID"

    replay = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/compile-success",
        json={"setup_notes": "Compiled exactly in TradingView"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["installation"]["id"] == installation["id"]

    with session_scope() as db:
        request = db.get(
            models.PineConversionRequest, conversion["id"]
        )
        instance = db.get(
            models.StrategyInstance, installation["strategy_instance_id"]
        )
        setup = db.get(models.TradingViewSetup, installation["id"])
        candidate = db.get(models.StrategyVersion, request.candidate_version_id)
        assert request.owner_user_id == owner.id
        assert candidate.created_by_user_id == owner.id
        assert instance.user_id == owner.id
        assert setup.user_id == owner.id
        assert db.scalar(
            select(models.StrategyInstance).where(
                models.StrategyInstance.user_id == other.id,
                models.StrategyInstance.strategy_id == request.strategy_id,
            )
        ) is None


REAL_BOLLINGER_STRATEGY_SOURCE = """//@version=6
strategy("Bollinger Bands Strategy", overlay=true)
source = close
length = input.int(20, minval=1)
mult = input.float(2.0, minval=0.001, maxval=50)
basis = ta.sma(source, length)
dev = mult * ta.stdev(source, length)
upper = basis + dev
lower = basis - dev
buyEntry = ta.crossover(source, lower)
sellEntry = ta.crossunder(source, upper)
if (ta.crossover(source, lower))
	strategy.entry("BBandLE", strategy.long, stop=lower, oca_name="BollingerBands", oca_type=strategy.oca.cancel, comment="BBandLE")
else
	strategy.cancel(id="BBandLE")
if (ta.crossunder(source, upper))
	strategy.entry("BBandSE", strategy.short, stop=upper, oca_name="BollingerBands", oca_type=strategy.oca.cancel, comment="BBandSE")
else
	strategy.cancel(id="BBandSE")
"""

REAL_BOLLINGER_STRATEGY_LAYER_PRESERVED = """//@version=6
strategy("Bollinger Bands Strategy", overlay=true)
source = close
length = input.int(20, minval=1)
mult = input.float(2.0, minval=0.001, maxval=50)
basis = ta.sma(source, length)
dev = mult * ta.stdev(source, length)
upper = basis + dev
lower = basis - dev
buyEntry = ta.crossover(source, lower)
sellEntry = ta.crossunder(source, upper)
if (ta.crossover(source, lower))
	strategy.entry("BBandLE", strategy.long, stop=lower, oca_name="BollingerBands", oca_type=strategy.oca.cancel, comment="BBandLE", alert_message=novaWebhookPayload("BUY_CE", "BBandLE"))
else
	strategy.cancel(id="BBandLE")
if (ta.crossunder(source, upper))
	strategy.entry("BBandSE", strategy.short, stop=upper, oca_name="BollingerBands", oca_type=strategy.oca.cancel, comment="BBandSE", alert_message=novaWebhookPayload("BUY_PE", "BBandSE"))
else
	strategy.cancel(id="BBandSE")
"""


def _strategy_mode_output(source: str, layer: str, **overrides) -> ClaudePineConversionOutput:
    payload = {
        "schema_version": "nova.claude-pine-conversion.v1",
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "status": "CONVERTED",
        "strategy_layer": layer,
        "signal_mapping": {
            "buy_ce_source": "BBandLE stop entry fill",
            "buy_pe_source": "BBandSE stop entry fill",
            "exit_source": "no explicit flatten in source",
        },
        "behavior_preservation": {"logic_changed": False, "change_summary": []},
        "capabilities": {
            "handled": ["Bollinger band calculation preserved unchanged"],
            "unsupported": [
                "Pending stop-entry order placement -- preserved as-is, reported on fill",
            ],
            "manual_review": [],
        },
        "user_summary": "Preserved the original pending-stop strategy and attached order-fill reporting.",
        "admin_review_points": [],
    }
    payload.update(overrides)
    return ClaudePineConversionOutput.model_validate(payload)


def _strategy_mode_client(monkeypatch, output: ClaudePineConversionOutput):
    from app.auth.dependencies import get_current_user
    from app.routers import personal_pine, pine_conversion
    from app.services.user_context import current_user_from_model

    owner = make_user(f"strategy-mode-owner-{uuid.uuid4().hex[:8]}@example.com")
    current = {"user": owner}
    app = FastAPI()
    app.include_router(personal_pine.router)
    app.include_router(pine_conversion.router)
    app.include_router(pine_conversion.admin_router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(current["user"])
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-only-never-sent")
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MODEL", "claude-test")
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MAX_REPAIRS", 1)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MAX_INPUT_TOKENS", 10_000)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_DAILY_ADMIN_LIMIT", 10)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_DAILY_GLOBAL_LIMIT", 50)
    provider = pine_conversion_provider.FakePineConversionProvider(output)
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: provider)
    return TestClient(app), current, owner, provider


def _submit_for_conversion(client, name: str, source: str):
    created = client.post(
        "/api/personal-pine-strategies",
        json={"name": name, "source": source, "filename": "strategy.pine"},
    )
    assert created.status_code == 200, created.text
    strategy, version = created.json()["strategy"], created.json()["version"]
    convert_url = (
        f"/api/personal-pine-strategies/{strategy['id']}/versions/{version['id']}/claude-conversion"
    )
    payload = {
        "consent": True,
        "options": {
            "requested_setup_type": "USER_MANAGED_TRADINGVIEW",
            "intended_symbol": "NIFTY",
            "intended_timeframe": "5",
        },
    }
    return client.post(convert_url, json=payload)


def test_strategy_mode_preserves_pending_orders_and_attaches_order_fill_alerts(mu_db, monkeypatch):
    """The core ask behind the v4.0 redesign: a real TradingView strategy with
    pending stop entries + OCA cancellation must come back with those exact
    mechanisms preserved (not normalized away into confirmed-bar booleans),
    order-fill alert_message wiring attached, and land in admin review."""
    output = _strategy_mode_output(REAL_BOLLINGER_STRATEGY_SOURCE, REAL_BOLLINGER_STRATEGY_LAYER_PRESERVED)
    client, current, owner, provider = _strategy_mode_client(monkeypatch, output)
    del current, owner

    response = _submit_for_conversion(client, "Bollinger Bands Strategy", REAL_BOLLINGER_STRATEGY_SOURCE)
    assert response.status_code == 202, response.text
    conversion = response.json()["conversion"]
    assert conversion["conversion_status"] == "READY_FOR_ADMIN_REVIEW", conversion
    assert conversion["validation_status"] == "PASSED"
    assert provider.convert_calls == 1

    detail = client.get(f"/api/personal-pine-claude-conversions/{conversion['id']}").json()["conversion"]
    candidate = detail["final_candidate"]
    assert "strategy.entry" in candidate
    assert "oca_name=" in candidate
    assert "stop=lower" in candidate and "stop=upper" in candidate
    assert 'strategy.cancel(id="BBandLE")' in candidate
    assert "pine_transport_v3_strategy_fill" in candidate
    assert "novaBuyCeSignal" not in candidate


def test_strategy_mode_rejects_a_dropped_order_call(mu_db, monkeypatch):
    """If Claude drops an order-producing call instead of just instrumenting
    it, that's exactly the failure mode this redesign exists to prevent --
    must not silently pass validation."""
    layer_missing_short_side = REAL_BOLLINGER_STRATEGY_LAYER_PRESERVED.replace(
        'strategy.entry("BBandSE", strategy.short, stop=upper, oca_name="BollingerBands", '
        'oca_type=strategy.oca.cancel, comment="BBandSE", alert_message=novaWebhookPayload("BUY_PE", "BBandSE"))',
        "// dropped",
    )
    output = _strategy_mode_output(REAL_BOLLINGER_STRATEGY_SOURCE, layer_missing_short_side)
    client, current, owner, provider = _strategy_mode_client(monkeypatch, output)
    del current, owner

    response = _submit_for_conversion(client, "Bollinger Bands Strategy Dropped", REAL_BOLLINGER_STRATEGY_SOURCE)
    assert response.status_code == 202, response.text
    conversion = response.json()["conversion"]
    assert conversion["conversion_status"] == "MANUAL_CONVERSION_REQUIRED", conversion
    assert provider.convert_calls == 1
