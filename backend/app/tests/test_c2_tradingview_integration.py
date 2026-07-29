"""C2 product vertical-slice tests through real routes and database state."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.schemas.pine_conversion import ClaudePineConversionOutput
from app.tests.conftest_multiuser import make_user

pytest_plugins = ("app.tests.conftest_multiuser",)


SOURCE = """//@version=6
strategy("C2 Source", overlay=true)
fast = ta.ema(close, 5)
slow = ta.ema(close, 13)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""

LAYER = """//@version=6
strategy("C2 Source", overlay=true)
fast = ta.ema(close, 5)
slow = ta.ema(close, 13)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, alert_message=novaWebhookPayload("BUY_CE", "Long"))
if ta.crossunder(fast, slow)
    strategy.close("Long", alert_message=novaWebhookPayload("EXIT", "Long"))
"""


@pytest.fixture
def c2_app(mu_db, monkeypatch):
    from app.auth.dependencies import get_current_user
    from app.routers import (
        c2_tradingview,
        pine_conversion,
        private_webhook,
        strategy_instances,
    )
    from app.services.user_context import current_user_from_model

    admin = make_user("c2-admin@example.com", is_admin=True)
    owner = make_user("c2-owner@example.com")
    other = make_user("c2-other@example.com")
    current = {"user": admin}
    app = FastAPI()
    app.include_router(pine_conversion.admin_router)
    app.include_router(c2_tradingview.router)
    app.include_router(c2_tradingview.admin_router)
    app.include_router(private_webhook.router)
    app.include_router(strategy_instances.router)
    app.include_router(strategy_instances.engine_router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(
        current["user"]
    )
    monkeypatch.setattr(settings, "C2_TRADINGVIEW_INSTALLATION_ENABLED", True)
    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED", False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False)
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-only-never-sent")
    monkeypatch.setattr(settings, "CLAUDE_CONVERSION_MODEL", "test-model")
    return TestClient(app), current, admin, owner, other


def _output(conversion: dict) -> dict:
    matched = conversion["analysis"]["matched_capabilities"]
    return ClaudePineConversionOutput.model_validate(
        {
            "schema_version": "nova.claude-pine-conversion.v1",
            "source_sha256": conversion["source_sha256"],
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
                "handled": matched,
                "unsupported": [],
                "manual_review": [],
            },
            "user_summary": "Exact C2 test candidate.",
            "admin_review_points": [],
        }
    ).model_dump(mode="json")


def _conversion(client: TestClient, *, name: str | None = None) -> dict:
    response = client.post(
        "/api/admin/pine-conversions",
        json={
            "strategy_name": name or f"C2 {uuid.uuid4().hex[:8]}",
            "source": SOURCE,
            "original_filename": "c2-source.pine",
            "internal_notes": "C2 local test",
            "options": {
                "requested_setup_type": "USER_MANAGED_TRADINGVIEW",
                "intended_symbol": "NIFTY",
                "intended_timeframe": "5",
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["conversion"]


def _approved(client: TestClient, *, name: str | None = None) -> dict:
    conversion = _conversion(client, name=name)
    manual = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/manual-response",
        json={"response_json": json.dumps(_output(conversion))},
    )
    assert manual.status_code == 200, manual.text
    approval = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/approve",
        json={"reason": "C2 compile review"},
    )
    assert approval.status_code == 200, approval.text
    approved = approval.json()["conversion"]
    assert approved["approval_integrity"] is True
    return approved


def _compile(client: TestClient, conversion: dict) -> dict:
    response = client.post(
        f"/api/admin/pine-conversions/{conversion['id']}/compile-success",
        json={"setup_notes": "Compiled manually in TradingView"},
    )
    assert response.status_code == 200, response.text
    return response.json()["compile"]


def _install(
    client: TestClient,
    conversion: dict,
    owner_id,
    *,
    mode: str = "SELF",
) -> dict:
    response = client.post(
        "/api/admin/strategy-installations",
        json={
            "conversion_id": conversion["id"],
            "owner_user_id": str(owner_id),
            "mode": mode,
            "instance_label": f"{mode.title()} C2 {uuid.uuid4().hex[:5]}",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["installation"]


def _credential(client: TestClient, installation_id: str, *, admin: bool = True) -> dict:
    path = (
        f"/api/admin/strategy-installations/{installation_id}/credential"
        if admin
        else f"/api/strategies/my-installations/{installation_id}/self-credential"
    )
    response = client.post(path)
    assert response.status_code == 200, response.text
    return response.json()["credential"]


def _hold(token: str, *, signal_id: str | None = None, action: str = "HOLD") -> dict:
    return {
        "credential": token,
        "action": action,
        "signal_id": signal_id or f"c2-hold-{uuid.uuid4().hex[:10]}",
        "signal_time": datetime.now(timezone.utc).isoformat(),
        "strategy_version": "C1_CANDIDATE",
        "comment": "real route C2 test",
    }


def test_exact_approval_compile_and_feature_flag_gates(c2_app, monkeypatch):
    client, current, admin, owner, _ = c2_app
    pending = _conversion(client)
    blocked = client.post(
        f"/api/admin/pine-conversions/{pending['id']}/compile-success", json={}
    )
    assert blocked.status_code == 409
    assert blocked.json()["reason"] == "C1_APPROVAL_REQUIRED"

    approved = _approved(client)
    current["user"] = owner
    assert client.post(
        f"/api/admin/pine-conversions/{approved['id']}/compile-success", json={}
    ).status_code == 403
    current["user"] = admin

    monkeypatch.setattr(settings, "C2_TRADINGVIEW_INSTALLATION_ENABLED", False)
    disabled = client.post(
        f"/api/admin/pine-conversions/{approved['id']}/compile-success", json={}
    )
    assert disabled.status_code == 404
    assert disabled.json()["reason"] == "FEATURE_DISABLED"
    monkeypatch.setattr(settings, "C2_TRADINGVIEW_INSTALLATION_ENABLED", True)

    compile_row = _compile(client, approved)
    assert compile_row["candidate_sha256"] == approved["candidate_sha256"]
    assert compile_row["source_sha256"] == approved["source_sha256"]
    assert compile_row["prompt_version"] == "v4.0"
    assert compile_row["result"] == "SUCCESS"
    duplicate = client.post(
        f"/api/admin/pine-conversions/{approved['id']}/compile-success", json={}
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["compile"]["id"] == compile_row["id"]


def test_compile_failure_is_terminal_and_blocks_installation(c2_app):
    client, _, _, owner, _ = c2_app
    approved = _approved(client)
    failure = client.post(
        f"/api/admin/pine-conversions/{approved['id']}/compile-failure",
        json={
            "compiler_error_summary": (
                "line 12 syntax error token=should-not-survive password=hunter2"
            )
        },
    )
    assert failure.status_code == 200
    stored = failure.json()["compile"]
    assert stored["result"] == "FAILURE"
    assert "should-not-survive" not in stored["compiler_error_summary"]
    assert "hunter2" not in stored["compiler_error_summary"]
    install = client.post(
        "/api/admin/strategy-installations",
        json={
            "conversion_id": approved["id"],
            "owner_user_id": str(owner.id),
            "mode": "SELF",
            "instance_label": "blocked",
        },
    )
    assert install.status_code == 409
    assert install.json()["reason"] == "COMPILE_SUCCESS_REQUIRED"
    changed = client.post(
        f"/api/admin/pine-conversions/{approved['id']}/compile-success", json={}
    )
    assert changed.status_code == 409
    assert changed.json()["reason"] == "COMPILE_RESULT_FINAL"


def test_managed_self_installations_are_owner_bound_inert_and_idempotent(c2_app):
    client, _, _, owner, other = c2_app
    approved = _approved(client)
    _compile(client, approved)
    self_install = _install(client, approved, owner.id, mode="SELF")
    managed = _install(client, approved, other.id, mode="MANAGED")

    assert self_install["execution_mode"] == "signal_only"
    assert self_install["instance_status"] == "ready"
    assert self_install["live_eligible"] is False
    assert managed["mode"] == "MANAGED"
    assert managed["owner_user_id"] == str(other.id)

    duplicate = client.post(
        "/api/admin/strategy-installations",
        json={
            "conversion_id": approved["id"],
            "owner_user_id": str(owner.id),
            "mode": "SELF",
            "instance_label": "ignored duplicate label",
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["installation"]["id"] == self_install["id"]

    smuggled = client.post(
        "/api/admin/strategy-installations",
        json={
            "conversion_id": approved["id"],
            "owner_user_id": str(owner.id),
            "mode": "SELF",
            "instance_label": "unsafe",
            "execution_mode": "real_orders",
            "quantity": 999,
            "broker": "dhan",
        },
    )
    assert smuggled.status_code == 422

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        rows = db.scalars(select(models.StrategyInstance)).all()
        assert len(rows) == 2
        assert all(row.execution_mode == "signal_only" for row in rows)
        assert all(row.status == "ready" and not row.verification_mode for row in rows)
        assert all(row.legacy_subscription_id is None for row in rows)


def test_display_once_hash_only_credentials_and_managed_secret_policy(c2_app):
    client, current, admin, owner, other = c2_app
    approved = _approved(client)
    _compile(client, approved)
    self_install = _install(client, approved, owner.id, mode="SELF")
    managed = _install(client, approved, other.id, mode="MANAGED")

    current["user"] = owner
    issued = _credential(client, self_install["id"], admin=False)
    token = issued["token"]
    assert token.startswith("nwk_")
    assert token in issued["setup_package"]["alert_message"]
    assert token not in issued["setup_package"]["webhook_url"]
    detail = client.get(
        f"/api/strategies/my-installations/{self_install['id']}"
    ).json()["installation"]
    serialized = json.dumps(detail)
    assert token not in serialized
    assert hashlib.sha256(token.encode()).hexdigest() not in serialized
    assert "{{ONE_TIME_CREDENTIAL}}" in detail["setup_package"]["alert_message"]

    current["user"] = other
    denied = client.post(
        f"/api/strategies/my-installations/{managed['id']}/self-credential"
    )
    assert denied.status_code == 403
    assert denied.json()["reason"] == "ADMIN_ACTION_REQUIRED"
    current["user"] = admin
    managed_issued = _credential(client, managed["id"], admin=True)
    assert managed_issued["token"].startswith("nwk_")

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        credential = db.get(
            models.StrategyInstanceWebhookCredential, uuid.UUID(issued["id"])
        )
        assert credential.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert token not in credential.token_hash + credential.token_prefix
        audit_text = json.dumps(
            [
                {"action": row.action, "metadata": row.audit_metadata}
                for row in db.scalars(select(models.AuditLog)).all()
            ]
        )
        assert token not in audit_text


def test_real_hold_makes_paper_eligible_without_execution_and_owner_only(c2_app):
    client, current, admin, owner, other = c2_app
    approved = _approved(client)
    _compile(client, approved)
    installation = _install(client, approved, owner.id)
    current["user"] = owner
    issued = _credential(client, installation["id"], admin=False)
    body = _hold(issued["token"])

    local_test = client.post(
        f"/api/strategy-instances/{installation['strategy_instance_id']}/webhook-test"
    )
    assert local_test.status_code == 409
    assert local_test.json()["reason"] == "REAL_TRADINGVIEW_HOLD_REQUIRED"
    before_hold = client.get(
        f"/api/strategies/my-installations/{installation['id']}"
    ).json()["installation"]
    assert before_hold["hold_status"] == "AWAITING_HOLD"
    assert before_hold["paper_eligible"] is False

    accepted = client.post("/api/webhooks/private", json=body)
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["reason"] == "HOLD"
    assert accepted.json()["c2_verification"]["paper_eligible"] is True
    repeated = client.post("/api/webhooks/private", json=body)
    assert repeated.status_code == 200
    assert repeated.json()["duplicate"] is True

    detail = client.get(
        f"/api/strategies/my-installations/{installation['id']}"
    ).json()["installation"]
    assert detail["paper_eligible"] is True
    assert detail["live_eligible"] is False
    assert detail["hold_status"] == "VERIFIED"

    activated = client.post(
        f"/api/strategy-instances/{installation['strategy_instance_id']}/activate"
    )
    assert activated.status_code == 200, activated.text
    stopped = client.post(
        f"/api/strategy-instances/{installation['strategy_instance_id']}/stop",
        json={"reason": "Engine remains explicitly stopped"},
    )
    assert stopped.status_code == 200, stopped.text

    picker = client.get("/api/engine/strategies").json()
    entry = next(
        item
        for item in picker["strategies"]
        if item["instance_id"] == installation["strategy_instance_id"]
    )
    assert entry["selectable"] is True
    assert entry["instance_status"] == "stopped"
    selected = client.put(
        "/api/engine/selection",
        json={"strategy_instance_id": installation["strategy_instance_id"]},
    )
    assert selected.status_code == 200

    current["user"] = other
    assert client.get("/api/strategies/my-installations").json()["installations"] == []
    assert all(
        item["instance_id"] != installation["strategy_instance_id"]
        for item in client.get("/api/engine/strategies").json()["strategies"]
    )
    current["user"] = admin

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        instance = db.get(
            models.StrategyInstance, uuid.UUID(installation["strategy_instance_id"])
        )
        assert instance.status == "stopped"
        assert instance.execution_mode == "signal_only"
        assert not instance.verification_mode
        assert db.query(models.StrategyExecutionJob).count() == 0
        assert db.query(models.LiveOrderIntent).count() == 0
        assert db.query(models.StrategyInstancePosition).count() == 0
        assert db.query(models.StrategySignal).count() == 1


def test_existing_verified_hold_recomputes_persisted_readiness(c2_app):
    client, current, _, owner, _ = c2_app
    approved = _approved(client)
    _compile(client, approved)
    installation = _install(client, approved, owner.id)
    current["user"] = owner
    issued = _credential(client, installation["id"], admin=False)
    assert client.post(
        "/api/webhooks/private", json=_hold(issued["token"])
    ).status_code == 202

    from app.db import models
    from app.db.engine import session_scope
    from app.services import c2_tradingview_service

    with session_scope() as db:
        setup = db.get(
            models.TradingViewSetup, uuid.UUID(installation["id"])
        )
        instance = db.get(
            models.StrategyInstance, uuid.UUID(installation["strategy_instance_id"])
        )
        setup.paper_eligible_at = None
        setup.status = "HOLD_VERIFIED"
        setup.blocking_reason = "stale projection"
        instance.verification_mode = True
        instance.verification_completed_at = None

    result = c2_tradingview_service.recompute_verified_hold_readiness(
        installation["id"]
    )
    assert result == {
        "checked": 1,
        "eligible": 1,
        "repaired": 1,
        "eligible_installation_ids": [installation["id"]],
        "repaired_installation_ids": [installation["id"]],
        "live_eligible": False,
    }

    detail = client.get(
        f"/api/strategies/my-installations/{installation['id']}"
    ).json()["installation"]
    assert detail["hold_status"] == "VERIFIED"
    assert detail["paper_eligible"] is True
    assert detail["paper_eligible_at"] is not None
    assert detail["blocking_reasons"] == []
    entry = next(
        item
        for item in client.get("/api/engine/strategies").json()["strategies"]
        if item["instance_id"] == installation["strategy_instance_id"]
    )
    assert entry["selectable"] is True
    assert entry["verification_mode"] is False


def test_non_hold_wrong_and_revoked_credentials_never_verify(c2_app):
    client, current, _, owner, other = c2_app
    approved = _approved(client)
    _compile(client, approved)
    target = _install(client, approved, owner.id)
    foreign = _install(client, approved, other.id)

    current["user"] = owner
    target_credential = _credential(client, target["id"], admin=False)
    current["user"] = other
    foreign_credential = _credential(client, foreign["id"], admin=False)

    buy = client.post(
        "/api/webhooks/private",
        json=_hold(target_credential["token"], action="BUY_CE"),
    )
    assert buy.status_code == 409
    # Un-promoted C2 is inert: a BUY/SELL is rejected with the structured code so
    # logs/UI can name the required admin action instead of a bare 409.
    assert buy.json()["reason"] == "STRATEGY_NOT_EXECUTABLE"

    # A foreign credential resolves only its own server-side instance and cannot
    # mark the target installation.
    foreign_hold = client.post(
        "/api/webhooks/private", json=_hold(foreign_credential["token"])
    )
    assert foreign_hold.status_code == 202
    current["user"] = owner
    pending = client.get(
        f"/api/strategies/my-installations/{target['id']}"
    ).json()["installation"]
    assert pending["paper_eligible"] is False
    assert pending["hold_status"] == "AWAITING_HOLD"

    revoked = client.post(
        f"/api/strategies/my-installations/{target['id']}/credential/revoke"
    )
    assert revoked.status_code == 200
    rejected = client.post(
        "/api/webhooks/private", json=_hold(target_credential["token"])
    )
    assert rejected.status_code == 401

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        assert db.query(models.StrategyExecutionJob).count() == 0
        assert db.query(models.LiveOrderIntent).count() == 0
        assert db.query(models.StrategyInstancePosition).count() == 0


def test_rotation_and_suspension_clear_readiness(c2_app):
    client, current, admin, owner, _ = c2_app
    approved = _approved(client)
    _compile(client, approved)
    installation = _install(client, approved, owner.id)
    current["user"] = owner
    issued = _credential(client, installation["id"], admin=False)
    assert client.post("/api/webhooks/private", json=_hold(issued["token"])).status_code == 202

    rotated = client.post(
        f"/api/strategies/my-installations/{installation['id']}/credential/rotate"
    )
    assert rotated.status_code == 200
    replacement = rotated.json()["credential"]
    assert replacement["token"] != issued["token"]
    after_rotation = client.get(
        f"/api/strategies/my-installations/{installation['id']}"
    ).json()["installation"]
    assert after_rotation["paper_eligible"] is False
    assert after_rotation["hold_status"] == "AWAITING_HOLD"
    assert client.post(
        "/api/webhooks/private", json=_hold(issued["token"])
    ).status_code == 401
    assert client.post(
        "/api/webhooks/private", json=_hold(replacement["token"])
    ).status_code == 202

    current["user"] = admin
    suspended = client.post(
        f"/api/admin/strategy-installations/{installation['id']}/suspend",
        json={"reason": "Controlled local suspension"},
    )
    assert suspended.status_code == 200
    body = suspended.json()["installation"]
    assert body["paper_eligible"] is False
    assert body["live_eligible"] is False
    blocked = client.post(
        "/api/webhooks/private", json=_hold(replacement["token"])
    )
    assert blocked.status_code == 409
    assert blocked.json()["reason"] == "INSTALLATION_INVALID"


def test_candidate_mutation_after_compile_fails_closed_everywhere(c2_app):
    client, current, _, owner, _ = c2_app
    approved = _approved(client)
    _compile(client, approved)
    installation = _install(client, approved, owner.id)
    current["user"] = owner
    issued = _credential(client, installation["id"], admin=False)

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        artifact = db.scalar(
            select(models.StrategySourceArtifact).where(
                models.StrategySourceArtifact.strategy_version_id
                == uuid.UUID(approved["candidate_version_id"]),
                models.StrategySourceArtifact.artifact_type == "pine_script",
            )
        )
        artifact.content += "\n// unauthorized mutation\n"

    hold = client.post("/api/webhooks/private", json=_hold(issued["token"]))
    assert hold.status_code == 409
    assert hold.json()["reason"] == "CANDIDATE_INTEGRITY_INVALID"
    detail = client.get(
        f"/api/strategies/my-installations/{installation['id']}"
    ).json()["installation"]
    assert detail["paper_eligible"] is False
    assert "Candidate changed" in detail["blocking_reasons"]
    picker = client.get("/api/engine/strategies").json()["strategies"]
    entry = next(
        item
        for item in picker
        if item["instance_id"] == installation["strategy_instance_id"]
    )
    assert entry["selectable"] is False
    assert client.put(
        "/api/engine/selection",
        json={"strategy_instance_id": installation["strategy_instance_id"]},
    ).status_code == 409
    with session_scope() as db:
        assert db.query(models.StrategyExecutionJob).count() == 0
        assert db.query(models.LiveOrderIntent).count() == 0
        assert db.query(models.StrategyInstancePosition).count() == 0


def _promote(client, iid):
    return client.post(f"/api/admin/strategy-installations/{iid}/promote-paper-verification")


def _complete_c2_paper_job(instance_id: str, signal_id: str, action: str) -> None:
    from app.db import models
    from app.db.engine import session_scope
    from app.services import c2_tradingview_service

    with session_scope() as db:
        job = db.scalar(
            select(models.StrategyExecutionJob).where(
                models.StrategyExecutionJob.strategy_name
                == f"instance:{instance_id}",
                models.StrategyExecutionJob.signal_id == signal_id,
            )
        )
        if job is None:
            instance = db.get(models.StrategyInstance, uuid.UUID(instance_id))
            assert instance is not None
            signal = db.scalar(
                select(models.StrategySignal).where(
                    models.StrategySignal.strategy_name
                    == f"instance:{instance_id}",
                    models.StrategySignal.signal_id == signal_id,
                )
            )
            if signal is None:
                signal = models.StrategySignal(
                    strategy_name=f"instance:{instance_id}",
                    signal_id=signal_id,
                    status="completed",
                )
                db.add(signal)
                db.flush()
            job = models.StrategyExecutionJob(
                strategy_signal_id=signal.id,
                user_id=instance.user_id,
                strategy_name=f"instance:{instance_id}",
                signal_id=signal_id,
                signal_payload={"action": action},
                lots=1,
                execution_mode="paper_live_data",
                status="completed",
            )
            db.add(job)
            db.flush()
        job.status = "completed"
        job.signal_payload = {"action": action}
        job.result_summary = {
            "status": "completed",
            "execution_result": {
                "success": True,
                "status": "ORDER_PLACED",
                "order_id": f"PAPER-{signal_id}",
            },
        }
        job.completed_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        job_id = job.id
    evidence = c2_tradingview_service.record_paper_execution_evidence_from_job(
        job_id
    )
    assert evidence is not None
    assert evidence["recorded"] is True
    assert evidence["kind"] == f"PAPER_{action}"


def test_c2_admin_promotion_lifts_the_gate_only_after_hold_and_only_by_admin(
    c2_app,
    monkeypatch,
):
    client, current, admin, owner, _ = c2_app
    approved = _approved(client)
    _compile(client, approved)
    installation = _install(client, approved, owner.id)
    iid = installation["id"]
    current["user"] = owner
    issued = _credential(client, iid, admin=False)

    # Promotion before a verified HOLD is refused.
    current["user"] = admin
    early = _promote(client, iid)
    assert early.status_code == 409
    assert early.json()["reason"] == "HOLD_NOT_VERIFIED"

    # Verify routing with a real HOLD -> PAPER_ELIGIBLE.
    current["user"] = owner
    assert client.post("/api/webhooks/private", json=_hold(issued["token"])).status_code == 202
    from app.services import strategy_instance_service

    strategy_instance_service.set_engine_selection(
        owner.id,
        installation["strategy_instance_id"],
    )
    with pytest.raises(strategy_instance_service.InstanceError) as blocked_start:
        strategy_instance_service.require_selected_engine_strategy(owner.id)
    assert blocked_start.value.code == "ADMIN_PROMOTION_REQUIRED"

    # Still inert: a BUY is rejected with the structured code.
    buy = client.post("/api/webhooks/private", json=_hold(issued["token"], action="BUY_CE", signal_id="c2-buy-pre"))
    assert buy.status_code == 409 and buy.json()["reason"] == "STRATEGY_NOT_EXECUTABLE"

    # Non-admin cannot promote.
    denied = _promote(client, iid)
    assert denied.status_code in (401, 403)

    # Admin promotes -> PAPER_VERIFICATION, and it is idempotent.
    current["user"] = admin
    promoted = _promote(client, iid)
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["installation"]["status"] == "PAPER_VERIFICATION"
    assert promoted.json()["installation"]["execution_mode"] == "paper_live_data"
    again = _promote(client, iid)
    assert again.status_code == 200 and again.json()["installation"]["status"] == "PAPER_VERIFICATION"

    # The C2 gate no longer blocks a BUY (it now reaches the paper lifecycle).
    current["user"] = owner
    assert (
        strategy_instance_service.require_selected_engine_strategy(owner.id)[
            "instance_id"
        ]
        == installation["strategy_instance_id"]
    )
    buy_signal_id = "c2-buy-post"
    buy2 = client.post("/api/webhooks/private", json=_hold(issued["token"], action="BUY_CE", signal_id=buy_signal_id))
    assert buy2.json().get("reason") != "STRATEGY_NOT_EXECUTABLE"

    # Admin cannot mark READY until confirmed Paper entry and exit jobs exist.
    current["user"] = admin
    missing_evidence = client.post(
        f"/api/admin/strategy-installations/{iid}/mark-ready"
    )
    assert missing_evidence.status_code == 409
    assert (
        missing_evidence.json()["reason"]
        == "PAPER_EXECUTION_EVIDENCE_REQUIRED"
    )

    _complete_c2_paper_job(
        installation["strategy_instance_id"],
        buy_signal_id,
        "ENTRY",
    )
    current["user"] = owner
    exit_signal_id = "c2-exit-post"
    exit_response = client.post(
        "/api/webhooks/private",
        json=_hold(
            issued["token"],
            action="EXIT",
            signal_id=exit_signal_id,
        ),
    )
    assert exit_response.status_code == 202, exit_response.text
    _complete_c2_paper_job(
        installation["strategy_instance_id"],
        exit_signal_id,
        "EXIT",
    )

    # Admin marks the exact Paper-verified version READY.
    current["user"] = admin
    ready = client.post(f"/api/admin/strategy-installations/{iid}/mark-ready")
    assert ready.status_code == 200, ready.text
    assert ready.json()["installation"]["status"] == "READY"
    assert ready.json()["installation"]["paper_entry_verified_at"] is not None
    assert ready.json()["installation"]["paper_exit_verified_at"] is not None

    # A READY personal strategy may use the normal Live start path, but only
    # when every owner/environment authority is present.
    from app.services import entitlements, strategy_fanout, user_credential_vault

    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
    monkeypatch.setattr(
        settings,
        "PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED",
        True,
    )
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False)
    monkeypatch.setattr(
        entitlements,
        "has_live_entitlement_for_user",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        entitlements,
        "has_strategy_entitlement_for_user",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        entitlements,
        "require_live_entitlement_for_user",
        lambda _user_id: None,
    )
    monkeypatch.setattr(
        entitlements,
        "require_strategy_entitlement_for_user",
        lambda _user_id: None,
    )
    monkeypatch.setattr(
        user_credential_vault,
        "get_user_dhan_credentials",
        lambda _user_id: object(),
    )
    monkeypatch.setattr(
        strategy_fanout,
        "user_egress_status",
        lambda _user_id: {
            "active": True,
            "has_proxy": True,
            "verified": True,
        },
    )
    configured = strategy_instance_service.configure_and_activate_for_engine_start(
        owner.id,
        installation["strategy_instance_id"],
        execution_mode="real_orders",
        lots=1,
    )
    assert configured["execution_mode"] == "real_orders"
    live_detail = client.get(
        f"/api/admin/strategy-installations/{iid}"
    ).json()["installation"]
    assert live_detail["live_eligible"] is True


def test_managed_c2_installation_never_appears_in_the_legacy_managed_queue(c2_app):
    """A C2 installation with mode=MANAGED gets setup_type=NOVA_MANAGED_TRADINGVIEW
    -- the same setup_type the legacy (non-C2) admin queue filters on. Every
    action in that legacy panel (record_installation, admin_set_state)
    already rejects C2 rows with C2_ROUTE_REQUIRED, so the listing must
    exclude them too; otherwise an admin sees an entry nothing on that panel
    can ever act on ("C2 installations use the C2 lifecycle" on every click)."""
    client, current, admin, owner, _ = c2_app
    approved = _approved(client)
    _compile(client, approved)
    managed = _install(client, approved, owner.id, mode="MANAGED")

    from app.services import tradingview_setup_service

    legacy_queue = tradingview_setup_service.list_managed()
    assert managed["id"] not in {row["id"] for row in legacy_queue["setups"]}


def test_c2_mark_ready_requires_paper_verification_first(c2_app):
    client, current, admin, owner, _ = c2_app
    approved = _approved(client)
    _compile(client, approved)
    installation = _install(client, approved, owner.id)
    iid = installation["id"]
    current["user"] = owner
    _credential(client, iid, admin=False)
    # Not promoted yet -> mark-ready refused.
    current["user"] = admin
    r = client.post(f"/api/admin/strategy-installations/{iid}/mark-ready")
    assert r.status_code == 409 and r.json()["reason"] == "NOT_IN_VERIFICATION"


# ---------------------------------------------------------------------------
# End-to-end: does an admin-approved conversion's webhook actually place a
# real Paper order? Every other test above proves routing/state gating using
# _complete_c2_paper_job, which fabricates job.result_summary directly instead
# of running strategy_job_worker.process_queued_jobs_once() against the real
# execution router + PaperBroker. That left a real gap: nothing proved a
# genuine webhook, for a genuine Claude-approved installation, produces a
# genuine paper fill. This drives the actual worker instead of faking its
# output.
# ---------------------------------------------------------------------------

@pytest.fixture
def c2_worker_runtime(mu_db, monkeypatch, tmp_path):
    """Isolated per-user runtime dirs + a deterministic fill, so the real
    worker/PaperBroker can run offline. Mirrors test_private_webhook_execution's
    per_user_runtime + deterministic_paper_market fixtures."""
    from app.config import settings
    from app.services import (
        audit_logger,
        paper_broker,
        paper_portfolio,
        private_webhook_execution,
        shared_market_data,
        state_store,
        user_context,
    )
    from app.services.credential_vault import DhanCredentials
    from app.services.dhan_client import DhanLtpResult

    monkeypatch.setattr(settings, "STRATEGY_JOB_WORKER_ENABLED", True, raising=False)

    state_root = tmp_path / "state"
    log_root = tmp_path / "logs"
    monkeypatch.setattr(state_store, "RUNTIME_STATE_DIR", state_root)
    monkeypatch.setattr(state_store, "RUNTIME_LOG_DIR", log_root)
    monkeypatch.setattr(user_context, "RUNTIME_STATE_DIR", state_root)
    monkeypatch.setattr(user_context, "RUNTIME_LOG_DIR", log_root)
    for name, filename in {
        "APP_STATE_FILE": "app_state.json",
        "OPEN_POSITION_FILE": "open_position.json",
        "PAPER_POSITION_FILE": "paper_position.json",
        "PAPER_PORTFOLIO_FILE": "paper_portfolio.json",
        "EXTERNAL_POSITIONS_FILE": "external_positions.json",
        "SEEN_SIGNALS_FILE": "seen_signals.json",
        "SETTINGS_FILE": "settings.json",
    }.items():
        monkeypatch.setattr(state_store, name, state_root / filename)
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", state_root / "paper_portfolio.json")
    log_files = {
        "webhook": log_root / "webhook_events.jsonl",
        "order": log_root / "order_events.jsonl",
        "audit": log_root / "audit_events.jsonl",
        "error": log_root / "errors.jsonl",
        "paper_orders": log_root / "paper_orders.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(paper_broker, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    monkeypatch.setattr(paper_broker, "_market_is_open", lambda: True)

    monkeypatch.setattr(
        private_webhook_execution,
        "_resolve_entry_contract",
        lambda option_side, lots: {
            "security_id": "57046" if option_side == "CE" else "57047",
            "trading_symbol": f"NIFTY TEST {option_side}",
            "strike": 25000.0,
            "expiry": "2026-07-16",
        },
    )

    class _QuoteClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get_ltp(self, **_kwargs) -> DhanLtpResult:
            return DhanLtpResult(success=True, message="quote", ltp=100.0)

    monkeypatch.setattr(paper_broker, "RealDhanClient", _QuoteClient)
    fake_creds = DhanCredentials("shared-client", "shared-token", "shared_market_data")
    monkeypatch.setattr(
        paper_broker,
        "get_quote_snapshot",
        lambda **_kwargs: {"ltp": 100.0, "source": "DHAN_WEBSOCKET", "status": "FRESH", "stale": False},
    )
    monkeypatch.setattr(shared_market_data, "get_shared_market_credentials", lambda: fake_creds)
    monkeypatch.setattr(shared_market_data, "shared_market_data_configured", lambda: True)


def _run_worker(limit: int = 5) -> int:
    from app.workers.strategy_job_worker import process_queued_jobs_once

    return process_queued_jobs_once(limit=limit)


def test_hold_verified_flips_live_market_paper_test_ready_and_progress(c2_app):
    """The simplified post-HOLD flow: readiness and a 6-step progress summary
    are computed from HOLD/evidence fields already recorded elsewhere, so the
    UI never has to ask the admin anything new to show them."""
    client, current, admin, owner, _ = c2_app
    approved = _approved(client)
    _compile(client, approved)
    installation = _install(client, approved, owner.id)
    iid = installation["id"]
    current["user"] = owner
    issued = _credential(client, iid, admin=False)

    before = client.get(f"/api/strategies/my-installations/{iid}").json()["installation"]
    assert before["live_market_paper_test_ready"] is False
    assert before["symbol"] == "NIFTY"
    assert [step["status"] for step in before["progress"]] == ["WAITING"] * 6

    assert client.post("/api/webhooks/private", json=_hold(issued["token"])).status_code == 202

    after = client.get(f"/api/strategies/my-installations/{iid}").json()["installation"]
    assert after["live_market_paper_test_ready"] is True
    progress_by_key = {step["key"]: step["status"] for step in after["progress"]}
    assert progress_by_key["hold_connectivity"] == "PASSED"
    assert progress_by_key["entry_signal"] == "WAITING"
    assert progress_by_key["exit_signal"] == "WAITING"


def test_promotion_creates_no_order_and_never_touches_dhan(c2_app):
    client, current, admin, owner, _ = c2_app
    approved = _approved(client)
    _compile(client, approved)
    installation = _install(client, approved, owner.id)
    iid = installation["id"]
    current["user"] = owner
    issued = _credential(client, iid, admin=False)
    assert client.post("/api/webhooks/private", json=_hold(issued["token"])).status_code == 202

    current["user"] = admin
    promoted = _promote(client, iid)
    assert promoted.status_code == 200, promoted.text

    from sqlalchemy import select

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        assert db.scalar(select(models.LiveOrderIntent)) is None
        assert db.query(models.StrategyExecutionJob).count() == 0
        assert db.query(models.StrategyInstancePosition).count() == 0


def test_re_promotion_does_not_duplicate_or_overwrite_an_existing_configuration(c2_app):
    """Idempotent promotion: a second click must not create a second Paper
    revision, and must never clobber an owner-saved config that already
    exists (e.g. from the normal setup wizard) with different lots."""
    client, current, admin, owner, _ = c2_app
    approved = _approved(client)
    _compile(client, approved)
    installation = _install(client, approved, owner.id)
    iid = installation["id"]
    instance_id = installation["strategy_instance_id"]
    current["user"] = owner
    issued = _credential(client, iid, admin=False)
    assert client.post("/api/webhooks/private", json=_hold(issued["token"])).status_code == 202

    from sqlalchemy import select

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        instance_row = db.get(models.StrategyInstance, uuid.UUID(instance_id))
        owner_saved_revision = models.StrategyConfigurationRevision(
            user_id=owner.id,
            strategy_instance_id=instance_row.id,
            strategy_version_id=instance_row.strategy_version_id,
            mode="paper",
            revision=1,
            configuration_json={"lots": 7, "allowed_option_side": "CE"},
            risk_json={},
            status="active",
        )
        db.add(owner_saved_revision)
        db.flush()
        owner_saved_revision_id = owner_saved_revision.id

    current["user"] = admin
    first = _promote(client, iid)
    assert first.status_code == 200, first.text
    second = _promote(client, iid)  # idempotent re-promotion
    assert second.status_code == 200, second.text

    with session_scope() as db:
        revisions = db.scalars(
            select(models.StrategyConfigurationRevision).where(
                models.StrategyConfigurationRevision.strategy_instance_id == uuid.UUID(instance_id),
                models.StrategyConfigurationRevision.mode == "paper",
            )
        ).all()
        assert len(revisions) == 1  # no duplicate created
        assert revisions[0].id == owner_saved_revision_id  # the owner's real config wasn't replaced
        assert revisions[0].configuration_json == {"lots": 7, "allowed_option_side": "CE"}
        selection = db.scalar(
            select(models.UserEngineConfig).where(models.UserEngineConfig.user_id == owner.id)
        )
        assert selection.selected_configuration_revision_id == owner_saved_revision_id


def test_approved_installation_webhook_produces_a_real_paper_fill(c2_app, c2_worker_runtime):
    """The actual gap: an admin-approved, Claude-converted, HOLD-verified,
    admin-promoted installation receives a real BUY_CE webhook, the real
    worker (not a fabricated job) runs it through the real execution router
    and PaperBroker, and a real paper position + realized P&L land durably —
    then the owner's own EXIT closes it and mark-ready succeeds on genuine
    evidence."""
    client, current, admin, owner, _ = c2_app
    approved = _approved(client)
    _compile(client, approved)
    installation = _install(client, approved, owner.id)
    iid = installation["id"]
    instance_id = installation["strategy_instance_id"]

    current["user"] = owner
    issued = _credential(client, iid, admin=False)
    assert client.post("/api/webhooks/private", json=_hold(issued["token"])).status_code == 202

    current["user"] = admin
    promoted = _promote(client, iid)
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["installation"]["execution_mode"] == "paper_live_data"

    # Promotion itself now auto-binds a Paper configuration revision (lots
    # from instance.current_lots, default 1) and the owner's UserEngineConfig
    # selection -- the admin's one click is enough, no separate owner-side
    # setup-wizard step is needed before a genuine signal is executable.
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        selection = db.scalar(
            select(models.UserEngineConfig).where(models.UserEngineConfig.user_id == owner.id)
        )
        assert selection is not None
        assert str(selection.selected_strategy_instance_id) == instance_id
        revision = db.get(models.StrategyConfigurationRevision, selection.selected_configuration_revision_id)
        assert revision is not None
        assert revision.configuration_json["lots"] == 1

    from app.services import state_store
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    with bind_user_execution_context(current_user_from_model(owner)):
        state_store.init_runtime_files()
        state_store.update_app_state(
            engine_mode="paper", engine_started=True, webhook_trading_enabled=True
        )
        state_store.update_runtime_settings(
            allow_entry=True,
            allow_exit=True,
            allowed_option_side="BOTH",
            emergency_stop=False,
            global_kill_switch=False,
        )

    current["user"] = owner
    entry_signal_id = "e2e-entry-1"
    entry = client.post(
        "/api/webhooks/private",
        json=_hold(issued["token"], action="BUY_CE", signal_id=entry_signal_id),
    )
    assert entry.status_code == 202, entry.text
    assert entry.json().get("reason") != "STRATEGY_NOT_EXECUTABLE"
    completed = _run_worker()
    assert completed == 1

    with session_scope() as db:
        entry_job = db.scalar(
            select(models.StrategyExecutionJob).where(
                models.StrategyExecutionJob.strategy_name == f"instance:{instance_id}",
                models.StrategyExecutionJob.signal_id == entry_signal_id,
            )
        )
        assert entry_job is not None
        assert entry_job.status == "completed"
        execution = entry_job.result_summary["execution_result"]
        assert execution["success"] is True
        assert execution["order_id"].startswith("PAPER-")

    with bind_user_execution_context(current_user_from_model(owner)):
        position = state_store.get_open_position()
    assert position["has_open_position"] is True
    assert position["option_side"] == "CE"

    exit_signal_id = "e2e-exit-1"
    exit_response = client.post(
        "/api/webhooks/private",
        json=_hold(issued["token"], action="EXIT", signal_id=exit_signal_id),
    )
    assert exit_response.status_code == 202, exit_response.text
    assert _run_worker() == 1

    with bind_user_execution_context(current_user_from_model(owner)):
        position_after = state_store.get_open_position()
    assert position_after["has_open_position"] is False

    with session_scope() as db:
        trade = db.scalar(
            select(models.PortfolioTrade).where(
                models.PortfolioTrade.user_id == owner.id,
                models.PortfolioTrade.mode == "paper",
            )
        )
        assert trade is not None, "a real paper EXIT must persist a durable PortfolioTrade row"
        assert trade.symbol == "NIFTY TEST CE"

    # Genuine paper entry/exit evidence (from the real worker, not a fabricated
    # job) is exactly what admin mark-ready requires.
    current["user"] = admin
    ready = client.post(f"/api/admin/strategy-installations/{iid}/mark-ready")
    assert ready.status_code == 200, ready.text
    assert ready.json()["installation"]["status"] == "READY"
    assert ready.json()["installation"]["paper_entry_verified_at"] is not None
    assert ready.json()["installation"]["paper_exit_verified_at"] is not None
