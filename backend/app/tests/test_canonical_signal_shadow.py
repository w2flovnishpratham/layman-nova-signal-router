from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_private_webhook import (
    _issue_token,
    _jobs,
    _make_instance,
    _payload,
    _post,
    client,  # noqa: F401
    webhook_enabled,  # noqa: F401
)


def _signals(instance_id):
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        return db.scalars(
            select(models.StrategySignal).where(
                models.StrategySignal.strategy_name == f"instance:{instance_id}"
            )
        ).all()


def _event_hash(instance_id, signal_id):
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        row = db.scalar(select(models.WebhookEvent).where(
            models.WebhookEvent.provider == f"instance-webhook:{instance_id}",
            models.WebhookEvent.event_id == signal_id,
        ))
        return row.raw_body_sha256


@pytest.mark.parametrize("shadow", [False, True])
@pytest.mark.parametrize(("action", "jobs"), [("BUY_CE", 1), ("BUY_PE", 1), ("EXIT", 1), ("HOLD", 0)])
def test_shadow_flag_preserves_response_and_exact_job_count(client, monkeypatch, shadow, action, jobs):
    from app.config import settings

    monkeypatch.setattr(settings, "CANONICAL_SIGNAL_SHADOW", shadow)
    user = make_user(f"shadow-{shadow}-{action.lower()}@example.com")
    instance_id = _make_instance(user)
    response = _post(client, _issue_token(user, instance_id), _payload(action=action))
    assert response.status_code == 202
    assert len(_jobs(instance_id)) == jobs
    assert len(_signals(instance_id)) == 1
    if action == "HOLD":
        assert _signals(instance_id)[0].result_summary["reason"] == "HOLD"


@pytest.mark.parametrize("shadow", [False, True])
def test_shadow_preserves_fingerprint_duplicate_and_conflict_behavior(client, monkeypatch, shadow):
    from app.config import settings
    from app.services.private_webhook_service import PrivateWebhookPayload, payload_fingerprint

    monkeypatch.setattr(settings, "CANONICAL_SIGNAL_SHADOW", shadow)
    user = make_user(f"shadow-replay-{shadow}@example.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    body = _payload(action="BUY_CE", signal_id="stable-shadow-id")
    first = _post(client, token, body)
    duplicate = _post(client, token, body)
    conflict = _post(client, token, {**body, "action": "BUY_PE"})
    assert (first.status_code, duplicate.status_code, conflict.status_code) == (202, 200, 409)
    assert len(_jobs(instance_id)) == 1
    expected_fingerprint = payload_fingerprint(PrivateWebhookPayload.model_validate(body), "BUY_CE")
    assert _event_hash(instance_id, body["signal_id"]) == hashlib.sha256(expected_fingerprint.encode()).hexdigest()


def test_shadow_mismatch_logs_safely_and_cannot_change_execution(client, monkeypatch):
    from app.config import settings
    from app.domain import legacy_signal_adapter
    from app.services import private_webhook_service

    monkeypatch.setattr(settings, "CANONICAL_SIGNAL_SHADOW", True)
    original = legacy_signal_adapter.canonical_to_normalized_signal

    def wrong(event, **kwargs):
        signal = original(event, **kwargs)
        return signal.model_copy(update={"option_side": "PE"}) if signal else None

    records = []
    monkeypatch.setattr(legacy_signal_adapter, "canonical_to_normalized_signal", wrong)
    monkeypatch.setattr(private_webhook_service, "log_error_event", lambda *args, **kwargs: records.append((args, kwargs)))
    user = make_user("shadow-mismatch@example.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    response = _post(client, token, _payload(action="BUY_CE"))
    assert response.status_code == 202
    assert _jobs(instance_id)[0]["signal_payload"]["option_side"] == "CE"
    assert len(_jobs(instance_id)) == len(_signals(instance_id)) == 1
    assert records and records[0][0][0] == "CANONICAL_SIGNAL_SHADOW_MISMATCH"
    serialized = repr(records)
    assert token not in serialized and "credential" not in serialized.lower()


def test_shadow_error_does_not_break_pause_or_owner_binding(client, monkeypatch):
    from app.config import settings
    from app.domain import legacy_signal_adapter

    monkeypatch.setattr(settings, "CANONICAL_SIGNAL_SHADOW", True)
    monkeypatch.setattr(legacy_signal_adapter, "adapt_legacy_action", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("shadow")))
    owner = make_user("shadow-owner@example.com")
    other = make_user("shadow-other@example.com")
    paused_id = _make_instance(owner, status="paused")
    other_id = _make_instance(other)
    owner_token = _issue_token(owner, paused_id)
    assert _post(client, owner_token, _payload(action="EXIT")).status_code == 202
    assert len(_jobs(paused_id)) == 1
    # The credential still cannot select or affect the other user's instance.
    assert _jobs(other_id) == []


@pytest.mark.parametrize("shadow", [False, True])
def test_verification_mode_behavior_is_unchanged(client, monkeypatch, shadow):
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope

    monkeypatch.setattr(settings, "CANONICAL_SIGNAL_SHADOW", shadow)
    user = make_user(f"shadow-verification-{shadow}@example.com")
    instance_id = _make_instance(user, status="ready", execution_mode="paper_live_data")
    with session_scope() as db:
        instance = db.get(models.StrategyInstance, uuid.UUID(instance_id))
        instance.verification_mode = True
    response = _post(
        client,
        _issue_token(user, instance_id),
        _payload(action="BUY_CE"),
    )
    assert response.status_code == 202
    assert len(_jobs(instance_id)) == len(_signals(instance_id)) == 1


def test_shadow_observability_failure_cannot_reject_webhook(client, monkeypatch):
    from app.config import settings
    from app.domain import legacy_signal_adapter
    from app.services import private_webhook_service

    monkeypatch.setattr(settings, "CANONICAL_SIGNAL_SHADOW", True)
    monkeypatch.setattr(
        legacy_signal_adapter,
        "adapt_legacy_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("shadow")),
    )
    monkeypatch.setattr(
        private_webhook_service,
        "log_error_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("logger")),
    )
    user = make_user("shadow-logger@example.com")
    instance_id = _make_instance(user)
    response = _post(client, _issue_token(user, instance_id), _payload(action="BUY_CE"))
    assert response.status_code == 202
    assert len(_jobs(instance_id)) == len(_signals(instance_id)) == 1


def test_registry_failure_is_isolated_from_webhook_ingestion(client, monkeypatch):
    from app.config import settings
    from app.domain import pine_capabilities

    monkeypatch.setattr(settings, "CANONICAL_SIGNAL_SHADOW", True)
    monkeypatch.setattr(
        pine_capabilities,
        "load_registry",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("invalid registry")),
    )
    user = make_user("shadow-registry-isolation@example.com")
    instance_id = _make_instance(user)
    response = _post(client, _issue_token(user, instance_id), _payload(action="HOLD"))
    assert response.status_code == 202
    assert _jobs(instance_id) == []
    assert _signals(instance_id)[0].result_summary["reason"] == "HOLD"


def test_invalid_shadow_environment_value_fails_closed(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("CANONICAL_SIGNAL_SHADOW", "definitely")
    assert Settings(_env_file=None).CANONICAL_SIGNAL_SHADOW is False
