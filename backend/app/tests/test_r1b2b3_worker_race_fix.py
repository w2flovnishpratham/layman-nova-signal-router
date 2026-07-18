"""R1B-2B3F regression proofs for immutable trading-decision evidence."""
# ruff: noqa: F811
from __future__ import annotations

import ast
import inspect
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_private_webhook import (  # noqa: F401
    _issue_token,
    _make_instance,
    _payload,
    _post,
    client,
    webhook_enabled,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TRADING_HELPER = (
    BACKEND_ROOT / "app" / "services" / "trading_canonical_decision_evidence.py"
)
DECISION_WRITER = (
    BACKEND_ROOT / "app" / "services" / "canonical_signal_decision_persistence.py"
)


def _count(model) -> int:
    from app.db.engine import session_scope

    with session_scope() as db:
        return db.scalar(select(func.count()).select_from(model))


def _accepted_without_evidence(client, monkeypatch, action: str):
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", False, raising=False
    )
    user = make_user(f"race-fix-{uuid.uuid4().hex[:10]}@example.com")
    instance_id = _make_instance(user)
    body = _payload(action=action)
    response = _post(client, _issue_token(user, instance_id), body)
    assert response.status_code == 202
    with session_scope() as db:
        signal = db.scalar(select(models.StrategySignal))
        job = db.scalar(select(models.StrategyExecutionJob))
        event_id = db.scalar(select(models.WebhookEvent.id))
        job_snapshot = {
            "id": job.id,
            "strategy_signal_id": signal.id,
        }
    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    return user, instance_id, body, response, event_id, job_snapshot


def _run_evidence(event_id, instance_id, user_id) -> None:
    from app.services import trading_canonical_decision_evidence as evidence

    evidence.persist_trading_decision_best_effort(
        webhook_event_id=event_id,
        strategy_instance_id=instance_id,
        owner_user_id=user_id,
    )


@pytest.mark.parametrize("action", ["BUY_CE", "BUY_PE", "EXIT"])
@pytest.mark.parametrize(
    ("job_status", "result", "error"),
    [
        ("completed", {"status": "traded", "order_status": "TRADED"}, None),
        ("failed", None, "closed worker failure"),
        ("completed", {"status": "NO_OP", "reason": "ALREADY_POSITIONED"}, None),
    ],
    ids=["completed-traded", "failed", "blocked-no-op"],
)
def test_real_worker_completion_preserves_accepted_decision(
    client, monkeypatch, action, job_status, result, error
):
    from app.db import models
    from app.db.engine import session_scope
    from app.workers import strategy_job_worker

    user, instance_id, _, response, event_id, job = _accepted_without_evidence(
        client, monkeypatch, action
    )
    strategy_job_worker._complete_job(  # noqa: SLF001 - required real worker proof
        job, status=job_status, result=result, error=error
    )
    with session_scope() as db:
        signal = db.scalar(select(models.StrategySignal))
        assert signal.status in {"completed", "completed_with_errors"}
        assert "action" not in signal.result_summary

    _run_evidence(event_id, instance_id, user.id)
    with session_scope() as db:
        decision = db.scalar(select(models.CanonicalSignalDecision))
        signal = db.scalar(select(models.StrategySignal))
        job_row = db.scalar(select(models.StrategyExecutionJob))
        assert decision.wire_action == action
        assert signal.status in {"completed", "completed_with_errors"}
        assert job_row.status == job_status
    assert response.json()["action"] == action
    assert _count(models.CanonicalSignalOutcome) == 0
    assert _count(models.StrategySignalRejection) == 0


@pytest.mark.parametrize(
    ("action", "summary"),
    [
        ("BUY_CE", {}),
        ("BUY_CE", {"action": "HOLD"}),
        ("BUY_CE", {"action": "BUY_PE"}),
        ("BUY_PE", {}),
        ("BUY_PE", {"action": "HOLD"}),
        ("BUY_PE", {"action": "EXIT"}),
        ("EXIT", {}),
        ("EXIT", {"action": "HOLD"}),
        ("EXIT", {"action": "BUY_CE"}),
    ],
)
def test_mutable_summary_cannot_select_or_suppress_action(
    client, monkeypatch, action, summary
):
    from app.db import models
    from app.db.engine import session_scope

    user, instance_id, _, _, event_id, _ = _accepted_without_evidence(
        client, monkeypatch, action
    )
    with session_scope() as db:
        signal = db.scalar(select(models.StrategySignal))
        signal.result_summary = summary

    _run_evidence(event_id, instance_id, user.id)
    with session_scope() as db:
        assert db.scalar(select(models.CanonicalSignalDecision)).wire_action == action


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("normalized_action", "BUY_PE"),
        ("strategy_instance_id", "00000000-0000-0000-0000-000000000000"),
        ("signal_id", "foreign-signal"),
    ],
)
def test_immutable_event_job_mismatch_fails_closed(
    client, monkeypatch, field, replacement
):
    from app.db import models
    from app.db.engine import session_scope

    user, instance_id, _, _, event_id, _ = _accepted_without_evidence(
        client, monkeypatch, "BUY_CE"
    )
    with session_scope() as db:
        job = db.scalar(select(models.StrategyExecutionJob))
        changed = dict(job.signal_payload)
        raw = dict(changed["raw_payload"])
        raw[field] = replacement
        changed["raw_payload"] = raw
        job.signal_payload = changed
        before_status = (job.status, job.attempts)

    _run_evidence(event_id, instance_id, user.id)
    with session_scope() as db:
        job = db.scalar(select(models.StrategyExecutionJob))
        assert (job.status, job.attempts) == before_status
    assert _count(models.CanonicalSignalDecision) == 0


def test_owner_scoped_job_lookup_selects_no_foreign_owner(
    client, monkeypatch
):
    from app.db import models
    from app.db.engine import session_scope

    user, instance_id, _, _, event_id, _ = _accepted_without_evidence(
        client, monkeypatch, "EXIT"
    )
    foreign = make_user(f"foreign-job-{uuid.uuid4().hex[:10]}@example.com")
    with session_scope() as db:
        job = db.scalar(select(models.StrategyExecutionJob))
        job.user_id = foreign.id
        before = (job.status, job.attempts, job.signal_payload)

    _run_evidence(event_id, instance_id, user.id)
    with session_scope() as db:
        job = db.scalar(select(models.StrategyExecutionJob))
        assert (job.status, job.attempts, job.signal_payload) == before
    assert _count(models.CanonicalSignalDecision) == 0


@pytest.mark.parametrize("action", ["BUY_CE", "BUY_PE", "EXIT"])
@pytest.mark.parametrize("reenable_after_writer_check", [False, True])
def test_writer_gate_fails_closed_after_helper_gate(
    client, monkeypatch, action, reenable_after_writer_check
):
    from app.config import settings
    from app.db import models
    from app.services import trading_canonical_decision_evidence as evidence

    monkeypatch.setattr(
        settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False
    )
    real_writer = evidence.persist_canonical_signal_decision

    def disable_at_writer(*args, **kwargs):
        settings.R1B_CANONICAL_DECISION_PERSISTENCE = False
        try:
            return real_writer(*args, **kwargs)
        finally:
            if reenable_after_writer_check:
                settings.R1B_CANONICAL_DECISION_PERSISTENCE = True

    monkeypatch.setattr(evidence, "persist_canonical_signal_decision", disable_at_writer)
    user = make_user(f"dual-gate-{uuid.uuid4().hex[:10]}@example.com")
    instance_id = _make_instance(user)
    body = _payload(action=action)
    response = _post(client, _issue_token(user, instance_id), body)

    assert response.status_code == 202
    assert response.json() == {
        "ok": True,
        "signal_id": body["signal_id"],
        "status": "QUEUED",
        "action": action,
    }
    assert _count(models.StrategySignal) == 1
    assert _count(models.StrategyExecutionJob) == 1
    assert _count(models.CanonicalSignalDecision) == 0
    assert _count(models.CanonicalSignalOutcome) == 0
    assert _count(models.StrategySignalRejection) == 0


def test_static_immutable_action_owner_and_dual_gate_contract():
    from app.services import trading_canonical_decision_evidence as evidence

    helper_source = TRADING_HELPER.read_text(encoding="utf-8")
    helper_tree = ast.parse(helper_source)
    helper = next(
        node
        for node in helper_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "persist_trading_decision_best_effort"
    )
    assert "result_summary" not in ast.unparse(helper)
    assert "signal_payload" in ast.unparse(helper)
    assert "normalized_action" in helper_source
    assert "StrategyExecutionJob.user_id == owner_uuid" in helper_source

    parameters = inspect.signature(
        evidence.persist_trading_decision_best_effort
    ).parameters
    assert "action" not in parameters
    assert "payload_fingerprint" not in parameters

    writer_source = DECISION_WRITER.read_text(encoding="utf-8")
    assert "R1B_CANONICAL_DECISION_PERSISTENCE" in writer_source
    for bypass in ("skip_flag_check", "already_authorized", "force_persist"):
        assert bypass not in helper_source
        assert bypass not in writer_source
