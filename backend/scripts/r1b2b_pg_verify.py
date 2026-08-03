"""Authoritative PostgreSQL 18 verification for R1B-2B HOLD evidence.

Run only against a disposable database:

    docker run -d --name nova-pg-r1b2b -e POSTGRES_PASSWORD=nova \
        -e POSTGRES_DB=nova -p 55435:5432 postgres:18-alpine
    python scripts/r1b2b_pg_verify.py
    docker rm -f nova-pg-r1b2b
"""
from __future__ import annotations

import os
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app.config import settings  # noqa: E402

settings.DATABASE_URL = os.environ.get(
    "R1B2B_PG_URL", "postgresql://postgres:nova@localhost:55435/nova"
)
settings.PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED = True
settings.R1B_CANONICAL_OUTCOME_PERSISTENCE = False
settings.R1B_SIGNAL_REJECTION_PERSISTENCE = False

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.db import engine as eng, models  # noqa: E402
from app.services import hold_canonical_decision_evidence as evidence  # noqa: E402
from app.services import private_webhook_service as service  # noqa: E402

eng.reset_engine_for_tests()
config = Config()
config.set_main_option("script_location", str(BACKEND / "alembic"))
command.upgrade(config, "head")


def seed(label: str):
    with eng.session_scope() as db:
        user = models.User(email=f"pg2b-{label}-{uuid.uuid4().hex[:8]}@example.com")
        db.add(user)
        db.flush()
        strategy = models.StrategyCatalog(
            code=f"pg2b-{uuid.uuid4().hex[:10]}",
            display_name="R1B2B PG",
            owner_type="personal",
            owner_user_id=user.id,
            visibility="private",
            status="active",
        )
        db.add(strategy)
        db.flush()
        version = models.StrategyVersion(
            strategy_id=strategy.id,
            version="1.0",
            payload_spec_version="nova.v1",
            source_journey="personal_tradingview",
            status="approved",
            execution_kind="external_webhook",
        )
        db.add(version)
        db.flush()
        instance = models.StrategyInstance(
            user_id=user.id,
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            source_journey="PERSONAL_TRADINGVIEW",
            label=label,
            status="active",
            execution_mode="paper_live_data",
            current_lots=1,
        )
        db.add(instance)
        db.flush()
        return user.id, instance.id, strategy.id


def auth_for(user_id, instance_id, strategy_id):
    return {
        "credential_id": str(uuid.uuid4()),
        "correlation_id": uuid.uuid4().hex,
        "instance_id": str(instance_id),
        "user_id": str(user_id),
        "instance_status": "active",
        "source_journey": "PERSONAL_TRADINGVIEW",
        "execution_mode": "paper_live_data",
        "verification_mode": False,
        "current_lots": 1,
        "strategy_id": str(strategy_id),
    }


def hold(signal_id: str, comment: str | None = None):
    return service.PrivateWebhookPayload(
        action="HOLD",
        signal_id=signal_id,
        signal_time=datetime.now(timezone.utc),
        comment=comment,
    )


def count(model, *criteria) -> int:
    with eng.session_scope() as db:
        statement = select(func.count()).select_from(model)
        if criteria:
            statement = statement.where(*criteria)
        return db.scalar(statement)


def rows(model, *criteria):
    with eng.session_scope() as db:
        statement = select(model)
        if criteria:
            statement = statement.where(*criteria)
        return db.scalars(statement).all()


def instance_signal_criteria(instance_id):
    return models.StrategySignal.strategy_name == f"instance:{instance_id}"


# Flag-off is a true helper no-op and a fresh HOLD writes no decision.
settings.R1B_CANONICAL_DECISION_PERSISTENCE = False
original_evidence_scope = evidence.session_scope


def forbidden_scope():
    raise AssertionError("flag-off opened an evidence session")


evidence.session_scope = forbidden_scope
off_ids = seed("flag-off")
off_auth = auth_for(*off_ids)
off_payload = hold(f"pg-off-{uuid.uuid4().hex[:8]}")
off_result = service.ingest(off_auth, off_payload)
evidence.session_scope = original_evidence_scope
assert off_result["status"] == "NO_OP"
assert count(models.CanonicalSignalDecision) == 0
print("fresh HOLD flag-off + no evidence session OK")

# Enabling later cannot use an identical duplicate to repair missing evidence.
settings.R1B_CANONICAL_DECISION_PERSISTENCE = True
duplicate = service.ingest(off_auth, off_payload)
assert duplicate["duplicate"] is True
assert count(models.CanonicalSignalDecision) == 0
try:
    service.ingest(off_auth, off_payload.model_copy(update={"comment": "changed"}))
    raise AssertionError("conflicting duplicate accepted")
except service.PrivateWebhookError as exc:
    assert exc.status_code == 409
assert count(models.CanonicalSignalDecision) == 0
print("duplicate/no-backfill + conflicting duplicate OK")

# Fresh flag-on mapping, exact committed fingerprint and separate row links.
on_ids = seed("flag-on")
on_auth = auth_for(*on_ids)
on_payload = hold(f"pg-on-{uuid.uuid4().hex[:8]}", comment="f" * 64)
on_result = service.ingest(on_auth, on_payload)
assert on_result["status"] == "NO_OP"
decisions = rows(
    models.CanonicalSignalDecision,
    models.CanonicalSignalDecision.strategy_instance_id == on_ids[1],
)
assert len(decisions) == 1
decision = decisions[0]
events = rows(
    models.WebhookEvent,
    models.WebhookEvent.provider == f"instance-webhook:{on_ids[1]}",
)
signals = rows(models.StrategySignal, instance_signal_criteria(on_ids[1]))
assert len(events) == len(signals) == 1
event, signal = events[0], signals[0]
assert decision.strategy_signal_id == signal.id
assert decision.webhook_event_id == event.id
assert decision.user_id == on_ids[0]
assert decision.payload_fingerprint == event.event_metadata["payload_fingerprint"]
assert decision.payload_fingerprint != on_payload.comment
assert (
    decision.wire_action,
    decision.event_type,
    decision.desired_state,
    decision.intent_reason,
    decision.compatibility_action,
    decision.compatibility_side,
    decision.compatibility_option_side,
    decision.provenance_kind,
) == (
    "HOLD",
    "CONNECTIVITY_TEST",
    "NONE",
    "CONNECTIVITY_TEST",
    None,
    None,
    None,
    "LIVE",
)
assert count(models.StrategyExecutionJob) == 0
assert count(models.CanonicalSignalOutcome) == 0
assert count(models.StrategySignalRejection) == 0
print("fresh HOLD mapping + exact committed fingerprint + no jobs OK")

# Genuine concurrent identical ingress resolves to one signal and one decision.
race_ids = seed("ingress-race")
race_auth = auth_for(*race_ids)
race_payload = hold(f"pg-race-{uuid.uuid4().hex[:8]}")
race_results: list[dict | Exception] = []
barrier = threading.Barrier(2)


def concurrent_ingest():
    try:
        barrier.wait()
        race_results.append(service.ingest(race_auth, race_payload))
    except Exception as exc:
        race_results.append(exc)


threads = [threading.Thread(target=concurrent_ingest) for _ in range(2)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(30)
assert not any(isinstance(result, Exception) for result in race_results), race_results
assert sum(not result.get("duplicate", False) for result in race_results) == 1
assert count(models.StrategySignal, instance_signal_criteria(race_ids[1])) == 1
assert count(
    models.CanonicalSignalDecision,
    models.CanonicalSignalDecision.strategy_instance_id == race_ids[1],
) == 1
print("concurrent identical HOLD -> one signal + one decision OK")

# Concurrent conflicting ingress admits one payload and rejects the other.
conflict_ids = seed("conflict-race")
conflict_auth = auth_for(*conflict_ids)
conflict_signal_id = f"pg-conflict-race-{uuid.uuid4().hex[:8]}"
conflict_payloads = [
    hold(conflict_signal_id, comment="first"),
    hold(conflict_signal_id, comment="second"),
]
conflict_results: list[dict | Exception] = []
barrier = threading.Barrier(2)


def conflicting_ingest(payload):
    try:
        barrier.wait()
        conflict_results.append(service.ingest(conflict_auth, payload))
    except Exception as exc:
        conflict_results.append(exc)


threads = [
    threading.Thread(target=conflicting_ingest, args=(payload,))
    for payload in conflict_payloads
]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(30)
assert sum(isinstance(result, dict) for result in conflict_results) == 1
errors = [result for result in conflict_results if isinstance(result, Exception)]
assert (
    len(errors) == 1
    and isinstance(errors[0], service.PrivateWebhookError)
    and errors[0].status_code == 409
), conflict_results
assert count(models.StrategySignal, instance_signal_criteria(conflict_ids[1])) == 1
assert count(
    models.CanonicalSignalDecision,
    models.CanonicalSignalDecision.strategy_instance_id == conflict_ids[1],
) == 1
print("concurrent conflicting HOLD -> one accepted + one rejected OK")

# Genuine concurrent evidence unique race resolves idempotently to one row.
unique_ids = seed("evidence-race")
unique_auth = auth_for(*unique_ids)
settings.R1B_CANONICAL_DECISION_PERSISTENCE = False
unique_payload = hold(f"pg-evidence-race-{uuid.uuid4().hex[:8]}")
service.ingest(unique_auth, unique_payload)
unique_event = rows(
    models.WebhookEvent,
    models.WebhookEvent.provider == f"instance-webhook:{unique_ids[1]}",
)[0]
settings.R1B_CANONICAL_DECISION_PERSISTENCE = True
unique_results: list[object] = []
barrier = threading.Barrier(2)


def concurrent_evidence():
    try:
        barrier.wait()
        evidence.persist_hold_decision_best_effort(
            webhook_event_id=unique_event.id,
            strategy_instance_id=unique_ids[1],
            owner_user_id=unique_ids[0],
        )
        unique_results.append("ok")
    except Exception as exc:
        unique_results.append(exc)


threads = [threading.Thread(target=concurrent_evidence) for _ in range(2)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(30)
assert unique_results == ["ok", "ok"], unique_results
assert count(
    models.CanonicalSignalDecision,
    models.CanonicalSignalDecision.strategy_instance_id == unique_ids[1],
) == 1
print("concurrent evidence unique race -> one row OK")

# Evidence DB failure occurs after the authoritative commit and changes no result.
failure_ids = seed("evidence-db-failure")
failure_auth = auth_for(*failure_ids)
failure_payload = hold(f"pg-evidence-fail-{uuid.uuid4().hex[:8]}")


def unavailable_scope():
    raise RuntimeError("simulated evidence database outage")


evidence.session_scope = unavailable_scope
failure_result = service.ingest(failure_auth, failure_payload)
evidence.session_scope = original_evidence_scope
assert failure_result["status"] == "NO_OP"
assert count(models.StrategySignal, instance_signal_criteria(failure_ids[1])) == 1
assert count(
    models.CanonicalSignalDecision,
    models.CanonicalSignalDecision.strategy_instance_id == failure_ids[1],
) == 0
print("evidence DB failure after authoritative commit isolated OK")

# PostgreSQL evidence transaction rollback leaves no decision and preserves HOLD.
commit_ids = seed("evidence-commit-failure")
commit_auth = auth_for(*commit_ids)
commit_payload = hold(f"pg-evidence-commit-{uuid.uuid4().hex[:8]}")


@contextmanager
def rollback_evidence_scope():
    session = eng.get_session_factory()()
    try:
        yield session
        session.rollback()
        raise RuntimeError("simulated evidence commit failure")
    finally:
        session.close()


evidence.session_scope = rollback_evidence_scope
commit_result = service.ingest(commit_auth, commit_payload)
evidence.session_scope = original_evidence_scope
assert commit_result["status"] == "NO_OP"
assert count(models.StrategySignal, instance_signal_criteria(commit_ids[1])) == 1
assert count(
    models.CanonicalSignalDecision,
    models.CanonicalSignalDecision.strategy_instance_id == commit_ids[1],
) == 0
print("evidence commit failure rollback isolated OK")

# An authoritative transaction failure never reaches the evidence helper.
outer_ids = seed("outer-failure")
outer_auth = auth_for(*outer_ids)
outer_payload = hold(f"pg-outer-fail-{uuid.uuid4().hex[:8]}")
original_service_scope = service.session_scope
original_helper = evidence.persist_hold_decision_best_effort
helper_calls = 0


@contextmanager
def rollback_authoritative_scope():
    session = eng.get_session_factory()()
    try:
        yield session
        session.rollback()
        raise RuntimeError("simulated authoritative commit failure")
    finally:
        session.close()


def count_helper(**kwargs):
    global helper_calls
    helper_calls += 1


service.session_scope = rollback_authoritative_scope
evidence.persist_hold_decision_best_effort = count_helper
try:
    service.ingest(outer_auth, outer_payload)
    raise AssertionError("authoritative commit failure was accepted")
except RuntimeError as exc:
    assert "authoritative commit failure" in str(exc)
finally:
    service.session_scope = original_service_scope
    evidence.persist_hold_decision_best_effort = original_helper
assert helper_calls == 0
assert count(models.CanonicalSignalDecision) >= 1  # prior verified rows remain
assert count(models.StrategySignal, instance_signal_criteria(outer_ids[1])) == 0
print("authoritative commit failure prevents evidence invocation OK")

# Owner/instance/event mismatches are evidence-only failures.
before = count(models.CanonicalSignalDecision)
evidence.persist_hold_decision_best_effort(
    webhook_event_id=event.id,
    strategy_instance_id=on_ids[1],
    owner_user_id=off_ids[0],
)
evidence.persist_hold_decision_best_effort(
    webhook_event_id=event.id,
    strategy_instance_id=off_ids[1],
    owner_user_id=off_ids[0],
)
evidence.persist_hold_decision_best_effort(
    webhook_event_id=uuid.uuid4(),
    strategy_instance_id=on_ids[1],
    owner_user_id=on_ids[0],
)
assert count(models.CanonicalSignalDecision) == before
print("owner/reference mismatch suppression OK")

settings.R1B_CANONICAL_DECISION_PERSISTENCE = False
print("R1B2B PG VERIFY: ALL PASS")
