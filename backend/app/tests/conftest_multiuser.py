"""Shared fixtures for the multi-user (Neon) test suite.

Imported by the multi-user test modules. Uses a throwaway SQLite database so the
same models that run against Neon are exercised without a network dependency.
"""
from __future__ import annotations

import enum

import pytest

# Backport StrEnum for Python 3.10 runners (the app targets 3.11+).
if not hasattr(enum, "StrEnum"):  # pragma: no cover
    class _StrEnum(str, enum.Enum):
        def __str__(self) -> str:
            return str(self.value)

    enum.StrEnum = _StrEnum  # type: ignore[attr-defined]


@pytest.fixture
def mu_db(tmp_path, monkeypatch):
    """Configure an isolated SQLite DB + encryption key for a single test."""
    from cryptography.fernet import Fernet

    from app.config import settings
    from app.db import engine as eng

    db_path = tmp_path / "multiuser.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "x" * 48, raising=False)
    monkeypatch.setattr(settings, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode(), raising=False)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True, raising=False)

    eng.reset_engine_for_tests()
    eng.init_db()
    try:
        yield
    finally:
        eng.reset_engine_for_tests()


def make_user(email: str, *, is_admin: bool = False, google_sub: str | None = None):
    from app.db import crud
    from app.db.engine import session_scope

    with session_scope() as db:
        user = crud.upsert_google_user(
            db,
            google_sub=google_sub or f"sub-{email}",
            email=email,
            name=email.split("@")[0].title(),
            picture_url=None,
            is_admin=is_admin,
        )
        db.expunge(user)
        return user
