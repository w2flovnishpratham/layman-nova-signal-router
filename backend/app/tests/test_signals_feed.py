"""Signals read model: owner isolation, pagination, filtering, no payload leak."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.engine import session_scope
from app.services import signals_feed
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _event(user_id, *, event_id: str, received_at: datetime, processed_status: str = "accepted", meta=None):
    with session_scope() as db:
        row = models.WebhookEvent(
            provider="tradingview",
            event_id=event_id,
            user_id=user_id,
            raw_body_sha256="a" * 64,
            signature_ok=True,
            replay_status="fresh",
            processed_status=processed_status,
            event_metadata=meta or {"action": "ENTRY", "side": "BUY", "symbol": "NIFTY"},
            received_at=received_at,
            updated_at=received_at,
        )
        db.add(row)
        db.flush()
        return row.id


def test_signals_are_scoped_to_the_owner(mu_db):  # noqa: F811
    alice = make_user("alice-signals@example.com")
    bob = make_user("bob-signals@example.com")
    now = datetime.now(timezone.utc)
    _event(alice.id, event_id="alice-1", received_at=now)
    _event(bob.id, event_id="bob-1", received_at=now)

    result = signals_feed.list_signals(alice.id)
    ids = [i["event_id"] for i in result["items"]]
    assert ids == ["alice-1"]
    assert "bob-1" not in ids  # StrategySignal has no user_id; feed must not leak


def test_pagination_is_stable_and_newest_first(mu_db):  # noqa: F811
    user = make_user("page-signals@example.com")
    base = datetime.now(timezone.utc)
    for i in range(5):
        _event(user.id, event_id=f"e-{i}", received_at=base - timedelta(minutes=i))

    first = signals_feed.list_signals(user.id, limit=2)
    assert [i["event_id"] for i in first["items"]] == ["e-0", "e-1"]
    assert first["next_cursor"]

    second = signals_feed.list_signals(user.id, limit=2, cursor=first["next_cursor"])
    assert [i["event_id"] for i in second["items"]] == ["e-2", "e-3"]
    # No overlap between pages.
    assert not set(i["id"] for i in first["items"]) & set(i["id"] for i in second["items"])


def test_status_filter_uses_real_persisted_values(mu_db):  # noqa: F811
    user = make_user("filter-signals@example.com")
    now = datetime.now(timezone.utc)
    _event(user.id, event_id="ok-1", received_at=now, processed_status="accepted")
    _event(user.id, event_id="bad-1", received_at=now - timedelta(seconds=1), processed_status="rejected")

    accepted = signals_feed.list_signals(user.id, status="accepted")
    assert [i["event_id"] for i in accepted["items"]] == ["ok-1"]

    rejected = signals_feed.list_signals(user.id, status="rejected")
    assert [i["event_id"] for i in rejected["items"]] == ["bad-1"]

    # The mockup's invented vocabulary is not a persisted value and is refused.
    unknown = signals_feed.list_signals(user.id, status="routed")
    assert unknown["ok"] is False


def test_response_never_contains_the_raw_payload_or_a_secret(mu_db):  # noqa: F811
    user = make_user("safe-signals@example.com")
    _event(
        user.id,
        event_id="secret-1",
        received_at=datetime.now(timezone.utc),
        meta={"action": "ENTRY", "secret": "SUPER_SECRET_VALUE", "raw_body": "RAW_PAYLOAD_CONTENTS"},
    )
    result = signals_feed.list_signals(user.id)
    blob = repr(result)
    # The curated summary keeps only known-safe keys, so neither the secret nor
    # the raw payload body can reach the client.
    assert "SUPER_SECRET_VALUE" not in blob
    assert "RAW_PAYLOAD_CONTENTS" not in blob
    # Only the digest is exposed (the key name raw_body_sha256 is expected).
    assert result["items"][0]["raw_body_sha256"] == "a" * 64
    assert result["items"][0]["summary"] == {"action": "ENTRY"}


def test_empty_state_and_counts(mu_db):  # noqa: F811
    user = make_user("empty-signals@example.com")
    result = signals_feed.list_signals(user.id)
    assert result["items"] == []
    assert result["next_cursor"] is None
    assert result["counts"].get("total", 0) == 0


def test_invalid_cursor_is_rejected(mu_db):  # noqa: F811
    user = make_user("cursor-signals@example.com")
    result = signals_feed.list_signals(user.id, cursor="!!!not-a-cursor!!!")
    assert result["ok"] is False


def test_counts_group_by_persisted_status(mu_db):  # noqa: F811
    user = make_user("counts-signals@example.com")
    now = datetime.now(timezone.utc)
    _event(user.id, event_id="c-1", received_at=now, processed_status="accepted")
    _event(user.id, event_id="c-2", received_at=now - timedelta(seconds=1), processed_status="accepted")
    _event(user.id, event_id="c-3", received_at=now - timedelta(seconds=2), processed_status="rejected")

    counts = signals_feed.list_signals(user.id)["counts"]
    assert counts["accepted"] == 2
    assert counts["rejected"] == 1
    assert counts["total"] == 3
