from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Column, Index, String, text
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


class UserRuntimeProfile(SQLModel, table=True):
    __tablename__ = "user_runtime_profiles"

    user_id: str = Field(primary_key=True, foreign_key="users.id")
    webhook_secret_hash: str | None = Field(default=None, sa_column=Column(String, unique=True, index=True))
    webhook_secret_set_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now_dt)
    updated_at: datetime = Field(default_factory=utc_now_dt)


class DhanAccount(SQLModel, table=True):
    __tablename__ = "dhan_accounts"

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    dhan_client_id_hash: str = Field(sa_column=Column(String, unique=True, index=True, nullable=False))
    dhan_client_id_masked: str | None = None
    status: str = Field(default="connected", index=True)
    access_token_present: bool = False
    connected_at: datetime = Field(default_factory=utc_now_dt)
    last_validated_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now_dt)


class EgressNode(SQLModel, table=True):
    __tablename__ = "egress_nodes"

    id: str = Field(primary_key=True)
    name: str = Field(sa_column=Column(String, unique=True, index=True, nullable=False))
    provider: str = Field(default="vultr", index=True)
    region: str = Field(index=True)
    public_ip: str = Field(sa_column=Column(String, unique=True, index=True, nullable=False))
    internal_base_url: str | None = None
    status: str = Field(default="ready", index=True)
    capacity: int = 1
    created_at: datetime = Field(default_factory=utc_now_dt)
    updated_at: datetime = Field(default_factory=utc_now_dt)


class UserEgressAssignment(SQLModel, table=True):
    __tablename__ = "user_egress_assignments"
    __table_args__ = (
        Index(
            "ix_user_egress_assignment_active_user",
            "user_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active = true"),
        ),
        Index(
            "ix_user_egress_assignment_active_node",
            "egress_node_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active = true"),
        ),
    )

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    egress_node_id: str = Field(index=True, foreign_key="egress_nodes.id")
    is_active: bool = Field(default=True, index=True)
    assigned_at: datetime = Field(default_factory=utc_now_dt)
    released_at: datetime | None = None
    release_reason: str | None = None


class OrderRouteAudit(SQLModel, table=True):
    __tablename__ = "order_route_audit"

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    dhan_account_id: str | None = Field(default=None, index=True, foreign_key="dhan_accounts.id")
    egress_node_id: str | None = Field(default=None, index=True, foreign_key="egress_nodes.id")
    route_kind: str = Field(default="order", index=True)
    status: str = Field(index=True)
    signal_id: str | None = Field(default=None, index=True)
    request_id: str | None = Field(default=None, index=True)
    order_id: str | None = Field(default=None, index=True)
    source_ip: str | None = None
    message: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now_dt, index=True)
