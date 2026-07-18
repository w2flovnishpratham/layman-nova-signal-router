"""R1B-2B3 integration contract: fresh committed trading-action decisions."""
# ruff: noqa: F811
from __future__ import annotations

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

EXPECTED_MAPPING = {
    "BUY_CE": ("STRATEGY_SIGNAL", "BULLISH", "DIRECTIONAL_SIGNAL", "ENTRY", "BUY", "CE"),
    "BUY_PE": ("STRATEGY_SIGNAL", "BEARISH", "DIRECTIONAL_SIGNAL", "ENTRY", "BUY", "PE"),
    "EXIT": ("STRATEGY_SIGNAL", "FLAT", "EXPLICIT_EXIT", "EXIT", "SELL", None),
}


def _rows(model):
    from app.db.engine import session_scope

    with session_scope() as db:
        return db.scalars(select(model)).all()


def _count(model) -> int:
    from app.db.engine import session_scope

    with session_scope() as db:
        return db.scalar(select(func.count()).select_from(model))


def _new_action(client, monkeypatch, action, *, enabled: bool, comment=None, **make_kwargs):
    from app.config import settings

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", enabled, raising=False
    )
    user = make_user(f"t2b3-{uuid.uuid4().hex[:12]}@example.com")
    instance_id = _make_instance(user, **make_kwargs)
    token = _issue_token(user, instance_id)
    body = _payload(action=action)
    if comment is not None:
        body["comment"] = comment
    return user, instance_id, token, body, _post(client, token, body)


def test_flag_false_is_a_true_no_op_before_session(monkeypatch):
    from app.config import settings
    from app.services import trading_canonical_decision_evidence as evidence

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", False, raising=False
    )

    def forbidden_session():
        raise AssertionError("disabled evidence opened a database session")

    monkeypatch.setattr(evidence, "session_scope", forbidden_session)
    evidence.persist_trading_decision_best_effort(
        webhook_event_id=None,
        strategy_instance_id="not-a-uuid",
        owner_user_id="not-a-uuid",
    )


def test_flag_is_evaluated_exactly_once(monkeypatch, mu_db):  # noqa: F811
    from app.services import trading_canonical_decision_evidence as evidence

    class _CountingSettings:
        def __init__(self):
            self.reads = 0

        def __getattr__(self, name):
            raise AssertionError(f"unexpected settings access: {name}")

        @property
        def R1B_CANONICAL_DECISION_PERSISTENCE(self):  # noqa: N802
            self.__dict__["reads"] = self.__dict__.get("reads", 0) + 1
            return False

    counting = _CountingSettings()
    monkeypatch.setattr(evidence, "settings", counting)
    evidence.persist_trading_decision_best_effort(
        webhook_event_id=None, strategy_instance_id="x", owner_user_id="y"
    )
    assert counting.__dict__["reads"] == 1


@pytest.mark.parametrize("action", ["BUY_CE", "BUY_PE", "EXIT"])
def test_fresh_action_persists_exact_committed_decision(client, monkeypatch, action):
    from app.db import models
    from app.domain.legacy_signal_adapter import LEGACY_ADAPTER_VERSION
    from app.services.private_webhook_service import (
        PrivateWebhookPayload,
        payload_fingerprint,
    )

    user, instance_id, _, body, response = _new_action(
        client, monkeypatch, action, enabled=True, comment="f" * 64
    )
    assert response.status_code == 202
    assert response.json() == {
        "ok": True,
        "signal_id": body["signal_id"],
        "status": "QUEUED",
        "action": action,
    }
    decisions = _rows(models.CanonicalSignalDecision)
    events = _rows(models.WebhookEvent)
    signals = _rows(models.StrategySignal)
    assert len(decisions) == len(events) == len(signals) == 1
    decision, event, signal = decisions[0], events[0], signals[0]
    jobs = _jobs(instance_id)
    assert len(jobs) == 1

    event_type, desired, reason, compat_action, compat_side, compat_option = (
        EXPECTED_MAPPING[action]
    )
    assert decision.strategy_signal_id == signal.id
    assert decision.webhook_event_id == event.id
    assert decision.user_id == user.id
    assert str(decision.strategy_instance_id) == instance_id
    assert decision.wire_action == action
    assert decision.event_type == event_type
    assert decision.desired_state == desired
    assert decision.intent_reason == reason
    assert decision.compatibility_action == compat_action
    assert decision.compatibility_side == compat_side
    assert decision.compatibility_option_side == compat_option
    assert decision.adapter_version == LEGACY_ADAPTER_VERSION
    assert decision.provenance_kind == "LIVE"
    assert decision.source == "private_tradingview_webhook"
    # Exact committed fingerprint; a 64-hex user comment cannot replace it.
    assert decision.payload_fingerprint == event.event_metadata["payload_fingerprint"]
    assert decision.payload_fingerprint != body["comment"]
    assert decision.payload_fingerprint == payload_fingerprint(
        PrivateWebhookPayload(**body), action
    )
    assert _count(models.CanonicalSignalOutcome) == 0
    assert _count(models.StrategySignalRejection) == 0
    assert "decision" not in response.text.lower()


@pytest.mark.parametrize("action", ["BUY_CE", "BUY_PE", "EXIT"])
def test_flag_does_not_change_trading_response(client, monkeypatch, action):
    _, _, _, _, disabled = _new_action(client, monkeypatch, action, enabled=False)
    _, _, _, _, enabled = _new_action(client, monkeypatch, action, enabled=True)
    disabled_body = disabled.json()
    enabled_body = enabled.json()
    disabled_body["signal_id"] = "<signal>"
    enabled_body["signal_id"] = "<signal>"
    assert (
        disabled.status_code,
        disabled_body,
        disabled.headers["content-type"],
    ) == (enabled.status_code, enabled_body, enabled.headers["content-type"])


@pytest.mark.parametrize("action", ["BUY_CE", "BUY_PE", "EXIT"])
def test_duplicate_never_backfills_missing_decision(client, monkeypatch, action):
    from app.config import settings
    from app.db import models

    _, _, token, body, first = _new_action(client, monkeypatch, action, enabled=False)
    assert first.status_code == 202
    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    duplicate = _post(client, token, body)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert _count(models.CanonicalSignalDecision) == 0

    other = "BUY_PE" if action != "BUY_PE" else "BUY_CE"
    conflict = _post(client, token, dict(body, action=other))
    assert conflict.status_code == 409
    assert _count(models.CanonicalSignalDecision) == 0


def test_signal_and_job_rows_are_untouched_by_evidence(client, monkeypatch):
    from app.db import models
    from app.db.engine import session_scope

    _, instance_id, _, body, response = _new_action(
        client, monkeypatch, "BUY_CE", enabled=True
    )
    assert response.status_code == 202
    with session_scope() as db:
        signal = db.scalar(select(models.StrategySignal))
        job = db.scalar(select(models.StrategyExecutionJob))
        assert signal.status == "queued"
        assert signal.result_summary["action"] == "BUY_CE"
        assert job.status == "queued"
        assert job.lots == 1
        assert job.execution_mode == "paper_live_data"
        assert job.signal_payload["raw_payload"]["normalized_action"] == "BUY_CE"
        assert job.strategy_signal_id == signal.id


@pytest.mark.parametrize(
    ("action", "make_kwargs"),
    [
        ("BUY_CE", {"status": "paused"}),
        ("BUY_PE", {"status": "paused"}),
        ("EXIT", {"status": "paused"}),
    ],
)
def test_paused_accepted_actions_record_intent(client, monkeypatch, action, make_kwargs):
    from app.db import models

    _, instance_id, _, _, response = _new_action(
        client, monkeypatch, action, enabled=True, **make_kwargs
    )
    assert response.status_code == 202
    assert _count(models.CanonicalSignalDecision) == 1
    assert len(_jobs(instance_id)) == 1


@pytest.mark.parametrize("action", ["BUY_CE", "BUY_PE", "EXIT"])
def test_verification_mode_accepted_actions_record_intent(client, monkeypatch, action):
    from app.db import models
    from app.db.engine import session_scope

    from app.config import settings

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    user = make_user(f"verif-{uuid.uuid4().hex[:8]}@example.com")
    instance_id = _make_instance(user, status="ready")
    with session_scope() as db:
        db.get(models.StrategyInstance, uuid.UUID(instance_id)).verification_mode = True
    response = _post(client, _issue_token(user, instance_id), _payload(action=action))
    assert response.status_code == 202
    assert _count(models.CanonicalSignalDecision) == 1


@pytest.mark.parametrize(
    ("signal_status", "job_status", "summary_extra"),
    [
        ("processing", "running", {}),
        ("completed", "completed", {"status": "NO_OP", "reason": "ALREADY_IN_CE"}),
        ("completed_with_errors", "failed", {"status": "error"}),
    ],
)
def test_worker_ahead_states_still_record_decisions(
    client, monkeypatch, signal_status, job_status, summary_extra
):
    """The worker may claim/finish the job before evidence runs; the decision
    binds to immutable accepted intent, never transient status."""
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.services import trading_canonical_decision_evidence as evidence

    user, instance_id, _, _, response = _new_action(
        client, monkeypatch, "BUY_CE", enabled=False
    )
    assert response.status_code == 202
    with session_scope() as db:
        signal = db.scalar(select(models.StrategySignal))
        job = db.scalar(select(models.StrategyExecutionJob))
        signal.status = signal_status
        summary = dict(signal.result_summary)
        summary.update(summary_extra)
        signal.result_summary = summary
        job.status = job_status
        job.attempts = 1
        event_id = db.scalar(select(models.WebhookEvent.id))

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    evidence.persist_trading_decision_best_effort(
        webhook_event_id=event_id,
        strategy_instance_id=instance_id,
        owner_user_id=user.id,
    )
    assert _count(models.CanonicalSignalDecision) == 1
    decisions = _rows(models.CanonicalSignalDecision)
    assert decisions[0].wire_action == "BUY_CE"


def test_hold_still_uses_the_hold_helper_only(client, monkeypatch):
    """Regression freeze: the trading helper never runs for HOLD."""
    from app.db import models
    from app.services import trading_canonical_decision_evidence as evidence

    calls = []

    def spy(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(evidence, "persist_trading_decision_best_effort", spy)
    _, _, _, body, response = _new_action(client, monkeypatch, "HOLD", enabled=True)
    assert response.status_code == 202
    assert calls == []
    assert _count(models.CanonicalSignalDecision) == 1  # via the HOLD helper
