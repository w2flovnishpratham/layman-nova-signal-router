"""R1B-2A insert-only canonical decision writer."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.tests.conftest_multiuser import mu_db  # noqa: F401

NOW = datetime(2026, 7, 18, 9, 30, tzinfo=timezone.utc)
FINGERPRINT = "f" * 64


@pytest.fixture
def db(mu_db):  # noqa: F811
    from app.db.engine import session_scope

    with session_scope() as session:
        yield session


@pytest.fixture
def decision_flag_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False)


def seed_ingress(db, *, signal_id: str = "sig-1", action: str = "BUY_CE"):
    """One trusted post-ingress context: owner, instance, signal, event."""
    from app.db import models
    from app.domain.legacy_signal_adapter import adapt_legacy_action

    user = models.User(email=f"d-{uuid.uuid4().hex[:10]}@example.com")
    db.add(user)
    db.flush()
    strategy = models.StrategyCatalog(
        code=f"d-{uuid.uuid4().hex[:10]}", display_name="D", owner_type="personal",
        owner_user_id=user.id, visibility="private", status="active",
    )
    db.add(strategy)
    db.flush()
    version = models.StrategyVersion(
        strategy_id=strategy.id, version="1.0", payload_spec_version="nova.v1",
        source_journey="personal_tradingview", status="approved", execution_kind="external_webhook",
    )
    db.add(version)
    db.flush()
    instance = models.StrategyInstance(
        user_id=user.id, strategy_id=strategy.id, strategy_version_id=version.id,
        source_journey="PERSONAL_TRADINGVIEW", label=f"d-{uuid.uuid4().hex[:6]}",
    )
    db.add(instance)
    db.flush()
    signal = models.StrategySignal(
        strategy_name=f"instance:{instance.id}", signal_id=signal_id, status="queued",
    )
    event = models.WebhookEvent(
        provider=f"instance-webhook:{instance.id}", event_id=signal_id, raw_body_sha256=FINGERPRINT,
    )
    db.add_all([signal, event])
    db.flush()
    job = models.StrategyExecutionJob(
        strategy_signal_id=signal.id, user_id=user.id, strategy_name=signal.strategy_name,
        signal_id=signal_id, signal_payload={}, lots=1, execution_mode="paper_live_data",
    )
    db.add(job)
    db.flush()
    canonical_event = adapt_legacy_action(
        action, signal_id=signal_id, signal_time=NOW,
        strategy_version="legend-v3.1", timeframe="15m",
        metadata={"reference_price": 25000.5, "comment": "never persisted"},
    )
    return {
        "user": user, "instance": instance, "signal": signal, "event": event,
        "job": job, "canonical_event": canonical_event,
    }


def persist(db, ctx, **overrides):
    from app.services.canonical_signal_decision_persistence import (
        persist_canonical_signal_decision,
    )

    values = dict(
        strategy_signal=ctx["signal"],
        strategy_instance=ctx["instance"],
        owner_user_id=ctx["user"].id,
        canonical_event=ctx["canonical_event"],
        payload_fingerprint=FINGERPRINT,
        received_at=NOW,
        webhook_event=ctx["event"],
    )
    values.update(overrides)
    return persist_canonical_signal_decision(db, **values)


def decision_count(db) -> int:
    from app.db import models

    return db.scalar(select(func.count()).select_from(models.CanonicalSignalDecision))


class _Bomb:
    """Session stand-in that fails loudly if the writer touches it."""

    def __getattr__(self, name):
        raise AssertionError(f"session accessed before flag check: {name}")


def test_flag_off_refuses_before_any_session_access(db):
    from app.config import settings
    from app.services.canonical_signal_decision_persistence import (
        CanonicalDecisionPersistenceError,
    )

    assert settings.R1B_CANONICAL_DECISION_PERSISTENCE is False
    ctx = seed_ingress(db)
    with pytest.raises(CanonicalDecisionPersistenceError) as excinfo:
        persist(_Bomb(), ctx)
    assert excinfo.value.code == "PERSISTENCE_DISABLED"
    assert decision_count(db) == 0


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("BUY_CE", ("BUY_CE", "STRATEGY_SIGNAL", "BULLISH", "DIRECTIONAL_SIGNAL", "ENTRY", "BUY", "CE")),
        ("BUY_PE", ("BUY_PE", "STRATEGY_SIGNAL", "BEARISH", "DIRECTIONAL_SIGNAL", "ENTRY", "BUY", "PE")),
        ("EXIT", ("EXIT", "STRATEGY_SIGNAL", "FLAT", "EXPLICIT_EXIT", "EXIT", "SELL", None)),
        ("HOLD", ("HOLD", "CONNECTIVITY_TEST", "NONE", "CONNECTIVITY_TEST", None, None, None)),
    ],
)
def test_exact_action_mapping(db, decision_flag_on, action, expected):
    from app.domain.legacy_signal_adapter import LEGACY_ADAPTER_VERSION

    ctx = seed_ingress(db, action=action)
    row = persist(db, ctx)
    assert (
        row.wire_action, row.event_type, row.desired_state, row.intent_reason,
        row.compatibility_action, row.compatibility_side, row.compatibility_option_side,
    ) == expected
    assert row.strategy_signal_id == ctx["signal"].id
    assert row.webhook_event_id == ctx["event"].id
    assert row.user_id == ctx["user"].id
    assert row.strategy_instance_id == ctx["instance"].id
    assert row.adapter_version == LEGACY_ADAPTER_VERSION
    assert row.contract_version == "nova.canonical-signal.v1"
    assert row.payload_fingerprint == FINGERPRINT
    assert row.provenance_kind == "LIVE"
    assert row.source == "private_tradingview_webhook"
    # Closed metadata only: comment never survives; version/timeframe/price do.
    assert row.safe_metadata == {
        "strategy_version": "legend-v3.1", "timeframe": "15m", "reference_price": 25000.5,
    }
    db.flush()  # row satisfies every DB CHECK


def test_idempotent_reuse_and_single_row(db, decision_flag_on):
    ctx = seed_ingress(db)
    first = persist(db, ctx)
    second = persist(db, ctx)
    assert first.id == second.id
    assert decision_count(db) == 1


def test_conflicting_reinterpretation_never_updates(db, decision_flag_on):
    from app.domain.legacy_signal_adapter import adapt_legacy_action
    from app.services.canonical_signal_decision_persistence import (
        CanonicalDecisionPersistenceError,
    )

    ctx = seed_ingress(db, action="BUY_CE")
    original = persist(db, ctx)
    conflicting = adapt_legacy_action(
        "EXIT", signal_id=ctx["signal"].signal_id, signal_time=NOW,
    )
    with pytest.raises(CanonicalDecisionPersistenceError) as excinfo:
        persist(db, ctx, canonical_event=conflicting)
    assert excinfo.value.code == "CANONICAL_DECISION_CONFLICT"
    db.expire_all()
    assert decision_count(db) == 1
    from app.db import models

    row = db.get(models.CanonicalSignalDecision, original.id)
    assert row.wire_action == "BUY_CE"  # untouched


@pytest.mark.parametrize(
    ("override_builder", "code"),
    [
        (lambda ctx: {"adapter_version": "nova.legacy-signal-adapter.v2"}, "INVALID_INPUT"),
        (lambda ctx: {"provenance_kind": "REALTIME"}, "INVALID_INPUT"),
        (lambda ctx: {"source": "public_webhook"}, "INVALID_INPUT"),
        (lambda ctx: {"payload_fingerprint": "F" * 64}, "INVALID_INPUT"),
        (lambda ctx: {"payload_fingerprint": "f" * 63}, "INVALID_INPUT"),
        (lambda ctx: {"received_at": NOW.replace(tzinfo=None)}, "INVALID_INPUT"),
        (lambda ctx: {"canonical_event": "not-an-event"}, "INVALID_INPUT"),
    ],
)
def test_invalid_trusted_inputs_fail_closed(db, decision_flag_on, override_builder, code):
    from app.services.canonical_signal_decision_persistence import (
        CanonicalDecisionPersistenceError,
    )

    ctx = seed_ingress(db)
    with pytest.raises(CanonicalDecisionPersistenceError) as excinfo:
        persist(db, ctx, **override_builder(ctx))
    assert excinfo.value.code == code
    assert decision_count(db) == 0


def test_owner_instance_and_reference_isolation(db, decision_flag_on):
    from app.services.canonical_signal_decision_persistence import (
        CanonicalDecisionPersistenceError,
    )

    ctx = seed_ingress(db)
    other = seed_ingress(db, signal_id="sig-other")

    # Wrong owner for the instance.
    with pytest.raises(CanonicalDecisionPersistenceError) as excinfo:
        persist(db, ctx, owner_user_id=other["user"].id)
    assert excinfo.value.code == "OWNER_INSTANCE_MISMATCH"
    # Signal that belongs to a different instance.
    with pytest.raises(CanonicalDecisionPersistenceError) as excinfo:
        persist(db, ctx, strategy_signal=other["signal"])
    assert excinfo.value.code == "FOREIGN_REFERENCE"
    # Webhook event from a different ingress context.
    with pytest.raises(CanonicalDecisionPersistenceError) as excinfo:
        persist(db, ctx, webhook_event=other["event"])
    assert excinfo.value.code == "FOREIGN_REFERENCE"
    # Canonical event for a different signal_id.
    with pytest.raises(CanonicalDecisionPersistenceError) as excinfo:
        persist(db, ctx, canonical_event=other["canonical_event"])
    assert excinfo.value.code == "INVALID_INPUT"
    assert decision_count(db) == 0


class _RaceSession:
    """First lookup misses, simulating a concurrent not-yet-visible insert."""

    def __init__(self, real):
        self._real = real
        self._calls = 0

    def scalar(self, statement):
        self._calls += 1
        if self._calls == 1:
            return None
        return self._real.scalar(statement)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_concurrent_identical_inserts_create_one_row(db, decision_flag_on):
    ctx = seed_ingress(db)
    first = persist(db, ctx)
    winner = persist(_RaceSession(db), ctx)
    assert winner.id == first.id
    assert decision_count(db) == 1


def test_concurrent_conflicting_insert_is_conflict_not_idempotency(db, decision_flag_on):
    from app.domain.legacy_signal_adapter import adapt_legacy_action
    from app.services.canonical_signal_decision_persistence import (
        CanonicalDecisionPersistenceError,
    )

    ctx = seed_ingress(db, action="BUY_CE")
    persist(db, ctx)
    conflicting = adapt_legacy_action("BUY_PE", signal_id=ctx["signal"].signal_id, signal_time=NOW)
    with pytest.raises(CanonicalDecisionPersistenceError) as excinfo:
        persist(_RaceSession(db), ctx, canonical_event=conflicting)
    assert excinfo.value.code == "CANONICAL_DECISION_CONFLICT"
    assert decision_count(db) == 1


def test_caller_transaction_stays_caller_controlled(mu_db, monkeypatch):  # noqa: F811
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope

    monkeypatch.setattr(settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False)
    with session_scope() as db:
        ctx = seed_ingress(db)
        persist(db, ctx)
        assert decision_count(db) == 1
        # The writer never committed the outer transaction: roll it back and
        # the evidence row disappears with it.
        db.rollback()
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(models.CanonicalSignalDecision)) == 0


def test_database_failure_translates_to_closed_code(db, decision_flag_on):
    from sqlalchemy.exc import OperationalError

    from app.services.canonical_signal_decision_persistence import (
        CanonicalDecisionPersistenceError,
    )

    class _Dead:
        def scalar(self, *_a, **_k):
            raise OperationalError("SELECT secret_sql FROM x", {}, Exception("db down"))

        def __getattr__(self, name):
            return getattr(db, name)

    ctx = seed_ingress(db)
    with pytest.raises(CanonicalDecisionPersistenceError) as excinfo:
        persist(_Dead(), ctx)
    assert excinfo.value.code == "PERSISTENCE_UNAVAILABLE"
    assert "secret_sql" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None  # raw DB exception never chained
