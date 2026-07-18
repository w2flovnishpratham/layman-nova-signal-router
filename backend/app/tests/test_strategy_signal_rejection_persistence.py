"""R1B-2A insert-only authenticated signal-rejection writer."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.tests.conftest_multiuser import mu_db  # noqa: F401
from app.tests.test_canonical_signal_decision_persistence import (  # noqa: F401
    NOW,
    seed_ingress,
)

FINGERPRINT_A = "a" * 64
FINGERPRINT_B = "b" * 64


@pytest.fixture
def db(mu_db):  # noqa: F811
    from app.db.engine import session_scope

    with session_scope() as session:
        yield session


@pytest.fixture
def rejection_flag_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "R1B_SIGNAL_REJECTION_PERSISTENCE", True, raising=False)


def persist(db, ctx, **overrides):
    from app.services.strategy_signal_rejection_persistence import (
        persist_strategy_signal_rejection,
    )

    values = dict(
        strategy_instance=ctx["instance"],
        owner_user_id=ctx["user"].id,
        stage="SIGNAL_TIME",
        rejection_code="STALE_SIGNAL",
        received_at=NOW,
        signal_id="sig-1",
        webhook_event=ctx["event"],
        safe_detail="STALE_SIGNAL",
    )
    values.update(overrides)
    return persist_strategy_signal_rejection(db, **values)


def rejection_count(db) -> int:
    from app.db import models

    return db.scalar(select(func.count()).select_from(models.StrategySignalRejection))


def test_flag_off_refuses_before_any_session_access(db):
    from app.config import settings
    from app.services.strategy_signal_rejection_persistence import (
        SignalRejectionPersistenceError,
    )

    assert settings.R1B_SIGNAL_REJECTION_PERSISTENCE is False
    ctx = seed_ingress(db)

    class _Bomb:
        def __getattr__(self, name):
            raise AssertionError(f"session accessed: {name}")

    with pytest.raises(SignalRejectionPersistenceError) as excinfo:
        persist(_Bomb(), ctx)
    assert excinfo.value.code == "PERSISTENCE_DISABLED"
    assert rejection_count(db) == 0


def test_valid_insert_stores_only_safe_projections(db, rejection_flag_on):
    ctx = seed_ingress(db)
    row = persist(db, ctx)
    db.flush()  # satisfies every DB CHECK
    assert row.signal_id_safe == "sig-1"
    assert row.signal_id_sha256 == hashlib.sha256(b"sig-1").hexdigest()
    assert row.stage == "SIGNAL_TIME"
    assert row.rejection_code == "STALE_SIGNAL"
    assert row.received_at.tzinfo is not None
    assert len(row.dedupe_key) == 64


def test_same_invalid_retry_reuses_one_row(db, rejection_flag_on):
    ctx = seed_ingress(db)
    first = persist(db, ctx)
    # Same UTC day, different wall-clock: identical dedupe identity.
    second = persist(db, ctx, received_at=NOW + timedelta(hours=3))
    assert first.id == second.id
    assert rejection_count(db) == 1
    # Next UTC day: a new bounded row.
    third = persist(db, ctx, received_at=NOW + timedelta(days=1))
    assert third.id != first.id
    assert rejection_count(db) == 2


def test_date_bucket_is_utc_deterministic(db, rejection_flag_on):
    ctx = seed_ingress(db)
    # 2026-07-19 03:00 IST is still 2026-07-18 UTC — same bucket as NOW.
    ist = timezone(timedelta(hours=5, minutes=30))
    same_bucket = datetime(2026, 7, 19, 3, 0, tzinfo=ist)
    first = persist(db, ctx)
    second = persist(db, ctx, received_at=same_bucket)
    assert first.id == second.id
    assert rejection_count(db) == 1


def test_conflicting_duplicate_dedupes_per_fingerprint(db, rejection_flag_on):
    ctx = seed_ingress(db)
    first = persist(
        db, ctx, stage="REPLAY_CONFLICT", rejection_code="CONFLICTING_DUPLICATE",
        request_fingerprint=FINGERPRINT_A, safe_detail="CONFLICTING_DUPLICATE",
    )
    same = persist(
        db, ctx, stage="REPLAY_CONFLICT", rejection_code="CONFLICTING_DUPLICATE",
        request_fingerprint=FINGERPRINT_A, safe_detail="CONFLICTING_DUPLICATE",
    )
    different = persist(
        db, ctx, stage="REPLAY_CONFLICT", rejection_code="CONFLICTING_DUPLICATE",
        request_fingerprint=FINGERPRINT_B, safe_detail="CONFLICTING_DUPLICATE",
    )
    assert first.id == same.id
    assert different.id != first.id
    assert rejection_count(db) == 2


def test_no_signal_id_cases_stay_bounded(db, rejection_flag_on):
    ctx = seed_ingress(db)
    first = persist(db, ctx, signal_id=None, webhook_event=None)
    second = persist(db, ctx, signal_id=None, webhook_event=None)
    assert first.id == second.id
    assert rejection_count(db) == 1


def test_owner_and_reference_isolation(db, rejection_flag_on):
    from app.services.strategy_signal_rejection_persistence import (
        SignalRejectionPersistenceError,
    )

    ctx = seed_ingress(db)
    other = seed_ingress(db, signal_id="sig-other")
    with pytest.raises(SignalRejectionPersistenceError) as excinfo:
        persist(db, ctx, owner_user_id=other["user"].id)
    assert excinfo.value.code == "OWNER_INSTANCE_MISMATCH"
    with pytest.raises(SignalRejectionPersistenceError) as excinfo:
        persist(db, ctx, webhook_event=other["event"])
    assert excinfo.value.code == "FOREIGN_REFERENCE"
    assert rejection_count(db) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"stage": "PRE_AUTH"},
        {"rejection_code": "HACKED"},
        {"safe_detail": "free text"},
        {"request_fingerprint": "Z" * 64},
        {"request_fingerprint": "a" * 63},
        {"received_at": NOW.replace(tzinfo=None)},
        {"adapter_version": "unknown.v9"},
        {"contract_version": "nova.other.v9"},
        {"strategy_instance": "not-an-instance"},
    ],
)
def test_invalid_or_unauthenticated_style_inputs_refused(db, rejection_flag_on, overrides):
    from app.services.strategy_signal_rejection_persistence import (
        SignalRejectionPersistenceError,
    )

    ctx = seed_ingress(db)
    with pytest.raises(SignalRejectionPersistenceError) as excinfo:
        persist(db, ctx, **overrides)
    assert excinfo.value.code in {"INVALID_INPUT"}
    assert rejection_count(db) == 0


def test_taint_suppresses_the_row_and_never_echoes(db, rejection_flag_on, capsys):
    from app.services.strategy_signal_rejection_persistence import (
        SignalRejectionPersistenceError,
    )

    ctx = seed_ingress(db)
    secret = "nwk_super_secret_credential_value"
    with pytest.raises(SignalRejectionPersistenceError) as excinfo:
        persist(db, ctx, signal_id=secret)
    assert excinfo.value.code == "TAINT_DETECTED"
    assert secret not in str(excinfo.value)
    # Request-local known secret (not nwk_-shaped) is also suppressed.
    opaque = "opaque-request-secret"
    with pytest.raises(SignalRejectionPersistenceError) as excinfo:
        persist(db, ctx, signal_id=opaque, known_secrets=(opaque,))
    assert excinfo.value.code == "TAINT_DETECTED"
    assert opaque not in str(excinfo.value)
    assert rejection_count(db) == 0
    assert secret not in capsys.readouterr().out


def test_malformed_signal_id_stores_projection_without_hash(db, rejection_flag_on):
    ctx = seed_ingress(db)
    row = persist(db, ctx, signal_id="line\nbreak")
    assert row.signal_id_safe == "invalid-signal-id"
    assert row.signal_id_sha256 is None


def test_caller_transaction_stays_caller_controlled(mu_db, monkeypatch):  # noqa: F811
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope

    monkeypatch.setattr(settings, "R1B_SIGNAL_REJECTION_PERSISTENCE", True, raising=False)
    with session_scope() as db:
        ctx = seed_ingress(db)
        persist(db, ctx)
        assert rejection_count(db) == 1
        db.rollback()
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(models.StrategySignalRejection)) == 0


def test_database_failure_translates_to_closed_code(db, rejection_flag_on):
    from sqlalchemy.exc import OperationalError

    from app.services.strategy_signal_rejection_persistence import (
        SignalRejectionPersistenceError,
    )

    class _Dead:
        def scalar(self, *_a, **_k):
            raise OperationalError("SELECT hidden", {}, Exception("down"))

        def __getattr__(self, name):
            return getattr(db, name)

    ctx = seed_ingress(db)
    with pytest.raises(SignalRejectionPersistenceError) as excinfo:
        persist(_Dead(), ctx)
    assert excinfo.value.code == "PERSISTENCE_UNAVAILABLE"
    assert "hidden" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
