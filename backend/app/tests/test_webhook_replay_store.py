# ruff: noqa: F811
"""count_stuck_webhook_events: the reconciliation signal for a webhook event
that never reached a terminal processed_status -- see strategy_job_worker's
_refresh_signal_summary, the thing that's supposed to close it out."""
from __future__ import annotations

from datetime import timedelta

from app.tests.conftest_multiuser import mu_db  # noqa: F401


def _claim(provider: str, event_id: str):
    from app.services import webhook_replay_store

    return webhook_replay_store.claim_webhook_event(
        provider=provider,
        event_id=event_id,
        raw_body="{}",
        signature_ok=True,
    )


def _age_event(provider: str, event_id: str, *, seconds_old: int) -> None:
    from sqlalchemy import update

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        db.execute(
            update(models.WebhookEvent)
            .where(
                models.WebhookEvent.provider == provider.lower(),
                models.WebhookEvent.event_id == event_id,
            )
            .values(updated_at=models.utcnow() - timedelta(seconds=seconds_old))
        )


def test_recently_claimed_event_is_not_yet_stuck(mu_db):
    from app.services import webhook_replay_store

    _claim("test:provider", "recent-1")
    assert webhook_replay_store.count_stuck_webhook_events(older_than_seconds=120) == 0


def test_old_non_terminal_event_counts_as_stuck(mu_db):
    from app.services import webhook_replay_store

    _claim("test:provider", "stuck-1")
    _age_event("test:provider", "stuck-1", seconds_old=180)
    assert webhook_replay_store.count_stuck_webhook_events(older_than_seconds=120) == 1


def test_count_returns_none_instead_of_raising_when_the_database_is_down(mu_db, monkeypatch):
    """Regression: this metric is read by /api/health. When Neon was
    unreachable (data-transfer quota exceeded) the raised OperationalError
    turned /api/health into a 500 -- monitoring went blind at exactly the
    moment the database was down. Unknown must degrade to None, not explode.
    """
    from app.services import webhook_replay_store

    def exploding_session_scope():
        raise RuntimeError("connection failed: data transfer quota exceeded")

    monkeypatch.setattr(webhook_replay_store, "session_scope", exploding_session_scope)

    assert webhook_replay_store.count_stuck_webhook_events() is None


def test_old_event_closed_to_a_terminal_status_does_not_count(mu_db):
    from app.services import webhook_replay_store

    _claim("test:provider", "closed-1")
    _age_event("test:provider", "closed-1", seconds_old=180)
    webhook_replay_store.update_webhook_event(
        provider="test:provider", event_id="closed-1", processed_status="completed",
    )
    assert webhook_replay_store.count_stuck_webhook_events(older_than_seconds=120) == 0
