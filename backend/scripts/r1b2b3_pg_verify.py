"""R1B-2B3 PostgreSQL verification for trading-action decision evidence.

Runs against a disposable PostgreSQL container (never a shared database):

    docker run -d --name nova-pg-r1b2b3 -e POSTGRES_PASSWORD=nova \
        -e POSTGRES_DB=nova -p 55434:5432 postgres:18-alpine
    python scripts/r1b2b3_pg_verify.py
    docker rm -f nova-pg-r1b2b3

PostgreSQL is authoritative for commit ordering, evidence rollback and
concurrency. Verifies, per action: flag-off zero evidence; fresh decision with
exact mapping + committed fingerprint; duplicate/no-backfill; conflicting
duplicate; concurrent identical ingest races; evidence unique race;
worker-ahead states; evidence DB/commit failures after authoritative commit;
authoritative failure inverse; owner/reference suppression; flag-once rule.
"""
from __future__ import annotations

import os
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app.config import settings  # noqa: E402

settings.DATABASE_URL = os.environ.get(
    "R1B2B3_PG_URL", "postgresql://postgres:nova@localhost:55434/nova"
)
settings.PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED = True
settings.STRATEGY_JOB_WORKER_ENABLED = False  # jobs stay queued; no worker thread

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.db import engine as eng, models  # noqa: E402

eng.reset_engine_for_tests()
config = Config()
config.set_main_option("script_location", str(BACKEND / "alembic"))
command.upgrade(config, "head")

from app.services import private_webhook_service as service  # noqa: E402
from app.services import trading_canonical_decision_evidence as evidence  # noqa: E402
from app.services.private_webhook_service import PrivateWebhookPayload  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def seed(label: str):
    with eng.session_scope() as db:
        user = models.User(email=f"{label}-{uuid.uuid4().hex[:8]}@example.com")
        db.add(user)
        db.flush()
        strategy = models.StrategyCatalog(code=f"{label}-{uuid.uuid4().hex[:8]}",
                                          display_name="T", owner_type="personal",
                                          owner_user_id=user.id, visibility="private",
                                          status="active")
        db.add(strategy)
        db.flush()
        version = models.StrategyVersion(strategy_id=strategy.id, version="1.0",
                                         payload_spec_version="nova.v1",
                                         source_journey="personal_tradingview",
                                         status="approved", execution_kind="external_webhook")
        db.add(version)
        db.flush()
        instance = models.StrategyInstance(user_id=user.id, strategy_id=strategy.id,
                                           strategy_version_id=version.id,
                                           source_journey="PERSONAL_TRADINGVIEW",
                                           label=label, status="active",
                                           execution_mode="paper_live_data", current_lots=1)
        db.add(instance)
        db.flush()
        return str(user.id), str(instance.id), str(strategy.id)


def auth_for(user_id: str, instance_id: str, strategy_id: str):
    return {
        "credential_id": uuid.uuid4().hex,
        "correlation_id": uuid.uuid4().hex,
        "instance_id": instance_id,
        "user_id": user_id,
        "instance_status": "active",
        "source_journey": "PERSONAL_TRADINGVIEW",
        "execution_mode": "paper_live_data",
        "verification_mode": False,
        "current_lots": 1,
        "strategy_id": strategy_id,
    }


def payload(action: str, signal_id: str, comment: str | None = None):
    body = {"action": action, "signal_id": signal_id, "signal_time": now_iso()}
    if comment is not None:
        body["comment"] = comment
    return PrivateWebhookPayload.model_validate(body)


def count(model, *criteria) -> int:
    with eng.session_scope() as db:
        statement = select(func.count()).select_from(model)
        for criterion in criteria:
            statement = statement.where(criterion)
        return db.scalar(statement)


def decisions_for(instance_id: str):
    with eng.session_scope() as db:
        return db.scalars(
            select(models.CanonicalSignalDecision).where(
                models.CanonicalSignalDecision.strategy_instance_id == uuid.UUID(instance_id)
            )
        ).all()


# --- flag off: fresh trading action writes nothing --------------------------
settings.R1B_CANONICAL_DECISION_PERSISTENCE = False
ids_off = seed("off")
off_payload = payload("BUY_CE", "off-1")  # one payload = one fingerprint
result = service.ingest(auth_for(*ids_off), off_payload)
assert result["status"] == "QUEUED"
assert count(models.CanonicalSignalDecision) == 0
print("flag-off fresh BUY_CE: signal+job committed, zero decisions OK")

# identical duplicate after enabling: no backfill
settings.R1B_CANONICAL_DECISION_PERSISTENCE = True
duplicate = service.ingest(auth_for(*ids_off), off_payload)
assert duplicate.get("duplicate") is True
assert count(models.CanonicalSignalDecision) == 0
print("duplicate after flag-on: no backfill OK")

# --- fresh decisions with exact mapping + fingerprint, per action -----------
MAPPING = {
    "BUY_CE": ("STRATEGY_SIGNAL", "BULLISH", "DIRECTIONAL_SIGNAL", "ENTRY", "BUY", "CE"),
    "BUY_PE": ("STRATEGY_SIGNAL", "BEARISH", "DIRECTIONAL_SIGNAL", "ENTRY", "BUY", "PE"),
    "EXIT": ("STRATEGY_SIGNAL", "FLAT", "EXPLICIT_EXIT", "EXIT", "SELL", None),
}
for action, expected in MAPPING.items():
    ids = seed(f"map-{action.lower()}")
    result = service.ingest(auth_for(*ids), payload(action, f"sig-{action}", comment="f" * 64))
    assert result == {"ok": True, "signal_id": f"sig-{action}", "status": "QUEUED", "action": action}
    rows = decisions_for(ids[1])
    assert len(rows) == 1
    row = rows[0]
    assert (row.event_type, row.desired_state, row.intent_reason,
            row.compatibility_action, row.compatibility_side,
            row.compatibility_option_side) == expected
    assert row.wire_action == action and row.provenance_kind == "LIVE"
    with eng.session_scope() as db:
        event = db.scalar(select(models.WebhookEvent).where(
            models.WebhookEvent.provider == f"instance-webhook:{ids[1]}"))
        job = db.scalar(select(models.StrategyExecutionJob).where(
            models.StrategyExecutionJob.strategy_name == f"instance:{ids[1]}"))
    assert row.payload_fingerprint == event.event_metadata["payload_fingerprint"]
    assert row.payload_fingerprint != "f" * 64  # user comment cannot replace it
    assert job is not None and job.status == "queued"
print("fresh BUY_CE/BUY_PE/EXIT: exact mapping + committed fingerprint + job OK")

# --- conflicting duplicate ---------------------------------------------------
ids_conf = seed("conflict")
service.ingest(auth_for(*ids_conf), payload("BUY_CE", "conf-1", comment="a"))
try:
    service.ingest(auth_for(*ids_conf), payload("BUY_PE", "conf-1", comment="b"))
    raise AssertionError("conflicting duplicate accepted")
except service.PrivateWebhookError as exc:
    assert exc.reason == "CONFLICTING_DUPLICATE"
rows = decisions_for(ids_conf[1])
assert len(rows) == 1 and rows[0].wire_action == "BUY_CE"
print("conflicting duplicate: winner decision only OK")

# --- concurrent identical ingest per action ---------------------------------
for action in ("BUY_CE", "BUY_PE", "EXIT"):
    ids_race = seed(f"race-{action.lower()}")
    race_payload = payload(action, f"race-{action}")  # shared identity
    results: list[object] = []
    barrier = threading.Barrier(2)

    def race():
        try:
            barrier.wait()
            results.append(service.ingest(auth_for(*ids_race), race_payload))
        except Exception as exc:  # pragma: no cover
            results.append(exc)

    threads = [threading.Thread(target=race) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert all(isinstance(r, dict) for r in results), results
    fresh = [r for r in results if not r.get("duplicate")]
    assert len(fresh) == 1, results
    name = f"instance:{ids_race[1]}"
    assert count(models.StrategySignal, models.StrategySignal.strategy_name == name) == 1
    assert count(models.StrategyExecutionJob, models.StrategyExecutionJob.strategy_name == name) == 1
    assert len(decisions_for(ids_race[1])) == 1
    print(f"concurrent identical {action}: one signal/job/decision OK")

# --- evidence unique race (direct helper) ------------------------------------
ids_ev = seed("evrace")
settings.R1B_CANONICAL_DECISION_PERSISTENCE = False
service.ingest(auth_for(*ids_ev), payload("EXIT", "evrace-1"))
settings.R1B_CANONICAL_DECISION_PERSISTENCE = True
with eng.session_scope() as db:
    event_id = db.scalar(select(models.WebhookEvent.id).where(
        models.WebhookEvent.provider == f"instance-webhook:{ids_ev[1]}"))
barrier = threading.Barrier(2)


def direct():
    barrier.wait()
    evidence.persist_trading_decision_best_effort(
        webhook_event_id=event_id,
        strategy_instance_id=ids_ev[1],
        owner_user_id=ids_ev[0],
    )


threads = [threading.Thread(target=direct) for _ in range(2)]
for t in threads:
    t.start()
for t in threads:
    t.join(30)
assert len(decisions_for(ids_ev[1])) == 1
print("evidence unique race: one immutable row OK")

# --- worker-ahead states -----------------------------------------------------
for signal_status, job_status in (("processing", "running"), ("completed", "completed"),
                                  ("completed_with_errors", "failed")):
    ids_w = seed(f"worker-{job_status}")
    settings.R1B_CANONICAL_DECISION_PERSISTENCE = False
    service.ingest(auth_for(*ids_w), payload("BUY_PE", f"w-{job_status}"))
    with eng.session_scope() as db:
        name = f"instance:{ids_w[1]}"
        signal = db.scalar(select(models.StrategySignal).where(models.StrategySignal.strategy_name == name))
        job = db.scalar(select(models.StrategyExecutionJob).where(models.StrategyExecutionJob.strategy_name == name))
        signal.status = signal_status
        job.status = job_status
        job.attempts = 1
        event_id = db.scalar(select(models.WebhookEvent.id).where(
            models.WebhookEvent.provider == f"instance-webhook:{ids_w[1]}"))
    settings.R1B_CANONICAL_DECISION_PERSISTENCE = True
    evidence.persist_trading_decision_best_effort(
        webhook_event_id=event_id, strategy_instance_id=ids_w[1], owner_user_id=ids_w[0])
    rows = decisions_for(ids_w[1])
    assert len(rows) == 1 and rows[0].wire_action == "BUY_PE"
print("worker-ahead (running/completed/failed): decision still recorded OK")

# --- evidence DB failure + commit failure after authoritative commit ---------
ids_fail = seed("evfail")
real_scope = evidence.session_scope


def broken_scope():
    raise RuntimeError("evidence db down")


evidence.session_scope = broken_scope
result = service.ingest(auth_for(*ids_fail), payload("BUY_CE", "evfail-1"))
assert result["status"] == "QUEUED"
name = f"instance:{ids_fail[1]}"
assert count(models.StrategySignal, models.StrategySignal.strategy_name == name) == 1
assert count(models.StrategyExecutionJob, models.StrategyExecutionJob.strategy_name == name) == 1
assert len(decisions_for(ids_fail[1])) == 0
evidence.session_scope = real_scope
print("evidence DB outage after authoritative commit: isolated OK")

ids_cfail = seed("evcommit")


@contextmanager
def failing_commit_scope():
    session = eng.get_session_factory()()
    try:
        yield session
        session.rollback()
        raise RuntimeError("simulated evidence commit failure")
    finally:
        session.close()


evidence.session_scope = failing_commit_scope
result = service.ingest(auth_for(*ids_cfail), payload("EXIT", "evcommit-1"))
assert result["status"] == "QUEUED"
name = f"instance:{ids_cfail[1]}"
assert count(models.StrategySignal, models.StrategySignal.strategy_name == name) == 1
assert count(models.StrategyExecutionJob, models.StrategyExecutionJob.strategy_name == name) == 1
assert len(decisions_for(ids_cfail[1])) == 0  # PG: rollback fully discards evidence
evidence.session_scope = real_scope
print("evidence commit failure: decision rolled back, signal/job durable OK")

# --- authoritative failure inverse -------------------------------------------
ids_af = seed("authfail")
calls = 0
real_persist_job = service._persist_execution_job
real_helper = evidence.persist_trading_decision_best_effort


def failing_job(*args, **kwargs):
    raise RuntimeError("authoritative failure")


def counting_helper(**kwargs):
    global calls
    calls += 1


service._persist_execution_job = failing_job
service.trading_canonical_decision_evidence.persist_trading_decision_best_effort = counting_helper
try:
    service.ingest(auth_for(*ids_af), payload("BUY_CE", "authfail-1"))
    raise AssertionError("authoritative failure did not propagate")
except RuntimeError:
    pass
finally:
    service._persist_execution_job = real_persist_job
    service.trading_canonical_decision_evidence.persist_trading_decision_best_effort = real_helper
assert calls == 0
assert len(decisions_for(ids_af[1])) == 0
print("authoritative commit failure: evidence never invoked OK")

# --- owner/reference mismatch -------------------------------------------------
ids_a = seed("mm-a")
ids_b = seed("mm-b")
service.ingest(auth_for(*ids_a), payload("BUY_CE", "mm-1"))
with eng.session_scope() as db:
    event_id = db.scalar(select(models.WebhookEvent.id).where(
        models.WebhookEvent.provider == f"instance-webhook:{ids_a[1]}"))
before = count(models.CanonicalSignalDecision)
evidence.persist_trading_decision_best_effort(
    webhook_event_id=event_id, strategy_instance_id=ids_a[1], owner_user_id=ids_b[0])
evidence.persist_trading_decision_best_effort(
    webhook_event_id=event_id, strategy_instance_id=ids_b[1], owner_user_id=ids_b[0])
assert count(models.CanonicalSignalDecision) == before
print("owner/reference mismatch: suppression only OK")

# --- flag observed once at helper entry --------------------------------------
ids_flag = seed("flagflip")
settings.R1B_CANONICAL_DECISION_PERSISTENCE = False
service.ingest(auth_for(*ids_flag), payload("BUY_PE", "flag-1"))
# Simulates "authoritative commit done, flag flipped false before helper
# entry": ingest above already skipped evidence because the helper observed
# False once at entry. Enabling now must not retroactively write anything.
settings.R1B_CANONICAL_DECISION_PERSISTENCE = True
assert len(decisions_for(ids_flag[1])) == 0
print("flag-once rule: value observed at helper entry controls the attempt OK")

settings.R1B_CANONICAL_DECISION_PERSISTENCE = False
print("R1B2B3 PG VERIFY: ALL PASS")
