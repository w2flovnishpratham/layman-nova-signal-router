"""Shared backoff so background pollers stop hammering an unreachable database.

Observed in production: with Neon refusing connections ("data transfer quota
exceeded"), the polling workers kept retrying at their normal cadence --
~45,000 failed connections per hour, dead flat overnight with the market
closed, because every failure was caught and logged per-iteration and the
loop simply slept its usual 0.5s and tried again. Each attempt still pays a
TCP+TLS handshake, which itself consumes the metered data-transfer quota
that caused the outage, so the storm actively prevented recovery.

Detection lives at the session layer rather than in the loops because the
loops never see these failures: option_position_monitor swallows them
per-user inside its ThreadPoolExecutor, so its outer loop always looks
healthy. session_scope() is the one place every caller passes through.

Only connection-level failures count. IntegrityError and friends are normal
business outcomes on the dedup/claim paths and must never trigger backoff.
"""
from __future__ import annotations

import threading

from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError

# Connection-level only -- deliberately NOT IntegrityError (duplicate-claim
# conflicts are an expected, healthy outcome in webhook_replay_store and
# order_idempotency, and counting them would back off during normal trading).
DB_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, DisconnectionError)

MAX_BACKOFF_SECONDS = 60.0

_lock = threading.Lock()
_consecutive_failures = 0


def note_outcome(exc: BaseException | None) -> None:
    """Record one session outcome. Never raises: this sits in session_scope's
    exception path, where a failure of its own would mask the real error."""
    try:
        if exc is None:
            note_success()
        elif isinstance(exc, DB_UNAVAILABLE_ERRORS):
            note_failure()
        # Any other exception is a business/logic error, not a sign the
        # database is unreachable -- leave the counter untouched.
    except Exception:
        pass


def note_success() -> None:
    global _consecutive_failures
    with _lock:
        _consecutive_failures = 0


def note_failure() -> None:
    global _consecutive_failures
    with _lock:
        _consecutive_failures += 1


def consecutive_failures() -> int:
    with _lock:
        return _consecutive_failures


def poll_delay(base_seconds: float) -> float:
    """The interval a polling worker should sleep for.

    Normal operation returns base_seconds unchanged. Each consecutive
    connection failure doubles it, capped, so a 0.5s poller drops to one
    attempt per minute within a few seconds of the database going away --
    roughly a 100x reduction in connection churn -- and returns to full
    cadence on the first success.
    """
    failures = consecutive_failures()
    if failures <= 0:
        return base_seconds
    # Production incident: a sustained outage pushed failures into the
    # thousands, and 2**failures overflowed converting to a float *before*
    # min() ever got to cap it -- raising OverflowError uncaught in every
    # worker thread that calls this (strategy jobs, EOD square-off, ghost
    # watcher, option monitor all died within the same minute). Capping the
    # exponent first removes the overflow entirely: any base_seconds used in
    # this codebase already saturates MAX_BACKOFF_SECONDS well before 20
    # doublings, so this changes no observable behavior.
    capped_failures = min(failures, 20)
    return min(base_seconds * (2 ** capped_failures), MAX_BACKOFF_SECONDS)


def reset() -> None:
    """Test hook."""
    note_success()
