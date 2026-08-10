"""SQLAlchemy engine + session management for Neon PostgreSQL.

The engine is created lazily on first use so importing this module (or the rest
of the app) never requires a live database. This keeps the existing paper-mode
test suite and local dev flow working without a DATABASE_URL.

Neon connection strings look like:
    postgresql://user:password@host/db?sslmode=require&channel_binding=require

SQLAlchemy + psycopg(v3) needs the explicit driver:
    postgresql+psycopg://user:password@host/db?sslmode=require&channel_binding=require

normalize_database_url() performs that conversion while preserving every query
parameter (sslmode, channel_binding, etc.).
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit

from app.config import settings
from app.db import backoff

logger = logging.getLogger("nova_signal_router.db")

# These imports are intentionally light; SQLAlchemy is a hard dependency of the
# multi-user layer but is only touched when a DATABASE_URL is configured.
try:  # pragma: no cover - import guard
    from sqlalchemy import create_engine
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker
except Exception:  # pragma: no cover
    create_engine = None  # type: ignore[assignment]
    Engine = object  # type: ignore[assignment]
    Session = object  # type: ignore[assignment]
    sessionmaker = None  # type: ignore[assignment]


_ENGINE: "Engine | None" = None
_SESSION_FACTORY = None
_READONLY_SESSION_FACTORY = None
_LOCK = threading.RLock()


class DatabaseNotConfigured(RuntimeError):
    """Raised when a DB operation is attempted without a DATABASE_URL."""


# Drivers we know how to normalize to the psycopg(v3) dialect.
_POSTGRES_SCHEMES = {
    "postgres": "postgresql+psycopg",
    "postgresql": "postgresql+psycopg",
    "postgresql+psycopg2": "postgresql+psycopg",
    "postgresql+psycopg": "postgresql+psycopg",
}


def normalize_database_url(url: str) -> str:
    """Convert a Neon/Postgres URL to the psycopg(v3) SQLAlchemy dialect.

    - postgresql://...            -> postgresql+psycopg://...
    - postgres://...              -> postgresql+psycopg://...
    - postgresql+psycopg2://...   -> postgresql+psycopg://...
    - postgresql+psycopg://...    -> unchanged

    All query parameters (sslmode, channel_binding, ...) are preserved exactly.
    Non-postgres URLs (e.g. sqlite:// used in tests) pass through untouched.
    """
    raw = (url or "").strip()
    if not raw:
        return raw

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in _POSTGRES_SCHEMES:
        # Not a postgres URL (e.g. sqlite). Leave it alone.
        return raw

    new_scheme = _POSTGRES_SCHEMES[scheme]
    # urlunsplit preserves netloc, path, query (sslmode/channel_binding) and fragment.
    return urlunsplit((new_scheme, parts.netloc, parts.path, parts.query, parts.fragment))


def database_configured() -> bool:
    return bool((settings.DATABASE_URL or "").strip())


def get_engine() -> "Engine":
    global _ENGINE, _SESSION_FACTORY, _READONLY_SESSION_FACTORY
    if _ENGINE is not None:
        return _ENGINE
    with _LOCK:
        if _ENGINE is not None:
            return _ENGINE
        if create_engine is None:  # pragma: no cover
            raise DatabaseNotConfigured("SQLAlchemy is not installed.")
        if not database_configured():
            raise DatabaseNotConfigured(
                "DATABASE_URL is not set. The multi-user layer requires a Neon "
                "PostgreSQL connection string (set via environment only)."
            )
        normalized = normalize_database_url(settings.DATABASE_URL)
        # pool_pre_ping avoids stale connections against Neon's serverless pooler.
        # pool_size/max_overflow: the SQLAlchemy default (5/10, 15 total) was too
        # small once callers started firing several independent reads concurrently
        # (see trading_bootstrap) -- new connections queued behind the default's
        # overflow limit, adding setup latency on top of Neon's own round-trip.
        engine = create_engine(
            normalized,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_size=15,
            max_overflow=15,
            future=True,
        )
        _ENGINE = engine
        _SESSION_FACTORY = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)
        # Read-only sessions run in AUTOCOMMIT. SQLAlchemy's default mode wraps
        # every statement -- reads included -- in an explicit BEGIN...COMMIT,
        # which costs two extra network round trips. Measured against this
        # deployment (VPS in Mumbai, Neon in ap-southeast-1, ~65ms RTT):
        # a trivial SELECT 1 is 277ms with the transaction wrapper and 60ms
        # without it. Reads that never write don't need the transaction.
        #
        # engine.execution_options() returns a shallow copy sharing the SAME
        # connection pool, so this does not double the connection count against
        # Neon -- it only changes the isolation level for sessions bound to it.
        #
        # autoflush=False so a stray session.add() in a read scope can never be
        # silently flushed by a later query. Combined with the guard in
        # readonly_session_scope(), an accidental write is caught before it
        # reaches the database rather than after AUTOCOMMIT has already made it
        # permanent.
        _READONLY_SESSION_FACTORY = sessionmaker(
            bind=engine.execution_options(isolation_level="AUTOCOMMIT"),
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
            future=True,
        )
        logger.info("Database engine initialised (host=%s).", urlsplit(normalized).hostname)
        return _ENGINE


def get_session_factory():
    if _SESSION_FACTORY is None:
        get_engine()
    return _SESSION_FACTORY


def get_readonly_session_factory():
    if _READONLY_SESSION_FACTORY is None:
        get_engine()
    return _READONLY_SESSION_FACTORY


@contextmanager
def session_scope() -> Iterator["Session"]:
    """Transactional session scope. Commits on success, rolls back on error."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception as exc:
        session.rollback()
        # Observation only -- the exception still propagates unchanged. Lets
        # background pollers back off when the database is unreachable; see
        # app/db/backoff.py for why detection has to live here.
        backoff.note_outcome(exc)
        raise
    else:
        backoff.note_outcome(None)
    finally:
        session.close()


class ReadOnlySessionMisuse(RuntimeError):
    """Raised when a read-only session scope was used to modify data."""


@contextmanager
def readonly_session_scope() -> Iterator["Session"]:
    """AUTOCOMMIT session scope for queries that only read.

    Skips the BEGIN/COMMIT round trips that a transactional scope pays -- worth
    ~4.6x on this deployment's cross-region latency (see get_engine()). Use it
    for read models and dashboards.

    NOT for anything that writes: in AUTOCOMMIT each statement is durable
    immediately, so there is no rollback to fall back on. Money paths, order
    execution and anything with a compare-and-set must keep using
    session_scope(). The guard below fails loudly on pending ORM changes rather
    than letting a write quietly land through the read path.
    """
    factory = get_readonly_session_factory()
    session = factory()
    try:
        yield session
        if session.new or session.dirty or session.deleted:
            # Discard the accidental writes before surfacing the misuse.
            session.rollback()
            raise ReadOnlySessionMisuse(
                "readonly_session_scope() received pending writes "
                f"(new={len(session.new)}, dirty={len(session.dirty)}, "
                f"deleted={len(session.deleted)}). Use session_scope() instead."
            )
    finally:
        # close() only -- deliberately no rollback on the success path. There is
        # no open transaction to roll back under AUTOCOMMIT, and rollback()
        # *expires* every ORM object in the identity map, so callers that return
        # rows out of the scope (risk_overview, terminal feeds) would then raise
        # DetachedInstanceError on attribute access. close() leaves already
        # loaded values readable, matching what session_scope() gives callers.
        session.close()


def init_db() -> None:
    """Create tables if they do not exist. Safe to call repeatedly.

    In dev this is enough to bootstrap the schema. For production a dedicated
    init script (scripts/init_db.py) runs the same call explicitly.
    """
    from app.db import models  # noqa: F401 - ensure models are registered

    engine = get_engine()
    models.Base.metadata.create_all(bind=engine)
    logger.info("Database tables ensured (create_all).")


def reset_engine_for_tests() -> None:
    """Dispose and clear the cached engine (used by the test suite)."""
    global _ENGINE, _SESSION_FACTORY, _READONLY_SESSION_FACTORY
    with _LOCK:
        if _ENGINE is not None:
            try:
                _ENGINE.dispose()
            except Exception:  # pragma: no cover
                pass
        _ENGINE = None
        _SESSION_FACTORY = None
        # Must clear too: it is bound to a shallow copy of the disposed engine,
        # so leaving it set would hand tests a session on a dead pool.
        _READONLY_SESSION_FACTORY = None
