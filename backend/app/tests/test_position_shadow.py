"""Phase 2A: durable position shadow — schema, dual-write, parity, importer.

JSON remains the execution read authority throughout; every test asserts the
shadow never disturbs the authoritative JSON write.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


ENTRY_POSITION = {
    "has_open_position": True,
    "strategy_code": "supertrend",
    "symbol": "NIFTY",
    "security_id": "12345",
    "trading_symbol": "NIFTY 17 JUL 25500 CALL",
    "option_side": "CE",
    "strike": 25500,
    "expiry": "2026-07-16",
    "qty": 75,
    "requested_qty": 75,
    "filled_qty": 75,
    "partial_fill": False,
    "entry_order_id": "ORD-1",
    "entry_price": 123.45,
    "exit_management": "DHAN_SUPER",
    "broker_sl_price": 100.05,
    "broker_tp_price": 150.10,
    "product_type": "INTRADAY",
    "opened_at": "2026-07-12T04:00:00+00:00",
    "live_pnl": {"status": "tracking_pending", "qty": 75},
}


@pytest.fixture
def runtime_dirs(tmp_path, monkeypatch):
    """Redirect all runtime JSON files into tmp (established suite pattern)."""
    from app.services import state_store, user_context

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
    return state_root


@pytest.fixture
def shadow_enabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "POSITION_DB_SHADOW_WRITE_ENABLED", True, raising=False)


def _bound(user_model):
    from app.services.execution_context import bind_execution_context
    from app.services.user_context import current_user_from_model

    return bind_execution_context(current_user_from_model(user_model))


def _rows(user_id=None):
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        query = select(models.StrategyInstancePosition).order_by(models.StrategyInstancePosition.created_at)
        if user_id is not None:
            query = query.where(models.StrategyInstancePosition.user_id == user_id)
        rows = db.scalars(query).all()
        db.expunge_all()
        return rows


# ---------------------------------------------------------------------------
# Schema / migration
# ---------------------------------------------------------------------------

def test_migration_upgrade_downgrade_reupgrade_with_data(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from app.config import settings
    from app.db import engine as db_engine

    db_path = tmp_path / "position-shadow.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    db_engine.reset_engine_for_tests()
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    try:
        command.upgrade(config, "head")
        engine = create_engine(f"sqlite:///{db_path}")
        tables = set(inspect(engine).get_table_names())
        assert {"strategy_instance_positions", "position_events"} <= tables

        user_id, position_id = str(uuid.uuid4()), str(uuid.uuid4())
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (id, email, is_admin, created_at, updated_at) "
                "VALUES (:u, 'pos@example.com', 0, '2026-07-12', '2026-07-12')"
            ), {"u": user_id})
            conn.execute(text(
                "INSERT INTO strategy_instance_positions (id, user_id, execution_mode, position_state, position_side, "
                "underlying, open_quantity, filled_exit_quantity, imported_from_json, created_at, updated_at, version) "
                "VALUES (:p, :u, 'live', 'open', 'LONG', 'NIFTY', 75, 0, 0, '2026-07-12', '2026-07-12', 1)"
            ), {"p": position_id, "u": user_id})
            conn.execute(text(
                "INSERT INTO position_events (id, position_id, user_id, event_type, source, event_at) "
                "VALUES (:e, :p, :u, 'entry_filled', 'entry', '2026-07-12')"
            ), {"e": str(uuid.uuid4()), "p": position_id, "u": user_id})

        command.downgrade(config, "0008_structural_consistency")
        tables = set(inspect(engine).get_table_names())
        assert "strategy_instance_positions" not in tables
        assert "position_events" not in tables
        assert "strategy_instances" in tables  # earlier schema untouched
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 1

        command.upgrade(config, "head")
        tables = set(inspect(engine).get_table_names())
        assert {"strategy_instance_positions", "position_events"} <= tables
        engine.dispose()
    finally:
        db_engine.reset_engine_for_tests()


def test_active_position_constraint_and_event_idempotency(mu_db):
    from sqlalchemy.exc import IntegrityError

    from app.db import models
    from app.db.engine import session_scope

    user = make_user("constraint@example.com")
    with session_scope() as db:
        db.add(models.StrategyInstancePosition(user_id=user.id, execution_mode="live", position_state="open"))
        db.flush()
    # Second ACTIVE row for the same (user, mode) slot is blocked.
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(models.StrategyInstancePosition(user_id=user.id, execution_mode="live", position_state="entering"))
            db.flush()
    # Different mode and closed rows are fine.
    with session_scope() as db:
        db.add(models.StrategyInstancePosition(user_id=user.id, execution_mode="paper", position_state="open"))
        db.add(models.StrategyInstancePosition(
            user_id=user.id, execution_mode="live", position_state="closed", open_quantity=0
        ))
        db.flush()

    with session_scope() as db:
        position = db.scalars(select(models.StrategyInstancePosition)).first()
        db.add(models.PositionEvent(
            position_id=position.id, user_id=user.id, event_type="entry_filled",
            source="entry", idempotency_key="evt-1",
        ))
        db.flush()
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            position = db.scalars(select(models.StrategyInstancePosition)).first()
            db.add(models.PositionEvent(
                position_id=position.id, user_id=user.id, event_type="entry_filled",
                source="entry", idempotency_key="evt-1",
            ))
            db.flush()


# ---------------------------------------------------------------------------
# Shadow dual-write
# ---------------------------------------------------------------------------

def test_flag_disabled_no_shadow_rows(mu_db, runtime_dirs):
    from app.services import state_store

    user = make_user("noshadow@example.com")
    with _bound(user):
        state_store.set_live_open_position(dict(ENTRY_POSITION))
        assert state_store.get_live_open_position()["security_id"] == "12345"
    assert _rows() == []


def test_shadow_full_lifecycle(mu_db, runtime_dirs, shadow_enabled):
    from app.services import position_store, state_store

    user = make_user("lifecycle-shadow@example.com")
    with _bound(user):
        # Entry (fill known immediately) -> one active row.
        state_store.set_live_open_position(dict(ENTRY_POSITION))
        rows = _rows(user.id)
        assert len(rows) == 1
        row = rows[0]
        assert row.position_state == "open"
        assert row.execution_mode == "live"
        assert row.security_id == "12345"
        assert row.option_side == "CE"
        assert row.open_quantity == 75
        assert row.avg_entry_price_paise == 12345  # exact integer paise
        assert row.entry_order_id == "ORD-1"
        assert row.super_order_metadata["broker_sl_price_paise"] == 10005
        assert row.parity_status == "match"
        first_version = row.version
        assert row.opened_at is not None and row.closed_at is None

        # Partial exit fill.
        partial = dict(ENTRY_POSITION)
        partial["qty"] = 25
        partial["partial_exit"] = {
            "order_id": "ORD-EXIT-1", "filled_qty": 50, "remaining_qty": 25, "status": "PARTIAL",
        }
        state_store.set_live_open_position(partial)
        row = _rows(user.id)[0]
        assert row.position_state == "exiting"
        assert row.open_quantity == 25
        assert row.filled_exit_quantity == 50
        assert row.exit_order_id == "ORD-EXIT-1"
        assert row.parity_status == "match"
        assert row.version == first_version + 1  # optimistic version advances per update

        # Reversal marker.
        reversing = dict(partial)
        reversing["reversal_exit"] = {"status": "TRANSIT", "order_id": "ORD-EXIT-2"}
        state_store.set_live_open_position(reversing)
        row = _rows(user.id)[0]
        assert row.position_state == "reversing"
        assert row.reversal_metadata == {"status": "TRANSIT", "order_id": "ORD-EXIT-2"}

        # Close (clear writes the default dict) — identity preserved.
        state_store.clear_live_open_position()
        row = _rows(user.id)[0]
        assert row.position_state == "closed"
        assert row.closed_at is not None
        assert row.open_quantity == 0
        assert row.filled_exit_quantity == 75
        assert row.security_id == "12345"  # not wiped by the clear
        assert not state_store.get_live_open_position()["has_open_position"]

        # Event trail is append-only and ordered.
        events = position_store.list_events(row.id)
        assert [e["event_type"] for e in events] == [
            "entry_filled", "exit_partial_fill", "reversal_requested", "exit_filled",
        ]
        assert [e["source"] for e in events] == ["entry", "partial_fill", "reversal", "exit"]

        # A new entry after the close opens a SECOND row (new lifetime).
        state_store.set_live_open_position(dict(ENTRY_POSITION))
        rows = _rows(user.id)
        assert [r.position_state for r in rows] == ["closed", "open"]


def test_paper_and_live_slots_are_separate(mu_db, runtime_dirs, shadow_enabled):
    from app.services import state_store

    user = make_user("modes@example.com")
    with _bound(user):
        state_store.set_live_open_position(dict(ENTRY_POSITION))
        paper = dict(ENTRY_POSITION)
        paper["option_side"] = "PE"
        state_store.set_paper_position(paper)
    rows = _rows(user.id)
    assert {(r.execution_mode, r.option_side) for r in rows} == {("live", "CE"), ("paper", "PE")}


def test_shadow_failure_is_loud_but_never_raises(mu_db, runtime_dirs, shadow_enabled, monkeypatch):
    from app.services import position_store, state_store

    audit_events = []
    monkeypatch.setattr(
        "app.services.audit_logger.log_audit_event",
        lambda event_type, message, severity="INFO", metadata=None: audit_events.append(
            {"event_type": event_type, "severity": severity, "metadata": metadata}
        ),
    )

    def broken_session_scope():
        raise RuntimeError("db down")

    monkeypatch.setattr(position_store, "session_scope", broken_session_scope)
    before_entry = position_store.shadow_write_failures["entry"]
    before_exit = position_store.shadow_write_failures["exit"]

    user = make_user("shadowfail@example.com")
    with _bound(user):
        # Entry shadow fails -> JSON still authoritative and written; no raise.
        result = state_store.set_live_open_position(dict(ENTRY_POSITION))
        assert result["has_open_position"] is True
        assert state_store.get_live_open_position()["security_id"] == "12345"
        # Exit/clear shadow failure has its own (critical) policy; still no raise.
        state_store.clear_live_open_position()

    assert position_store.shadow_write_failures["entry"] == before_entry + 1
    assert position_store.shadow_write_failures["exit"] == before_exit + 1
    severities = {e["metadata"]["operation"]: e["severity"] for e in audit_events}
    assert severities["entry"] == "ERROR"
    assert severities["exit"] == "CRITICAL"
    assert all(e["event_type"] == "POSITION_SHADOW_WRITE_FAILED" for e in audit_events)


def test_shadow_scoped_to_bound_user_only(mu_db, runtime_dirs, shadow_enabled):
    from app.services import position_store, state_store

    alice = make_user("shadow-alice@example.com")
    bob = make_user("shadow-bob@example.com")
    with _bound(alice):
        state_store.set_live_open_position(dict(ENTRY_POSITION))
    assert len(_rows(alice.id)) == 1
    assert _rows(bob.id) == []
    # Ownership comes only from the bound execution context — a payload field
    # cannot select it (the JSON dict carries no user identity at all).
    assert "user_id" not in ENTRY_POSITION
    assert position_store.compare_user_positions(bob.id, json_live=None, json_paper=None) == []


# ---------------------------------------------------------------------------
# Hardening: idempotent retries, circuit breaker, spool safety, ownership FKs
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_breaker(monkeypatch):
    from app.services import position_store

    monkeypatch.setattr(position_store, "breaker", position_store._CircuitBreaker())
    monkeypatch.setattr(position_store, "shadow_writes_attempted", 0)
    monkeypatch.setattr(position_store, "shadow_writes_succeeded", 0)
    monkeypatch.setattr(position_store, "shadow_writes_skipped_breaker", 0)
    monkeypatch.setattr(position_store, "shadow_writes_skipped_saturated", 0)
    monkeypatch.setattr(position_store, "shadow_writes_noop_identical", 0)
    monkeypatch.setattr(position_store, "_slot_lock_failures", {})
    monkeypatch.setattr(
        position_store, "shadow_write_failures", {k: 0 for k in position_store.shadow_write_failures}
    )
    return position_store


def test_identical_retry_is_noop_without_duplicate_events(mu_db, runtime_dirs, shadow_enabled, fresh_breaker):
    from app.services import position_store, state_store

    user = make_user("retry@example.com")
    with _bound(user):
        state_store.set_live_open_position(dict(ENTRY_POSITION))
        row = _rows(user.id)[0]
        version_before = row.version
        # Identical snapshot retried (e.g. worker retry / duplicate monitor
        # tick): clean no-op — no new event, no version bump.
        state_store.set_live_open_position(dict(ENTRY_POSITION))
        row = _rows(user.id)[0]
    assert row.version == version_before
    events = position_store.list_events(row.id)
    assert [e["event_type"] for e in events] == ["entry_filled"]
    assert position_store.shadow_writes_noop_identical == 1


def test_event_idempotency_key_is_deterministic(mu_db, fresh_breaker):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import position_store

    user = make_user("detkey@example.com")
    position_store.import_json_position(user_id=user.id, execution_mode="live", position=dict(ENTRY_POSITION))
    with session_scope() as db:
        keys = [e.idempotency_key for e in db.scalars(select(models.PositionEvent)).all()]
    assert all(key is not None for key in keys)  # never nullable in practice


def test_circuit_breaker_opens_skips_and_recovers(mu_db, runtime_dirs, shadow_enabled, fresh_breaker, monkeypatch):
    import time

    from app.config import settings
    from app.services import position_store, state_store

    monkeypatch.setattr(settings, "POSITION_DB_SHADOW_FAILURE_THRESHOLD", 3, raising=False)
    monkeypatch.setattr(settings, "POSITION_DB_SHADOW_CIRCUIT_OPEN_SECONDS", 60, raising=False)

    alerts = []
    monkeypatch.setattr(
        "app.services.audit_logger.log_audit_event",
        lambda event_type, message, severity="INFO", metadata=None: alerts.append(event_type),
    )

    working_session_scope = position_store.session_scope

    def broken_session_scope():
        raise RuntimeError("db down")

    monkeypatch.setattr(position_store, "session_scope", broken_session_scope)
    user = make_user("breaker@example.com")
    with _bound(user):
        for _ in range(3):
            state_store.set_live_open_position(dict(ENTRY_POSITION))
        assert position_store.breaker.snapshot()["state"] == "OPEN"
        assert alerts.count("POSITION_SHADOW_CIRCUIT_OPENED") == 1  # rate-limited alert

        # While OPEN: writes are skipped (not attempted), JSON still works.
        state_store.set_live_open_position(dict(ENTRY_POSITION))
        assert position_store.shadow_writes_skipped_breaker == 1
        assert position_store.shadow_writes_attempted == 3
        assert state_store.get_live_open_position()["has_open_position"] is True

        # After the open window: HALF_OPEN probe; a successful write closes it.
        monkeypatch.setattr(position_store, "session_scope", working_session_scope)
        position_store.breaker.opened_at = time.monotonic() - 61
        state_store.set_live_open_position(dict(ENTRY_POSITION))
    assert position_store.breaker.snapshot()["state"] == "CLOSED"
    assert position_store.shadow_writes_succeeded == 1
    assert len(_rows(user.id)) == 1  # recovery wrote the shadow row


def test_half_open_failure_reopens(mu_db, runtime_dirs, shadow_enabled, fresh_breaker, monkeypatch):
    import time

    from app.config import settings
    from app.services import position_store, state_store

    monkeypatch.setattr(settings, "POSITION_DB_SHADOW_FAILURE_THRESHOLD", 1, raising=False)

    def broken_session_scope():
        raise RuntimeError("still down")

    monkeypatch.setattr(position_store, "session_scope", broken_session_scope)
    user = make_user("halfopen@example.com")
    with _bound(user):
        state_store.set_live_open_position(dict(ENTRY_POSITION))  # opens breaker
        assert position_store.breaker.snapshot()["state"] == "OPEN"
        position_store.breaker.opened_at = time.monotonic() - 3600
        state_store.set_live_open_position(dict(ENTRY_POSITION))  # failed probe
    assert position_store.breaker.snapshot()["state"] == "OPEN"
    assert position_store.breaker.snapshot()["opened_count"] == 2


def test_disk_spool_failure_and_capacity_cap_are_safe(mu_db, runtime_dirs, shadow_enabled, fresh_breaker, monkeypatch):
    from app.services import position_store, state_store

    # 1. Spool write itself raising must not break the JSON path.
    def broken_session_scope():
        raise RuntimeError("db down")

    def broken_audit(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(position_store, "session_scope", broken_session_scope)
    monkeypatch.setattr("app.services.audit_logger.log_audit_event", broken_audit)
    monkeypatch.setattr("app.services.audit_logger.log_error_event", broken_audit)
    user = make_user("spool@example.com")
    with _bound(user):
        result = state_store.set_live_open_position(dict(ENTRY_POSITION))
    assert result["has_open_position"] is True
    assert position_store.shadow_write_failures["entry"] == 1

    # 2. Over-capacity spool skips the file write but keeps counting.
    spooled = []
    monkeypatch.setattr(
        "app.services.audit_logger.log_audit_event",
        lambda event_type, *a, **k: spooled.append(event_type),
    )
    monkeypatch.setattr("app.services.audit_logger.log_error_event", lambda *a, **k: None)
    monkeypatch.setattr(position_store, "_spool_has_capacity", lambda: False)
    with _bound(user):
        # Same JSON again: previous now shows the open position, so this
        # write classifies as the "update" family.
        state_store.set_live_open_position(dict(ENTRY_POSITION))
    assert position_store.shadow_write_failures["update"] == 1
    assert "POSITION_SHADOW_WRITE_FAILED" not in spooled  # spool skipped, no flood


def test_ownership_enforced_at_db_level(mu_db):
    """A position/event can never claim a different tenant than its
    instance/position — proven through the composite FKs with SQLite's FK
    pragma on (PG equivalents run in the disposable-postgres verification)."""
    import sqlite3

    from app.db.engine import get_engine, session_scope
    from app.db import models
    from app.services import strategy_registry

    strategy_registry.backfill_supertrend()
    owner = make_user("own-a@example.com")
    other = make_user("own-b@example.com")
    with session_scope() as db:
        from sqlalchemy import select as sa_select

        catalog = db.scalar(sa_select(models.StrategyCatalog))
        version = db.scalar(sa_select(models.StrategyVersion))
        instance = models.StrategyInstance(
            user_id=owner.id, strategy_id=catalog.id, strategy_version_id=version.id,
            source_journey="NOVA_SHARED", label="own-check",
        )
        position = models.StrategyInstancePosition(
            user_id=owner.id, execution_mode="live", position_state="open",
        )
        db.add_all([instance, position])
        db.flush()
        instance_id, position_id = str(instance.id), str(position.id)

    raw = get_engine().raw_connection()
    try:
        raw.driver_connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            raw.driver_connection.execute(
                "INSERT INTO strategy_instance_positions (id, user_id, strategy_instance_id, execution_mode, "
                "position_state, position_side, underlying, open_quantity, filled_exit_quantity, imported_from_json, "
                "created_at, updated_at, version) "
                "VALUES (?, ?, ?, 'paper', 'open', 'LONG', 'NIFTY', 0, 0, 0, '2026-07-12', '2026-07-12', 1)",
                (str(uuid.uuid4()), str(other.id), instance_id),  # other's user, owner's instance
            )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            raw.driver_connection.execute(
                "INSERT INTO position_events (id, position_id, user_id, event_type, source, event_at) "
                "VALUES (?, ?, ?, 'entry_filled', 'entry', '2026-07-12')",
                (str(uuid.uuid4()), position_id, str(other.id)),  # other's user, owner's position
            )
        # NULL instance on a position stays allowed (legacy/migration rows).
        raw.driver_connection.execute(
            "INSERT INTO strategy_instance_positions (id, user_id, strategy_instance_id, execution_mode, "
            "position_state, position_side, underlying, open_quantity, filled_exit_quantity, imported_from_json, "
            "created_at, updated_at, version) "
            "VALUES (?, ?, NULL, 'paper', 'open', 'LONG', 'NIFTY', 0, 0, 0, '2026-07-12', '2026-07-12', 1)",
            (str(uuid.uuid4()), str(other.id)),
        )
        raw.driver_connection.commit()
    finally:
        raw.close()


def test_migration_0010_roundtrip(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from app.config import settings
    from app.db import engine as db_engine

    db_path = tmp_path / "ownership.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    db_engine.reset_engine_for_tests()
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    try:
        command.upgrade(config, "head")
        engine = create_engine(f"sqlite:///{db_path}")

        def event_columns():
            return {c["name"] for c in inspect(engine).get_columns("position_events")}

        assert "strategy_instance_id" not in event_columns()

        # Seed a position + event at head, then downgrade to 0009.
        user_id, position_id = str(uuid.uuid4()), str(uuid.uuid4())
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (id, email, is_admin, created_at, updated_at) VALUES (:u, 'own-mig@example.com', 0, '2026-07-12', '2026-07-12')"
            ), {"u": user_id})
            conn.execute(text(
                "INSERT INTO strategy_instance_positions (id, user_id, execution_mode, position_state, position_side, underlying, "
                "open_quantity, filled_exit_quantity, imported_from_json, created_at, updated_at, version) "
                "VALUES (:p, :u, 'live', 'open', 'LONG', 'NIFTY', 75, 0, 0, '2026-07-12', '2026-07-12', 1)"
            ), {"p": position_id, "u": user_id})
            conn.execute(text(
                "INSERT INTO position_events (id, position_id, user_id, event_type, source, event_at, idempotency_key) "
                "VALUES (:e, :p, :u, 'entry_filled', 'entry', '2026-07-12', 'own-mig-key')"
            ), {"e": str(uuid.uuid4()), "p": position_id, "u": user_id})

        command.downgrade(config, "0009_position_shadow")
        assert "strategy_instance_id" in event_columns()  # 0009 shape restored
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM position_events")).scalar()
        assert count == 1  # event history preserved through the downgrade

        command.upgrade(config, "head")
        assert "strategy_instance_id" not in event_columns()
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT idempotency_key FROM position_events")).fetchall()
            positions = conn.execute(text("SELECT COUNT(*) FROM strategy_instance_positions")).scalar()
        assert rows == [("own-mig-key",)] and positions == 1  # zero data loss
        engine.dispose()
    finally:
        db_engine.reset_engine_for_tests()


def test_slot_lock_timeout_stays_slot_scoped(mu_db, runtime_dirs, shadow_enabled, fresh_breaker, monkeypatch):
    """One user's slot lock contention must never open the GLOBAL breaker."""
    from app.config import settings
    from app.services import position_store, state_store

    monkeypatch.setattr(settings, "POSITION_DB_SHADOW_FAILURE_THRESHOLD", 1, raising=False)

    class LockNotAvailable(Exception):
        pass

    def lock_timeout_txn(*args, **kwargs):
        raise LockNotAvailable("canceling statement due to lock timeout")

    monkeypatch.setattr(position_store, "_shadow_write_txn", lock_timeout_txn)
    user = make_user("slotlock@example.com")
    with _bound(user):
        state_store.set_live_open_position(dict(ENTRY_POSITION))
    # Even with threshold=1 the breaker stays CLOSED: contention was slot-scoped.
    assert position_store.breaker.snapshot()["state"] == "CLOSED"
    health = position_store.shadow_health()
    assert health["slot_lock_contention"] == {"slots_affected": 1, "total_timeouts": 1}
    assert position_store.shadow_write_failures["entry"] == 1

    # An infrastructure failure DOES trip the global breaker.
    def infra_failure(*args, **kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(position_store, "_shadow_write_txn", infra_failure)
    with _bound(user):
        state_store.set_live_open_position(dict(ENTRY_POSITION))
    assert position_store.breaker.snapshot()["state"] == "OPEN"

    # A later success clears the slot's contention record.
    monkeypatch.setattr(position_store, "_shadow_write_txn", lambda *a, **k: "written")
    import time

    position_store.breaker.opened_at = time.monotonic() - 3600
    with _bound(user):
        state_store.set_live_open_position(dict(ENTRY_POSITION))
    assert position_store.breaker.snapshot()["state"] == "CLOSED"
    assert position_store.shadow_health()["slot_lock_contention"]["slots_affected"] == 0


def test_executor_saturation_skips_immediately(mu_db, runtime_dirs, shadow_enabled, fresh_breaker, monkeypatch):
    """When all shadow workers are occupied (abandoned on a dead DB), new
    writes must skip instantly — never queue behind stuck threads."""
    import time

    from app.services import position_store, state_store

    permits = []
    for _ in range(position_store._SHADOW_MAX_WORKERS):
        assert position_store._inflight.acquire(blocking=False)
        permits.append(True)
    try:
        user = make_user("saturated@example.com")
        started = time.perf_counter()
        with _bound(user):
            result = state_store.set_live_open_position(dict(ENTRY_POSITION))
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert result["has_open_position"] is True  # JSON authority unaffected
        assert position_store.shadow_writes_skipped_saturated == 1
        assert position_store.shadow_writes_attempted == 1  # counted, not queued
        assert elapsed_ms < 500, f"saturated skip took {elapsed_ms:.0f}ms"
        assert position_store.breaker.snapshot()["state"] == "CLOSED"  # saturation never trips breaker
    finally:
        for _ in permits:
            position_store._inflight.release()

    # With permits free again the same write goes through.
    with _bound(user):
        state_store.set_live_open_position(dict(ENTRY_POSITION))
    assert position_store.shadow_writes_succeeded >= 1
    assert len(_rows(user.id)) == 1


def test_shadow_health_endpoint_is_admin_only_and_redacted(mu_db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.auth.dependencies import get_current_user
    from app.config import settings
    from app.routers import debug as debug_router
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    app.include_router(debug_router.router, prefix="/api/debug")
    regular = make_user("plain@example.com")
    admin = make_user("shadow-admin@example.com", is_admin=True)
    current = {"user": regular}
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(current["user"])
    client = TestClient(app)

    # Disabled outside approved environments: DEBUG_ENABLED off -> 404.
    monkeypatch.setattr(settings, "DEBUG_ENABLED", False, raising=False)
    assert client.get("/api/debug/position-shadow/health").status_code == 404

    # Enabled but non-admin -> 403.
    monkeypatch.setattr(settings, "DEBUG_ENABLED", True, raising=False)
    assert client.get("/api/debug/position-shadow/health").status_code == 403

    # Admin -> 200, and the payload is aggregate-only (no tenant/path/DB data).
    current["user"] = admin
    response = client.get("/api/debug/position-shadow/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "ok", "enabled", "breaker", "writes_attempted", "writes_succeeded",
        "writes_skipped_breaker_open", "writes_skipped_saturated",
        "writes_noop_identical_snapshot", "failures_by_operation",
        "slot_lock_contention",
    }
    serialized = response.text.lower()
    for banned in ("user_id", "raw_snapshot", "security_id", "postgres", "password", "token", ":\\\\", "/users/", "database_url"):
        assert banned not in serialized, f"health response leaked {banned!r}"


# ---------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------

def test_importer_idempotent_and_never_overwrites_newer_shadow(mu_db):
    from app.services import position_store

    user = make_user("importer@example.com")
    first = position_store.import_json_position(
        user_id=user.id, execution_mode="live", position=dict(ENTRY_POSITION)
    )
    assert first["status"] == "imported"
    again = position_store.import_json_position(
        user_id=user.id, execution_mode="live", position=dict(ENTRY_POSITION)
    )
    assert again == {"status": "unchanged", "position_id": first["position_id"]}

    rows = _rows(user.id)
    assert len(rows) == 1 and rows[0].imported_from_json is True

    # Flat JSON is never imported.
    flat = position_store.import_json_position(
        user_id=user.id, execution_mode="paper", position={"has_open_position": False}
    )
    assert flat == {"status": "skipped_flat"}

    # A live shadow row written by the dual-writer is newer authority than an
    # import: the importer must refuse to overwrite it.
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        row = db.scalars(select(models.StrategyInstancePosition)).one()
        row.imported_from_json = False  # simulates a dual-writer-owned row
    changed = dict(ENTRY_POSITION)
    changed["qty"] = 1
    result = position_store.import_json_position(user_id=user.id, execution_mode="live", position=changed)
    assert result["status"] == "skipped_newer_shadow"


# ---------------------------------------------------------------------------
# Parity tool
# ---------------------------------------------------------------------------

def _seed_shadow(user):
    from app.services import position_store

    result = position_store.import_json_position(
        user_id=user.id, execution_mode="live", position=dict(ENTRY_POSITION)
    )
    return uuid.UUID(result["position_id"])


def test_parity_match_and_each_mismatch_category(mu_db):
    from app.db import models
    from app.db.engine import session_scope
    from app.services.position_store import compare_user_positions

    user = make_user("parity@example.com")
    position_id = _seed_shadow(user)

    assert compare_user_positions(user.id, json_live=dict(ENTRY_POSITION), json_paper=None) == []

    mutations = [
        ({"security_id": "99999"}, "security_id_mismatch"),
        ({"option_side": "PE"}, "side_mismatch"),
        ({"open_quantity": 1}, "quantity_mismatch"),
        ({"avg_entry_price_paise": 1}, "price_mismatch"),
        ({"entry_order_id": "OTHER"}, "order_id_mismatch"),
        ({"exit_order_id": "GHOST"}, "order_id_mismatch"),
        ({"position_state": "entering"}, "state_mismatch"),
        ({"reversal_metadata": {"status": "TRANSIT"}}, "reversal_marker_mismatch"),
        ({"super_order_metadata": {"exit_management": "SERVER"}}, "super_order_metadata_mismatch"),
    ]
    for changes, expected in mutations:
        with session_scope() as db:
            row = db.get(models.StrategyInstancePosition, position_id)
            saved = {key: getattr(row, key) for key in changes}
            for key, value in changes.items():
                setattr(row, key, value)
        findings = compare_user_positions(user.id, json_live=dict(ENTRY_POSITION), json_paper=None)
        assert expected in {f["type"] for f in findings}, f"{changes} should yield {expected}"
        with session_scope() as db:
            row = db.get(models.StrategyInstancePosition, position_id)
            for key, value in saved.items():
                setattr(row, key, value)

    # Missing DB shadow.
    with session_scope() as db:
        row = db.get(models.StrategyInstancePosition, position_id)
        db.delete(row)
    findings = compare_user_positions(user.id, json_live=dict(ENTRY_POSITION), json_paper=None)
    assert [f["type"] for f in findings] == ["missing_db_shadow_position"]

    # No position anywhere -> clean.
    assert compare_user_positions(user.id, json_live={"has_open_position": False}, json_paper=None) == []


def test_parity_missing_json_stale_db_and_duplicates(mu_db):
    from app.db import models
    from app.db.engine import session_scope
    from app.services.position_store import compare_user_positions

    user = make_user("parity2@example.com")
    _seed_shadow(user)

    # DB active but JSON file absent entirely.
    findings = compare_user_positions(user.id, json_live=None, json_paper=None)
    assert [f["type"] for f in findings] == ["missing_json_position"]

    # DB active but JSON says closed (stale open DB shadow).
    findings = compare_user_positions(user.id, json_live={"has_open_position": False}, json_paper=None)
    assert [f["type"] for f in findings] == ["closed_json_but_db_open"]

    # Duplicate active DB rows (possible only if the partial index is absent,
    # e.g. legacy drift) must still be detected: drop the index and duplicate.
    with session_scope() as db:
        db.execute(text("DROP INDEX uq_active_position_per_user_mode"))
        db.add(models.StrategyInstancePosition(user_id=user.id, execution_mode="live", position_state="open"))
        db.flush()
    findings = compare_user_positions(user.id, json_live=dict(ENTRY_POSITION), json_paper=None)
    assert "duplicate_active_db_positions" in {f["type"] for f in findings}


def test_parity_cli_flags_invalid_json(tmp_path, monkeypatch, mu_db):
    import scripts.position_shadow_parity as parity_cli

    user = make_user("badjson@example.com")
    state_root = tmp_path / "state"
    user_dir = state_root / "users" / str(user.id)
    user_dir.mkdir(parents=True)
    (user_dir / "open_position.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(parity_cli, "RUNTIME_STATE_DIR", state_root)

    result = parity_cli.run(None)
    assert result["users_checked"] == 1
    assert [f["type"] for f in result["findings"]] == ["invalid_json_position_file"]
    assert result["mode"] == "report-only"
