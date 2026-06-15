"""SQLAlchemy 2.0 ORM models for the multi-user SaaS layer.

Tables:
    users                  - one row per Google-authenticated user
    user_sessions          - server-side cookie sessions
    user_credential_vaults - per-user encrypted Dhan/webhook credentials
    user_runs              - paper/live/backtest run history per user
    audit_logs             - per-user security/trade audit trail

A portable GUID type stores native UUIDs on PostgreSQL and CHAR(36) elsewhere
(SQLite) so the same models power both Neon and the test suite.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import CHAR, JSON, TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GUID(TypeDecorator):
    """Platform-independent UUID: native UUID on PG, CHAR(36) elsewhere."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class JSONType(TypeDecorator):
    """JSONB on PostgreSQL, generic JSON elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    picture_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    credential_vault: Mapped["UserCredentialVault | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    runs: Mapped[list["UserRun"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_metadata: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")


class UserCredentialVault(Base):
    __tablename__ = "user_credential_vaults"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    # Ciphertext only. Encrypted with CREDENTIAL_ENCRYPTION_KEY (Fernet).
    dhan_client_id_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    dhan_access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    dhan_api_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    broker_name: Mapped[str] = mapped_column(String(50), default="dhan", nullable=False)
    mode: Mapped[str] = mapped_column(String(20), default="paper", nullable=False)
    # Stored when the token was saved so the UI can show token age without ever
    # exposing the token itself.
    dhan_token_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="credential_vault")


class UserRun(Base):
    __tablename__ = "user_runs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_type: Mapped[str] = mapped_column(String(20), default="paper", nullable=False)  # paper/live/backtest
    strategy_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="created", nullable=False)
    # created/running/stopped/error/completed
    execution_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # signal_only/paper_live_data/real_orders
    mode_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="runs")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    audit_metadata: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class StrategySubscription(Base):
    """A user subscribes to a strategy. One TradingView alert for that strategy
    fans out to every active subscriber, executing with each user's own creds."""

    __tablename__ = "strategy_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "strategy_name", name="uq_user_strategy"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    lots: Mapped[int] = mapped_column(default=1, nullable=False)
    # signal_only / paper_live_data / real_orders — per-user, per-subscription
    execution_mode: Mapped[str] = mapped_column(String(30), default="signal_only", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class StrategySignal(Base):
    """Durable idempotency record for one inbound strategy alert."""

    __tablename__ = "strategy_signals"
    __table_args__ = (
        UniqueConstraint("strategy_name", "signal_id", name="uq_strategy_signal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    strategy_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    signal_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="accepted", nullable=False)
    result_summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class UserEgress(Base):
    """Per-user egress node: the droplet whose static IP is whitelisted in that
    user's Dhan account. Live order calls for the user route through proxy_url so
    they exit from public_ip. proxy_url is encrypted (it may carry credentials)."""

    __tablename__ = "user_egress"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    public_ip: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    proxy_url_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_observed_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
