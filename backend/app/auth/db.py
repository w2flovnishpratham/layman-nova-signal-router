from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from app.config import RUNTIME_STATE_DIR, settings
from app.auth import models  # noqa: F401 - imports table metadata


def _database_url() -> str:
    configured = settings.AUTH_DATABASE_URL.strip()
    if configured:
        return configured
    RUNTIME_STATE_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{RUNTIME_STATE_DIR / 'auth.sqlite3'}"


def _connect_args(url: str) -> dict[str, object]:
    return {"check_same_thread": False} if url.startswith("sqlite") else {}


engine = create_engine(
    _database_url(),
    connect_args=_connect_args(_database_url()),
    pool_pre_ping=not _database_url().startswith("sqlite"),
)


def init_auth_db() -> None:
    RUNTIME_STATE_DIR.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
