"""Phase 0 strategy registry: models, migration, backfill, immutability."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _alembic_config():
    from alembic.config import Config

    return Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))


REGISTRY_TABLES = {
    "strategy_catalog",
    "strategy_versions",
    "strategy_source_artifacts",
    "strategy_validation_reports",
    "strategy_admin_reviews",
}


def test_migration_upgrade_downgrade_and_reupgrade(tmp_path, monkeypatch):
    """upgrade head creates+seeds the registry; downgrade removes only it;
    re-upgrade recreates and re-seeds idempotently."""
    from alembic import command
    from sqlalchemy import create_engine, inspect

    from app.config import settings
    from app.db import engine as db_engine

    db_path = tmp_path / "registry-migration.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    db_engine.reset_engine_for_tests()
    config = _alembic_config()
    try:
        command.upgrade(config, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        tables = set(inspect(engine).get_table_names())
        assert REGISTRY_TABLES.issubset(tables)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT c.code, c.owner_type, c.status, v.version, v.status "
                    "FROM strategy_catalog c JOIN strategy_versions v ON v.strategy_id = c.id"
                )
            ).fetchall()
        assert rows == [("supertrend", "nova", "active", "1.0.0", "approved")]

        command.downgrade(config, "0005_user_setup_profiles")
        tables_after_downgrade = set(inspect(engine).get_table_names())
        assert REGISTRY_TABLES.isdisjoint(tables_after_downgrade)
        assert "users" in tables_after_downgrade  # nothing else was dropped

        command.upgrade(config, "head")
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM strategy_catalog")).scalar()
        assert count == 1
        engine.dispose()
    finally:
        db_engine.reset_engine_for_tests()


def test_migration_backfill_is_idempotent_on_rerun(tmp_path, monkeypatch):
    """Re-running the 0006 upgrade path (e.g. after a stamp/retry) never duplicates the seed."""
    from alembic import command
    from sqlalchemy import create_engine

    from app.config import settings
    from app.db import engine as db_engine

    db_path = tmp_path / "registry-rerun.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    db_engine.reset_engine_for_tests()
    config = _alembic_config()
    try:
        command.upgrade(config, "head")
        command.stamp(config, "0005_user_setup_profiles")
        command.upgrade(config, "head")  # tables exist, guards skip, backfill re-runs
    finally:
        db_engine.reset_engine_for_tests()

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM strategy_catalog")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM strategy_versions")).scalar() == 1
    engine.dispose()


def test_service_backfill_idempotent(mu_db):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import strategy_registry

    first = strategy_registry.backfill_supertrend()
    second = strategy_registry.backfill_supertrend()
    assert first["strategy_id"] == second["strategy_id"]
    assert first["version_id"] == second["version_id"]
    assert second["version_status"] == "approved"

    with session_scope() as db:
        assert db.scalar(select(models.StrategyCatalog.id).where(models.StrategyCatalog.code == "supertrend"))
        catalog_count = len(db.scalars(select(models.StrategyCatalog)).all())
        version_count = len(db.scalars(select(models.StrategyVersion)).all())
    assert (catalog_count, version_count) == (1, 1)


def test_nova_strategy_code_unique(mu_db):
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        db.add(models.StrategyCatalog(code="alpha", display_name="A", owner_type="nova"))
        db.flush()
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(models.StrategyCatalog(code="alpha", display_name="A2", owner_type="nova"))
            db.flush()


def test_personal_strategy_code_namespaced_per_owner(mu_db):
    from app.db import models
    from app.db.engine import session_scope

    owner_a = make_user("a@example.com")
    owner_b = make_user("b@example.com")

    with session_scope() as db:
        db.add(models.StrategyCatalog(code="mine", display_name="Mine", owner_type="personal", owner_user_id=owner_a.id))
        db.add(models.StrategyCatalog(code="mine", display_name="Mine", owner_type="personal", owner_user_id=owner_b.id))
        db.flush()  # same code, different owners: allowed

    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(models.StrategyCatalog(code="mine", display_name="Dup", owner_type="personal", owner_user_id=owner_a.id))
            db.flush()


def test_version_unique_per_strategy(mu_db):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import strategy_registry

    seeded = strategy_registry.backfill_supertrend()
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(
                models.StrategyVersion(
                    strategy_id=seeded["strategy_id"],
                    version="1.0.0",
                    payload_spec_version="nova.v1",
                    source_journey="nova_shared",
                    execution_kind="external_webhook",
                )
            )
            db.flush()


def test_personal_strategy_private_by_default_and_cascades_with_owner(mu_db):
    from app.db import models
    from app.db.engine import session_scope

    owner = make_user("owner@example.com")
    with session_scope() as db:
        row = models.StrategyCatalog(
            code="personal-one", display_name="P1", owner_type="personal", owner_user_id=owner.id
        )
        db.add(row)
        db.flush()
        assert row.visibility == "private"
        assert row.status == "coming_soon"

    # SQLite only enforces FK cascades with the pragma on, and the pragma is a
    # no-op inside a transaction — use a raw driver connection to exercise the
    # ON DELETE CASCADE DDL itself.
    from app.db.engine import get_engine

    raw = get_engine().raw_connection()
    try:
        raw.driver_connection.execute("PRAGMA foreign_keys=ON")
        raw.driver_connection.execute("DELETE FROM users WHERE id = ?", (str(owner.id),))
        raw.driver_connection.commit()
    finally:
        raw.close()

    with session_scope() as db:
        remaining = db.scalars(
            select(models.StrategyCatalog).where(models.StrategyCatalog.owner_user_id == owner.id)
        ).all()
        assert remaining == []


def test_approved_version_immutable_at_service_level(mu_db):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import strategy_registry

    seeded = strategy_registry.backfill_supertrend()
    version_id = seeded["version_id"]

    with pytest.raises(ValueError, match="immutable"):
        strategy_registry.update_version(version_id, changelog="tamper")
    with pytest.raises(ValueError, match="immutable"):
        strategy_registry.update_version(version_id, status="draft")

    # Lifecycle-only move out of approved is allowed.
    strategy_registry.update_version(version_id, status="deprecated")
    with session_scope() as db:
        assert db.get(models.StrategyVersion, version_id).status == "deprecated"

    # Draft versions stay editable, and approval is a one-way service action.
    with session_scope() as db:
        draft = models.StrategyVersion(
            strategy_id=seeded["strategy_id"],
            version="1.1.0",
            payload_spec_version="nova.v1",
            source_journey="nova_shared",
            execution_kind="external_webhook",
        )
        db.add(draft)
        db.flush()
        draft_id = draft.id
    strategy_registry.update_version(draft_id, changelog="editable while draft")
    strategy_registry.approve_version(draft_id)
    with pytest.raises(ValueError, match="immutable"):
        strategy_registry.update_version(draft_id, changelog="tamper after approval")

    with pytest.raises(ValueError, match="not updatable"):
        strategy_registry.update_version(draft_id, version="9.9.9")
