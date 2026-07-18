"""R1B-2B3 evidence failures cannot alter authoritative trading behavior."""
# ruff: noqa: F811
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_private_webhook import (  # noqa: F401
    _issue_token,
    _jobs,
    _make_instance,
    _payload,
    _post,
    client,
    webhook_enabled,
)


def _count(model) -> int:
    from app.db.engine import session_scope

    with session_scope() as db:
        return db.scalar(select(func.count()).select_from(model))


def _assert_authoritative_action(response, signal_id, action, instance_id):
    from app.db import models

    assert response.status_code == 202
    assert response.json() == {
        "ok": True,
        "signal_id": signal_id,
        "status": "QUEUED",
        "action": action,
    }
    assert response.headers["content-type"] == "application/json"
    assert "decision" not in response.text.lower()
    assert _count(models.StrategySignal) == 1
    assert _count(models.CanonicalSignalDecision) == 0
    assert _count(models.CanonicalSignalOutcome) == 0
    assert _count(models.StrategySignalRejection) == 0
    assert len(_jobs(instance_id)) == 1  # job durable and claimable


def _post_action(client, monkeypatch, action="BUY_CE", *, comment=None):
    from app.config import settings

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    user = make_user(f"tf-{uuid.uuid4().hex[:12]}@example.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    body = _payload(action=action)
    if comment is not None:
        body["comment"] = comment
    return user, instance_id, body, _post(client, token, body)


@pytest.mark.parametrize(
    "code",
    [
        "INVALID_INPUT",
        "OWNER_INSTANCE_MISMATCH",
        "FOREIGN_REFERENCE",
        "CANONICAL_DECISION_CONFLICT",
        "TAINT_DETECTED",
        "PERSISTENCE_UNAVAILABLE",
    ],
)
@pytest.mark.parametrize("action", ["BUY_CE", "EXIT"])
def test_closed_writer_failures_are_suppressed(client, monkeypatch, code, action):
    from app.services import trading_canonical_decision_evidence as evidence
    from app.services.r1b_evidence_safety import CanonicalDecisionPersistenceError

    def fail_writer(*args, **kwargs):
        raise CanonicalDecisionPersistenceError(code)

    monkeypatch.setattr(evidence, "persist_canonical_signal_decision", fail_writer)
    _, instance_id, body, response = _post_action(client, monkeypatch, action)
    _assert_authoritative_action(response, body["signal_id"], action, instance_id)


def test_evidence_session_outage_is_suppressed(client, monkeypatch):
    from app.services import trading_canonical_decision_evidence as evidence

    def unavailable():
        raise RuntimeError("sensitive database path")

    monkeypatch.setattr(evidence, "session_scope", unavailable)
    _, instance_id, body, response = _post_action(client, monkeypatch, "BUY_PE")
    _assert_authoritative_action(response, body["signal_id"], "BUY_PE", instance_id)


def test_evidence_commit_failure_rolls_back_only_decision(client, monkeypatch):
    from app.db import engine, models
    from app.services import trading_canonical_decision_evidence as evidence

    @contextmanager
    def fail_commit():
        session = engine.get_session_factory()()
        transaction = session.begin()
        try:
            yield session
            transaction.rollback()
            raise RuntimeError("simulated evidence commit failure")
        finally:
            session.close()

    monkeypatch.setattr(evidence, "session_scope", fail_commit)
    _, instance_id, body, response = _post_action(client, monkeypatch, "EXIT")
    # Response + authoritative rows only: the pysqlite driver auto-commits
    # around SAVEPOINT, so evidence rollback-on-commit-failure is proven
    # authoritatively on PostgreSQL (scripts/r1b2b3_pg_verify.py), mirroring
    # the reviewed R1B-2B HOLD test.
    assert response.status_code == 202
    assert response.json() == {
        "ok": True,
        "signal_id": body["signal_id"],
        "status": "QUEUED",
        "action": "EXIT",
    }
    assert _count(models.StrategySignal) == 1
    assert _count(models.CanonicalSignalOutcome) == 0
    assert _count(models.StrategySignalRejection) == 0
    assert len(_jobs(instance_id)) == 1


def test_unexpected_helper_and_diagnostic_failures_are_suppressed(client, monkeypatch):
    from app.services import trading_canonical_decision_evidence as evidence

    def fail(*args, **kwargs):
        raise RuntimeError("unexpected secret-bearing exception")

    monkeypatch.setattr(evidence, "persist_trading_decision_best_effort", fail)
    monkeypatch.setattr(evidence, "record_trading_decision_failure", fail)
    _, instance_id, body, response = _post_action(client, monkeypatch, "BUY_CE")
    _assert_authoritative_action(response, body["signal_id"], "BUY_CE", instance_id)


def test_logging_failure_is_suppressed(client, monkeypatch):
    from app.services import trading_canonical_decision_evidence as evidence
    from app.services.r1b_evidence_safety import CanonicalDecisionPersistenceError

    def fail_writer(*args, **kwargs):
        raise CanonicalDecisionPersistenceError("PERSISTENCE_UNAVAILABLE")

    def fail_log(*args, **kwargs):
        raise RuntimeError("logger unavailable")

    monkeypatch.setattr(evidence, "persist_canonical_signal_decision", fail_writer)
    monkeypatch.setattr(evidence.logger, "warning", fail_log)
    _, instance_id, body, response = _post_action(client, monkeypatch, "BUY_CE")
    _assert_authoritative_action(response, body["signal_id"], "BUY_CE", instance_id)


def test_failure_log_contains_only_closed_diagnostics(client, monkeypatch):
    from app.services import trading_canonical_decision_evidence as evidence
    from app.services.r1b_evidence_safety import CanonicalDecisionPersistenceError

    secret = "nwk_" + "S" * 43

    def fail_writer(*args, **kwargs):
        raise CanonicalDecisionPersistenceError("INVALID_INPUT")

    messages: list[str] = []

    def capture_log(message, *args, **kwargs):
        messages.append(message % args)

    monkeypatch.setattr(evidence, "persist_canonical_signal_decision", fail_writer)
    monkeypatch.setattr(evidence.logger, "warning", capture_log)
    _, instance_id, body, response = _post_action(
        client, monkeypatch, "BUY_CE", comment=secret
    )
    _assert_authoritative_action(response, body["signal_id"], "BUY_CE", instance_id)
    rendered = "\n".join(messages)
    assert "event=r1b_trading_decision_evidence_failed" in rendered
    assert "action_class=ENTRY" in rendered
    assert "error_code=INVALID_INPUT" in rendered
    assert secret not in rendered
    assert body["signal_id"] not in rendered
    # The committed fingerprint must never be logged.
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        fingerprint = db.scalar(select(models.WebhookEvent)).event_metadata[
            "payload_fingerprint"
        ]
    assert fingerprint not in rendered


def test_owner_and_reference_mismatch_never_create_evidence(client, monkeypatch):
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.services import trading_canonical_decision_evidence as evidence

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", False, raising=False
    )
    user = make_user(f"own-a-{uuid.uuid4().hex[:8]}@example.com")
    other = make_user(f"own-b-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user)
    other_instance = _make_instance(other)
    response = _post(client, _issue_token(user, instance_id), _payload(action="BUY_CE"))
    assert response.status_code == 202
    with session_scope() as db:
        event_id = db.scalar(select(models.WebhookEvent.id))

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    # foreign owner / foreign instance / missing event
    evidence.persist_trading_decision_best_effort(
        webhook_event_id=event_id,
        strategy_instance_id=instance_id,
        owner_user_id=other.id,
    )
    evidence.persist_trading_decision_best_effort(
        webhook_event_id=event_id,
        strategy_instance_id=other_instance,
        owner_user_id=other.id,
    )
    evidence.persist_trading_decision_best_effort(
        webhook_event_id=uuid.uuid4(),
        strategy_instance_id=instance_id,
        owner_user_id=user.id,
    )
    assert _count(models.CanonicalSignalDecision) == 0


def test_hold_event_cannot_feed_the_trading_helper(client, monkeypatch):
    """Metadata action mismatch: a committed HOLD event is refused by the
    trading helper (and vice versa the HOLD helper refuses trading events)."""
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.services import trading_canonical_decision_evidence as evidence

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", False, raising=False
    )
    user = make_user(f"mm-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user)
    assert _post(client, _issue_token(user, instance_id), _payload(action="HOLD")).status_code == 202
    with session_scope() as db:
        event_id = db.scalar(select(models.WebhookEvent.id))
    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    evidence.persist_trading_decision_best_effort(
        webhook_event_id=event_id,
        strategy_instance_id=instance_id,
        owner_user_id=user.id,
    )
    assert _count(models.CanonicalSignalDecision) == 0


def test_missing_job_suppresses_evidence(client, monkeypatch):
    from app.db import models
    from app.db.engine import session_scope
    from app.services import trading_canonical_decision_evidence as evidence

    user, instance_id, body, response = _post_action(client, monkeypatch, "BUY_CE")
    # A decision was already written on the fresh path; remove it and the job
    # to probe the job-proof requirement in isolation.
    with session_scope() as db:
        import sqlalchemy as sa

        db.execute(sa.text("DELETE FROM canonical_signal_decisions"))
        db.execute(sa.text("DELETE FROM strategy_execution_jobs"))
        event_id = db.scalar(select(models.WebhookEvent.id))
    evidence.persist_trading_decision_best_effort(
        webhook_event_id=event_id,
        strategy_instance_id=instance_id,
        owner_user_id=user.id,
    )
    assert _count(models.CanonicalSignalDecision) == 0


def test_authoritative_failure_prevents_evidence_call(client, monkeypatch):
    from app.config import settings
    from app.services import private_webhook_service as service

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    user = make_user(f"cf-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    auth = service.authenticate_credential(token)
    payload = service.PrivateWebhookPayload.model_validate(_payload(action="BUY_CE"))
    evidence_calls = 0

    def fail_authoritative(*args, **kwargs):
        raise RuntimeError("authoritative transaction failed")

    def count_evidence(*args, **kwargs):
        nonlocal evidence_calls
        evidence_calls += 1

    monkeypatch.setattr(service, "_persist_execution_job", fail_authoritative)
    monkeypatch.setattr(
        service.trading_canonical_decision_evidence,
        "persist_trading_decision_best_effort",
        count_evidence,
    )
    with pytest.raises(RuntimeError, match="authoritative transaction failed"):
        service.ingest(auth, payload)
    assert evidence_calls == 0


def test_rejected_requests_never_create_decisions(client, monkeypatch):
    from app.config import settings
    from app.db import models
    from app.services import strategy_instance_service

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    assert _post(client, "nwk_" + "x" * 43, _payload(action="BUY_CE")).status_code == 401

    user = make_user(f"rvk-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    strategy_instance_service.revoke_webhook_credential(user.id, instance_id)
    assert _post(client, token, _payload(action="BUY_PE")).status_code == 401

    user2 = make_user(f"tm-{uuid.uuid4().hex[:8]}@example.com")
    token2 = _issue_token(user2, _make_instance(user2))
    stale = _payload(
        action="BUY_CE",
        signal_time=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    )
    future = _payload(
        action="EXIT",
        signal_time=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    )
    assert _post(client, token2, stale).status_code == 422
    assert _post(client, token2, future).status_code == 422
    assert _post(client, token2, _payload(action="SELL_ALL")).status_code == 422
    assert _count(models.CanonicalSignalDecision) == 0
