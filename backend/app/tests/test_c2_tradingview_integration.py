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
