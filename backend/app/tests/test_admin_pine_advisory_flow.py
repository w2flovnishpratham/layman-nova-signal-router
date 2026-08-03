"""Regression coverage for the simplified admin Pine conversion flow.

Pre-conversion capability analysis is advisory context only, never an
execution gate: submit -> automatic Claude conversion -> converted Pine
stored -> post-conversion validation -> admin review (approve / request
changes / reject). The Bollinger Bands strategy below (pending stop entries +
strategy.cancel_all + an opposite-direction reversal) is the regression case:
it must reach Claude and a completed review package, never get stuck at
UNSUPPORTED_STRATEGY / Validation: NOT_RUN.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.services import pine_conversion_provider
from app.tests.test_admin_claude_pine_conversion import (
    _client,
    _enable,
    _output,
    make_user,
)

pytest_plugins = ("app.tests.conftest_multiuser",)

BOLLINGER_SOURCE = """//@version=6
indicator("Bollinger Bands strategy", overlay=true)
[middle, upper, lower] = ta.bb(close, 20, 2)
if ta.crossunder(close, lower)
    strategy.entry("Long", strategy.long, stop=lower)
if ta.crossover(close, upper)
    strategy.entry("Short", strategy.short, stop=upper)
strategy.cancel_all()
"""

NORMALIZED_LAYER = """//@version=6
indicator("Bollinger Bands converted", overlay=true)
[middle, upper, lower] = ta.bb(close, 20, 2)
bool novaBuyCeSignal = ta.crossunder(close, lower)
bool novaBuyPeSignal = ta.crossover(close, upper)
bool novaExitSignal = ta.crossover(close, upper) or ta.crossunder(close, lower)
"""


def _submit_bollinger(client: TestClient, name: str = "Bollinger Bands") -> dict:
    response = client.post("/api/admin/pine-conversions", json={
        "strategy_name": name,
        "source": BOLLINGER_SOURCE,
        "original_filename": "bollinger.pine",
        "options": {
            "requested_setup_type": "USER_MANAGED_TRADINGVIEW",
            "intended_symbol": "NIFTY",
            "intended_timeframe": "5",
        },
    })
    assert response.status_code == 200, response.text
    return response.json()["conversion"]


def test_bollinger_submission_is_immediately_ready_for_conversion_not_unsupported(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("bb-admin@example.com", is_admin=True))
    conversion = _submit_bollinger(client)

    # The old bug: this used to become a terminal UNSUPPORTED_STRATEGY here.
    assert conversion["conversion_status"] == "READY_FOR_CONVERSION"
    assert conversion["analysis_status"] == "ANALYZED"
    assert conversion["validation_status"] == "NOT_RUN"
    assert conversion["review_status"] == "PENDING"

    # The finding is still visible (advisory), just no longer a blocker.
    assert conversion["analysis"]["blockers"] == ["BLK_PENDING_ENGINE"]
    assert set(conversion["analysis"]["matched_capabilities"]) >= {
        "OPPOSITE_DIRECTION_REVERSAL_NORMALIZATION",
        "PENDING_ORDER_CANCELLATION",
        "PENDING_STOP_ENTRY",
    }
    guidance = conversion["conversion_guidance"]
    assert guidance["blockers"] == ["BLK_PENDING_ENGINE"]
    note = guidance["notes"][0]
    assert any("pending" in item.lower() for item in note["original_semantics"])
    assert any("exit" in item.lower() for item in note["proposed_semantics"])

    # No manual-normalization gate: conversion is reachable right away.
    assert client.post(f"/api/admin/pine-conversions/{conversion['id']}/manual-package").status_code == 200


def test_bollinger_conversion_normalizes_pending_orders_and_reaches_admin_review(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("bb-convert-admin@example.com", is_admin=True))
    conversion = _submit_bollinger(client)

    captured_prompt = {}
    from app.services import admin_pine_conversion_service
    real_build = admin_pine_conversion_service._build_request

    def spy_build(row, source):
        request = real_build(row, source)
        captured_prompt["prompt"] = request.prompt
        return request

    monkeypatch.setattr(admin_pine_conversion_service, "_build_request", spy_build)
    output = _output(conversion, layer=NORMALIZED_LAYER)
    fake = pine_conversion_provider.FakePineConversionProvider(output)
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)

    result = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert")
    assert result.status_code == 200, result.text
    detail = result.json()["conversion"]

    # Claude was actually called (no gate in the way) and given advisory context.
    assert fake.count_calls == fake.convert_calls == 1
    assert "ADVISORY PRE-ANALYSIS CONTEXT" in captured_prompt["prompt"]
    assert "BLK_PENDING_ENGINE" in captured_prompt["prompt"]

    # Post-conversion validation ran against the converted source and is the
    # real gate: BUY_CE/BUY_PE/EXIT present, pending-order/cancel gone.
    assert detail["conversion_status"] == "READY_FOR_ADMIN_REVIEW"
    assert detail["validation_status"] == "PASSED"
    assert detail["validation"]["eligible_for_review"] is True
    assert "novaBuyCeSignal" in detail["strategy_layer"]
    assert "novaBuyPeSignal" in detail["strategy_layer"]
    assert "novaExitSignal" in detail["strategy_layer"]
    assert "strategy.entry" not in detail["final_candidate"]
    assert "stop=" not in detail["final_candidate"]
    assert "strategy.cancel" not in detail["final_candidate"]

    approve = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/approve",
        json={"reason": "Converted candidate reviewed for TradingView compile only"},
    )
    assert approve.status_code == 200, approve.text
    final = approve.json()["conversion"]
    assert final["conversion_status"] == "APPROVED_FOR_TRADINGVIEW_COMPILE"
    assert final["approval_integrity"] is True


def test_admin_can_request_changes_as_a_third_review_action(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("bb-changes-admin@example.com", is_admin=True))
    conversion = _submit_bollinger(client)
    fake = pine_conversion_provider.FakePineConversionProvider(_output(conversion, layer=NORMALIZED_LAYER))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)
    converted = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert").json()["conversion"]
    assert converted["conversion_status"] == "READY_FOR_ADMIN_REVIEW"

    missing_reason = client.post(f"/api/admin/pine-conversions/{conversion['id']}/request-changes", json={})
    assert missing_reason.status_code == 422
    assert missing_reason.json()["reason"] == "CHANGES_REASON_REQUIRED"

    requested = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/request-changes",
        json={"reason": "Confirm NIFTY-only compatibility before approval."},
    )
    assert requested.status_code == 200, requested.text
    detail = requested.json()["conversion"]
    assert detail["conversion_status"] == "CHANGES_REQUESTED"
    assert detail["review_status"] == "CHANGES_REQUESTED"

    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db:
        row = db.get(models.PineConversionRequest, uuid.UUID(conversion["id"]))
        reviews = db.query(models.StrategyAdminReview).filter_by(strategy_version_id=row.candidate_version_id).all()
        assert len(reviews) == 1
        assert reviews[0].decision == "changes_requested"

    # Terminal for this exact candidate: neither approve nor a second
    # changes-requested call is allowed once it has been requested.
    assert client.post(f"/api/admin/pine-conversions/{conversion['id']}/approve", json={}).status_code == 409
    assert client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/request-changes", json={"reason": "again"}
    ).status_code == 409


def test_duplicate_conversion_clicks_create_only_one_candidate_version(mu_db, monkeypatch):
    _enable(monkeypatch)
    client = _client(make_user("bb-double-admin@example.com", is_admin=True))
    conversion = _submit_bollinger(client)
    fake = pine_conversion_provider.FakePineConversionProvider(_output(conversion, layer=NORMALIZED_LAYER))
    monkeypatch.setattr(pine_conversion_provider, "get_claude_provider", lambda: fake)

    first = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert")
    assert first.status_code == 200
    second = client.post(f"/api/admin/pine-conversions/{conversion['id']}/convert")
    assert second.status_code == 409
    assert fake.count_calls == fake.convert_calls == 1

    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db:
        row = db.get(models.PineConversionRequest, uuid.UUID(conversion["id"]))
        assert db.query(models.StrategyVersion).filter_by(strategy_id=row.strategy_id).count() == 2  # original + candidate


def test_another_admin_cannot_access_someone_elses_submission_or_output(mu_db, monkeypatch):
    _enable(monkeypatch)
    owner = _client(make_user("bb-owner-admin@example.com", is_admin=True))
    other = _client(make_user("bb-other-admin@example.com", is_admin=True))
    conversion = _submit_bollinger(owner)

    assert other.get(f"/api/admin/pine-conversions/{conversion['id']}").status_code == 404
    assert other.post(f"/api/admin/pine-conversions/{conversion['id']}/convert").status_code == 404
    assert other.post(f"/api/admin/pine-conversions/{conversion['id']}/manual-package").status_code == 404
