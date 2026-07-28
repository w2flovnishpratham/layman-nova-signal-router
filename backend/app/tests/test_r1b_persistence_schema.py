"""R1B-1 additive evidence schema: migration 0015 and database constraints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.tests.conftest_multiuser import mu_db  # noqa: F401

BACKEND_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 18, 9, 30, tzinfo=timezone.utc)
EVIDENCE_TABLES = (
    "canonical_signal_decisions",
    "canonical_signal_outcomes",
    "strategy_signal_rejections",
    "pine_semantic_analyses",
)
# Existing tables that must not gain a column and whose rows must stay
# readable across migration cycles.
GUARDED_TABLES = (
    "strategy_signals",
    "strategy_execution_jobs",
    "webhook_events",
    "strategy_source_artifacts",
    "strategy_instances",
    "strategy_instance_positions",
    "position_events",
)


# ---------------------------------------------------------------------------
# Alembic metadata and SQLite migration cycle
# ---------------------------------------------------------------------------


def _alembic_config():
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return config


def test_migration_metadata_is_exact():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    r1b = script.get_revision("0015_r1b_persistence")
    assert script.get_heads() == ["0020_engine_start_entry"]
    assert r1b.down_revision == "0014_verify_select"
    assert len(r1b.revision) == 20
    assert len(r1b.revision) <= 32


def test_migration_adds_no_column_no_backfill_and_cycles_on_sqlite(tmp_path, monkeypatch):
    from alembic import command

    from app.config import settings
    from app.db import engine as eng

    db_path = tmp_path / "r1b_migration.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    eng.reset_engine_for_tests()
    config = _alembic_config()

    # Exercise the R1B migration at its own revision, independent of newer
    # product migrations that may extend the single Alembic head.
    command.upgrade(config, "0015_r1b_persistence")
    engine = eng.get_engine()

    def _columns():
        inspector = sa.inspect(engine)
        return {table: {col["name"] for col in inspector.get_columns(table)} for table in GUARDED_TABLES}

    def _tables():
        return set(sa.inspect(engine).get_table_names())

    assert set(EVIDENCE_TABLES) <= _tables()
    with engine.connect() as conn:
        for table in EVIDENCE_TABLES:
            assert conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() == 0, table

    # Seed representative existing rows that must survive every cycle.
    signal_id, job_id = uuid.uuid4(), uuid.uuid4()
    user_id, event_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO users (id, email, is_admin, created_at, updated_at)"
            " VALUES (:id, :email, 0, :now, :now)"
        ), {"id": str(user_id), "email": "r1b@example.com", "now": NOW.isoformat()})
        conn.execute(sa.text(
            "INSERT INTO strategy_signals (id, strategy_name, signal_id, status, created_at, updated_at)"
            " VALUES (:id, 'r1b-strategy', 'sig-1', 'accepted', :now, :now)"
        ), {"id": str(signal_id), "now": NOW.isoformat()})
        conn.execute(sa.text(
            "INSERT INTO strategy_execution_jobs (id, strategy_signal_id, user_id, strategy_name, signal_id,"
            " signal_payload, lots, execution_mode, status, attempts, max_attempts, available_at, created_at, updated_at)"
            " VALUES (:id, :signal, :user, 'r1b-strategy', 'sig-1', '{}', 1, 'signal_only', 'queued', 0, 2, :now, :now, :now)"
        ), {"id": str(job_id), "signal": str(signal_id), "user": str(user_id), "now": NOW.isoformat()})
        conn.execute(sa.text(
            "INSERT INTO webhook_events (id, provider, event_id, raw_body_sha256, signature_ok,"
            " replay_status, processed_status, received_at, updated_at)"
            " VALUES (:id, 'tradingview', :event, :hash, 1, 'fresh', 'received', :now, :now)"
        ), {"id": str(event_id), "event": "evt-1", "hash": "a" * 64, "now": NOW.isoformat()})

    def _existing_rows_readable(conn):
        assert conn.execute(sa.text("SELECT status FROM strategy_signals WHERE id = :id"), {"id": str(signal_id)}).scalar() == "accepted"
        assert conn.execute(sa.text("SELECT status FROM strategy_execution_jobs WHERE id = :id"), {"id": str(job_id)}).scalar() == "queued"
        assert conn.execute(sa.text("SELECT provider FROM webhook_events WHERE id = :id"), {"id": str(event_id)}).scalar() == "tradingview"

    columns_at_head = _columns()

    # Downgrade one step: exactly the four evidence tables disappear.
    command.downgrade(config, "-1")
    assert not set(EVIDENCE_TABLES) & _tables()
    assert _columns() == columns_at_head
    with engine.connect() as conn:
        _existing_rows_readable(conn)

    # Upgrade again: real op.create_table DDL path (tables were dropped).
    command.upgrade(config, "0015_r1b_persistence")
    assert set(EVIDENCE_TABLES) <= _tables()
    assert _columns() == columns_at_head, "0015 must not add columns to existing tables"
    inspector = sa.inspect(engine)
    index_names = {
        table: {index["name"] for index in inspector.get_indexes(table)} for table in EVIDENCE_TABLES
    }
    assert {"ix_canonical_decisions_user_received", "ix_canonical_decisions_instance_received",
            "ix_canonical_decisions_webhook_event"} <= index_names["canonical_signal_decisions"]
    assert {"ix_canonical_outcomes_decision_created", "ix_canonical_outcomes_execution_job"} <= index_names["canonical_signal_outcomes"]
    assert {"ix_signal_rejections_instance_received", "ix_signal_rejections_user_received",
            "ix_signal_rejections_stage_code_received", "ix_signal_rejections_webhook_event"} <= index_names["strategy_signal_rejections"]
    assert {"ix_pine_analyses_artifact_created", "ix_pine_analyses_level",
            "ix_pine_analyses_supersedes"} <= index_names["pine_semantic_analyses"]
    unique_names = {
        constraint["name"]
        for table in EVIDENCE_TABLES
        for constraint in inspector.get_unique_constraints(table)
    }
    assert {"uq_canonical_decision_signal", "uq_canonical_outcome_idempotency",
            "uq_signal_rejection_dedupe", "uq_pine_analysis_provenance"} <= unique_names
    with engine.connect() as conn:
        _existing_rows_readable(conn)
        for table in EVIDENCE_TABLES:
            assert conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() == 0, table

    eng.reset_engine_for_tests()


def test_migration_source_never_touches_existing_tables():
    source = (BACKEND_ROOT / "alembic" / "versions" / "20260718_0015_r1b_persistence.py").read_text(encoding="utf-8")
    for forbidden in ("add_column", "alter_column", "drop_column", "execute(", "INSERT", "UPDATE ", "DELETE FROM", "bulk_insert"):
        assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# ORM-level constraint behavior (SQLite via create_all, FK pragma enabled)
# ---------------------------------------------------------------------------


def _seed(db):
    from app.db import models

    user = models.User(email=f"r1b-{uuid.uuid4().hex[:10]}@example.com")
    db.add(user)
    db.flush()
    strategy = models.StrategyCatalog(
        code=f"r1b-{uuid.uuid4().hex[:10]}", display_name="R1B", owner_type="personal",
        owner_user_id=user.id, visibility="private", status="active",
    )
    db.add(strategy)
    db.flush()
    version = models.StrategyVersion(
        strategy_id=strategy.id, version="1.0", payload_spec_version="nova.v1",
        source_journey="personal_tradingview", status="approved", execution_kind="external_webhook",
    )
    db.add(version)
    db.flush()
    artifact = models.StrategySourceArtifact(
        strategy_version_id=version.id, artifact_type="pine_script",
        content='//@version=6\nindicator("r1b")\nplot(close)\n',
        content_sha256="c" * 64,
    )
    instance = models.StrategyInstance(
        user_id=user.id, strategy_id=strategy.id, strategy_version_id=version.id,
        source_journey="PERSONAL_TRADINGVIEW", label=f"L-{uuid.uuid4().hex[:8]}",
    )
    signal = models.StrategySignal(strategy_name=f"instance:{uuid.uuid4()}", signal_id="sig-1")
    event = models.WebhookEvent(provider="tradingview", event_id=f"evt-{uuid.uuid4().hex[:10]}", raw_body_sha256="a" * 64)
    db.add_all([artifact, instance, signal, event])
    db.flush()
    job = models.StrategyExecutionJob(
        strategy_signal_id=signal.id, user_id=user.id, strategy_name=signal.strategy_name,
        signal_id="sig-1", signal_payload={}, lots=1, execution_mode="signal_only",
    )
    db.add(job)
    db.flush()
    return user, strategy, version, artifact, instance, signal, event, job


def _decision_values(user, instance, signal, event, **overrides):
    values = {
        "strategy_signal_id": signal.id,
        "webhook_event_id": event.id,
        "user_id": user.id,
        "strategy_instance_id": instance.id,
        "contract_version": "nova.canonical.v1",
        "adapter_version": "nova.legacy-adapter.v1",
        "wire_action": "BUY_CE",
        "event_type": "STRATEGY_SIGNAL",
        "desired_state": "BULLISH",
        "intent_reason": "DIRECTIONAL_SIGNAL",
        "signal_time": NOW,
        "received_at": NOW,
        "compatibility_action": "ENTRY",
        "compatibility_side": "BUY",
        "compatibility_option_side": "CE",
        "source": "private_webhook",
        "payload_fingerprint": "f" * 64,
        "provenance_kind": "LIVE",
    }
    values.update(overrides)
    return values


@pytest.fixture
def fk_session(mu_db):  # noqa: F811
    from app.db.engine import session_scope

    with session_scope() as db:
        db.execute(sa.text("PRAGMA foreign_keys=ON"))
        yield db


def _rejects(db, row):
    # Savepoint-scoped so a rejected row never rolls back the seeded fixture data.
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(row)


def test_decision_uniqueness_and_consistency_constraints(fk_session):
    from app.db import models

    db = fk_session
    user, _, _, _, instance, signal, event, _ = _seed(db)
    db.add(models.CanonicalSignalDecision(**_decision_values(user, instance, signal, event)))
    db.flush()

    # One decision per StrategySignal.
    _rejects(db, models.CanonicalSignalDecision(**_decision_values(
        user, instance, signal, event, wire_action="EXIT", desired_state="FLAT",
        intent_reason="EXPLICIT_EXIT", compatibility_action="EXIT",
        compatibility_side="SELL", compatibility_option_side=None,
    )))

    # Owner/instance composite FK: another user cannot claim this instance.
    _seedable = _seed(db)
    other_user, other_signal = _seedable[0], _seedable[5]
    _rejects(db, models.CanonicalSignalDecision(**_decision_values(
        other_user, instance, other_signal, event,
    )))


@pytest.mark.parametrize(
    "overrides",
    [
        # HOLD consistency violations.
        {"wire_action": "HOLD", "event_type": "STRATEGY_SIGNAL", "desired_state": "NONE",
         "intent_reason": "CONNECTIVITY_TEST", "compatibility_action": None,
         "compatibility_side": None, "compatibility_option_side": None},
        {"wire_action": "HOLD", "event_type": "CONNECTIVITY_TEST", "desired_state": "NONE",
         "intent_reason": "CONNECTIVITY_TEST", "compatibility_action": "ENTRY",
         "compatibility_side": None, "compatibility_option_side": None},
        # BUY_CE consistency violations.
        {"desired_state": "BEARISH"},
        {"compatibility_option_side": "PE"},
        {"compatibility_side": "SELL"},
        # BUY_PE consistency violations.
        {"wire_action": "BUY_PE", "desired_state": "BULLISH", "compatibility_option_side": "PE"},
        {"wire_action": "BUY_PE", "desired_state": "BEARISH", "compatibility_option_side": "CE"},
        # EXIT consistency violations.
        {"wire_action": "EXIT", "desired_state": "FLAT", "compatibility_action": "EXIT",
         "compatibility_side": "SELL", "compatibility_option_side": "CE"},
        {"wire_action": "EXIT", "desired_state": "BULLISH", "compatibility_action": "EXIT",
         "compatibility_side": "SELL", "compatibility_option_side": None},
        # Closed-value violations.
        {"wire_action": "SELL_CE"},
        {"event_type": "OTHER"},
        {"desired_state": "SIDEWAYS"},
        {"intent_reason": "GUESS"},
        {"provenance_kind": "IMPORT"},
        # Hash-shape violations.
        {"payload_fingerprint": "F" * 64},
        {"payload_fingerprint": "f" * 63},
    ],
)
def test_decision_check_constraints_reject_invalid_rows(fk_session, overrides):
    from app.db import models

    db = fk_session
    user, _, _, _, instance, signal, event, _ = _seed(db)
    _rejects(db, models.CanonicalSignalDecision(**_decision_values(user, instance, signal, event, **overrides)))


def test_decision_valid_action_shapes_are_accepted(fk_session):
    from app.db import models

    db = fk_session
    for overrides in (
        {},
        {"wire_action": "BUY_PE", "desired_state": "BEARISH", "compatibility_option_side": "PE"},
        {"wire_action": "EXIT", "desired_state": "FLAT", "intent_reason": "EXPLICIT_EXIT",
         "compatibility_action": "EXIT", "compatibility_side": "SELL", "compatibility_option_side": None},
        {"wire_action": "HOLD", "event_type": "CONNECTIVITY_TEST", "desired_state": "NONE",
         "intent_reason": "CONNECTIVITY_TEST", "compatibility_action": None,
         "compatibility_side": None, "compatibility_option_side": None},
    ):
        user, _, _, _, instance, signal, event, _ = _seed(db)
        db.add(models.CanonicalSignalDecision(**_decision_values(user, instance, signal, event, **overrides)))
        db.flush()


def _outcome_values(decision, job, **overrides):
    values = {
        "canonical_decision_id": decision.id,
        "execution_job_id": job.id,
        "attempt": 1,
        "phase": "ROUTING_COMPLETED",
        "current_state": "FLAT",
        "routing_result": "ENTRY_ROUTED",
        "no_op_reason": None,
        "exit_reason": None,
        "safe_detail_code": None,
        "result_sha256": "d" * 64,
        "idempotency_key": uuid.uuid4().hex + uuid.uuid4().hex,
        "created_at": NOW,
    }
    values.update(overrides)
    return values


def test_outcome_constraints(fk_session):
    from app.db import models

    db = fk_session
    user, _, _, _, instance, signal, event, job = _seed(db)
    decision = models.CanonicalSignalDecision(**_decision_values(user, instance, signal, event))
    db.add(decision)
    db.flush()

    accepted = models.CanonicalSignalOutcome(**_outcome_values(decision, job))
    db.add(accepted)
    db.flush()

    # Idempotency uniqueness.
    _rejects(db, models.CanonicalSignalOutcome(**_outcome_values(
        decision, job, idempotency_key=accepted.idempotency_key,
    )))
    # Positive attempt.
    _rejects(db, models.CanonicalSignalOutcome(**_outcome_values(decision, job, attempt=0)))
    _rejects(db, models.CanonicalSignalOutcome(**_outcome_values(decision, job, attempt=-3)))
    # Closed values.
    _rejects(db, models.CanonicalSignalOutcome(**_outcome_values(decision, job, phase="STARTED")))
    _rejects(db, models.CanonicalSignalOutcome(**_outcome_values(decision, job, current_state="LONG")))
    _rejects(db, models.CanonicalSignalOutcome(**_outcome_values(decision, job, routing_result="MAYBE")))
    _rejects(db, models.CanonicalSignalOutcome(**_outcome_values(decision, job, no_op_reason="BUSY")))
    _rejects(db, models.CanonicalSignalOutcome(**_outcome_values(decision, job, exit_reason="OTHER")))
    # Hash shapes.
    _rejects(db, models.CanonicalSignalOutcome(**_outcome_values(decision, job, result_sha256="X" * 64)))
    _rejects(db, models.CanonicalSignalOutcome(**_outcome_values(decision, job, idempotency_key="short")))


def _rejection_values(user, instance, event, **overrides):
    values = {
        "user_id": user.id,
        "strategy_instance_id": instance.id,
        "webhook_event_id": event.id,
        "signal_id_safe": "sig-1",
        "signal_id_sha256": "a" * 64,
        "payload_fingerprint": "b" * 64,
        "stage": "LIFECYCLE",
        "rejection_code": "INACTIVE_INSTANCE",
        "safe_detail": "canonical-evidence-unavailable",
        "adapter_version": "nova.legacy-adapter.v1",
        "contract_version": "nova.canonical.v1",
        "dedupe_key": uuid.uuid4().hex + uuid.uuid4().hex,
        "received_at": NOW,
        "created_at": NOW,
    }
    values.update(overrides)
    return values


def test_rejection_constraints(fk_session):
    from app.db import models

    db = fk_session
    user, _, _, _, instance, _, event, _ = _seed(db)
    accepted = models.StrategySignalRejection(**_rejection_values(user, instance, event))
    db.add(accepted)
    db.flush()

    _rejects(db, models.StrategySignalRejection(**_rejection_values(
        user, instance, event, dedupe_key=accepted.dedupe_key,
    )))
    _rejects(db, models.StrategySignalRejection(**_rejection_values(user, instance, event, stage="OTHER")))
    _rejects(db, models.StrategySignalRejection(**_rejection_values(user, instance, event, rejection_code="BAD_LUCK")))
    _rejects(db, models.StrategySignalRejection(**_rejection_values(user, instance, event, signal_id_sha256="a" * 63)))
    _rejects(db, models.StrategySignalRejection(**_rejection_values(user, instance, event, payload_fingerprint="B" * 64)))
    _rejects(db, models.StrategySignalRejection(**_rejection_values(user, instance, event, dedupe_key="nothex")))
    # Cross-owner composite FK.
    other = _seed(db)
    _rejects(db, models.StrategySignalRejection(**_rejection_values(other[0], instance, event)))


def _analysis_values(artifact, **overrides):
    values = {
        "source_artifact_id": artifact.id,
        "source_sha256": "c" * 64,
        "analyzer_version": "nova.pine-semantic-preanalyzer.v1",
        "registry_id": "nova.pine-capabilities",
        "registry_version": "v1",
        "registry_sha256": "e" * 64,
        "analysis_schema_version": "nova.pine-semantic-analysis-persistence.v1",
        "effective_capability_level": "L2_SUPPORTED_WITH_DISCLOSED_CHANGE",
        "confidence": "HIGH_CONFIDENCE_MATCH",
        "analysis_payload": {"matched_capabilities": [], "temporal_classes": [], "blocker_codes": [],
                             "disclosure_codes": [], "admin_review_points": []},
        "analysis_payload_sha256": "9" * 64,
        "supersedes_analysis_id": None,
        "created_at": NOW,
    }
    values.update(overrides)
    return values


def test_analysis_constraints(fk_session):
    from app.db import models

    db = fk_session
    _, _, _, artifact, _, _, _, _ = _seed(db)
    first = models.PineSemanticAnalysis(**_analysis_values(artifact))
    db.add(first)
    db.flush()

    # Exact provenance tuple uniqueness.
    _rejects(db, models.PineSemanticAnalysis(**_analysis_values(artifact)))
    # A different tuple member is a different row.
    db.add(models.PineSemanticAnalysis(**_analysis_values(artifact, registry_sha256="f" * 64)))
    db.flush()
    # Hash shapes and closed values.
    _rejects(db, models.PineSemanticAnalysis(**_analysis_values(artifact, source_sha256="C" * 64)))
    _rejects(db, models.PineSemanticAnalysis(**_analysis_values(artifact, registry_sha256="e" * 63, analyzer_version="other")))
    _rejects(db, models.PineSemanticAnalysis(**_analysis_values(artifact, analysis_payload_sha256="9" * 63, analyzer_version="other2")))
    _rejects(db, models.PineSemanticAnalysis(**_analysis_values(artifact, confidence="CERTAIN", analyzer_version="other3")))
    _rejects(db, models.PineSemanticAnalysis(**_analysis_values(artifact, effective_capability_level="L5", analyzer_version="other4")))


def test_deletion_behavior(fk_session):
    from app.db import models

    db = fk_session
    user, _, _, artifact, instance, signal, event, job = _seed(db)
    decision = models.CanonicalSignalDecision(**_decision_values(user, instance, signal, event))
    db.add(decision)
    db.flush()
    outcome = models.CanonicalSignalOutcome(**_outcome_values(decision, job))
    rejection = models.StrategySignalRejection(**_rejection_values(user, instance, event))
    first_analysis = models.PineSemanticAnalysis(**_analysis_values(artifact))
    db.add_all([outcome, rejection, first_analysis])
    db.flush()
    second_analysis = models.PineSemanticAnalysis(**_analysis_values(
        artifact, analyzer_version="nova.pine-semantic-preanalyzer.v2",
        supersedes_analysis_id=first_analysis.id,
    ))
    db.add(second_analysis)
    db.flush()
    decision_id, outcome_id = decision.id, outcome.id
    rejection_id = rejection.id
    first_id, second_id = first_analysis.id, second_analysis.id

    # WebhookEvent delete → decision/rejection references become NULL.
    db.execute(sa.text("DELETE FROM webhook_events WHERE id = :id"), {"id": str(event.id)})
    db.expire_all()
    assert db.get(models.CanonicalSignalDecision, decision_id).webhook_event_id is None
    assert db.get(models.StrategySignalRejection, rejection_id).webhook_event_id is None

    # Superseded analysis delete → child pointer becomes NULL.
    db.execute(sa.text("DELETE FROM pine_semantic_analyses WHERE id = :id"), {"id": str(first_id)})
    db.expire_all()
    assert db.get(models.PineSemanticAnalysis, second_id).supersedes_analysis_id is None

    # Source artifact delete → remaining analyses cascade away.
    db.execute(sa.text("DELETE FROM strategy_source_artifacts WHERE id = :id"), {"id": str(artifact.id)})
    db.expire_all()
    assert db.get(models.PineSemanticAnalysis, second_id) is None

    # StrategySignal delete → decision cascades → outcomes cascade.
    db.execute(sa.text("DELETE FROM strategy_signals WHERE id = :id"), {"id": str(signal.id)})
    db.expire_all()
    assert db.get(models.CanonicalSignalDecision, decision_id) is None
    assert db.get(models.CanonicalSignalOutcome, outcome_id) is None


def test_evidence_rows_are_immutable_via_orm(mu_db):  # noqa: F811
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        user, _, _, artifact, instance, signal, event, job = _seed(db)
        decision = models.CanonicalSignalDecision(**_decision_values(user, instance, signal, event))
        db.add(decision)
        db.flush()
        outcome = models.CanonicalSignalOutcome(**_outcome_values(decision, job))
        rejection = models.StrategySignalRejection(**_rejection_values(user, instance, event))
        analysis = models.PineSemanticAnalysis(**_analysis_values(artifact))
        db.add_all([outcome, rejection, analysis])
        db.flush()
        targets = [
            ("CanonicalSignalDecision", decision.id, "wire_action", "EXIT"),
            ("CanonicalSignalOutcome", outcome.id, "routing_result", "FAILED"),
            ("StrategySignalRejection", rejection.id, "rejection_code", "STALE_SIGNAL"),
            ("PineSemanticAnalysis", analysis.id, "confidence", "PARTIAL_MATCH"),
        ]

    for model_name, row_id, field, value in targets:
        with (
            pytest.raises(ValueError, match="immutable R1B evidence"),
            session_scope() as db,
        ):
            row = db.get(getattr(models, model_name), row_id)
            setattr(row, field, value)
        # The rejected update never reached the database.
        with session_scope() as db:
            row = db.get(getattr(models, model_name), row_id)
            assert getattr(row, field) != value
