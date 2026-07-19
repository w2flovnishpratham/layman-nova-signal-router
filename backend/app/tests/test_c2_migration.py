"""C2 migration metadata and reversible SQLite DDL checks."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


BACKEND_ROOT = Path(__file__).resolve().parents[2]
C2_SETUP_COLUMNS = {
    "pine_conversion_request_id",
    "compile_evidence_id",
    "approved_candidate_sha256",
    "approved_source_sha256",
    "approved_strategy_layer_sha256",
    "installed_by_user_id",
    "current_credential_id",
    "hold_credential_id",
    "hold_webhook_event_id",
    "paper_eligible_at",
    "credential_revoked_at",
    "suspended_at",
}


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return config


def test_c2_migration_up_down_up_cycle(tmp_path, monkeypatch):
    from app.config import settings
    from app.db import engine as eng

    db_path = tmp_path / "c2_migration.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    eng.reset_engine_for_tests()
    config = _config()

    try:
        command.upgrade(config, "head")
        engine = eng.get_engine()
        inspector = sa.inspect(engine)
        assert "tradingview_compile_evidence" in inspector.get_table_names()
        assert C2_SETUP_COLUMNS <= {
            column["name"] for column in inspector.get_columns("tradingview_setups")
        }
        assert {"approved_candidate_sha256", "installation_mode"} <= {
            column["name"] for column in inspector.get_columns("strategy_instances")
        }

        command.downgrade(config, "0015_r1b_persistence")
        inspector = sa.inspect(engine)
        assert "tradingview_compile_evidence" not in inspector.get_table_names()
        assert not C2_SETUP_COLUMNS & {
            column["name"] for column in inspector.get_columns("tradingview_setups")
        }
        assert not {"approved_candidate_sha256", "installation_mode"} & {
            column["name"] for column in inspector.get_columns("strategy_instances")
        }

        command.upgrade(config, "head")
        inspector = sa.inspect(engine)
        assert "tradingview_compile_evidence" in inspector.get_table_names()
        assert C2_SETUP_COLUMNS <= {
            column["name"] for column in inspector.get_columns("tradingview_setups")
        }
    finally:
        eng.reset_engine_for_tests()


def _status_lengths(inspector) -> dict[str, int]:
    return {
        column["name"]: column["type"].length
        for column in inspector.get_columns("strategy_admin_reviews")
        if column["name"] in {"previous_status", "new_status"}
    }


def _insert_review(engine, *, new_status: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO strategy_admin_reviews "
                "(id, strategy_version_id, decision, previous_status, new_status, reviewed_at) "
                "VALUES (:id, :ver, 'approved', :prev, :new, :ts)"
            ),
            {
                "id": uuid.uuid4().hex,
                "ver": uuid.uuid4().hex,
                "prev": "ready_for_admin_review",
                "new": new_status,
                "ts": "2026-07-19T00:00:00+00:00",
            },
        )


# NOTE: Column-width transitions (20 -> 64 -> 20) are NOT asserted on SQLite.
# Alembic's SQLite batch_alter_table(recreate="always") rebuilds tables by
# reflecting types from the ORM's Base.metadata, so the models.py String(64)
# edit alone makes the column VARCHAR(64) even at migration 0011 -- SQLite never
# emits a real ALTER. The genuine width transition is only observable on
# PostgreSQL and is verified in test_c2_postgres_status_width.py. This is the
# exact SQLite-masking behaviour that hid the original defect.


def test_0017_stores_full_c1_approval_vocabulary(tmp_path, monkeypatch):
    """The 23-char approved_for_tv_compile value persists intact at head."""
    from app.config import settings
    from app.db import engine as eng

    db_path = tmp_path / "vocab.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    eng.reset_engine_for_tests()
    config = _config()
    try:
        command.upgrade(config, "0017_expand_admin_review_status")
        engine = eng.get_engine()
        _insert_review(engine, new_status="approved_for_tv_compile")
        with engine.connect() as conn:
            stored = conn.execute(
                sa.text("SELECT previous_status, new_status FROM strategy_admin_reviews")
            ).one()
        assert stored == ("ready_for_admin_review", "approved_for_tv_compile")
    finally:
        eng.reset_engine_for_tests()


def test_0017_downgrade_blocked_by_long_review_evidence(tmp_path, monkeypatch):
    """Downgrade refuses to truncate a stored value longer than 20 chars."""
    from app.config import settings
    from app.db import engine as eng

    db_path = tmp_path / "block.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    eng.reset_engine_for_tests()
    config = _config()
    try:
        command.upgrade(config, "0017_expand_admin_review_status")
        engine = eng.get_engine()
        _insert_review(engine, new_status="approved_for_tv_compile")
        with pytest.raises(Exception) as excinfo:
            command.downgrade(config, "0016_c2_tv_installation")
        assert "VARCHAR(20)" in str(excinfo.value)
        # Column stays wide; evidence preserved.
        assert _status_lengths(sa.inspect(engine)) == {"previous_status": 64, "new_status": 64}
        with engine.connect() as conn:
            count = conn.execute(
                sa.text("SELECT COUNT(*) FROM strategy_admin_reviews")
            ).scalar()
        assert count == 1
    finally:
        eng.reset_engine_for_tests()


def test_admin_review_model_metadata_is_width_64():
    from app.db import models

    table = models.StrategyAdminReview.__table__
    assert table.c.previous_status.type.length == 64
    assert table.c.new_status.type.length == 64
