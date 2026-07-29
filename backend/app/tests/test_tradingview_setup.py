from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_personal_pine import VALID_PINE, _acceptance
from app.tests.test_strategy_instances import _create_instance, _seed_supertrend


def _client(user):
    from app.auth.dependencies import get_current_user
    from app.routers import personal_pine, strategy_instances, tradingview_setup
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    for router in (
        personal_pine.router, personal_pine.link_router, personal_pine.admin_router,
        strategy_instances.router, tradingview_setup.router, tradingview_setup.admin_router,
    ):
        app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(user)
    return TestClient(app)


def _approved_personal_instance(owner, admin, setup_type="USER_MANAGED_TRADINGVIEW"):
    client, admin_client = _client(owner), _client(admin)
    created = client.post("/api/personal-pine-strategies", json={
        "name": "Manual TV", "source": VALID_PINE, "filename": "original.pine",
    }).json()
    strategy_id, version_id = created["strategy"]["id"], created["version"]["id"]
    assert client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/validate").status_code == 200
    assert client.post(
        f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/submit",
        json={**_acceptance(version_id), "setup_type": setup_type},
    ).status_code == 200
    assert admin_client.post(f"/api/admin/pine-reviews/{version_id}/start", json={}).status_code == 200
    assert admin_client.post(f"/api/admin/pine-reviews/{version_id}/approve", json={"acknowledge_warnings": True}).status_code == 200
    _seed_supertrend()
    instance = _create_instance(client, source_journey="PERSONAL_TRADINGVIEW", execution_mode="paper_live_data")
    assert client.post(f"/api/personal-strategies/{instance['id']}/link-version", json={
        "strategy_id": strategy_id, "version_id": version_id,
    }).status_code == 200
    return client, admin_client, instance["id"], version_id


def _paper_evidence(user_id, instance_id: str, signal_id: str, action: str, *, reversal=False):
    from app.db import models
    from app.db.engine import session_scope
    from app.services.private_webhook_service import instance_strategy_name

    with session_scope() as db:
        signal = models.StrategySignal(
            strategy_name=instance_strategy_name(instance_id), signal_id=signal_id,
            status="completed", result_summary={"action": action},
        )
        db.add(signal); db.flush()
        db.add(models.StrategyExecutionJob(
            strategy_signal_id=signal.id, user_id=user_id,
            strategy_name=instance_strategy_name(instance_id), signal_id=signal_id,
            signal_payload={"raw_payload": {"normalized_action": action}}, lots=1,
            execution_mode="paper_live_data", status="completed", completed_at=datetime.now(timezone.utc),
            result_summary={"status": "completed", "execution_result": {
                "success": True, "status": "REVERSAL_ORDER_PLACED" if reversal else "ORDER_PLACED",
                "order_id": f"PAPER-{signal_id}",
            }},
        ))


def test_premium_workflow_requires_durable_gates_and_is_owner_scoped(mu_db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True)
    owner = make_user("tv-premium@example.com")
    admin = make_user("tv-premium-admin@example.com", is_admin=True)
    foreign = make_user("tv-premium-foreign@example.com")
    client, _, instance_id, _ = _approved_personal_instance(owner, admin)

    setup = client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup", json={
        "setup_type": "USER_MANAGED_TRADINGVIEW",
    })
    assert setup.status_code == 200
    assert setup.json()["setup"]["ready_for_paper"] is False
    assert _client(foreign).get(f"/api/strategy-instances/{instance_id}/tradingview-setup").status_code == 404
    assert client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup", json={
        "setup_type": "NOVA_MANAGED_TRADINGVIEW",
    }).json()["reason"] == "RESET_REQUIRED"

    assert client.post(f"/api/strategy-instances/{instance_id}/webhook-credential").status_code == 200
    assert client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/compiled", json={"compiled": True}).status_code == 200
    hold = client.post(f"/api/strategy-instances/{instance_id}/webhook-test").json()
    assert hold["status"] == "NO_OP"
    assert client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/verification", json={
        "kind": "HOLD", "signal_id": hold["signal_id"],
    }).status_code == 200

    _paper_evidence(owner.id, instance_id, "paper-entry", "BUY_CE")
    _paper_evidence(owner.id, instance_id, "paper-exit", "EXIT")
    _paper_evidence(owner.id, instance_id, "paper-reversal", "BUY_PE", reversal=True)
    for kind, signal_id in (("PAPER_ENTRY", "paper-entry"), ("PAPER_EXIT", "paper-exit")):
        response = client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/verification", json={"kind": kind, "signal_id": signal_id})
        assert response.status_code == 200, response.text
    assert client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/verification", json={
        "kind": "REVERSAL", "signal_id": "paper-reversal",
    }).json()["setup"]["reversal_verified_at"]
    final = client.get(f"/api/strategy-instances/{instance_id}/tradingview-setup").json()["setup"]
    assert final["status"] == "READY"
    assert final["ready_for_paper"] is True
    rotated = client.post(f"/api/strategy-instances/{instance_id}/webhook-credential/rotate")
    assert rotated.status_code == 200 and rotated.json()["credential"]["token"]
    after_rotation = client.get(f"/api/strategy-instances/{instance_id}/tradingview-setup").json()["setup"]
    assert after_rotation["ready_for_paper"] is False
    assert after_rotation["gates"]["hold_received"] is False
    reset = client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/reset", json={
        "setup_type": "NOVA_MANAGED_TRADINGVIEW", "reason": "Owner requested managed setup",
    })
    assert reset.status_code == 200
    assert reset.json()["setup"]["setup_type"] == "NOVA_MANAGED_TRADINGVIEW"
    assert reset.json()["setup"]["reset_count"] == 1


def test_managed_queue_installation_does_not_create_false_ready(mu_db):
    owner = make_user("tv-managed@example.com")
    admin = make_user("tv-managed-admin@example.com", is_admin=True)
    client, admin_client, instance_id, version_id = _approved_personal_instance(owner, admin, "NOVA_MANAGED_TRADINGVIEW")
    setup = client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup", json={
        "setup_type": "NOVA_MANAGED_TRADINGVIEW", "requested_timeframe": "5",
    }).json()["setup"]
    queue = admin_client.get("/api/admin/managed-tradingview-setups").json()
    assert queue["total"] == 1 and queue["setups"][0]["user_id"] == str(owner.id)

    installed = admin_client.post(f"/api/admin/managed-tradingview-setups/{setup['id']}/installation", json={
        "installed_version_hash": _version_hash(version_id), "workspace_reference": "nova-workspace-1",
        "alert_reference": "manual-alert-1", "symbol": "NIFTY", "timeframe": "5",
        "installed_at": datetime.now(timezone.utc).isoformat(),
    })
    assert installed.status_code == 200
    assert installed.json()["setup"]["ready_for_paper"] is False
    rejected = admin_client.post(f"/api/admin/managed-tradingview-setups/{setup['id']}/installation", json={
        "installed_version_hash": _version_hash(version_id), "workspace_reference": "safe",
        "alert_reference": "safe", "symbol": "NIFTY", "timeframe": "5",
        "installed_at": datetime.now(timezone.utc).isoformat(), "password": "must-not-store",
    })
    assert rejected.status_code == 422
    assert admin_client.post(f"/api/admin/managed-tradingview-setups/{setup['id']}/state", json={"status": "BLOCKED"}).status_code == 422
    assert client.get(f"/api/strategy-instances/{instance_id}/tradingview-setup").json()["setup"].get("installation_metadata") is None


def test_managed_installation_auto_fills_workspace_and_alert_reference_when_blank(mu_db):
    """The installation is already identified by setup_id + the matched
    version hash; workspace/alert reference are admin-facing notes only, so
    omitting them must not block recording the installation."""
    owner = make_user("tv-autofill@example.com")
    admin = make_user("tv-autofill-admin@example.com", is_admin=True)
    client, admin_client, instance_id, version_id = _approved_personal_instance(owner, admin, "NOVA_MANAGED_TRADINGVIEW")
    setup = client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup", json={
        "setup_type": "NOVA_MANAGED_TRADINGVIEW", "requested_timeframe": "5",
    }).json()["setup"]

    installed = admin_client.post(f"/api/admin/managed-tradingview-setups/{setup['id']}/installation", json={
        "installed_version_hash": _version_hash(version_id),
        "symbol": "NIFTY", "timeframe": "5",
        "installed_at": datetime.now(timezone.utc).isoformat(),
    })
    assert installed.status_code == 200, installed.text

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        row = db.get(models.TradingViewSetup, uuid.UUID(setup["id"]))
        metadata = row.installation_metadata
        assert metadata["workspace_reference"], "must not be stored blank"
        assert metadata["alert_reference"], "must not be stored blank"
        assert metadata["workspace_reference"] == f"managed-{row.id.hex[:8]}"
        assert metadata["alert_reference"] == f"alert-{_version_hash(version_id)[:12]}"


def _version_hash(version_id: str) -> str:
    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db:
        return db.get(models.StrategyVersion, uuid.UUID(version_id)).source_sha256


def test_prompt_approval_requires_real_qualification_and_users_cannot_qualify(mu_db):
    owner = make_user("prompt-user@example.com")
    admin = make_user("prompt-admin@example.com", is_admin=True)
    client, admin_client = _client(owner), _client(admin)
    current = client.get("/api/pine-prompts/current")
    assert current.status_code == 200
    prompt = current.json()["prompt"]
    assert prompt["status"] == "QUALIFICATION"
    assert prompt["ai_enabled"] is False
    assert client.post("/api/admin/pine-prompts/qualification-trials", json={}).status_code == 403
    approval = admin_client.post(f"/api/admin/pine-prompts/{prompt['id']}/approve", json={"change_notes": "reviewed campaign"})
    assert approval.status_code == 409
    assert approval.json()["reason"] == "QUALIFICATION_INCOMPLETE"
