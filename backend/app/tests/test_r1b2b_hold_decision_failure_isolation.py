"""R1B-2B evidence failures cannot alter authoritative HOLD behavior."""
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


def _assert_authoritative_hold(response, signal_id: str, instance_id: str) -> None:
    from app.db import models

    assert response.status_code == 202
    assert response.json() == {
        "ok": True,
        "signal_id": signal_id,
        "status": "NO_OP",
        "reason": "HOLD",
    }
    assert response.headers["content-type"] == "application/json"
    assert "decision" not in response.text.lower()
    assert _count(models.StrategySignal) == 1
    assert _count(models.CanonicalSignalDecision) == 0
    assert _count(models.CanonicalSignalOutcome) == 0
    assert _count(models.StrategySignalRejection) == 0
    assert _jobs(instance_id) == []


def _post_hold(client, monkeypatch, *, comment: str | None = None):
    from app.config import settings

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    user = make_user(f"failure-{uuid.uuid4().hex[:12]}@example.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    body = _payload(action="HOLD")
    if comment is not None:
        body["comment"] = comment
    return instance_id, body, _post(client, token, body)


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
def test_closed_writer_failures_are_suppressed(
    client, monkeypatch, code
):  # noqa: F811
    from app.services import hold_canonical_decision_evidence as evidence
    from app.services.r1b_evidence_safety import CanonicalDecisionPersistenceError

    def fail_writer(*args, **kwargs):
        raise CanonicalDecisionPersistenceError(code)

    monkeypatch.setattr(evidence, "persist_canonical_signal_decision", fail_writer)
    instance_id, body, response = _post_hold(client, monkeypatch)
    _assert_authoritative_hold(response, body["signal_id"], instance_id)


def test_evidence_session_outage_is_suppressed(client, monkeypatch):  # noqa: F811
    from app.services import hold_canonical_decision_evidence as evidence

    def unavailable():
        raise RuntimeError("sensitive database path")

    monkeypatch.setattr(evidence, "session_scope", unavailable)
    instance_id, body, response = _post_hold(client, monkeypatch)
    _assert_authoritative_hold(response, body["signal_id"], instance_id)


def test_logging_failure_is_suppressed(client, monkeypatch):  # noqa: F811
    from app.services import hold_canonical_decision_evidence as evidence
    from app.services.r1b_evidence_safety import CanonicalDecisionPersistenceError

    def fail_writer(*args, **kwargs):
        raise CanonicalDecisionPersistenceError("PERSISTENCE_UNAVAILABLE")

    def fail_log(*args, **kwargs):
        raise RuntimeError("logger unavailable")

    monkeypatch.setattr(evidence, "persist_canonical_signal_decision", fail_writer)
    monkeypatch.setattr(evidence.logger, "warning", fail_log)
    instance_id, body, response = _post_hold(client, monkeypatch)
    _assert_authoritative_hold(response, body["signal_id"], instance_id)


def test_unexpected_helper_and_diagnostic_failures_are_suppressed(
    client, monkeypatch
):  # noqa: F811
    from app.services import hold_canonical_decision_evidence as evidence

    def fail(*args, **kwargs):
        raise RuntimeError("unexpected secret-bearing exception")

    monkeypatch.setattr(evidence, "persist_hold_decision_best_effort", fail)
    monkeypatch.setattr(evidence, "record_hold_decision_failure", fail)
    instance_id, body, response = _post_hold(client, monkeypatch)
    _assert_authoritative_hold(response, body["signal_id"], instance_id)


def test_failure_log_contains_only_closed_diagnostics(
    client, monkeypatch
):  # noqa: F811
    from app.services import hold_canonical_decision_evidence as evidence
    from app.services.r1b_evidence_safety import CanonicalDecisionPersistenceError

    secret = "nwk_" + "S" * 43

    def fail_writer(*args, **kwargs):
        raise CanonicalDecisionPersistenceError("INVALID_INPUT")

    messages: list[str] = []

    def capture_log(message, *args, **kwargs):
        messages.append(message % args)

    monkeypatch.setattr(evidence, "persist_canonical_signal_decision", fail_writer)
    monkeypatch.setattr(evidence.logger, "warning", capture_log)
    instance_id, body, response = _post_hold(client, monkeypatch, comment=secret)
    _assert_authoritative_hold(response, body["signal_id"], instance_id)
    rendered = "\n".join(messages)
    assert "event=r1b_hold_decision_evidence_failed" in rendered
    assert "error_code=INVALID_INPUT" in rendered
    assert secret not in rendered
    assert body["signal_id"] not in rendered
    assert "failure-" not in rendered


def test_owner_and_reference_mismatch_never_create_evidence(
    client, monkeypatch
):  # noqa: F811
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.services import hold_canonical_decision_evidence as evidence

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", False, raising=False
    )
    user = make_user(f"owner-a-{uuid.uuid4().hex[:8]}@example.com")
    other = make_user(f"owner-b-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user)
    other_instance = _make_instance(other)
    body = _payload(action="HOLD")
    response = _post(client, _issue_token(user, instance_id), body)
    assert response.status_code == 202
    with session_scope() as db:
        event_id = db.scalar(select(models.WebhookEvent.id))

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    evidence.persist_hold_decision_best_effort(
        webhook_event_id=event_id,
        strategy_instance_id=instance_id,
        owner_user_id=other.id,
    )
    evidence.persist_hold_decision_best_effort(
        webhook_event_id=event_id,
        strategy_instance_id=other_instance,
        owner_user_id=other.id,
    )
    evidence.persist_hold_decision_best_effort(
        webhook_event_id=uuid.uuid4(),
        strategy_instance_id=instance_id,
        owner_user_id=user.id,
    )
    assert _count(models.CanonicalSignalDecision) == 0


def test_authoritative_hold_failure_prevents_evidence_call(
    client, monkeypatch
):  # noqa: F811
    from app.config import settings
    from app.services import private_webhook_service as service

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    user = make_user(f"commit-fail-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    auth = service.authenticate_credential(token)
    payload = service.PrivateWebhookPayload.model_validate(_payload(action="HOLD"))
    evidence_calls = 0

    def fail_authoritative(*args, **kwargs):
        raise RuntimeError("authoritative transaction failed")

    def count_evidence(*args, **kwargs):
        nonlocal evidence_calls
        evidence_calls += 1

    monkeypatch.setattr(service, "_persist_hold", fail_authoritative)
    monkeypatch.setattr(
        service.hold_canonical_decision_evidence,
        "persist_hold_decision_best_effort",
        count_evidence,
    )
    with pytest.raises(RuntimeError, match="authoritative transaction failed"):
        service.ingest(auth, payload)
    assert evidence_calls == 0


def test_evidence_commit_failure_rolls_back_only_decision(
    client, monkeypatch
):  # noqa: F811
    from app.db import engine, models
    from app.services import hold_canonical_decision_evidence as evidence

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
    instance_id, body, response = _post_hold(client, monkeypatch)
    assert response.status_code == 202
    assert response.json() == {
        "ok": True,
        "signal_id": body["signal_id"],
        "status": "NO_OP",
        "reason": "HOLD",
    }
    assert _count(models.StrategySignal) == 1
    assert _count(models.CanonicalSignalOutcome) == 0
    assert _count(models.StrategySignalRejection) == 0
    assert _jobs(instance_id) == []


def test_rejected_holds_never_create_decisions(client, monkeypatch):  # noqa: F811
    from app.config import settings
    from app.db import models
    from app.services import strategy_instance_service

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    unknown = _post(client, "nwk_" + "x" * 43, _payload(action="HOLD"))
    assert unknown.status_code == 401

    user = make_user(f"revoked-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    strategy_instance_service.revoke_webhook_credential(user.id, instance_id)
    assert _post(client, token, _payload(action="HOLD")).status_code == 401

    user2 = make_user(f"timing-{uuid.uuid4().hex[:8]}@example.com")
    instance2 = _make_instance(user2)
    token2 = _issue_token(user2, instance2)
    stale = _payload(
        action="HOLD",
        signal_time=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    )
    future = _payload(
        action="HOLD",
        signal_time=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    )
    assert _post(client, token2, stale).status_code == 422
    assert _post(client, token2, future).status_code == 422
    assert _post(client, token2, _payload(action="INVALID")).status_code == 422
    assert _count(models.CanonicalSignalDecision) == 0


@pytest.mark.parametrize(
    ("status", "verification_mode"),
    [("paused", False), ("ready", True)],
)
def test_paused_and_verification_hold_behavior_is_preserved(
    client, monkeypatch, status, verification_mode
):  # noqa: F811
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    user = make_user(f"lifecycle-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user, status=status)
    if verification_mode:
        with session_scope() as db:
            db.get(
                models.StrategyInstance, uuid.UUID(instance_id)
            ).verification_mode = True
    response = _post(
        client,
        _issue_token(user, instance_id),
        _payload(action="HOLD"),
    )
    assert response.status_code == 202
    assert response.json()["status"] == "NO_OP"
    assert _count(models.CanonicalSignalDecision) == 1
    assert _jobs(instance_id) == []
