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

BOLLINGER_BACKTEST_LAYER = """//@version=6
strategy("Bollinger Bands backtest", overlay=true, default_qty_type=strategy.fixed, default_qty_value=1)
[middle, upper, lower] = ta.bb(close, 20, 2)
bool novaBuyCeSignal = ta.crossunder(close, lower)
bool novaBuyPeSignal = ta.crossover(close, upper)
bool novaExitSignal = ta.crossover(close, upper) or ta.crossunder(close, lower)
if novaBuyCeSignal
    strategy.entry("CE", strategy.long)
if novaBuyPeSignal
    strategy.entry("PE", strategy.short)
if novaExitSignal
    strategy.close_all()
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
        "backtest_layer": BOLLINGER_BACKTEST_LAYER,
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
        "backtest_layer": BOLLINGER_BACKTEST_LAYER,
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


def _strategy_mode_client(monkeypatch, output: ClaudePineConversionOutput, *, repair_output: ClaudePineConversionOutput | None = None):
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
    provider = pine_conversion_provider.FakePineConversionProvider(output, repair_output=repair_output)
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


def test_withdrawing_a_strategy_submitted_for_claude_conversion_is_a_clean_409_not_a_500(owner_flow):
    """Regression: input_version_id on pine_conversion_requests is
    ondelete=RESTRICT, so a strategy still referenced by a (even
    unapproved) conversion request used to hit an unhandled IntegrityError
    -> 500 on withdraw instead of the same clean 409 an approved or
    instance-linked strategy already gets."""
    client, current, owner, other, admin, provider = owner_flow
    created = client.post(
        "/api/personal-pine-strategies",
        json={"name": "Submitted for conversion", "source": SOURCE, "filename": "owner.pine"},
    )
    strategy = created.json()["strategy"]
    version = created.json()["version"]
    conversion_response = client.post(
        f"/api/personal-pine-strategies/{strategy['id']}/versions/{version['id']}/claude-conversion",
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

    blocked = client.delete(f"/api/personal-pine-strategies/{strategy['id']}")
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["reason"] == "CONVERSION_REQUEST_EXISTS"
    assert client.get(f"/api/personal-pine-strategies/{strategy['id']}").status_code == 200


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
    assert "pine_transport_v3_fill" in candidate
    assert "novaBuyCeSignal" not in candidate
    # Regression for a real TradingView compile error (CE10271: "Could not
    # find function or function reference 'novaWebhookPayload'") -- Pine
    # requires a function to be defined before its first call. The
    # definition must appear before every alert_message=novaWebhookPayload(
    # call site, not after (which is where INDICATOR mode's transport goes,
    # since that one is only ever read from, never called by, the layer).
    definition_at = candidate.index("novaWebhookPayload(string action")
    first_call_at = candidate.index("alert_message=novaWebhookPayload(")
    assert definition_at < first_call_at, "transport must define novaWebhookPayload before the layer calls it"


def test_strategy_mode_dropped_order_call_still_reaches_admin_review(mu_db, monkeypatch):
    """No in-between checkup: if Claude drops an order-producing call instead
    of just instrumenting it, that's no longer a backend block -- it reaches
    admin review as-is, with the deterministic validator's finding attached
    as advisory info for the admin to read before approving/rejecting."""
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
    assert conversion["conversion_status"] == "READY_FOR_ADMIN_REVIEW", conversion
    assert conversion["candidate_version_id"] is not None
    assert provider.repair_calls == 0


def test_strategy_mode_accepts_a_faithfully_preserved_pine_v5_source(mu_db, monkeypatch):
    """Source intake already accepts Pine v5 or v6 (_validate_exact_source),
    and STRATEGY mode's whole design is to preserve the declaration exactly
    -- a real user hit "Conversion stopped safely: pine version unsupported"
    because the candidate validator hard-required //@version=6 even for a
    faithfully-preserved v5 candidate. Forcing a version upgrade would also
    mean asking Claude to touch more than instrumentation. Must accept v5
    candidates the same way intake does."""
    v5_source = REAL_BOLLINGER_STRATEGY_SOURCE.replace("//@version=6", "//@version=5")
    v5_layer = REAL_BOLLINGER_STRATEGY_LAYER_PRESERVED.replace("//@version=6", "//@version=5")
    output = _strategy_mode_output(v5_source, v5_layer)
    client, current, owner, provider = _strategy_mode_client(monkeypatch, output)
    del current, owner

    response = _submit_for_conversion(client, "Bollinger Bands Strategy v5", v5_source)
    assert response.status_code == 202, response.text
    conversion = response.json()["conversion"]
    assert conversion["conversion_status"] == "READY_FOR_ADMIN_REVIEW", conversion
    assert conversion["safe_error_code"] is None
    assert provider.convert_calls == 1


REAL_ATST_STRATEGY_SOURCE = """//@version=5
strategy("AlphaTrend Strategy", shorttitle='ATSt', overlay=true, format=format.price, precision=2, margin_long=100, margin_short=100)
buySignalk = ta.crossover(close, open)
sellSignalk = ta.crossunder(close, open)

longCondition = buySignalk
if (longCondition)
    strategy.entry("Long", strategy.long)

shortCondition = sellSignalk
if (shortCondition)
    strategy.entry("Short", strategy.short)
"""


def test_strategy_mode_missing_alert_message_reaches_admin_review_without_repair(mu_db, monkeypatch):
    """Regression for the real reported "ATSt" strategy: a plain Long/Short
    strategy.entry pair with no pre-existing alert()/alertcondition()/webhook
    of any kind. Claude sometimes preserves the order calls but forgets to
    add alert_message -- no in-between checkup fixes or blocks this anymore,
    it reaches admin review as Claude returned it, with the validator's
    ALERT_MESSAGE_MISSING finding attached as advisory info only."""
    missing_alert_message = ClaudePineConversionOutput.model_validate({
        "schema_version": "nova.claude-pine-conversion.v1",
        "source_sha256": hashlib.sha256(REAL_ATST_STRATEGY_SOURCE.encode()).hexdigest(),
        "status": "CONVERTED",
        "strategy_layer": REAL_ATST_STRATEGY_SOURCE,  # preserved verbatim, but no alert_message added
        "signal_mapping": {"buy_ce_source": "Long entry", "buy_pe_source": "Short entry", "exit_source": "reversal only"},
        "behavior_preservation": {"logic_changed": False, "change_summary": []},
        "capabilities": {"handled": ["Long/Short entries preserved"], "unsupported": [], "manual_review": []},
        "user_summary": "Preserved the strategy as-is.",
        "admin_review_points": [],
    })
    client, current, owner, provider = _strategy_mode_client(monkeypatch, missing_alert_message)
    del current, owner

    response = _submit_for_conversion(client, "AlphaTrend Strategy", REAL_ATST_STRATEGY_SOURCE)
    assert response.status_code == 202, response.text
    conversion = response.json()["conversion"]
    assert conversion["conversion_status"] == "READY_FOR_ADMIN_REVIEW", conversion
    assert conversion["validation_status"] == "FAILED"
    assert provider.convert_calls == 1
    assert provider.repair_calls == 0


LICENSED_ATST_STRATEGY_SOURCE = """// This source code is subject to the terms of the Mozilla Public License 2.0
// author © KivancOzbilgic
//@version=5
strategy("AlphaTrend Strategy", shorttitle='ATSt', overlay=true)
buySignalk = ta.crossover(close, open)
sellSignalk = ta.crossunder(close, open)

longCondition = buySignalk
if (longCondition)
    strategy.entry("Long", strategy.long, alert_message=novaWebhookPayload("BUY_CE", "Long"))

shortCondition = sellSignalk
if (shortCondition)
    strategy.entry("Short", strategy.short, alert_message=novaWebhookPayload("BUY_PE", "Short"))
"""


def test_strategy_mode_dropped_version_directive_reaches_admin_review_without_repair(mu_db, monkeypatch):
    """Regression for a second real ATSt failure mode: with a license/author
    comment header above //@version=5, Claude sometimes drops the version
    directive along with the header it was cleaning up around. No in-between
    checkup repairs this anymore -- it reaches admin review as Claude
    returned it, with the validator's finding attached as advisory info."""
    dropped_version = LICENSED_ATST_STRATEGY_SOURCE.replace("//@version=5\n", "")
    missing_version = _strategy_mode_output(LICENSED_ATST_STRATEGY_SOURCE, dropped_version)
    client, current, owner, provider = _strategy_mode_client(monkeypatch, missing_version)
    del current, owner

    response = _submit_for_conversion(client, "AlphaTrend Strategy Licensed", LICENSED_ATST_STRATEGY_SOURCE)
    assert response.status_code == 202, response.text
    conversion = response.json()["conversion"]
    assert conversion["conversion_status"] == "READY_FOR_ADMIN_REVIEW", conversion
    assert conversion["validation_status"] == "FAILED"
    assert provider.convert_calls == 1
    assert provider.repair_calls == 0


REAL_GREEDY_STRATEGY_SOURCE = """//@version=6
strategy("Greedy Strategy", pyramiding = 100, calc_on_order_fills=false, overlay=true)
tp = input(10, "Take profit")
sl = input(10, "Stop loss")
upGap = open > high[1]
dnGap = open < low[1]
dn = strategy.position_size < 0 and open > close
up = strategy.position_size > 0 and open < close
if upGap
    strategy.entry("GapUp", strategy.long, stop = high[1])
else
    strategy.cancel("GapUp")
if dn
    strategy.entry("Dn", strategy.short, stop = close)
else
    strategy.cancel("Dn")
if dnGap
    strategy.entry("GapDn", strategy.short, stop = low[1])
else
    strategy.cancel("GapDn")
if up
    strategy.entry("Up", strategy.long, stop = close)
else
    strategy.cancel("Up")
XQty = strategy.position_size < 0 ? -strategy.position_size : strategy.position_size
dir = strategy.position_size < 0 ? -1 : 1
lmP = strategy.position_avg_price + dir*tp*syminfo.mintick
slP = strategy.position_avg_price - dir*sl*syminfo.mintick
float nav = na
if XQty > 0
    strategy.order("TP", strategy.position_size < 0 ? strategy.long : strategy.short, XQty, lmP, nav, "TPSL", strategy.oca.reduce, "TPSL")
    strategy.order("SL", strategy.position_size < 0 ? strategy.long : strategy.short, XQty, nav, slP, "TPSL", strategy.oca.reduce, "TPSL")
else
    strategy.cancel("TP")
    strategy.cancel("SL")
"""

REAL_GREEDY_STRATEGY_LAYER_PRESERVED = """//@version=6
strategy("Greedy Strategy", pyramiding = 100, calc_on_order_fills=false, overlay=true)
tp = input(10, "Take profit")
sl = input(10, "Stop loss")
upGap = open > high[1]
dnGap = open < low[1]
dn = strategy.position_size < 0 and open > close
up = strategy.position_size > 0 and open < close
if upGap
    strategy.entry("GapUp", strategy.long, stop = high[1], alert_message=novaWebhookPayload("BUY_CE", "GapUp"))
else
    strategy.cancel("GapUp")
if dn
    strategy.entry("Dn", strategy.short, stop = close, alert_message=novaWebhookPayload("BUY_PE", "Dn"))
else
    strategy.cancel("Dn")
if dnGap
    strategy.entry("GapDn", strategy.short, stop = low[1], alert_message=novaWebhookPayload("BUY_PE", "GapDn"))
else
    strategy.cancel("GapDn")
if up
    strategy.entry("Up", strategy.long, stop = close, alert_message=novaWebhookPayload("BUY_CE", "Up"))
else
    strategy.cancel("Up")
XQty = strategy.position_size < 0 ? -strategy.position_size : strategy.position_size
dir = strategy.position_size < 0 ? -1 : 1
lmP = strategy.position_avg_price + dir*tp*syminfo.mintick
slP = strategy.position_avg_price - dir*sl*syminfo.mintick
float nav = na
if XQty > 0
    strategy.order("TP", strategy.position_size < 0 ? strategy.long : strategy.short, XQty, lmP, nav, "TPSL", strategy.oca.reduce, "TPSL", alert_message=novaWebhookPayload("EXIT", "TP"))
    strategy.order("SL", strategy.position_size < 0 ? strategy.long : strategy.short, XQty, nav, slP, "TPSL", strategy.oca.reduce, "TPSL", alert_message=novaWebhookPayload("EXIT", "SL"))
else
    strategy.cancel("TP")
    strategy.cancel("SL")
"""


def test_strategy_mode_advisory_reassures_preserve_for_matched_blockers(mu_db, monkeypatch):
    """Regression for the actual reported 'greedy' bug: for a STRATEGY-mode
    source, the advisory block must not tell Claude to normalize/remove
    fill-dependent state, pyramiding, or generic order semantics -- it must
    say to keep them exactly as-is. The old INDICATOR-oriented wording
    directly contradicted the main STRATEGY-mode instructions and caused
    Claude to report logic_changed=true for mechanisms that need no change
    at all in this mode."""
    from app.services import admin_pine_conversion_service as svc
    from app.services import pine_semantic_preanalyzer
    from types import SimpleNamespace

    analysis_result = pine_semantic_preanalyzer.analyze_source(REAL_GREEDY_STRATEGY_SOURCE)
    row = SimpleNamespace(
        input_source_sha256="a" * 64,
        usage_summary={"analysis": svc._analysis_public(analysis_result), "conversion_guidance": svc._conversion_guidance(analysis_result)},
        options={},
        model="claude-test-model",
    )
    prompt = svc._build_request(row, REAL_GREEDY_STRATEGY_SOURCE).prompt
    assert "keep as-is, no change needed" in prompt
    assert "recompute from confirmed bar data only" not in prompt
    assert "Only one open position per side is tracked" not in prompt
    assert "Orders are re-expressed as directional BUY_CE" not in prompt


def test_strategy_mode_logic_changed_manual_review_reaches_admin_without_retry(mu_db, monkeypatch):
    """End-to-end reproduction of the real 'greedy' strategy: even when
    Claude's response reports logic_changed=true/MANUAL_REVIEW_REQUIRED, no
    in-between checkup retries Claude anymore -- the response goes straight
    to admin review as Claude returned it, self-reported concern included."""
    overly_conservative = _strategy_mode_output(
        REAL_GREEDY_STRATEGY_SOURCE, REAL_GREEDY_STRATEGY_LAYER_PRESERVED,
        status="MANUAL_REVIEW_REQUIRED",
        behavior_preservation={"logic_changed": True, "change_summary": ["fill-dependent TP/SL calc"]},
    )
    client, current, owner, provider = _strategy_mode_client(monkeypatch, overly_conservative)
    del current, owner

    response = _submit_for_conversion(client, "Greedy Strategy", REAL_GREEDY_STRATEGY_SOURCE)
    assert response.status_code == 202, response.text
    conversion = response.json()["conversion"]
    assert conversion["conversion_status"] == "READY_FOR_ADMIN_REVIEW", conversion
    assert provider.convert_calls == 1
    assert provider.repair_calls == 0
