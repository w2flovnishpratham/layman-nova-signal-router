"""Phase 1: strategy instances, state machine, webhook credentials, backfill."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _client(user_model):
    from app.auth.dependencies import get_current_user
    from app.routers import strategy_instances
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    app.include_router(strategy_instances.router)
    app.include_router(strategy_instances.admin_router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(user_model)
    return TestClient(app)


def _seed_supertrend():
    from app.services import strategy_registry

    return strategy_registry.backfill_supertrend()


def _create_instance(client, **overrides):
    payload = {"strategy_code": "supertrend", "source_journey": "PERSONAL_TRADINGVIEW", "lots": 2}
    payload.update(overrides)
    response = client.post("/api/strategy-instances", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["instance"]


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def test_migration_upgrade_downgrade_and_backfill(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from app.config import settings
    from app.db import engine as db_engine

    db_path = tmp_path / "instances-migration.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    db_engine.reset_engine_for_tests()
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    try:
        command.upgrade(config, "head")
        engine = create_engine(f"sqlite:///{db_path}")
        tables = set(inspect(engine).get_table_names())
        assert {"strategy_instances", "strategy_instance_webhook_credentials"} <= tables

        # Downgrade removes only the two new tables.
        command.downgrade(config, "0006_strategy_registry")
        tables = set(inspect(engine).get_table_names())
        assert "strategy_instances" not in tables
        assert "strategy_instance_webhook_credentials" not in tables
        assert "strategy_catalog" in tables  # 0006 untouched

        # Seed a user + subscription, then re-upgrade: 0007 must backfill it.
        user_id, sub_id = str(uuid.uuid4()), str(uuid.uuid4())
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (id, email, is_admin, created_at, updated_at) "
                "VALUES (:id, 'mig@example.com', 0, '2026-07-11', '2026-07-11')"
            ), {"id": user_id})
            conn.execute(text(
                "INSERT INTO strategy_subscriptions (id, user_id, strategy_name, active, lots, execution_mode, created_at, updated_at) "
                "VALUES (:id, :uid, 'supertrend', 1, 3, 'paper_live_data', '2026-07-11', '2026-07-11')"
            ), {"id": sub_id, "uid": user_id})
        command.upgrade(config, "head")
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT source_journey, status, current_lots, execution_mode, legacy_subscription_id "
                "FROM strategy_instances"
            )).fetchall()
        assert rows == [("NOVA_SHARED", "active", 3, "paper_live_data", sub_id)]

        # Re-running the backfill (stamp back + upgrade) must not duplicate.
        command.stamp(config, "0006_strategy_registry")
        command.upgrade(config, "head")
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM strategy_instances")).scalar()
        assert count == 1
        engine.dispose()
    finally:
        db_engine.reset_engine_for_tests()


def test_migration_history_0008_roundtrip_preserves_data(tmp_path, monkeypatch):
    """0008 must be truly reversible: downgrade restores the exact 0007
    credential schema (user_id backfilled from the owning instance) and no
    rows or token history are lost in either direction."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from app.config import settings
    from app.db import engine as db_engine

    db_path = tmp_path / "hygiene-roundtrip.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    db_engine.reset_engine_for_tests()
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    try:
        command.upgrade(config, "head")
        engine = create_engine(f"sqlite:///{db_path}")

        def columns(table):
            return {c["name"]: c for c in inspect(engine).get_columns(table)}

        def instance_fk_shapes():
            return [sorted(fk["constrained_columns"]) for fk in inspect(engine).get_foreign_keys("strategy_instances")]

        # Head (hardened): no user_id, composite FK present.
        assert "user_id" not in columns("strategy_instance_webhook_credentials")
        assert ["strategy_id", "strategy_version_id"] in instance_fk_shapes()

        # Downgrade -> original 0007 shape.
        command.downgrade(config, "0007_strategy_instances")
        creds_cols = columns("strategy_instance_webhook_credentials")
        assert "user_id" in creds_cols and not creds_cols["user_id"]["nullable"]
        assert ["strategy_id", "strategy_version_id"] not in instance_fk_shapes()
        assert ["strategy_version_id"] in instance_fk_shapes()

        # Seed data in the 0007 shape (credential carries user_id).
        user_id, strategy_id, version_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        instance_id, active_cred, revoked_cred = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (id, email, is_admin, created_at, updated_at) "
                "VALUES (:u, 'roundtrip@example.com', 0, '2026-07-11', '2026-07-11')"
            ), {"u": user_id})
            conn.execute(text(
                "INSERT INTO strategy_catalog (id, code, display_name, owner_type, visibility, status, created_at, updated_at) "
                "VALUES (:s, 'rt-code', 'RT', 'nova', 'nova_shared', 'active', '2026-07-11', '2026-07-11')"
            ), {"s": strategy_id})
            conn.execute(text(
                "INSERT INTO strategy_versions (id, strategy_id, version, payload_spec_version, source_journey, status, execution_kind, created_at, updated_at) "
                "VALUES (:v, :s, '1.0.0', 'nova.v1', 'nova_shared', 'approved', 'external_webhook', '2026-07-11', '2026-07-11')"
            ), {"v": version_id, "s": strategy_id})
            conn.execute(text(
                "INSERT INTO strategy_instances (id, user_id, strategy_id, strategy_version_id, source_journey, label, status, execution_mode, current_lots, created_at, updated_at) "
                "VALUES (:i, :u, :s, :v, 'PERSONAL_TRADINGVIEW', 'rt', 'ready', 'signal_only', 1, '2026-07-11', '2026-07-11')"
            ), {"i": instance_id, "u": user_id, "s": strategy_id, "v": version_id})
            for cred_id, token_hash, revoked in ((revoked_cred, "a" * 64, "'2026-07-11'"), (active_cred, "b" * 64, "NULL")):
                conn.execute(text(
                    "INSERT INTO strategy_instance_webhook_credentials (id, strategy_instance_id, user_id, token_hash, token_prefix, created_at, revoked_at) "
                    f"VALUES (:c, :i, :u, :h, 'nwk_rt', '2026-07-11', {revoked})"
                ), {"c": cred_id, "i": instance_id, "u": user_id, "h": token_hash})

        def credential_rows(conn):
            return conn.execute(text(
                "SELECT id, token_hash, revoked_at IS NOT NULL FROM strategy_instance_webhook_credentials ORDER BY token_hash"
            )).fetchall()

        # Upgrade to 0008: user_id gone, both credential rows + hashes intact.
        command.upgrade(config, "head")
        assert "user_id" not in columns("strategy_instance_webhook_credentials")
        assert ["strategy_id", "strategy_version_id"] in instance_fk_shapes()
        with engine.connect() as conn:
            rows = credential_rows(conn)
        assert [(r[1], bool(r[2])) for r in rows] == [("a" * 64, True), ("b" * 64, False)]

        # Downgrade again: user_id restored and backfilled to the instance owner.
        command.downgrade(config, "0007_strategy_instances")
        with engine.connect() as conn:
            restored = conn.execute(text(
                "SELECT DISTINCT user_id FROM strategy_instance_webhook_credentials"
            )).fetchall()
            rows = credential_rows(conn)
        assert restored == [(user_id,)]
        assert [(r[1], bool(r[2])) for r in rows] == [("a" * 64, True), ("b" * 64, False)]

        # And back up: final schema + zero data loss.
        command.upgrade(config, "head")
        with engine.connect() as conn:
            rows = credential_rows(conn)
            instance_count = conn.execute(text("SELECT COUNT(*) FROM strategy_instances")).scalar()
        assert [(r[1], bool(r[2])) for r in rows] == [("a" * 64, True), ("b" * 64, False)]
        assert instance_count == 1
        assert "user_id" not in columns("strategy_instance_webhook_credentials")
        engine.dispose()
    finally:
        db_engine.reset_engine_for_tests()


# ---------------------------------------------------------------------------
# Backfill + legacy sync
# ---------------------------------------------------------------------------

def test_service_backfill_idempotent_and_synced(mu_db):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import strategy_fanout, strategy_instance_service as svc

    _seed_supertrend()
    user = make_user("backfill@example.com")
    strategy_fanout.subscribe_user(user.id, "supertrend", lots=4, execution_mode="paper_live_data")

    # subscribe_user auto-creates the mirror instance; backfill finds nothing new.
    first = svc.backfill_instances_from_subscriptions()
    assert first == {"created": 0, "skipped": 1}

    instances = svc.list_instances(user.id)
    assert len(instances) == 1
    assert instances[0]["current_lots"] == 4
    assert instances[0]["execution_mode"] == "paper_live_data"
    assert instances[0]["source_journey"] == "NOVA_SHARED"
    assert instances[0]["status"] == "active"

    # Legacy subscribe API updates flow onto the instance (no divergence).
    strategy_fanout.subscribe_user(user.id, "supertrend", lots=7, execution_mode="signal_only")
    instances = svc.list_instances(user.id)
    assert len(instances) == 1
    assert instances[0]["current_lots"] == 7
    assert instances[0]["execution_mode"] == "signal_only"

    # Instance lot updates flow back onto the executing subscription row.
    svc.update_lots(user.id, instances[0]["id"], 9)
    with session_scope() as db:
        sub = db.scalar(select(models.StrategySubscription).where(models.StrategySubscription.user_id == user.id))
        assert sub.lots == 9


def test_backfill_creates_missing_instances(mu_db):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import strategy_instance_service as svc

    _seed_supertrend()
    user = make_user("legacy@example.com")
    # Simulate a pre-instance subscription written before Phase 1 (no sync hook).
    with session_scope() as db:
        db.add(models.StrategySubscription(user_id=user.id, strategy_name="supertrend", lots=2, execution_mode="signal_only"))
        db.add(models.StrategySubscription(user_id=user.id, strategy_name="unknown-strategy", lots=1, execution_mode="signal_only"))
        db.flush()

    result = svc.backfill_instances_from_subscriptions()
    assert result == {"created": 1, "skipped": 1}  # unknown strategy skipped, not failed
    assert svc.backfill_instances_from_subscriptions() == {"created": 0, "skipped": 2}


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------

def test_tenant_isolation_owner_only(mu_db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True)
    _seed_supertrend()
    user_a = make_user("a@example.com")
    user_b = make_user("b@example.com")
    client_a, client_b = _client(user_a), _client(user_b)

    instance = _create_instance(client_a)
    instance_id = instance["id"]

    assert client_a.get(f"/api/strategy-instances/{instance_id}").status_code == 200
    # User B gets the same 404 as a nonexistent id — no probing.
    assert client_b.get(f"/api/strategy-instances/{instance_id}").status_code == 404
    assert client_b.post(f"/api/strategy-instances/{instance_id}/lots", json={"lots": 5}).status_code == 404
    assert client_b.post(f"/api/strategy-instances/{instance_id}/pause").status_code == 404
    assert client_b.post(f"/api/strategy-instances/{instance_id}/webhook-credential").status_code == 404
    assert client_b.post(f"/api/strategy-instances/{instance_id}/webhook-credential/rotate").status_code == 404
    assert client_b.delete(f"/api/strategy-instances/{instance_id}/webhook-credential").status_code == 404
    assert client_b.post(f"/api/strategy-instances/{instance_id}/webhook-test").status_code == 404
    assert client_b.get(f"/api/strategy-instances/{instance_id}/webhook-executions").status_code == 404
    assert client_b.get("/api/strategy-instances").json()["instances"] == []

    # Admin listing is explicit and admin-gated.
    assert client_b.get("/api/admin/strategy-instances").status_code == 403
    admin = make_user("admin@example.com", is_admin=True)
    admin_rows = _client(admin).get("/api/admin/strategy-instances").json()["instances"]
    assert any(row["id"] == instance_id for row in admin_rows)


# ---------------------------------------------------------------------------
# Lifecycle state machine
# ---------------------------------------------------------------------------

def test_lifecycle_transitions_and_invalid_moves(mu_db):
    _seed_supertrend()
    user = make_user("lifecycle@example.com")
    client = _client(user)
    instance_id = _create_instance(client, source_journey="NOVA_SHARED")["id"]

    def status_of(response):
        assert response.status_code == 200, response.text
        return response.json()["instance"]["status"]

    # ready -> paused is not a defined move.
    assert client.post(f"/api/strategy-instances/{instance_id}/pause").status_code == 409
    assert status_of(client.post(f"/api/strategy-instances/{instance_id}/activate")) == "active"
    # active -> active (double activate) rejected.
    assert client.post(f"/api/strategy-instances/{instance_id}/activate").status_code == 409
    assert status_of(client.post(f"/api/strategy-instances/{instance_id}/pause", json={"reason": "lunch"})) == "paused"
    assert status_of(client.post(f"/api/strategy-instances/{instance_id}/resume")) == "active"
    # active -> archived must go through a stop/pause first.
    assert client.post(f"/api/strategy-instances/{instance_id}/archive").status_code == 409
    assert status_of(client.post(f"/api/strategy-instances/{instance_id}/stop")) == "stopped"
    # stopped -> paused invalid.
    assert client.post(f"/api/strategy-instances/{instance_id}/pause").status_code == 409
    assert status_of(client.post(f"/api/strategy-instances/{instance_id}/archive")) == "archived"
    # archived is terminal.
    assert client.post(f"/api/strategy-instances/{instance_id}/activate").status_code == 409
    assert client.post(f"/api/strategy-instances/{instance_id}/lots", json={"lots": 3}).status_code == 409
    # Archived hidden by default, visible on request.
    assert client.get("/api/strategy-instances").json()["instances"] == []
    assert len(client.get("/api/strategy-instances?include_archived=true").json()["instances"]) == 1


def test_lot_update_validation_and_audit(mu_db):
    from app.db import models
    from app.db.engine import session_scope

    _seed_supertrend()
    user = make_user("lots@example.com")
    client = _client(user)
    instance_id = _create_instance(client)["id"]

    assert client.post(f"/api/strategy-instances/{instance_id}/lots", json={"lots": 0}).status_code == 422
    assert client.post(f"/api/strategy-instances/{instance_id}/lots", json={"lots": 1001}).status_code == 422
    response = client.post(f"/api/strategy-instances/{instance_id}/lots", json={"lots": 10})
    assert response.status_code == 200
    assert response.json()["instance"]["current_lots"] == 10

    with session_scope() as db:
        actions = [
            row.action
            for row in db.scalars(select(models.AuditLog).where(models.AuditLog.user_id == user.id)).all()
        ]
    assert "STRATEGY_INSTANCE_LOTS_UPDATED" in actions


def test_clone_creates_ready_copy(mu_db):
    _seed_supertrend()
    user = make_user("clone@example.com")
    client = _client(user)
    original = _create_instance(client, lots=5)

    response = client.post(f"/api/strategy-instances/{original['id']}/clone", json={})
    assert response.status_code == 200
    copy = response.json()["instance"]
    assert copy["id"] != original["id"]
    assert copy["current_lots"] == 5
    assert copy["status"] == "ready"
    assert copy["label"] != original["label"]

    # Duplicate label rejected.
    duplicate = client.post(f"/api/strategy-instances/{original['id']}/clone", json={"label": copy["label"]})
    assert duplicate.status_code == 409


def test_create_real_orders_requires_entitlement(mu_db):
    _seed_supertrend()
    user = make_user("noent@example.com")
    client = _client(user)
    response = client.post(
        "/api/strategy-instances",
        json={"strategy_code": "supertrend", "source_journey": "NOVA_SHARED", "execution_mode": "real_orders"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Webhook credentials
# ---------------------------------------------------------------------------

def test_webhook_credential_lifecycle_and_hashing(mu_db):
    import hashlib

    from app.db import models
    from app.db.engine import session_scope

    _seed_supertrend()
    user = make_user("webhook@example.com")
    client = _client(user)
    instance_id = _create_instance(client)["id"]

    created = client.post(f"/api/strategy-instances/{instance_id}/webhook-credential")
    assert created.status_code == 200
    credential = created.json()["credential"]
    token = credential["token"]
    assert token.startswith("nwk_") and len(token) > 30
    assert credential["token_prefix"] == token[:10]

    # Only the hash is persisted; plaintext appears nowhere in the DB.
    with session_scope() as db:
        rows = db.scalars(select(models.StrategyInstanceWebhookCredential)).all()
        assert len(rows) == 1
        assert rows[0].token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert token not in (rows[0].token_hash + rows[0].token_prefix)

    # Reads never return the token.
    detail = client.get(f"/api/strategy-instances/{instance_id}").json()["instance"]
    assert detail["webhook_credential"] is not None
    assert "token" not in detail["webhook_credential"]
    assert "token_hash" not in detail["webhook_credential"]

    # Second generate is rejected while one is active.
    assert client.post(f"/api/strategy-instances/{instance_id}/webhook-credential").status_code == 409

    # Rotate: new token, old row revoked with immutable history.
    rotated = client.post(f"/api/strategy-instances/{instance_id}/webhook-credential/rotate")
    assert rotated.status_code == 200
    new_token = rotated.json()["credential"]["token"]
    assert new_token != token
    with session_scope() as db:
        rows = db.scalars(
            select(models.StrategyInstanceWebhookCredential).order_by(
                models.StrategyInstanceWebhookCredential.created_at
            )
        ).all()
        assert len(rows) == 2
        old, new = rows
        assert old.revoked_at is not None and old.revoked_reason == "rotated"
        assert old.replaced_by_id == new.id
        assert new.revoked_at is None

    # Revoke: no active credential left; revoking again 404s.
    assert client.delete(f"/api/strategy-instances/{instance_id}/webhook-credential").status_code == 200
    assert client.delete(f"/api/strategy-instances/{instance_id}/webhook-credential").status_code == 404
    assert client.post(f"/api/strategy-instances/{instance_id}/webhook-credential/rotate").status_code == 404
    assert client.get(f"/api/strategy-instances/{instance_id}").json()["instance"]["credential_status"] == "revoked"
    # After revocation a fresh generate is allowed again.
    assert client.post(f"/api/strategy-instances/{instance_id}/webhook-credential").status_code == 200


# ---------------------------------------------------------------------------
# Hardening pass 1: lifecycle must gate the executing fan-out path
# ---------------------------------------------------------------------------

def _fanout_eligible_user_ids():
    """The exact eligibility predicate enqueue_strategy_signal uses."""
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        return {
            str(row.user_id)
            for row in db.scalars(
                select(models.StrategySubscription).where(
                    models.StrategySubscription.strategy_name == "supertrend",
                    models.StrategySubscription.active.is_(True),
                )
            ).all()
        }


def test_pause_stop_and_archive_remove_fanout_eligibility(mu_db):
    from app.services import strategy_fanout

    _seed_supertrend()
    user = make_user("gate@example.com")
    client = _client(user)
    strategy_fanout.subscribe_user(user.id, "supertrend", lots=1, execution_mode="signal_only")
    instance_id = client.get("/api/strategy-instances").json()["instances"][0]["id"]
    assert str(user.id) in _fanout_eligible_user_ids()

    # Pause must make the user ineligible for fan-out in the same transaction.
    assert client.post(f"/api/strategy-instances/{instance_id}/pause").status_code == 200
    assert str(user.id) not in _fanout_eligible_user_ids()

    # Resume restores eligibility.
    assert client.post(f"/api/strategy-instances/{instance_id}/resume").status_code == 200
    assert str(user.id) in _fanout_eligible_user_ids()

    # Stop removes it again; archive (after stop) keeps it removed and detaches.
    assert client.post(f"/api/strategy-instances/{instance_id}/stop").status_code == 200
    assert str(user.id) not in _fanout_eligible_user_ids()
    assert client.post(f"/api/strategy-instances/{instance_id}/archive").status_code == 200
    assert str(user.id) not in _fanout_eligible_user_ids()

    # Re-subscribing after archive creates a fresh instance; the archived one
    # stays archived and unlinked.
    strategy_fanout.subscribe_user(user.id, "supertrend", lots=2, execution_mode="signal_only")
    rows = client.get("/api/strategy-instances?include_archived=true").json()["instances"]
    statuses = sorted(row["status"] for row in rows)
    assert statuses == ["active", "archived"]
    assert str(user.id) in _fanout_eligible_user_ids()


def test_legacy_unsubscribe_and_resubscribe_mirror_lifecycle(mu_db):
    from app.services import strategy_fanout, strategy_instance_service as svc

    _seed_supertrend()
    user = make_user("legacy-toggle@example.com")
    strategy_fanout.subscribe_user(user.id, "supertrend", lots=1, execution_mode="signal_only")
    instance = svc.list_instances(user.id)[0]
    assert instance["status"] == "active"

    strategy_fanout.unsubscribe_user(user.id, "supertrend")
    assert svc.list_instances(user.id)[0]["status"] == "paused"

    # A stopped instance is walked stopped -> ready -> active on reactivation.
    svc.stop_instance(user.id, instance["id"])
    strategy_fanout.subscribe_user(user.id, "supertrend", lots=1, execution_mode="signal_only")
    assert svc.list_instances(user.id)[0]["status"] == "active"
    assert str(user.id) in _fanout_eligible_user_ids()


def test_activate_real_orders_requires_entitlement(mu_db, monkeypatch):
    from app.db import models
    from app.db.engine import session_scope

    seeded = _seed_supertrend()
    user = make_user("resume-ent@example.com")
    with session_scope() as db:
        row = models.StrategyInstance(
            user_id=user.id,
            strategy_id=seeded["strategy_id"],
            strategy_version_id=seeded["version_id"],
            source_journey="NOVA_SHARED",
            label="real orders paused",
            status="paused",
            execution_mode="real_orders",
            current_lots=1,
        )
        db.add(row)
        db.flush()
        instance_id = str(row.id)

    client = _client(user)
    assert client.post(f"/api/strategy-instances/{instance_id}/resume").status_code == 403

    from app.services import entitlements

    monkeypatch.setattr(entitlements, "require_live_entitlement_for_user", lambda *a, **k: None)
    monkeypatch.setattr(entitlements, "require_strategy_entitlement_for_user", lambda *a, **k: None)
    assert client.post(f"/api/strategy-instances/{instance_id}/resume").status_code == 200


# ---------------------------------------------------------------------------
# Hardening pass 2: atomic sync + drift detection
# ---------------------------------------------------------------------------

def test_sync_failure_rolls_back_subscription_write(mu_db, monkeypatch):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import strategy_fanout, strategy_instance_service as svc

    _seed_supertrend()
    user = make_user("atomic@example.com")

    def boom(db, subscription):
        raise RuntimeError("mirror sync failed")

    monkeypatch.setattr(svc, "sync_instance_from_subscription", boom)
    with pytest.raises(RuntimeError, match="mirror sync failed"):
        strategy_fanout.subscribe_user(user.id, "supertrend", lots=1, execution_mode="signal_only")

    # The whole transaction rolled back: no subscription, no instance.
    with session_scope() as db:
        assert db.scalars(
            select(models.StrategySubscription).where(models.StrategySubscription.user_id == user.id)
        ).all() == []
        assert db.scalars(
            select(models.StrategyInstance).where(models.StrategyInstance.user_id == user.id)
        ).all() == []


def test_drift_detector_reports_and_repairs(mu_db):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import strategy_fanout, strategy_instance_service as svc

    _seed_supertrend()
    user = make_user("drift@example.com")
    strategy_fanout.subscribe_user(user.id, "supertrend", lots=3, execution_mode="paper_live_data")

    # No drift after a normal synced write.
    assert svc.detect_drift() == {"findings": [], "repaired": 0, "repair": False}

    # Manufacture drift by writing the instance directly (bypassing services).
    with session_scope() as db:
        row = db.scalars(select(models.StrategyInstance).where(models.StrategyInstance.user_id == user.id)).one()
        row.current_lots = 99
        row.execution_mode = "signal_only"
        row.status = "paused"
        instance_id = str(row.id)

    report = svc.detect_drift()
    kinds = sorted(f["type"] for f in report["findings"])
    assert kinds == ["execution_mode_mismatch", "lifecycle_mismatch", "lots_mismatch"]
    assert report["repaired"] == 0  # report-only by default

    # Nothing changed without explicit repair.
    assert svc.get_instance(user.id, instance_id)["current_lots"] == 99

    # Explicit repair: subscription (execution authority) wins, audited.
    repaired = svc.detect_drift(repair=True)
    assert repaired["repaired"] == 1
    fixed = svc.get_instance(user.id, instance_id)
    assert fixed["current_lots"] == 3
    assert fixed["execution_mode"] == "paper_live_data"
    assert fixed["status"] == "active"
    assert svc.detect_drift() == {"findings": [], "repaired": 0, "repair": False}

    with session_scope() as db:
        actions = [
            row.action for row in db.scalars(select(models.AuditLog).where(models.AuditLog.user_id == user.id)).all()
        ]
    assert "INSTANCE_DRIFT_REPAIRED" in actions


def test_drift_detector_missing_instance_and_missing_subscription(mu_db):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import strategy_fanout, strategy_instance_service as svc

    _seed_supertrend()
    user = make_user("drift-missing@example.com")
    strategy_fanout.subscribe_user(user.id, "supertrend", lots=2, execution_mode="signal_only")

    # missing_instance: drop the instance but keep the subscription.
    with session_scope() as db:
        row = db.scalars(select(models.StrategyInstance).where(models.StrategyInstance.user_id == user.id)).one()
        db.delete(row)
    report = svc.detect_drift()
    assert [f["type"] for f in report["findings"]] == ["missing_instance"]
    repaired = svc.detect_drift(repair=True)
    assert repaired["repaired"] == 1
    assert len(svc.list_instances(user.id)) == 1

    # missing_subscription: dangling link is reported, never auto-repaired.
    with session_scope() as db:
        sub = db.scalars(select(models.StrategySubscription).where(models.StrategySubscription.user_id == user.id)).one()
        db.delete(sub)
    report = svc.detect_drift(repair=True)
    assert [f["type"] for f in report["findings"]] == ["missing_subscription"]
    assert report["repaired"] == 0


# ---------------------------------------------------------------------------
# Hardening pass 3: structural relationship consistency
# ---------------------------------------------------------------------------

def test_version_must_belong_to_strategy_at_db_level(mu_db):
    from app.db import models
    from app.db.engine import get_engine, session_scope
    from app.services import strategy_registry

    _seed_supertrend()
    user = make_user("fkcheck@example.com")
    with session_scope() as db:
        other = models.StrategyCatalog(code="other-strategy", display_name="Other", owner_type="nova", status="active")
        db.add(other)
        db.flush()
        other_version = models.StrategyVersion(
            strategy_id=other.id,
            version="1.0.0",
            payload_spec_version="nova.v1",
            source_journey="nova_shared",
            status="approved",
            execution_kind="external_webhook",
        )
        db.add(other_version)
        db.flush()
        supertrend_id = db.scalar(
            select(models.StrategyCatalog.id).where(models.StrategyCatalog.code == "supertrend")
        )
        mismatched = (str(user.id), str(supertrend_id), str(other_version.id))

    # SQLite enforces FKs only with the pragma on (no-op inside a transaction),
    # so exercise the composite FK through a raw driver connection.
    import sqlite3

    raw = get_engine().raw_connection()
    try:
        raw.driver_connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            raw.driver_connection.execute(
                "INSERT INTO strategy_instances (id, user_id, strategy_id, strategy_version_id, source_journey, "
                "label, status, execution_mode, current_lots, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'NOVA_SHARED', 'bad', 'ready', 'signal_only', 1, '2026-07-11', '2026-07-11')",
                (str(uuid.uuid4()), *mismatched),
            )
    finally:
        raw.close()


def test_credential_has_no_user_id_column(mu_db):
    """Ownership is structural: the credential table carries no user identity
    to diverge — it derives entirely from the instance row."""
    from app.db import models

    assert "user_id" not in models.StrategyInstanceWebhookCredential.__table__.columns


def test_webhook_credential_only_for_personal_tradingview(mu_db):
    _seed_supertrend()
    user = make_user("sharedwk@example.com")
    client = _client(user)
    shared = _create_instance(client, source_journey="NOVA_SHARED", label="shared one")
    response = client.post(f"/api/strategy-instances/{shared['id']}/webhook-credential")
    assert response.status_code == 409


def test_personal_readiness_and_hold_connection_test(mu_db, monkeypatch):
    from sqlalchemy import func

    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope

    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True)
    _seed_supertrend()
    user = make_user("personal-ready@example.com")
    client = _client(user)
    instance_id = _create_instance(client, execution_mode="paper_live_data")["id"]

    blocked = client.post(f"/api/strategy-instances/{instance_id}/activate")
    assert blocked.status_code == 409
    issued = client.post(f"/api/strategy-instances/{instance_id}/webhook-credential")
    assert issued.status_code == 200 and issued.json()["credential"]["token"]

    tested = client.post(f"/api/strategy-instances/{instance_id}/webhook-test")
    assert tested.status_code == 200
    assert tested.json()["status"] == "NO_OP"
    detail = client.get(f"/api/strategy-instances/{instance_id}").json()["instance"]
    assert detail["readiness"] == {
        "paper_mode": True,
        "valid_lots": True,
        "active_credential": True,
        "connection_tested": True,
        "can_activate": True,
    }
    assert detail["estimated_quantity"] == detail["current_lots"] * detail["lot_size"]
    assert client.post(f"/api/strategy-instances/{instance_id}/activate").status_code == 200

    with session_scope() as db:
        assert db.scalar(select(func.count(models.StrategyExecutionJob.id))) == 0
        assert db.scalar(select(func.count(models.StrategySignal.id))) == 1


def test_webhook_test_is_owner_scoped_and_paper_only(mu_db, monkeypatch):
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope

    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True)
    _seed_supertrend()
    owner = make_user("personal-owner@example.com")
    foreign = make_user("personal-foreign@example.com")
    owner_client = _client(owner)
    instance_id = _create_instance(owner_client)["id"]
    owner_client.post(f"/api/strategy-instances/{instance_id}/webhook-credential")

    assert _client(foreign).post(f"/api/strategy-instances/{instance_id}/webhook-test").status_code == 404
    with session_scope() as db:
        row = db.get(models.StrategyInstance, uuid.UUID(instance_id))
        row.execution_mode = "real_orders"
    response = owner_client.post(f"/api/strategy-instances/{instance_id}/webhook-test")
    assert response.status_code == 409
    assert response.json()["reason"] == "LIVE_MODE_UNAVAILABLE"
