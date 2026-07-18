"""R1B-2A insert-only canonical outcome writer."""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import func, select

from app.tests.conftest_multiuser import mu_db  # noqa: F401
from app.tests.test_canonical_signal_decision_persistence import (  # noqa: F401
    NOW,
    _RaceSession,
    persist as persist_decision,
    seed_ingress,
)


@pytest.fixture
def db(mu_db):  # noqa: F811
    from app.db.engine import session_scope

    with session_scope() as session:
        yield session


@pytest.fixture
def outcome_flag_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "R1B_CANONICAL_OUTCOME_PERSISTENCE", True, raising=False)


@pytest.fixture
def decision_ctx(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False)
    ctx = seed_ingress(db)
    ctx["decision"] = persist_decision(db, ctx)
    db.flush()
    return ctx


def persist(db, ctx, **overrides):
    from app.services.canonical_signal_outcome_persistence import (
        persist_canonical_signal_outcome,
    )

    values = dict(
        decision=ctx["decision"],
        phase="ROUTING_COMPLETED",
        routing_result="ENTRY_ROUTED",
        current_state="FLAT",
        execution_job=ctx["job"],
        attempt=1,
        safe_detail="ENTRY_TRADED",
    )
    values.update(overrides)
    return persist_canonical_signal_outcome(db, **values)


def outcome_count(db) -> int:
    from app.db import models

    return db.scalar(select(func.count()).select_from(models.CanonicalSignalOutcome))


def test_flag_off_refuses_before_any_session_access(db, decision_ctx):
    from app.config import settings
    from app.services.canonical_signal_outcome_persistence import (
        CanonicalOutcomePersistenceError,
    )

    assert settings.R1B_CANONICAL_OUTCOME_PERSISTENCE is False

    class _Bomb:
        def __getattr__(self, name):
            raise AssertionError(f"session accessed: {name}")

    with pytest.raises(CanonicalOutcomePersistenceError) as excinfo:
        persist(_Bomb(), decision_ctx)
    assert excinfo.value.code == "PERSISTENCE_DISABLED"
    assert outcome_count(db) == 0


def test_valid_insert_and_idempotent_reuse(db, decision_ctx, outcome_flag_on):
    first = persist(db, decision_ctx)
    db.flush()  # satisfies every DB CHECK
    second = persist(db, decision_ctx)
    assert first.id == second.id
    assert outcome_count(db) == 1
    assert first.idempotency_key == second.idempotency_key
    assert len(first.idempotency_key) == 64
    assert first.result_sha256 is None


def test_key_distinguishes_phase_attempt_result_detail_and_job(db, decision_ctx, outcome_flag_on):
    base = persist(db, decision_ctx)
    variants = [
        persist(db, decision_ctx, phase="ROUTING_FAILED", routing_result="FAILED", safe_detail="ENTRY_REJECTED"),
        persist(db, decision_ctx, attempt=2),
        persist(db, decision_ctx, routing_result="PENDING", safe_detail="ENTRY_PENDING"),
        persist(db, decision_ctx, safe_detail="ENTRY_SUBMITTED"),
        persist(db, decision_ctx, execution_job=None, attempt=None,
                phase="STATE_EVALUATED", routing_result="STATE_NO_OP",
                no_op_reason="ALREADY_BULLISH", safe_detail="ALREADY_IN_CE"),
    ]
    ids = {base.id, *(row.id for row in variants)}
    assert len(ids) == 6
    assert outcome_count(db) == 6


def test_reversal_sequence_creates_ordered_distinct_rows(db, decision_ctx, outcome_flag_on):
    started = persist(
        db, decision_ctx, phase="ROUTING_COMPLETED", routing_result="REVERSAL_ROUTED",
        exit_reason="REVERSAL_EXIT", safe_detail="REVERSAL_EXIT_STARTED",
    )
    completed = persist(
        db, decision_ctx, phase="ROUTING_COMPLETED", routing_result="REVERSAL_ROUTED",
        exit_reason="REVERSAL_EXIT", safe_detail="REVERSAL_COMPLETED",
    )
    assert started.id != completed.id
    assert outcome_count(db) == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"phase": "INGRESS"},
        {"routing_result": "MAYBE"},
        {"current_state": "LONG"},
        {"no_op_reason": "BUSY"},
        {"exit_reason": "OTHER"},
        {"safe_detail": "free text detail"},
        {"attempt": 0},
        {"attempt": -1},
        {"attempt": True},
        {"decision": "not-a-decision"},
    ],
)
def test_closed_input_violations_fail_closed(db, decision_ctx, outcome_flag_on, overrides):
    from app.services.canonical_signal_outcome_persistence import (
        CanonicalOutcomePersistenceError,
    )

    with pytest.raises(CanonicalOutcomePersistenceError) as excinfo:
        persist(db, decision_ctx, **overrides)
    assert excinfo.value.code == "INVALID_INPUT"
    assert outcome_count(db) == 0


def test_foreign_job_reference_rejected(db, decision_ctx, outcome_flag_on):
    from app.services.canonical_signal_outcome_persistence import (
        CanonicalOutcomePersistenceError,
    )

    foreign = seed_ingress(db, signal_id="sig-foreign")
    with pytest.raises(CanonicalOutcomePersistenceError) as excinfo:
        persist(db, decision_ctx, execution_job=foreign["job"])
    assert excinfo.value.code == "FOREIGN_REFERENCE"
    assert outcome_count(db) == 0


def test_absent_job_reference_allowed_for_connectivity(db, decision_ctx, outcome_flag_on):
    row = persist(
        db, decision_ctx, execution_job=None, attempt=None,
        phase="STATE_EVALUATED", routing_result="CONNECTIVITY_NO_JOB",
        current_state=None, no_op_reason="CONNECTIVITY_TEST", safe_detail="HOLD",
    )
    db.flush()
    assert row.execution_job_id is None


def test_concurrent_identical_inserts_create_one_row(db, decision_ctx, outcome_flag_on):
    first = persist(db, decision_ctx)
    winner = persist(_RaceSession(db), decision_ctx)
    assert winner.id == first.id
    assert outcome_count(db) == 1


def test_unrelated_integrity_error_is_not_idempotency(mu_db, monkeypatch):  # noqa: F811
    """A foreign-key violation inside the insert must surface as
    PERSISTENCE_UNAVAILABLE, never as a phantom idempotent success."""
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.services.canonical_signal_outcome_persistence import (
        CanonicalOutcomePersistenceError,
    )

    monkeypatch.setattr(settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False)
    monkeypatch.setattr(settings, "R1B_CANONICAL_OUTCOME_PERSISTENCE", True, raising=False)
    with session_scope() as db:
        db.execute(sa.text("PRAGMA foreign_keys=ON"))
        ctx = seed_ingress(db)
        decision = persist_decision(db, ctx)
        db.flush()
        # Delete the decision row underneath the detached-looking object.
        db.execute(sa.text("DELETE FROM canonical_signal_decisions WHERE id = :i"), {"i": str(decision.id)})
        db.expunge(decision)
        with pytest.raises(CanonicalOutcomePersistenceError) as excinfo:
            persist(db, {"decision": decision, "job": ctx["job"]})
        assert excinfo.value.code == "PERSISTENCE_UNAVAILABLE"


def test_caller_transaction_stays_caller_controlled(mu_db, monkeypatch):  # noqa: F811
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope

    monkeypatch.setattr(settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False)
    monkeypatch.setattr(settings, "R1B_CANONICAL_OUTCOME_PERSISTENCE", True, raising=False)
    with session_scope() as db:
        ctx = seed_ingress(db)
        ctx["decision"] = persist_decision(db, ctx)
        db.flush()
        persist(db, ctx)
        assert outcome_count(db) == 1
        db.rollback()
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(models.CanonicalSignalOutcome)) == 0


def test_writer_never_imports_execution_machinery():
    import ast
    from pathlib import Path

    from app.services import canonical_signal_outcome_persistence as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ("execution_router", "paper_broker", "dhan", "state_store",
                 "strategy_fanout", "risk_manager", "position", "broker")
    for name in imported:
        assert not any(token in name.lower() for token in forbidden), name
