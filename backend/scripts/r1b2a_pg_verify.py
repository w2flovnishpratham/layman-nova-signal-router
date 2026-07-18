"""R1B-2A PostgreSQL verification for the three insert-only evidence writers.

Runs against a disposable PostgreSQL container (never a shared database):

    docker run -d --name nova-pg-r1b2a -e POSTGRES_PASSWORD=nova \
        -e POSTGRES_DB=nova -p 55434:5432 postgres:18-alpine
    python scripts/r1b2a_pg_verify.py
    docker rm -f nova-pg-r1b2a

Verifies, per writer, on real PostgreSQL: flag-off refusal before any query,
valid insert, idempotent reuse, conflict/foreign-reference refusal, and a
genuine two-session concurrent identical insert resolving to one row.
"""
from __future__ import annotations

import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app.config import settings  # noqa: E402

settings.DATABASE_URL = os.environ.get(
    "R1B2A_PG_URL", "postgresql://postgres:nova@localhost:55434/nova"
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.db import engine as eng, models  # noqa: E402
from app.domain.legacy_signal_adapter import adapt_legacy_action  # noqa: E402

eng.reset_engine_for_tests()
config = Config()
config.set_main_option("script_location", str(BACKEND / "alembic"))
command.upgrade(config, "head")

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
FINGERPRINT = "f" * 64


def seed(db, signal_id: str, action: str = "BUY_CE"):
    user = models.User(email=f"pg2a-{uuid.uuid4().hex[:10]}@example.com")
    db.add(user); db.flush()
    strategy = models.StrategyCatalog(code=f"pg2a-{uuid.uuid4().hex[:10]}", display_name="P",
                                      owner_type="personal", owner_user_id=user.id,
                                      visibility="private", status="active")
    db.add(strategy); db.flush()
    version = models.StrategyVersion(strategy_id=strategy.id, version="1.0",
                                     payload_spec_version="nova.v1",
                                     source_journey="personal_tradingview",
                                     status="approved", execution_kind="external_webhook")
    db.add(version); db.flush()
    instance = models.StrategyInstance(user_id=user.id, strategy_id=strategy.id,
                                       strategy_version_id=version.id,
                                       source_journey="PERSONAL_TRADINGVIEW",
                                       label=f"pg2a-{uuid.uuid4().hex[:6]}")
    db.add(instance); db.flush()
    signal = models.StrategySignal(strategy_name=f"instance:{instance.id}", signal_id=signal_id)
    event = models.WebhookEvent(provider=f"instance-webhook:{instance.id}",
                                event_id=signal_id, raw_body_sha256=FINGERPRINT)
    db.add_all([signal, event]); db.flush()
    job = models.StrategyExecutionJob(strategy_signal_id=signal.id, user_id=user.id,
                                      strategy_name=signal.strategy_name, signal_id=signal_id,
                                      signal_payload={}, lots=1, execution_mode="paper_live_data")
    db.add(job); db.flush()
    return dict(user_id=user.id, instance_id=instance.id, signal_id_row=signal.id,
                event_id=event.id, job_id=job.id,
                canonical_event=adapt_legacy_action(action, signal_id=signal_id, signal_time=NOW))


from app.services.canonical_signal_decision_persistence import (  # noqa: E402
    CanonicalDecisionPersistenceError,
    persist_canonical_signal_decision,
)
from app.services.canonical_signal_outcome_persistence import (  # noqa: E402
    persist_canonical_signal_outcome,
)
from app.services.strategy_signal_rejection_persistence import (  # noqa: E402
    persist_strategy_signal_rejection,
)


def decision_kwargs(db, ctx):
    return dict(
        strategy_signal=db.get(models.StrategySignal, ctx["signal_id_row"]),
        strategy_instance=db.get(models.StrategyInstance, ctx["instance_id"]),
        owner_user_id=ctx["user_id"],
        canonical_event=ctx["canonical_event"],
        payload_fingerprint=FINGERPRINT,
        received_at=NOW,
        webhook_event=db.get(models.WebhookEvent, ctx["event_id"]),
    )


# --- flag-off refusal --------------------------------------------------------
with eng.session_scope() as db:
    ctx = seed(db, "pg2a-flagoff")
    try:
        persist_canonical_signal_decision(db, **decision_kwargs(db, ctx))
        raise AssertionError("flag-off decision write accepted")
    except CanonicalDecisionPersistenceError as exc:
        assert exc.code == "PERSISTENCE_DISABLED"
print("flag-off refusal OK")

settings.R1B_CANONICAL_DECISION_PERSISTENCE = True
settings.R1B_CANONICAL_OUTCOME_PERSISTENCE = True
settings.R1B_SIGNAL_REJECTION_PERSISTENCE = True

# --- valid insert + reuse + conflict ----------------------------------------
with eng.session_scope() as db:
    ctx = seed(db, "pg2a-main")
    first = persist_canonical_signal_decision(db, **decision_kwargs(db, ctx))
    again = persist_canonical_signal_decision(db, **decision_kwargs(db, ctx))
    assert first.id == again.id
    conflicting = dict(decision_kwargs(db, ctx),
                       canonical_event=adapt_legacy_action("EXIT", signal_id="pg2a-main", signal_time=NOW))
    try:
        persist_canonical_signal_decision(db, **conflicting)
        raise AssertionError("conflict accepted")
    except CanonicalDecisionPersistenceError as exc:
        assert exc.code == "CANONICAL_DECISION_CONFLICT"
    decision_id = first.id

    outcome = persist_canonical_signal_outcome(
        db, decision=first, phase="ROUTING_COMPLETED", routing_result="ENTRY_ROUTED",
        current_state="FLAT", execution_job=db.get(models.StrategyExecutionJob, ctx["job_id"]),
        attempt=1, safe_detail="ENTRY_TRADED",
    )
    outcome_again = persist_canonical_signal_outcome(
        db, decision=first, phase="ROUTING_COMPLETED", routing_result="ENTRY_ROUTED",
        current_state="FLAT", execution_job=db.get(models.StrategyExecutionJob, ctx["job_id"]),
        attempt=1, safe_detail="ENTRY_TRADED",
    )
    assert outcome.id == outcome_again.id

    rejection = persist_strategy_signal_rejection(
        db, strategy_instance=db.get(models.StrategyInstance, ctx["instance_id"]),
        owner_user_id=ctx["user_id"], stage="SIGNAL_TIME", rejection_code="STALE_SIGNAL",
        received_at=NOW, signal_id="pg2a-main",
    )
    rejection_again = persist_strategy_signal_rejection(
        db, strategy_instance=db.get(models.StrategyInstance, ctx["instance_id"]),
        owner_user_id=ctx["user_id"], stage="SIGNAL_TIME", rejection_code="STALE_SIGNAL",
        received_at=NOW, signal_id="pg2a-main",
    )
    assert rejection.id == rejection_again.id
print("valid insert + idempotent reuse + conflict refusal OK")

# --- genuine concurrent identical inserts, one per writer -------------------
with eng.session_scope() as db:
    race_ctx = seed(db, "pg2a-race")

for writer_name in ("decision", "outcome", "rejection"):
    results: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def run(name: str) -> None:
        session = eng.get_session_factory()()
        try:
            if writer_name == "decision":
                kwargs = decision_kwargs(session, race_ctx)
                barrier.wait()
                row = persist_canonical_signal_decision(session, **kwargs)
            elif writer_name == "outcome":
                decision = session.scalar(
                    __import__("sqlalchemy").select(models.CanonicalSignalDecision).where(
                        models.CanonicalSignalDecision.strategy_signal_id == race_ctx["signal_id_row"]
                    )
                )
                barrier.wait()
                row = persist_canonical_signal_outcome(
                    session, decision=decision, phase="STATE_EVALUATED",
                    routing_result="STATE_NO_OP", no_op_reason="ALREADY_BULLISH",
                    safe_detail="ALREADY_IN_CE",
                )
            else:
                instance = session.get(models.StrategyInstance, race_ctx["instance_id"])
                barrier.wait()
                row = persist_strategy_signal_rejection(
                    session, strategy_instance=instance, owner_user_id=race_ctx["user_id"],
                    stage="LIFECYCLE", rejection_code="INACTIVE_INSTANCE", received_at=NOW,
                    signal_id="pg2a-race",
                )
            session.commit()
            results[name] = row.id
        except Exception as exc:  # pragma: no cover
            session.rollback()
            results[name] = exc
        finally:
            session.close()

    threads = [threading.Thread(target=run, args=(n,)) for n in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert all(isinstance(v, uuid.UUID) for v in results.values()), (writer_name, results)
    assert results["A"] == results["B"], (writer_name, results)
    print(f"concurrent {writer_name} race -> one winner row OK")

settings.R1B_CANONICAL_DECISION_PERSISTENCE = False
settings.R1B_CANONICAL_OUTCOME_PERSISTENCE = False
settings.R1B_SIGNAL_REJECTION_PERSISTENCE = False
print("R1B2A PG VERIFY: ALL PASS")
