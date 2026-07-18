"""R1B-2B integration contract: fresh committed HOLD decisions only."""
# ruff: noqa: F811
from __future__ import annotations

import hashlib
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


def _rows(model):
    from app.db.engine import session_scope

    with session_scope() as db:
        return db.scalars(select(model)).all()


def _count(model) -> int:
    from app.db.engine import session_scope

    with session_scope() as db:
        return db.scalar(select(func.count()).select_from(model))


def _signature(response) -> tuple[int, bytes, str]:
    return response.status_code, response.content, response.headers["content-type"]


def _new_hold(client, monkeypatch, *, enabled: bool, comment: str | None = None):
    from app.config import settings

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", enabled, raising=False
    )
    user = make_user(f"r1b2b-{uuid.uuid4().hex[:12]}@example.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    body = _payload(action="HOLD")
    if comment is not None:
        body["comment"] = comment
    return user, instance_id, token, body, _post(client, token, body)


def test_flag_false_is_a_true_no_op_before_session(monkeypatch):
    from app.config import settings
    from app.services import hold_canonical_decision_evidence as evidence

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", False, raising=False
    )

    def forbidden_session():
        raise AssertionError("disabled evidence opened a database session")

    monkeypatch.setattr(evidence, "session_scope", forbidden_session)
    evidence.persist_hold_decision_best_effort(
        webhook_event_id=None,
        strategy_instance_id="not-a-uuid",
        owner_user_id="not-a-uuid",
    )


def test_fresh_hold_persists_exact_committed_evidence(client, monkeypatch):  # noqa: F811
    from app.db import models
    from app.domain.legacy_signal_adapter import LEGACY_ADAPTER_VERSION
    from app.services.private_webhook_service import payload_fingerprint
    from app.services.private_webhook_service import PrivateWebhookPayload

    user, instance_id, _, body, response = _new_hold(
        client,
        monkeypatch,
        enabled=True,
        comment="f" * 64,
    )
    assert response.status_code == 202
    assert response.json() == {
        "ok": True,
        "signal_id": body["signal_id"],
        "status": "NO_OP",
        "reason": "HOLD",
    }
    decisions = _rows(models.CanonicalSignalDecision)
    events = _rows(models.WebhookEvent)
    signals = _rows(models.StrategySignal)
    assert len(decisions) == len(events) == len(signals) == 1
    decision, event, signal = decisions[0], events[0], signals[0]

    assert decision.strategy_signal_id == signal.id
    assert decision.webhook_event_id == event.id
    assert decision.user_id == user.id
    assert str(decision.strategy_instance_id) == instance_id
    assert decision.wire_action == "HOLD"
    assert decision.event_type == "CONNECTIVITY_TEST"
    assert decision.desired_state == "NONE"
    assert decision.intent_reason == "CONNECTIVITY_TEST"
    assert decision.compatibility_action is None
    assert decision.compatibility_side is None
    assert decision.compatibility_option_side is None
    assert decision.adapter_version == LEGACY_ADAPTER_VERSION
    assert decision.source == "private_tradingview_webhook"
    assert decision.provenance_kind == "LIVE"
    assert decision.payload_fingerprint == event.event_metadata["payload_fingerprint"]
    assert decision.payload_fingerprint != body["comment"]
    expected_fingerprint = payload_fingerprint(PrivateWebhookPayload(**body), "HOLD")
    assert decision.payload_fingerprint == expected_fingerprint
    assert event.raw_body_sha256 == hashlib.sha256(
        expected_fingerprint.encode("utf-8")
    ).hexdigest()
    assert _jobs(instance_id) == []
    assert _count(models.CanonicalSignalOutcome) == 0
    assert _count(models.StrategySignalRejection) == 0
    assert "decision" not in response.text.lower()


def test_flag_does_not_change_hold_response(client, monkeypatch):  # noqa: F811
    _, _, _, _, disabled = _new_hold(client, monkeypatch, enabled=False)
    _, _, _, _, enabled = _new_hold(client, monkeypatch, enabled=True)
    disabled_body = disabled.json()
    enabled_body = enabled.json()
    disabled_body["signal_id"] = "<signal>"
    enabled_body["signal_id"] = "<signal>"
    assert (
        disabled.status_code,
        disabled_body,
        disabled.headers["content-type"],
    ) == (
        enabled.status_code,
        enabled_body,
        enabled.headers["content-type"],
    )


def test_duplicate_never_backfills_missing_decision(client, monkeypatch):  # noqa: F811
    from app.config import settings
    from app.db import models

    _, _, token, body, first = _new_hold(client, monkeypatch, enabled=False)
    assert first.status_code == 202
    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    duplicate = _post(client, token, body)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert _count(models.CanonicalSignalDecision) == 0

    conflict = _post(client, token, dict(body, comment="different"))
    assert conflict.status_code == 409
    assert _count(models.CanonicalSignalDecision) == 0


@pytest.mark.parametrize("action", ["BUY_CE", "BUY_PE", "EXIT"])
def test_trading_actions_never_persist_decisions(
    client, monkeypatch, action
):  # noqa: F811
    from app.config import settings
    from app.db import models

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    user = make_user(f"trade-{action.lower()}-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user)
    response = _post(client, _issue_token(user, instance_id), _payload(action=action))
    assert response.status_code == 202
    assert _count(models.CanonicalSignalDecision) == 0


def test_hold_remains_valid_setup_evidence_without_job(client, monkeypatch):  # noqa: F811
    from app.db import models
    from app.db.engine import session_scope
    from app.services.tradingview_setup_service import _validate_signal_evidence

    _, instance_id, _, body, response = _new_hold(client, monkeypatch, enabled=True)
    assert response.status_code == 202
    with session_scope() as db:
        instance = db.get(models.StrategyInstance, uuid.UUID(instance_id))
        _validate_signal_evidence(db, None, instance, "HOLD", body["signal_id"])
    assert _jobs(instance_id) == []


def test_concurrent_identical_holds_create_one_signal_and_decision(
    client, monkeypatch
):  # noqa: F811
    from app.config import settings
    from app.db import models

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    user = make_user(f"race-{uuid.uuid4().hex[:10]}@example.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    body = _payload(action="HOLD", signal_id=f"hold-race-{uuid.uuid4().hex[:8]}")
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
    assert _count(models.CanonicalSignalDecision) == 1
    assert _jobs(instance_id) == []


def test_concurrent_conflicting_holds_create_no_extra_decision(
    client, monkeypatch
):  # noqa: F811
    from app.config import settings
    from app.db import models

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    user = make_user(f"conflict-race-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    signal_id = f"hold-conflict-{uuid.uuid4().hex[:8]}"
    bodies = [
        _payload(action="HOLD", signal_id=signal_id, comment="first"),
        _payload(action="HOLD", signal_id=signal_id, comment="second"),
    ]
    statuses: list[int] = []
    barrier = threading.Barrier(2)

    def fire(body):
        barrier.wait()
        statuses.append(_post(client, token, body).status_code)

    threads = [threading.Thread(target=fire, args=(body,)) for body in bodies]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(statuses) == [202, 409]
    assert _count(models.StrategySignal) == 1
    assert _count(models.CanonicalSignalDecision) == 1
    assert _jobs(instance_id) == []


def test_concurrent_evidence_attempts_are_idempotent(client, monkeypatch):  # noqa: F811
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.services import hold_canonical_decision_evidence as evidence

    user, instance_id, _, _, response = _new_hold(
        client, monkeypatch, enabled=False
    )
    assert response.status_code == 202
    with session_scope() as db:
        event_id = db.scalar(select(models.WebhookEvent.id))
    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )

    def persist():
        evidence.persist_hold_decision_best_effort(
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
