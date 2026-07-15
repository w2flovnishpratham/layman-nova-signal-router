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
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false as sa_false,
    text as sa_text,
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


class WebhookEvent(Base):
    """Durable inbound webhook idempotency record.

    Unique provider/event_id rows are the source of truth for duplicate alert
    protection across process restarts and multiple workers.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_webhook_event_provider_event"),
        Index("ix_webhook_events_user_received", "user_id", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    raw_body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    replay_status: Mapped[str] = mapped_column(String(20), default="fresh", nullable=False)
    processed_status: Mapped[str] = mapped_column(String(30), default="received", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class WebhookNonce(Base):
    """Durable per-user webhook nonce replay guard."""

    __tablename__ = "webhook_nonces"
    __table_args__ = (
        UniqueConstraint("user_id", "nonce", name="uq_webhook_nonce_user_nonce"),
        Index("ix_webhook_nonces_seen_at", "seen_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nonce: Mapped[str] = mapped_column(String(255), nullable=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class UserRiskControl(Base):
    """Per-user server-side risk gates for strategy fan-out."""

    __tablename__ = "user_risk_controls"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_lots_per_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_notional_per_trade_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    max_orders_per_day: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_loss_per_day_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class UserStrategyRiskControl(Base):
    """Per-user, per-strategy overrides for fan-out risk gates."""

    __tablename__ = "user_strategy_risk_controls"
    __table_args__ = (
        UniqueConstraint("user_id", "strategy_name", name="uq_user_strategy_risk_control"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_lots_per_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_notional_per_trade_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    max_orders_per_day: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_loss_per_day_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class UserStrategyDailyRiskCounter(Base):
    """Durable IST-day risk counter for user+strategy order intents."""

    __tablename__ = "user_strategy_daily_risk_counters"
    __table_args__ = (
        UniqueConstraint("user_id", "strategy_name", "trade_date_ist", name="uq_user_strategy_daily_risk"),
        Index("ix_user_strategy_daily_risk_user_date", "user_id", "trade_date_ist"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    trade_date_ist: Mapped[date] = mapped_column(Date(), nullable=False)
    orders_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notional_used_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    realized_pnl_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


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


class UserEntitlement(Base):
    """Server-owned paid/trial entitlement state for one user."""

    __tablename__ = "user_entitlements"
    __table_args__ = (
        Index("ix_user_entitlements_user_status", "user_id", "status"),
        Index("ix_user_entitlements_user_expires", "user_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_code: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live_orders_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    static_ip_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    strategy_access_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_strategy_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Safe operational metadata only. Do not store raw provider payloads,
    # secrets, or full PII in this JSON field.
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class PaymentEvent(Base):
    """Durable payment-provider event audit/idempotency record."""

    __tablename__ = "payment_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_payment_event_provider_event"),
        Index("ix_payment_events_user_id", "user_id"),
        Index("ix_payment_events_received_at", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    event_status: Mapped[str] = mapped_column(String(40), nullable=False)
    # Store amount in provider minor units when available.
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    # Safe event metadata only. Raw provider payloads/secrets must stay out of
    # this table; payload_hash is the durable audit/idempotency key.
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


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


class StrategyExecutionJob(Base):
    """Durable per-user work item created before a strategy webhook is acknowledged."""

    __tablename__ = "strategy_execution_jobs"
    __table_args__ = (
        UniqueConstraint(
            "strategy_name",
            "signal_id",
            "user_id",
            name="uq_strategy_signal_user_job",
        ),
        Index("ix_strategy_jobs_status_available", "status", "available_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    strategy_signal_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("strategy_signals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    signal_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    signal_payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    lots: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class LiveOrderIntent(Base):
    """Durable idempotency gate for live Dhan write intents."""

    __tablename__ = "live_order_intents"
    __table_args__ = (
        UniqueConstraint("user_id", "scope", "idempotency_key", name="uq_live_order_intent_user_scope_key"),
        Index("ix_live_order_intents_user_created", "user_id", "created_at"),
        Index("ix_live_order_intents_status_updated", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    signal_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    action: Mapped[str | None] = mapped_column(String(20), nullable=True)
    side: Mapped[str | None] = mapped_column(String(20), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(120), nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    broker_correlation_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    result_summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    intent_metadata: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class UserEgress(Base):
    """Per-user Nova Static IP assignment for Dhan live order routing.

    Live order calls for the user route through the encrypted proxy_url so they
    exit from public_ip. proxy_url is backend-only and may carry credentials.
    """

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


class PortfolioTrade(Base):
    """A completed round-trip the engine actually bought and sold for a user.

    One row per closed trade (entry order + matching exit order). This is the
    durable backing store for the analytics dashboard: only orders NOVA tracked
    end-to-end land here, so the dashboard never shows untracked broker activity.
    Idempotent on (user_id, exit_order_id) so repeated syncs never duplicate.
    """

    __tablename__ = "portfolio_trades"
    __table_args__ = (
        UniqueConstraint("user_id", "exit_order_id", name="uq_portfolio_trade_exit"),
        Index("ix_portfolio_trades_user_closed", "user_id", "closed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String(20), default="paper", nullable=False)  # paper/live
    strategy_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(120), nullable=True)
    option_side: Mapped[str | None] = mapped_column(String(8), nullable=True)  # CE/PE
    qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_charges: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    exit_charges: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    gross_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    entry_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    exit_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    signal_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class StrategyCatalog(Base):
    """One registered strategy (Nova-owned or personal).

    Phase 0 registry foundation: nothing in the execution path reads this yet.
    Nova-owned rows have owner_user_id NULL and a globally unique code
    (partial unique index); personal rows are namespaced per owner.
    """

    __tablename__ = "strategy_catalog"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "code", name="uq_strategy_catalog_owner_code"),
        Index(
            "uq_strategy_catalog_nova_code",
            "code",
            unique=True,
            postgresql_where=sa_text("owner_user_id IS NULL"),
            sqlite_where=sa_text("owner_user_id IS NULL"),
        ),
        Index("ix_strategy_catalog_owner_status", "owner_user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(20), default="nova", nullable=False)  # nova/personal
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # private / nova_shared / marketplace (defined now, unused until a later release)
    visibility: Mapped[str] = mapped_column(String(20), default="private", nullable=False)
    # coming_soon / active / beta / disabled / deprecated
    status: Mapped[str] = mapped_column(String(20), default="coming_soon", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    versions: Mapped[list["StrategyVersion"]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )


class StrategyVersion(Base):
    """One immutable-once-approved version of a catalog strategy.

    INVARIANT: approved StrategyVersion rows must never be modified through
    direct ORM assignment. All updates must go through
    app.services.strategy_registry (update_version/approve_version), which
    rejects every change to an approved version except approved -> deprecated.
    """

    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),
        UniqueConstraint(
            "strategy_id",
            "source_sha256",
            "pine_contract_version",
            name="uq_strategy_version_source_contract",
        ),
        # Composite key target so strategy_instances can enforce
        # "version belongs to strategy" structurally.
        UniqueConstraint("id", "strategy_id", name="uq_strategy_versions_id_strategy"),
        Index("ix_strategy_versions_strategy_status", "strategy_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategy_catalog.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    # Wire contract this version's alerts must use, e.g. "nova.v1".
    payload_spec_version: Mapped[str] = mapped_column(String(20), nullable=False)
    # nova_shared / nova_hosted_personal / personal_tradingview
    source_journey: Mapped[str] = mapped_column(String(30), nullable=False)
    # draft / pending_validation / validation_failed / pending_review / approved / rejected / deprecated
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # external_webhook (TradingView calls Nova) / nova_runtime (Nova evaluates internally)
    execution_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # Populated only for nova_runtime versions: the validated declarative rule
    # document the internal runtime interprets — never raw arbitrary source.
    runtime_definition: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pine_contract_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    strategy: Mapped["StrategyCatalog"] = relationship(back_populates="versions")


class StrategySourceArtifact(Base):
    """Inert source evidence for one strategy version (Pine text, prompt output).

    Never executed — input to validation and audit only.
    """

    __tablename__ = "strategy_source_artifacts"
    __table_args__ = (
        Index("ix_strategy_source_artifacts_version_type", "strategy_version_id", "artifact_type"),
        UniqueConstraint(
            "strategy_version_id", "artifact_type", name="uq_strategy_source_artifact_version_type"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # pine_script / master_prompt_output / ai_conversion_log / runtime_dsl
    artifact_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # SET NULL (not NOT NULL as drafted in the report) so audit rows survive
    # account deletion instead of blocking it.
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # manual_master_prompt / claude_api (future) — audit detail, never branched on
    conversion_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class StrategyValidationReport(Base):
    """Durable result of one automated validation stage for one version."""

    __tablename__ = "strategy_validation_reports"
    __table_args__ = (
        UniqueConstraint(
            "strategy_version_id",
            "stage",
            "validator_version",
            "contract_version",
            "source_sha256",
            name="uq_strategy_validation_identity",
        ),
        Index(
            "ix_strategy_validation_reports_version_stage",
            "strategy_version_id",
            "stage",
            "executed_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # structural / security / compatibility / replay
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    # passed / failed / warning
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Structured findings: [{code, severity, message, location}, ...]
    findings: Mapped[list] = mapped_column(JSONType, nullable=False)
    validator_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    contract_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validation_engine: Mapped[str | None] = mapped_column(String(50), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    info_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    eligible_for_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class StrategyAdminReview(Base):
    """Immutable human review decision history for one strategy version."""

    __tablename__ = "strategy_admin_reviews"
    __table_args__ = (
        Index("ix_strategy_admin_reviews_version_reviewed", "strategy_version_id", "reviewed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL so review history survives reviewer account deletion.
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # approved / rejected / changes_requested
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_report_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("strategy_validation_reports.id", ondelete="SET NULL"), nullable=True
    )
    previous_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PineConversionRequest(Base):
    __tablename__ = "pine_conversion_requests"
    __table_args__ = (
        UniqueConstraint("identity_sha256", "attempt", name="uq_pine_conversion_identity_attempt"),
        Index("ix_pine_conversion_status_available", "status", "available_at"),
        Index("ix_pine_conversion_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    strategy_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("strategy_catalog.id", ondelete="CASCADE"), nullable=False)
    input_version_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False)
    input_source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    options: Mapped[dict] = mapped_column(JSONType, nullable=False)
    options_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    provider_request_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    candidate_version_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True)
    validation_report_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("strategy_validation_reports.id", ondelete="SET NULL"), nullable=True)
    safe_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    usage_summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    estimated_cost_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    conversion_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    assumptions: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    unsupported_features: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    warnings: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    action_mapping: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class PinePromptVersion(Base):
    """Immutable manual conversion prompt metadata and qualification state."""

    __tablename__ = "pine_prompt_versions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    version_label: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUALIFICATION")
    change_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PineUserAcceptance(Base):
    """User acknowledgement pinned to exact source, prompt and validation evidence."""

    __tablename__ = "pine_user_acceptances"
    __table_args__ = (UniqueConstraint("candidate_version_id", name="uq_pine_acceptance_candidate"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    original_version_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False)
    candidate_version_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False)
    prompt_version_id: Mapped[str] = mapped_column(String(40), ForeignKey("pine_prompt_versions.id", ondelete="RESTRICT"), nullable=False)
    setup_type: Mapped[str] = mapped_column(String(40), nullable=False)
    validation_report_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("strategy_validation_reports.id", ondelete="RESTRICT"), nullable=False)
    validation_report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    assumptions: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TradingViewSetup(Base):
    """Manual TradingView installation and server-observed verification state."""

    __tablename__ = "tradingview_setups"
    __table_args__ = (
        UniqueConstraint("strategy_instance_id", name="uq_tradingview_setup_instance"),
        Index("ix_tradingview_setups_type_status", "setup_type", "status"),
        Index("ix_tradingview_setups_owner_updated", "user_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    strategy_instance_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("strategy_instances.id", ondelete="CASCADE"), nullable=False)
    approved_version_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False)
    setup_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="SETUP_PENDING")
    requested_timeframe: Mapped[str | None] = mapped_column(String(20), nullable=True)
    user_reported_compiled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    installation_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    installation_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    hold_signal_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hold_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paper_entry_signal_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paper_entry_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paper_exit_signal_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paper_exit_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversal_signal_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reversal_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocking_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reset_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class PinePromptQualificationTrial(Base):
    """Admin-owned evidence for one representative manual-prompt trial."""

    __tablename__ = "pine_prompt_qualification_trials"
    __table_args__ = (Index("ix_pine_prompt_trials_prompt_outcome", "prompt_version_id", "outcome"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    prompt_version_id: Mapped[str] = mapped_column(String(40), ForeignKey("pine_prompt_versions.id", ondelete="RESTRICT"), nullable=False)
    original_version_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False)
    candidate_version_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(80), nullable=False)
    test_classification: Mapped[str] = mapped_column(String(50), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONType, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tester_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class StrategyInstance(Base):
    """One user's configured copy of a strategy version — the universal
    server-side unit all three journeys route through.

    Phase 1: control-plane only. During the transition the legacy
    strategy_subscriptions row remains EXECUTION-AUTHORITATIVE (the fan-out
    reads it); this row is the future authority, kept transactionally
    synchronized in both directions (lots, execution_mode, and lifecycle
    active-eligibility) via legacy_subscription_id. Status transitions are
    enforced solely by app.domain.strategy_instance_state_machine — never
    assign status directly.
    """

    __tablename__ = "strategy_instances"
    __table_args__ = (
        UniqueConstraint("user_id", "strategy_id", "label", name="uq_strategy_instance_user_label"),
        # Composite target so positions can structurally prove instance ownership.
        UniqueConstraint("id", "user_id", name="uq_strategy_instances_id_user"),
        # Structural guarantee that the chosen version belongs to the chosen
        # strategy — not left to service-layer discipline.
        ForeignKeyConstraint(
            ["strategy_version_id", "strategy_id"],
            ["strategy_versions.id", "strategy_versions.strategy_id"],
            name="fk_strategy_instance_version_strategy",
        ),
        Index("ix_strategy_instances_user_status", "user_id", "status"),
        Index("ix_strategy_instances_strategy_status", "strategy_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # No ondelete: a catalog strategy/version with live instances must not be deletable.
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategy_catalog.id"), nullable=False, index=True
    )
    # FK enforced via the composite fk_strategy_instance_version_strategy above.
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    # NOVA_SHARED / NOVA_HOSTED_PERSONAL / PERSONAL_TRADINGVIEW
    source_journey: Mapped[str] = mapped_column(String(30), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    # See strategy_instance_state_machine.InstanceState for values/transitions.
    status: Mapped[str] = mapped_column(String(20), default="ready", nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # signal_only / paper_live_data / real_orders (live_engine.EXECUTION_MODES)
    execution_mode: Mapped[str] = mapped_column(String(30), default="signal_only", nullable=False)
    current_lots: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Controlled paper-only verification: when true, the private webhook path
    # executes paper signals for this instance even though it is not ACTIVE, so
    # a newly approved strategy can produce genuine paper entry/exit evidence
    # without the readiness deadlock. Live orders remain impossible (enforced in
    # the execution path). Cleared once full readiness is reached.
    verification_mode: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false(), nullable=False)
    verification_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Risk/config snapshot; structured but strategy-variable.
    risk_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Link to the pre-instance subscription this row was backfilled from
    # (dual-write target for lots/mode until execution is instance-aware).
    legacy_subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("strategy_subscriptions.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    webhook_credentials: Mapped[list["StrategyInstanceWebhookCredential"]] = relationship(
        back_populates="instance", cascade="all, delete-orphan"
    )


class StrategyInstanceWebhookCredential(Base):
    """One issued private-webhook token for one strategy instance.

    Only the SHA-256 hash of the opaque token is stored; plaintext is returned
    exactly once at issue/rotation time. Rotation revokes the old row and
    inserts a new one so issue/rotate/revoke history stays immutable. At most
    one active (revoked_at IS NULL) credential per instance (partial index).

    Ownership is derived through the instance (no duplicated user_id column),
    so a credential can never reference one user and another user's instance.
    """

    __tablename__ = "strategy_instance_webhook_credentials"
    __table_args__ = (
        Index(
            "uq_instance_webhook_credential_active",
            "strategy_instance_id",
            unique=True,
            postgresql_where=sa_text("revoked_at IS NULL"),
            sqlite_where=sa_text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    strategy_instance_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategy_instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # First characters of the plaintext token, for display only ("nwk_ab12…").
    token_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)

    instance: Mapped["StrategyInstance"] = relationship(back_populates="webhook_credentials")


class UserEngineConfig(Base):
    """One row per user pointing the engine picker at the user's currently
    selected strategy instance.

    Selection is a persisted UI/engine pointer, NOT execution authority: the
    per-instance private webhook credential remains the sole binding between an
    inbound signal and the instance it executes as, so this pointer never
    reroutes a signal between instances.
    """

    __tablename__ = "user_engine_configs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    # SET NULL: an archived/deleted instance simply clears the selection.
    selected_strategy_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("strategy_instances.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# Position states with an open broker exposure or an in-flight order. Shared
# by the model constraint, position_store, and the parity tool. Closed/flat
# rows never occupy the active-position slot.
ACTIVE_POSITION_STATES = (
    "entering",
    "open",
    "scaling_in",
    "exiting",
    "reversing",
    "error_reconciling",
)
_ACTIVE_STATES_SQL = "position_state IN ({})".format(
    ", ".join(f"'{state}'" for state in ACTIVE_POSITION_STATES)
)


class StrategyInstancePosition(Base):
    """Durable shadow of one tracked option position (Phase 2A).

    AUTHORITY: during Phase 2A the per-user JSON files (open_position.json /
    paper_position.json) remain the EXECUTION READ AUTHORITY. Rows here are
    shadow dual-writes for durability and parity comparison only — nothing on
    the execution path reads this table. Do not add fallback reads.

    One row represents one position lifetime for a (user, execution_mode)
    slot; the partial unique index enforces at most one active row per slot
    (which implies the product rule of one active live position per user).
    Money/price columns are integer paise — never floats. raw_snapshot keeps
    the full source JSON for lossless parity and audit.
    """

    __tablename__ = "strategy_instance_positions"
    __table_args__ = (
        # A position can never reference User A and an instance owned by
        # User B — enforced structurally, not by service discipline. NULL
        # strategy_instance_id (legacy/migration rows) passes (MATCH SIMPLE).
        ForeignKeyConstraint(
            ["strategy_instance_id", "user_id"],
            ["strategy_instances.id", "strategy_instances.user_id"],
            name="fk_position_instance_owner",
        ),
        # Composite target so position_events structurally prove tenant match.
        UniqueConstraint("id", "user_id", name="uq_positions_id_user"),
        Index(
            "uq_active_position_per_user_mode",
            "user_id",
            "execution_mode",
            unique=True,
            postgresql_where=sa_text(_ACTIVE_STATES_SQL),
            sqlite_where=sa_text(_ACTIVE_STATES_SQL),
        ),
        Index("ix_positions_user_state", "user_id", "position_state"),
        Index("ix_positions_instance", "strategy_instance_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nullable during migration: legacy JSON positions have no instance yet.
    strategy_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("strategy_instances.id", ondelete="SET NULL"), nullable=True
    )
    execution_mode: Mapped[str] = mapped_column(String(20), nullable=False)  # paper/live
    # flat/entering/open/scaling_in/exiting/reversing/error_reconciling/closed
    position_state: Mapped[str] = mapped_column(String(30), nullable=False)
    position_side: Mapped[str] = mapped_column(String(10), default="LONG", nullable=False)
    strategy_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    security_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    trading_symbol: Mapped[str | None] = mapped_column(String(120), nullable=True)
    underlying: Mapped[str] = mapped_column(String(20), default="NIFTY", nullable=False)
    option_side: Mapped[str | None] = mapped_column(String(8), nullable=True)  # CE/PE
    strike: Mapped[str | None] = mapped_column(String(20), nullable=True)
    expiry: Mapped[str | None] = mapped_column(String(20), nullable=True)
    product_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Quantities (contracts).
    entry_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filled_entry_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    open_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    filled_exit_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Money in integer paise (project convention; never float).
    avg_entry_price_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    realized_pnl_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Broker lifecycle.
    entry_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    exit_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    broker_correlation_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pending_order_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    reversal_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    super_order_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    reconciliation_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_broker_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Shadow bookkeeping.
    raw_snapshot: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parity_status: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_parity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    imported_from_json: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Audit/concurrency.
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class PositionEvent(Base):
    """Append-only history of position transitions (Phase 2A shadow).

    Rows are never updated or deleted. idempotency_key dedupes retried
    writes of the same logical event.
    """

    __tablename__ = "position_events"
    __table_args__ = (
        # An event can never claim a different tenant than its position.
        # (strategy_instance ownership derives through the position row —
        # events deliberately carry no instance column of their own.)
        ForeignKeyConstraint(
            ["position_id", "user_id"],
            ["strategy_instance_positions.id", "strategy_instance_positions.user_id"],
            name="fk_event_position_owner",
        ),
        Index("ix_position_events_position_at", "position_id", "event_at"),
        Index("ix_position_events_user_at", "user_id", "event_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    position_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategy_instance_positions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # entry_requested/entry_submitted/entry_partial_fill/entry_filled/
    # exit_requested/exit_submitted/exit_partial_fill/exit_filled/
    # reversal_requested/reconciliation_adjustment/eod_exit/manual_exit/
    # shadow_write_failure/imported_from_json/... (open string set)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # Operation family that produced the write: entry/exit/partial_fill/
    # reversal/reconciliation/update/import
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    signal_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    execution_job_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    order_intent_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    previous_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    new_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)


class PortfolioSnapshot(Base):
    """Point-in-time wallet/equity reading per user, used to persist the equity
    curve so the dashboard can render history beyond the in-memory log window."""

    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        Index("ix_portfolio_snapshots_user_captured", "user_id", "captured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String(20), default="paper", nullable=False)
    starting_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    available_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    utilized_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
