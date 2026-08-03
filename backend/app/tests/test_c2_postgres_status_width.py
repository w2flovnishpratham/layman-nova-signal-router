"""Real-PostgreSQL proof for the strategy_admin_reviews status-width fix.

SQLite cannot exercise this defect: Alembic's SQLite ``batch_alter_table`` rebuilds
tables by reflecting types from ``Base.metadata``, so the ORM ``String(64)`` edit
alone makes the column wide there -- no real ALTER runs and no length is enforced.
Only PostgreSQL enforces the declared VARCHAR length, so these checks run against a
disposable PostgreSQL database.

Enable by exporting a base PostgreSQL URL to a server where the test may create and
drop throwaway databases::

    NOVA_PG_TEST_URL=postgresql://postgres:pw@localhost:5432/postgres

The test never touches the named database except to CREATE/DROP uniquely-named
disposable siblings. No source is executed, no order is sent, Claude stays disabled.
"""
from __future__ import annotations

import datetime
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

PG_BASE_URL = os.environ.get("NOVA_PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_BASE_URL,
    reason="Set NOVA_PG_TEST_URL to a disposable PostgreSQL server to run real-schema width checks.",
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
STATUS_COLUMNS = ("previous_status", "new_status")
PREV_VALUE = "ready_for_admin_review"       # 22 chars, written by the C1 approval route
NEW_VALUE = "approved_for_tv_compile"       # 23 chars, written by the C1 approval route


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return config


def _admin_engine() -> sa.Engine:
    from app.db.engine import normalize_database_url

    return sa.create_engine(normalize_database_url(PG_BASE_URL), isolation_level="AUTOCOMMIT")


@pytest.fixture()
def disposable_db(monkeypatch):
    """Create a uniquely-named PostgreSQL database, point the app at it, drop it after."""
    from app.config import settings
    from app.db import engine as eng
    from app.db.engine import normalize_database_url

    name = f"nova_c2f2_{uuid.uuid4().hex[:12]}"
    admin = _admin_engine()
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))

    base = normalize_database_url(PG_BASE_URL)
    url = base.rsplit("/", 1)[0] + "/" + name
    monkeypatch.setattr(settings, "DATABASE_URL", url, raising=False)
    eng.reset_engine_for_tests()
    try:
        yield eng.get_engine()
    finally:
        eng.reset_engine_for_tests()
        with admin.connect() as conn:
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _lengths(engine) -> dict[str, int]:
    query = sa.text(
        "SELECT column_name, character_maximum_length FROM information_schema.columns "
        "WHERE table_name = 'strategy_admin_reviews' AND column_name IN ('previous_status', 'new_status')"
    )
    with engine.connect() as conn:
        return dict(conn.execute(query).all())


def _insert_approval_review(engine) -> None:
    """Insert exactly what admin_pine_conversion_service.approve() writes at the DB layer.

    FK enforcement is disabled for the transaction so the proof isolates column width
    from the (separately tested) parent-row graph. Disposable database only.
    """
    with engine.begin() as conn:
        conn.execute(sa.text("SET session_replication_role = replica"))
        conn.execute(
            sa.text(
                "INSERT INTO strategy_admin_reviews "
                "(id, strategy_version_id, decision, previous_status, new_status, reviewed_at) "
                "VALUES (:id, :ver, 'approved', :prev, :new, :ts)"
            ),
            {
                "id": uuid.uuid4().hex,
                "ver": uuid.uuid4().hex,
                "prev": PREV_VALUE,
                "new": NEW_VALUE,
                "ts": datetime.datetime.now(datetime.timezone.utc),
            },
        )


def _force_deployed_width_20(engine) -> None:
    """Reproduce the deployed pre-0017 schema.

    Under the new ORM metadata, reaching 0016 yields VARCHAR(64) because Alembic's
    batch recreate reflects Base.metadata. The *deployed* database was migrated when
    the ORM said String(20), so it holds a genuine VARCHAR(20). Narrow explicitly to
    match production before exercising migration 0017's real ALTER.
    """
    with engine.begin() as conn:
        for column in STATUS_COLUMNS:
            conn.execute(
                sa.text(
                    f"ALTER TABLE strategy_admin_reviews ALTER COLUMN {column} TYPE VARCHAR(20)"
                )
            )


def test_deployed_varchar20_truncates_then_0017_widens_and_fixes(disposable_db):
    """The end-to-end production-upgrade proof: 20 truncates, 0017 widens to 64, insert works."""
    engine = disposable_db
    config = _config()

    command.upgrade(config, "0016_c2_tv_installation")
    _force_deployed_width_20(engine)
    assert _lengths(engine) == {"previous_status": 20, "new_status": 20}

    # Defect: the valid C1 approval values do not fit VARCHAR(20).
    with pytest.raises(sa.exc.DataError) as excinfo:
        _insert_approval_review(engine)
    assert "StringDataRightTruncation" in str(excinfo.value) or "too long" in str(excinfo.value)

    # Migration 0017 emits a real widening ALTER on the genuine VARCHAR(20) column.
    emitted: list[str] = []

    @sa.event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        collapsed = " ".join(statement.split())
        if "strategy_admin_reviews" in collapsed and "ALTER COLUMN" in collapsed:
            emitted.append(collapsed)

    command.upgrade(config, "0017_expand_admin_review_status")
    sa.event.remove(engine, "before_cursor_execute", _capture)

    assert _lengths(engine) == {"previous_status": 64, "new_status": 64}
    assert any("previous_status TYPE VARCHAR(64)" in stmt for stmt in emitted), emitted
    assert any("new_status TYPE VARCHAR(64)" in stmt for stmt in emitted), emitted

    # Fix: the valid C1 approval values persist intact.
    _insert_approval_review(engine)
    with engine.connect() as conn:
        stored = conn.execute(
            sa.text("SELECT previous_status, new_status FROM strategy_admin_reviews")
        ).one()
    assert stored == (PREV_VALUE, NEW_VALUE)

    # Re-upgrade is idempotent (already-wide column stays 64).
    command.upgrade(config, "head")
    assert _lengths(engine) == {"previous_status": 64, "new_status": 64}


def test_empty_downgrade_narrows_back_to_20(disposable_db):
    engine = disposable_db
    config = _config()
    command.upgrade(config, "head")
    assert _lengths(engine) == {"previous_status": 64, "new_status": 64}
    # No rows -> safe downgrade narrows back to VARCHAR(20).
    command.downgrade(config, "0016_c2_tv_installation")
    assert _lengths(engine) == {"previous_status": 20, "new_status": 20}


def test_downgrade_blocked_when_evidence_exceeds_20(disposable_db):
    engine = disposable_db
    config = _config()
    command.upgrade(config, "head")
    _insert_approval_review(engine)  # 23-char new_status stored at width 64

    with pytest.raises(RuntimeError) as excinfo:
        command.downgrade(config, "0016_c2_tv_installation")
    assert "VARCHAR(20)" in str(excinfo.value)

    # Column stays wide; the review evidence is untouched.
    assert _lengths(engine) == {"previous_status": 64, "new_status": 64}
    with engine.connect() as conn:
        count = conn.execute(sa.text("SELECT COUNT(*) FROM strategy_admin_reviews")).scalar()
    assert count == 1
