"""Phase 4G-pre: unified readiness, managed credentials and engine picker.

Proves the three audited product gaps are closed without a separate readiness
standard and without weakening owner isolation.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_tradingview_setup import (
    _approved_personal_instance,
    _paper_evidence,
)


def _full_client(user):
    """A client mounting the engine picker in addition to the flow routers."""
    from app.auth.dependencies import get_current_user
    from app.routers import personal_pine, strategy_instances, tradingview_setup
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    for router in (
        personal_pine.router, personal_pine.link_router, personal_pine.admin_router,
        strategy_instances.router, strategy_instances.admin_router, strategy_instances.engine_router,
        tradingview_setup.router, tradingview_setup.admin_router,
    ):
        app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(user)
    return TestClient(app)


def _readiness(client, instance_id):
    return client.get(f"/api/strategy-instances/{instance_id}").json()["instance"]["readiness"]


def _drive_to_hold(client, instance_id):
    """Setup + credential + installation + genuine HOLD (no paper entry/exit yet)."""
    assert client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup", json={
        "setup_type": "USER_MANAGED_TRADINGVIEW",
    }).status_code == 200
    assert client.post(f"/api/strategy-instances/{instance_id}/webhook-credential").status_code == 200
    assert client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/compiled", json={"compiled": True}).status_code == 200
    hold = client.post(f"/api/strategy-instances/{instance_id}/webhook-test").json()
    assert client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/verification", json={
        "kind": "HOLD", "signal_id": hold["signal_id"],
    }).status_code == 200


# ---------------------------------------------------------------------------
# GAP 1 — unified readiness / activation
# ---------------------------------------------------------------------------

def test_hold_alone_does_not_permit_activation(mu_db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True)
    owner = make_user("gap1-hold@example.com")
    admin = make_user("gap1-hold-admin@example.com", is_admin=True)
    client, _, instance_id, _ = _approved_personal_instance(owner, admin)

    _drive_to_hold(client, instance_id)
    readiness = _readiness(client, instance_id)
    assert readiness["hold_verified"] is True
    assert readiness["paper_entry_verified"] is False
    assert readiness["paper_exit_verified"] is False
    assert readiness["can_activate"] is False

    blocked = client.post(f"/api/strategy-instances/{instance_id}/activate")
    assert blocked.status_code == 409
    assert blocked.json()["reason"] == "PAPER_ENTRY_NOT_VERIFIED"


def test_entry_without_exit_does_not_permit_activation(mu_db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True)
    owner = make_user("gap1-entry@example.com")
    admin = make_user("gap1-entry-admin@example.com", is_admin=True)
    client, _, instance_id, _ = _approved_personal_instance(owner, admin)

    _drive_to_hold(client, instance_id)
    _paper_evidence(owner.id, instance_id, "entry-1", "BUY_CE")
    assert client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/verification", json={
        "kind": "PAPER_ENTRY", "signal_id": "entry-1",
    }).status_code == 200

    readiness = _readiness(client, instance_id)
    assert readiness["paper_entry_verified"] is True
    assert readiness["paper_exit_verified"] is False
    assert readiness["can_activate"] is False
    blocked = client.post(f"/api/strategy-instances/{instance_id}/activate")
    assert blocked.status_code == 409
    assert blocked.json()["reason"] == "PAPER_EXIT_NOT_VERIFIED"


def test_hold_entry_exit_permits_activation(mu_db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True)
    owner = make_user("gap1-ready@example.com")
    admin = make_user("gap1-ready-admin@example.com", is_admin=True)
    client, _, instance_id, _ = _approved_personal_instance(owner, admin)

    _drive_to_hold(client, instance_id)
    _paper_evidence(owner.id, instance_id, "entry-2", "BUY_CE")
    _paper_evidence(owner.id, instance_id, "exit-2", "EXIT")
    for kind, signal_id in (("PAPER_ENTRY", "entry-2"), ("PAPER_EXIT", "exit-2")):
        assert client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/verification", json={
            "kind": kind, "signal_id": signal_id,
        }).status_code == 200

    readiness = _readiness(client, instance_id)
    assert readiness["can_activate"] is True
    assert client.post(f"/api/strategy-instances/{instance_id}/activate").status_code == 200


def test_fabricated_signal_id_cannot_create_readiness(mu_db, monkeypatch):
    """Admin/checkbox-style evidence (a signal id with no durable job) is refused."""
    from app.config import settings
    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True)
    owner = make_user("gap1-fake@example.com")
    admin = make_user("gap1-fake-admin@example.com", is_admin=True)
    client, _, instance_id, _ = _approved_personal_instance(owner, admin)
    _drive_to_hold(client, instance_id)

    forged = client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/verification", json={
        "kind": "PAPER_ENTRY", "signal_id": "does-not-exist",
    })
    assert forged.status_code == 409
    assert _readiness(client, instance_id)["paper_entry_verified"] is False


def test_live_execution_remains_disabled(mu_db):
    from app.config import settings
    assert settings.ENABLE_LIVE_ORDERS is False
    assert settings.PRIVATE_STRATEGY_WEBHOOK_LIVE_EXECUTION_ENABLED is False
    assert settings.PINE_CONVERSION_AI_ENABLED is False


# ---------------------------------------------------------------------------
# GAP 2 — NOVA-managed credential provisioning
# ---------------------------------------------------------------------------

def _managed_setup(owner, admin):
    client, admin_client, instance_id, _ = _approved_personal_instance(owner, admin, "NOVA_MANAGED_TRADINGVIEW")
    setup = client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup", json={
        "setup_type": "NOVA_MANAGED_TRADINGVIEW", "requested_timeframe": "5",
    }).json()["setup"]
    return client, admin_client, instance_id, setup["id"]


def test_managed_credential_is_admin_only_and_shown_once(mu_db):
    owner = make_user("gap2-user@example.com")
    admin = make_user("gap2-admin@example.com", is_admin=True)
    client, admin_client, instance_id, setup_id = _managed_setup(owner, admin)

    # Ordinary users cannot provision a managed credential.
    assert client.post(f"/api/admin/managed-tradingview-setups/{setup_id}/credential").status_code == 403

    issued = admin_client.post(f"/api/admin/managed-tradingview-setups/{setup_id}/credential")
    assert issued.status_code == 200
    token = issued.json()["credential"]["token"]
    assert token and token.startswith("nwk_")

    # Plaintext (and hash) never resurface in list/detail responses.
    queue = admin_client.get("/api/admin/managed-tradingview-setups").json()
    blob = str(queue)
    assert token not in blob and "token_hash" not in blob
    detail = client.get(f"/api/strategy-instances/{instance_id}").json()["instance"]
    cred = detail.get("webhook_credential") or {}
    assert token not in str(detail)  # the plaintext secret is never echoed back
    assert "token" not in cred and "token_hash" not in cred  # token_prefix only
    assert detail["credential_status"] == "active"


def test_managed_credential_rotation_invalidates_previous(mu_db):
    owner = make_user("gap2-rotate@example.com")
    admin = make_user("gap2-rotate-admin@example.com", is_admin=True)
    _, admin_client, _, setup_id = _managed_setup(owner, admin)

    first = admin_client.post(f"/api/admin/managed-tradingview-setups/{setup_id}/credential").json()["credential"]["token"]
    # A second generate without rotate is refused; rotate issues a fresh secret.
    assert admin_client.post(f"/api/admin/managed-tradingview-setups/{setup_id}/credential").json()["reason"] == "CREDENTIAL_EXISTS"
    second = admin_client.post(f"/api/admin/managed-tradingview-setups/{setup_id}/credential?rotate=true").json()["credential"]["token"]
    assert second and second != first


def test_managed_credential_rejected_for_user_managed_setup(mu_db):
    owner = make_user("gap2-usermanaged@example.com")
    admin = make_user("gap2-usermanaged-admin@example.com", is_admin=True)
    client, admin_client, instance_id, _ = _approved_personal_instance(owner, admin, "USER_MANAGED_TRADINGVIEW")
    setup = client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup", json={
        "setup_type": "USER_MANAGED_TRADINGVIEW",
    }).json()["setup"]
    rejected = admin_client.post(f"/api/admin/managed-tradingview-setups/{setup['id']}/credential")
    assert rejected.status_code == 409
    assert rejected.json()["reason"] == "NOT_MANAGED_SETUP"


# ---------------------------------------------------------------------------
# GAP 3 — engine strategy picker eligibility
# ---------------------------------------------------------------------------

def test_picker_marks_strategy_selectable_only_when_ready(mu_db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True)
    owner = make_user("gap3-ready@example.com")
    admin = make_user("gap3-ready-admin@example.com", is_admin=True)
    client, _, instance_id, _ = _approved_personal_instance(owner, admin)
    picker = _full_client(owner)

    def entry(response):
        return next(s for s in response.json()["strategies"] if s["instance_id"] == instance_id)

    _drive_to_hold(client, instance_id)
    unready = entry(picker.get("/api/engine/strategies"))
    assert unready["selectable"] is False
    assert unready["blocking_reason"] == "PAPER_ENTRY_NOT_VERIFIED"
    assert unready["source_type"] == "PERSONAL_TRADINGVIEW"

    _paper_evidence(owner.id, instance_id, "entry-3", "BUY_CE")
    _paper_evidence(owner.id, instance_id, "exit-3", "EXIT")
    for kind, signal_id in (("PAPER_ENTRY", "entry-3"), ("PAPER_EXIT", "exit-3")):
        client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/verification", json={"kind": kind, "signal_id": signal_id})
    ready = entry(picker.get("/api/engine/strategies"))
    assert ready["selectable"] is True and ready["status"] == "READY"
    assert ready["blocking_reason"] is None


def test_picker_is_owner_scoped(mu_db):
    owner = make_user("gap3-owner@example.com")
    admin = make_user("gap3-owner-admin@example.com", is_admin=True)
    stranger = make_user("gap3-stranger@example.com")
    _, _, instance_id, _ = _approved_personal_instance(owner, admin)

    stranger_ids = {s["instance_id"] for s in _full_client(stranger).get("/api/engine/strategies").json()["strategies"]}
    owner_ids = {s["instance_id"] for s in _full_client(owner).get("/api/engine/strategies").json()["strategies"]}
    assert instance_id in owner_ids
    assert instance_id not in stranger_ids
