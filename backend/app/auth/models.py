from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, String
from sqlmodel import Field, SQLModel


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(primary_key=True)
    email: str = Field(sa_column=Column(String, unique=True, index=True, nullable=False))
    google_sub: str = Field(sa_column=Column(String, unique=True, index=True, nullable=False))
    name: str | None = None
    avatar_url: str | None = None
    created_at: datetime = Field(default_factory=utc_now_dt)
    last_login: datetime | None = None
    plan_tier: str = "paper"
    status: str = "active"


class AuthSession(SQLModel, table=True):
    __tablename__ = "auth_sessions"

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utc_now_dt)
    expires_at: datetime

