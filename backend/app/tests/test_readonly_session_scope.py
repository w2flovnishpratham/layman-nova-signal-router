from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db import models
from app.db.engine import (
    ReadOnlySessionMisuse,
    readonly_session_scope,
    session_scope,
)
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def test_readonly_scope_runs_queries(mu_db):
    with readonly_session_scope() as db:
        assert db.execute(text("SELECT 1")).scalar() == 1


def test_readonly_scope_is_bound_to_an_autocommit_engine(mu_db):
    """The whole point: no BEGIN/COMMIT round trips. Measured on production
    (Postgres, ~65ms RTT): 277ms transactional vs ~120ms here.

    Asserted against the bind's execution options rather than
    Connection.get_isolation_level(), which reports the *server's* level
    (READ COMMITTED on Postgres, always SERIALIZABLE on the SQLite used by
    these tests) and so never reflects SQLAlchemy's driver-level AUTOCOMMIT.
    """
    with readonly_session_scope() as db:
        bind = db.get_bind()
        assert bind.get_execution_options().get("isolation_level") == "AUTOCOMMIT"


def test_readonly_scope_shares_the_main_connection_pool(mu_db):
    """The read-only bind must be a shallow copy of the main engine, not a
    second engine -- otherwise it would double the connection count against
    Neon's pooler for no reason."""
    from app.db.engine import get_engine

    with readonly_session_scope() as db:
        assert db.get_bind().pool is get_engine().pool


def test_readonly_scope_sees_committed_data(mu_db):
    user = make_user("readonly-visibility@example.com")
    with readonly_session_scope() as db:
        found = db.get(models.User, user.id)
        assert found is not None
        assert found.email == "readonly-visibility@example.com"


def test_readonly_scope_rejects_pending_writes(mu_db):
    """A write through the read path must fail loudly. In AUTOCOMMIT there is no
    rollback to lean on, so this has to be caught before it reaches the DB."""
    with pytest.raises(ReadOnlySessionMisuse):
        with readonly_session_scope() as db:
            db.add(models.User(email="should-not-persist@example.com", name="nope"))

    # And it must genuinely not have landed.
    with session_scope() as db:
        assert (
            db.query(models.User).filter_by(email="should-not-persist@example.com").one_or_none()
            is None
        )
