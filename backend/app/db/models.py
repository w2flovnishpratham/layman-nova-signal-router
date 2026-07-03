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
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import CHAR, JSON, TypeDecorator

from app.core.enums import (
    ExpiryMode,
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
    StrategySourceType,
    StrikeMode,
)


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


class StrategyCatalog(Base):
    """Catalog entry for a strategy Nova can offer or track in future phases."""

    __tablename__ = "strategy_catalog"
    __table_args__ = (
        UniqueConstraint("code", name="uq_strategy_catalog_code"),
        Index("ix_strategy_catalog_status", "status"),
        Index("ix_strategy_catalog_source_type", "source_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(50),
        default=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        default=StrategyCatalogStatus.COMING_SOON.value,
        nullable=False,
    )
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class StrategyVersion(Base):
    """Versioned strategy definition; not read by live execution in Phase 1."""

    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),
        Index("ix_strategy_versions_strategy_status", "strategy_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategy_catalog.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        default=StrategyCatalogStatus.COMING_SOON.value,
        nullable=False,
    )
    payload_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_schema: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class StrategyBacktestReport(Base):
    """Stored backtest metadata for a strategy version."""

    __tablename__ = "strategy_backtest_reports"
    __table_args__ = (
        Index("ix_strategy_backtest_reports_version_created", "strategy_version_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="available", nullable=False)
    report_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class UserStrategyInstance(Base):
    """Future per-user strategy instance model.

    Phase 1 only creates/backfills this table. Existing routing still reads
    strategy_subscriptions.
    """

    __tablename__ = "user_strategy_instances"
    __table_args__ = (
        UniqueConstraint("legacy_subscription_id", name="uq_user_strategy_instances_legacy_subscription"),
        Index("ix_user_strategy_instances_user_status", "user_id", "status"),
        Index("ix_user_strategy_instances_strategy_status", "strategy_id", "status"),
        Index(
            "uq_user_strategy_instances_active_real_orders_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("execution_mode = 'real_orders' AND status = 'active'"),
            sqlite_where=text("execution_mode = 'real_orders' AND status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategy_catalog.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    legacy_subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("strategy_subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    instance_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(50),
        default=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        default=StrategyInstanceStatus.ACTIVE.value,
        nullable=False,
    )
    execution_mode: Mapped[str] = mapped_column(
        String(30),
        default=StrategyExecutionMode.SIGNAL_ONLY.value,
        nullable=False,
    )
    lots: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    side_preference: Mapped[str | None] = mapped_column(String(8), nullable=True)
    config_json: Mapped[dict | None] = mapped_column("config", JSONType, nullable=True)
    risk_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class UserStrategyWebhookCredential(Base):
    """Hashed credential material for future custom strategy webhooks."""

    __tablename__ = "user_strategy_webhook_credentials"
    __table_args__ = (
        UniqueConstraint("webhook_key_hash", name="uq_user_strategy_webhook_key_hash"),
        Index("ix_user_strategy_webhook_credentials_instance", "instance_id"),
        Index("ix_user_strategy_webhook_credentials_active", "instance_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("user_strategy_instances.id", ondelete="CASCADE"), nullable=False
    )
    webhook_key_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message_secret_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("user_strategy_instances.id", ondelete="SET NULL"), nullable=True, index=True
    )
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    payload_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    received_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="accepted", nullable=False)
    result_summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class NormalizedOptionSignal(Base):
    """Future persisted nova.v1 option signal mapping.

    Phase 1 does not write this table from the live webhook.
    """

    __tablename__ = "normalized_option_signals"
    __table_args__ = (
        Index("ix_normalized_option_signals_strategy_signal", "strategy_signal_id"),
        Index("ix_normalized_option_signals_instance_created", "instance_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    strategy_signal_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategy_signals.id", ondelete="CASCADE"), nullable=False
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("user_strategy_instances.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    intent: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(40), nullable=False)
    option_side: Mapped[str] = mapped_column(String(8), nullable=False)
    strike_mode: Mapped[str] = mapped_column(String(20), default=StrikeMode.ATM.value, nullable=False)
    resolved_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    expiry_mode: Mapped[str] = mapped_column(String(20), default=ExpiryMode.NEXT_WEEKLY.value, nullable=False)
    resolved_expiry: Mapped[date | None] = mapped_column(Date(), nullable=True)
    qty_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    lots: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    product_type: Mapped[str] = mapped_column(String(30), nullable=False)
    raw_mapping_details: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


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
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("user_strategy_instances.id", ondelete="SET NULL"), nullable=True, index=True
    )
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    normalized_signal_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("normalized_option_signals.id", ondelete="SET NULL"), nullable=True, index=True
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
    locked_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dead_letter_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
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
