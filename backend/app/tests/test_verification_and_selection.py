"""Phase 4G-pre correction: controlled paper verification mode (deadlock fix)
and persisted engine strategy selection."""
from __future__ import annotations

import uuid

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_private_webhook import (  # noqa: F401
    _issue_token, _make_instance, _payload, _post, client, webhook_enabled,
)
from app.tests.test_private_webhook_execution import (  # noqa: F401
    _ingest, _job_rows, _position_for, _run_worker, _start_paper_engine,
    deterministic_paper_market, per_user_runtime, worker_enabled,
)
from app.tests.test_readiness_unification import _full_client
from app.tests.test_tradingview_setup import _approved_personal_instance, _paper_evidence


def _set_verification(instance_id: str, on: bool = True) -> None:
    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db:
        db.get(models.StrategyInstance, uuid.UUID(instance_id)).verification_mode = on


def _status(instance_id: str) -> str:
    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db:
        return db.get(models.StrategyInstance, uuid.UUID(instance_id)).status


# ---------------------------------------------------------------------------
# Deadlock prevention — through the real webhook → execution → PaperBroker path
# ---------------------------------------------------------------------------

def test_inactive_verification_instance_produces_paper_entry(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    """The core fix: a READY (never-activated) strategy in verification mode can
    execute a genuine paper entry, so it CAN produce the evidence readiness needs."""
    user = make_user("verif-entry@gmail.com")
    instance_id = _make_instance(user, status="ready", lots=2)
    _set_verification(instance_id, True)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    _ingest(client, token, action="BUY_CE")
    assert _run_worker() == 1
    jobs = _job_rows(instance_id)
    assert jobs[0]["status"] == "completed", jobs
    assert jobs[0]["result"]["execution_result"]["success"] is True
    assert _position_for(user)["has_open_position"] is True


def test_inactive_non_verification_instance_is_rejected(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    """Without verification mode, a READY instance still cannot execute — the
    deadlock fix does not weaken the normal inactive guard."""
    user = make_user("verif-none@gmail.com")
    instance_id = _make_instance(user, status="ready")
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    rejected = _post(client, token, _payload(action="BUY_CE"))
    assert rejected.status_code == 409 and rejected.json()["reason"] == "INACTIVE_INSTANCE"
    assert _position_for(user)["has_open_position"] is False


def test_verification_never_executes_a_live_instance(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    """Verification is paper-only: a real-orders instance can never execute a
    verification signal — the hard guarantee against live orders."""
    user = make_user("verif-live@gmail.com")
    instance_id = _make_instance(user, status="ready", execution_mode="real_orders")
    _set_verification(instance_id, True)
    token = _issue_token(user, instance_id)
    _start_paper_engine(user)

    rejected = _post(client, token, _payload(action="BUY_CE"))
    assert rejected.status_code == 409 and rejected.json()["reason"] == "LIVE_EXECUTION_SAFETY_BLOCK"
    assert _position_for(user)["has_open_position"] is False


# ---------------------------------------------------------------------------
# Verification lifecycle (start → HOLD → entry → exit → READY, no auto-activate)
# ---------------------------------------------------------------------------

def _hold(client, instance_id):
    hold = client.post(f"/api/strategy-instances/{instance_id}/webhook-test").json()
    assert client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/verification", json={
        "kind": "HOLD", "signal_id": hold["signal_id"],
    }).status_code == 200


def test_approved_strategy_verification_lifecycle_to_ready(mu_db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True)
    owner = make_user("life-owner@example.com")
    admin = make_user("life-admin@example.com", is_admin=True)
    client, _, instance_id, _ = _approved_personal_instance(owner, admin)

    assert client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup", json={"setup_type": "USER_MANAGED_TRADINGVIEW"}).status_code == 200
    assert client.post(f"/api/strategy-instances/{instance_id}/webhook-credential").status_code == 200
    # Cannot activate before verification (deadlock would be here without the fix).
    assert client.post(f"/api/strategy-instances/{instance_id}/activate").status_code == 409

    started = client.post(f"/api/strategy-instances/{instance_id}/verification-mode")
    assert started.status_code == 200, started.text
    assert started.json()["instance"]["verification_mode"] is True

    client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/compiled", json={"compiled": True})
    _hold(client, instance_id)
    _paper_evidence(owner.id, instance_id, "life-entry", "BUY_CE")
    _paper_evidence(owner.id, instance_id, "life-exit", "EXIT")
    assert client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/verification", json={"kind": "PAPER_ENTRY", "signal_id": "life-entry"}).status_code == 200
    # HOLD + entry only → still not ready, still in verification.
    mid = client.get(f"/api/strategy-instances/{instance_id}").json()["instance"]
    assert mid["verification_mode"] is True and mid["readiness"]["can_activate"] is False
    assert client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/verification", json={"kind": "PAPER_EXIT", "signal_id": "life-exit"}).status_code == 200

    ready = client.get(f"/api/strategy-instances/{instance_id}").json()["instance"]
    assert ready["verification_mode"] is False
    assert ready["verification_completed_at"] is not None
    assert ready["readiness"]["can_activate"] is True
    # READY does not auto-activate — the instance is still ready, not active.
    assert _status(instance_id) == "ready"
    assert client.post(f"/api/strategy-instances/{instance_id}/activate").status_code == 200


def test_unapproved_strategy_cannot_enter_verification(mu_db):
    from app.tests.test_strategy_instances import _create_instance, _seed_supertrend
    _seed_supertrend()
    owner = make_user("life-unapproved@example.com")
    client = _full_client(owner)
    instance = _create_instance(client, source_journey="PERSONAL_TRADINGVIEW", execution_mode="paper_live_data")
    # No TradingView setup / approved personal Pine linked yet.
    blocked = client.post(f"/api/strategy-instances/{instance['id']}/verification-mode")
    assert blocked.status_code == 409


def test_managed_verification_is_admin_only(mu_db):
    owner = make_user("mgd-owner@example.com")
    admin = make_user("mgd-admin@example.com", is_admin=True)
    client, admin_client, instance_id, _ = _approved_personal_instance(owner, admin, "NOVA_MANAGED_TRADINGVIEW")
    setup = client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup", json={"setup_type": "NOVA_MANAGED_TRADINGVIEW", "requested_timeframe": "5"}).json()["setup"]

    # The non-Premium user cannot start verification for a managed setup.
    assert client.post(f"/api/strategy-instances/{instance_id}/verification-mode").json()["reason"] == "ADMIN_ACTION_REQUIRED"
    # Admin cannot start until installation + credential are in place.
    assert admin_client.post(f"/api/admin/managed-tradingview-setups/{setup['id']}/verification-mode").status_code == 409
    _version_install(admin_client, setup["id"], instance_id)
    assert admin_client.post(f"/api/admin/managed-tradingview-setups/{setup['id']}/credential").status_code == 200
    started = admin_client.post(f"/api/admin/managed-tradingview-setups/{setup['id']}/verification-mode")
    assert started.status_code == 200, started.text
    assert started.json()["instance"]["verification_mode"] is True


def _version_install(admin_client, setup_id, instance_id):
    from datetime import datetime, timezone
    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db:
        from sqlalchemy import select
        setup = db.scalar(select(models.TradingViewSetup).where(models.TradingViewSetup.id == uuid.UUID(setup_id)))
        version = db.get(models.StrategyVersion, setup.approved_version_id)
        version_hash = version.source_sha256
    admin_client.post(f"/api/admin/managed-tradingview-setups/{setup_id}/installation", json={
        "installed_version_hash": version_hash, "workspace_reference": "ws", "alert_reference": "al",
        "symbol": "NIFTY", "timeframe": "5", "installed_at": datetime.now(timezone.utc).isoformat(),
    })


def test_verification_refused_when_live_globally_enabled(mu_db, monkeypatch):
    owner = make_user("live-block@example.com")
    admin = make_user("live-block-admin@example.com", is_admin=True)
    client, _, instance_id, _ = _approved_personal_instance(owner, admin)
    client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup", json={"setup_type": "USER_MANAGED_TRADINGVIEW"})
    client.post(f"/api/strategy-instances/{instance_id}/webhook-credential")
    from app.config import settings
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
    blocked = client.post(f"/api/strategy-instances/{instance_id}/verification-mode")
    assert blocked.status_code == 409 and blocked.json()["reason"] == "LIVE_EXECUTION_SAFETY_BLOCK"


# ---------------------------------------------------------------------------
# Engine selection
# ---------------------------------------------------------------------------

def _drive_to_ready(client, owner, instance_id):
    from app.config import settings
    client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup", json={"setup_type": "USER_MANAGED_TRADINGVIEW"})
    client.post(f"/api/strategy-instances/{instance_id}/webhook-credential")
    client.post(f"/api/strategy-instances/{instance_id}/verification-mode")
    client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/compiled", json={"compiled": True})
    _hold(client, instance_id)
    _paper_evidence(owner.id, instance_id, f"e-{instance_id[:6]}", "BUY_CE")
    _paper_evidence(owner.id, instance_id, f"x-{instance_id[:6]}", "EXIT")
    client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/verification", json={"kind": "PAPER_ENTRY", "signal_id": f"e-{instance_id[:6]}"})
    client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup/verification", json={"kind": "PAPER_EXIT", "signal_id": f"x-{instance_id[:6]}"})


def test_select_eligible_instance_and_persist(mu_db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True)
    owner = make_user("sel-owner@example.com")
    admin = make_user("sel-admin@example.com", is_admin=True)
    client, _, instance_id, _ = _approved_personal_instance(owner, admin)
    picker = _full_client(owner)

    # Not selectable while in verification.
    client.post(f"/api/strategy-instances/{instance_id}/tradingview-setup", json={"setup_type": "USER_MANAGED_TRADINGVIEW"})
    client.post(f"/api/strategy-instances/{instance_id}/webhook-credential")
    client.post(f"/api/strategy-instances/{instance_id}/verification-mode")
    assert picker.put("/api/engine/selection", json={"strategy_instance_id": instance_id}).status_code == 409

    _drive_to_ready(client, owner, instance_id)
    selected = picker.put("/api/engine/selection", json={"strategy_instance_id": instance_id})
    assert selected.status_code == 200, selected.text
    assert selected.json()["selected_instance_id"] == instance_id
    got = picker.get("/api/engine/selection").json()
    assert got["selected_instance_id"] == instance_id
    listing = picker.get("/api/engine/strategies").json()
    assert next(s for s in listing["strategies"] if s["instance_id"] == instance_id)["selected"] is True


def test_cannot_select_foreign_but_can_select_ready_stopped_personal_instance(mu_db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True)
    owner = make_user("iso-owner@example.com")
    admin = make_user("iso-admin@example.com", is_admin=True)
    stranger = make_user("iso-stranger@example.com")
    client, _, instance_id, _ = _approved_personal_instance(owner, admin)
    _drive_to_ready(client, owner, instance_id)

    # Foreign user gets a uniform 404 (no tenant probing).
    assert _full_client(stranger).put("/api/engine/selection", json={"strategy_instance_id": instance_id}).status_code == 404

    # Selection only changes the owner's pointer. A stopped personal instance
    # that still clears the activation-readiness gate remains selectable, and
    # selecting it never starts or activates the instance.
    _full_client(owner).put("/api/engine/selection", json={"strategy_instance_id": instance_id})
    client.post(f"/api/strategy-instances/{instance_id}/activate")
    client.post(f"/api/strategy-instances/{instance_id}/stop", json={"reason": "done"})
    selected = _full_client(owner).put(
        "/api/engine/selection", json={"strategy_instance_id": instance_id}
    )
    assert selected.status_code == 200
    assert _status(instance_id) == "stopped"


def test_selection_never_reroutes_webhook_signals(client, per_user_runtime, deterministic_paper_market, worker_enabled):
    """The private credential is execution authority: a signal for A's credential
    executes as A even when the engine selection points at B."""
    user = make_user("reroute@gmail.com")
    a = _make_instance(user, label="A", status="active")
    b = _make_instance(user, label="B", status="active")
    token_a = _issue_token(user, a)
    _start_paper_engine(user)
    # Point the persisted engine selection at B.
    from app.services import strategy_instance_service
    from app.db import models
    from app.db.engine import session_scope
    with session_scope() as db:
        db.add(models.UserEngineConfig(user_id=user.id, selected_strategy_instance_id=uuid.UUID(b)))

    _ingest(client, token_a, action="BUY_CE")
    assert _run_worker() == 1
    assert _job_rows(a) and _job_rows(a)[0]["status"] == "completed"  # executed as A
    assert _job_rows(b) == []  # never rerouted to B
