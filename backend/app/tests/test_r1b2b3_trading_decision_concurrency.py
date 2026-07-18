"""R1B-2B3 concurrency: SQLite thread-level; PostgreSQL is authoritative via
scripts/r1b2b3_pg_verify.py."""
# ruff: noqa: F811
from __future__ import annotations

import threading
import uuid

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


@pytest.mark.parametrize("action", ["BUY_CE", "BUY_PE", "EXIT"])
def test_concurrent_identical_actions_create_one_of_everything(
    client, monkeypatch, action
):
    from app.config import settings
    from app.db import models

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    user = make_user(f"race-{action.lower()}-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    body = _payload(action=action, signal_id=f"race-{uuid.uuid4().hex[:8]}")
    statuses: list[int] = []

    def fire():
        statuses.append(_post(client, token, body).status_code)

    threads = [threading.Thread(target=fire) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses.count(202) == 1
    assert all(status in {200, 202} for status in statuses)
    assert _count(models.StrategySignal) == 1
    assert len(_jobs(instance_id)) == 1
    assert _count(models.CanonicalSignalDecision) == 1


def test_concurrent_conflicting_duplicate_preserves_winner_decision(
    client, monkeypatch
):
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    user = make_user(f"conf-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    signal_id = f"conf-{uuid.uuid4().hex[:8]}"
    bodies = [
        _payload(action="BUY_CE", signal_id=signal_id, comment="first"),
        _payload(action="BUY_PE", signal_id=signal_id, comment="second"),
    ]
    barrier = threading.Barrier(2)
    results: list[tuple[int, str]] = []

    def fire(body):
        barrier.wait()
        response = _post(client, token, body)
        results.append((response.status_code, body["action"]))

    threads = [threading.Thread(target=fire, args=(body,)) for body in bodies]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    codes = sorted(code for code, _ in results)
    assert codes == [202, 409]
    winner_action = next(action for code, action in results if code == 202)
    assert _count(models.StrategySignal) == 1
    assert len(_jobs(instance_id)) == 1
    assert _count(models.CanonicalSignalDecision) == 1
    with session_scope() as db:
        decision = db.scalar(select(models.CanonicalSignalDecision))
        assert decision.wire_action == winner_action


def test_concurrent_direct_evidence_attempts_are_idempotent(client, monkeypatch):
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.services import trading_canonical_decision_evidence as evidence

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", False, raising=False
    )
    user = make_user(f"ev-race-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user)
    assert _post(client, _issue_token(user, instance_id), _payload(action="EXIT")).status_code == 202
    with session_scope() as db:
        event_id = db.scalar(select(models.WebhookEvent.id))
    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )

    def persist():
        evidence.persist_trading_decision_best_effort(
            webhook_event_id=event_id,
            strategy_instance_id=instance_id,
            owner_user_id=user.id,
        )

    threads = [threading.Thread(target=persist) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert _count(models.CanonicalSignalDecision) == 1
