"""Background pollers must stop hammering an unreachable database.

Production incident this pins: Neon began refusing connections ("data
transfer quota exceeded") and the workers kept retrying at their normal
cadence -- ~45,000 failed connections per hour, flat overnight with the
market closed. Each attempt still pays a TCP+TLS handshake, consuming the
very quota that caused the outage.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db import backoff
from app.tests.conftest_multiuser import mu_db  # noqa: F401


@pytest.fixture(autouse=True)
def _clean_counter():
    backoff.reset()
    yield
    backoff.reset()


def test_healthy_polling_is_left_at_its_normal_interval():
    assert backoff.poll_delay(0.5) == 0.5


def test_connection_failures_escalate_the_interval():
    exc = OperationalError("connect", None, Exception("quota exceeded"))
    backoff.note_outcome(exc)
    first = backoff.poll_delay(0.5)
    backoff.note_outcome(exc)
    second = backoff.poll_delay(0.5)

    assert first > 0.5
    assert second > first


def test_escalation_is_capped_so_recovery_is_still_detected():
    exc = OperationalError("connect", None, Exception("quota exceeded"))
    for _ in range(50):
        backoff.note_outcome(exc)

    assert backoff.poll_delay(0.5) == backoff.MAX_BACKOFF_SECONDS


def test_a_single_success_restores_full_cadence():
    exc = OperationalError("connect", None, Exception("quota exceeded"))
    for _ in range(5):
        backoff.note_outcome(exc)
    assert backoff.poll_delay(0.5) > 0.5

    backoff.note_outcome(None)

    assert backoff.poll_delay(0.5) == 0.5


def test_integrity_errors_never_trigger_backoff():
    """The dedup/claim paths (webhook_replay_store, order_idempotency) raise
    IntegrityError constantly during healthy trading. Counting those would
    throttle the engine in the middle of a normal session."""
    exc = IntegrityError("insert", None, Exception("duplicate key"))
    for _ in range(10):
        backoff.note_outcome(exc)

    assert backoff.consecutive_failures() == 0
    assert backoff.poll_delay(0.5) == 0.5


def test_business_exceptions_do_not_trigger_backoff():
    for _ in range(10):
        backoff.note_outcome(ValueError("bad input"))

    assert backoff.poll_delay(0.5) == 0.5


def test_session_scope_reports_connection_failures_to_the_backoff(mu_db):  # noqa: F811
    """Wiring test: the unit tests above prove the maths, this proves
    session_scope actually feeds it. Detection has to live there because
    option_position_monitor swallows DB errors per-user, so its loop never
    sees them."""
    from app.db.engine import session_scope

    with pytest.raises(OperationalError):
        with session_scope():
            raise OperationalError("connect", None, Exception("quota exceeded"))

    assert backoff.consecutive_failures() == 1
    assert backoff.poll_delay(0.5) > 0.5


def test_session_scope_clears_the_backoff_once_the_database_answers(mu_db):  # noqa: F811
    from app.db import models
    from app.db.engine import session_scope

    backoff.note_failure()
    backoff.note_failure()
    assert backoff.poll_delay(0.5) > 0.5

    with session_scope() as db:
        db.query(models.WebhookEvent).count()

    assert backoff.poll_delay(0.5) == 0.5


def test_storm_reduction_is_material_at_the_observed_poll_rate():
    """0.5s poll -> the observed ~12 connections/sec. After a handful of
    consecutive failures it must fall to roughly one per minute."""
    exc = OperationalError("connect", None, Exception("quota exceeded"))
    for _ in range(8):
        backoff.note_outcome(exc)

    per_second_before = 1 / 0.5
    per_second_after = 1 / backoff.poll_delay(0.5)

    assert per_second_before / per_second_after >= 100


def test_strategy_job_worker_survives_a_database_outage_at_startup(monkeypatch):
    """The worker thread must not die on a transient outage.

    Production incident: recover_stale_jobs() ran before the loop and outside
    its except, so a database outage raised straight out of the thread. The
    thread stayed dead after connectivity returned, leaving queued strategy
    jobs -- real orders -- unprocessed until someone restarted the process.
    """
    import threading

    from sqlalchemy.exc import OperationalError

    from app.workers import strategy_job_worker

    attempts: list[str] = []

    def flaky_recover() -> int:
        attempts.append("recover")
        if len(attempts) == 1:
            raise OperationalError("SELECT 1", {}, Exception("data transfer quota exceeded"))
        return 0

    stop = threading.Event()

    def process_once() -> int:
        # Let the loop turn a few times, then stop it.
        if len(attempts) >= 2:
            stop.set()
        return 0

    monkeypatch.setattr(strategy_job_worker, "recover_stale_jobs", flaky_recover)
    monkeypatch.setattr(strategy_job_worker, "process_queued_jobs_once", process_once)
    monkeypatch.setattr(strategy_job_worker, "_STOP_EVENT", stop)
    monkeypatch.setattr(strategy_job_worker.settings, "STRATEGY_JOB_WORKER_POLL_SECONDS", 0.01, raising=False)

    # The loop must return normally rather than propagating the outage.
    strategy_job_worker._loop()

    # Retried after the failure instead of giving up on recovery entirely.
    assert len(attempts) >= 2
