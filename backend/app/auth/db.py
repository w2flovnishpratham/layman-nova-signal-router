from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.config import RUNTIME_STATE_DIR, settings
from app.auth import models  # noqa: F401 - imports table metadata


def _database_url() -> str:
    configured = settings.DATABASE_URL.strip() or settings.AUTH_DATABASE_URL.strip()
    if configured:
        if settings.APP_ENV.lower() == "production" and configured.startswith("sqlite"):
            raise RuntimeError("PostgreSQL DATABASE_URL is required in production; SQLite is not allowed.")
        return configured
    if settings.APP_ENV.lower() == "production":
        raise RuntimeError("DATABASE_URL or AUTH_DATABASE_URL is required in production.")
    RUNTIME_STATE_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{RUNTIME_STATE_DIR / 'auth.sqlite3'}"


def _connect_args(url: str) -> dict[str, object]:
    return {"check_same_thread": False} if url.startswith("sqlite") else {}


DATABASE_URL = _database_url()

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args(DATABASE_URL),
    pool_pre_ping=not DATABASE_URL.startswith("sqlite"),
)


def init_auth_db() -> None:
    if settings.APP_ENV.lower() == "production":
        return
    RUNTIME_STATE_DIR.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    _ensure_local_auth_session_columns()


def init_database() -> None:
    init_auth_db()


def _ensure_local_auth_session_columns() -> None:
    inspector = inspect(engine)
    if "auth_sessions" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("auth_sessions")}
    additions = {
        "revoked_at": "DATETIME",
        "last_used_at": "DATETIME",
        "client_ip": "VARCHAR",
        "user_agent": "VARCHAR",
    }
    with engine.begin() as connection:
        for column, ddl_type in additions.items():
            if column not in existing:
                connection.execute(text(f"ALTER TABLE auth_sessions ADD COLUMN {column} {ddl_type}"))


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
