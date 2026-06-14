from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Float, JSON, Column, Index, String, UniqueConstraint, text
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
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
    client_ip: str | None = None
    user_agent: str | None = None


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


class WebhookSignalReceipt(SQLModel, table=True):
    __tablename__ = "webhook_signal_receipts"
    __table_args__ = (
        UniqueConstraint("user_id", "signal_id", name="uq_webhook_signal_receipt_user_signal"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    signal_id: str = Field(index=True)
    strategy_code: str | None = Field(default=None, index=True)
    payload_hash: str = Field(index=True)
    first_seen_at: datetime = Field(default_factory=utc_now_dt, index=True)
    last_seen_at: datetime = Field(default_factory=utc_now_dt)
    status: str = Field(default="received", index=True)
    correlation_id: str | None = Field(default=None, index=True)
    message: str | None = None


class WebhookNonceReceipt(SQLModel, table=True):
    __tablename__ = "webhook_nonce_receipts"
    __table_args__ = (
        UniqueConstraint("user_id", "nonce", name="uq_webhook_nonce_receipt_user_nonce"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    nonce: str = Field(index=True)
    request_timestamp: int = Field(sa_column=Column(BigInteger, nullable=False))
    first_seen_at: datetime = Field(default_factory=utc_now_dt, index=True)


class OrderRouteAttempt(SQLModel, table=True):
    __tablename__ = "order_route_attempts"
    __table_args__ = (
        UniqueConstraint("user_id", "correlation_id", name="uq_order_route_attempt_user_correlation"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    correlation_id: str = Field(index=True)
    signal_id: str = Field(index=True)
    strategy_code: str | None = Field(default=None, index=True)
    action: str = Field(index=True)
    instrument_identity: str = Field(index=True)
    payload_hash: str = Field(index=True)
    status: str = Field(default="pending", index=True)
    order_id: str | None = Field(default=None, index=True)
    message: str | None = None
    created_at: datetime = Field(default_factory=utc_now_dt, index=True)
    updated_at: datetime = Field(default_factory=utc_now_dt)


class Strategy(SQLModel, table=True):
    __tablename__ = "strategies"
    __table_args__ = (
        CheckConstraint("risk_level IN ('low', 'medium', 'high')", name="ck_strategies_risk_level"),
    )

    id: int | None = Field(default=None, primary_key=True)
    strategy_code: str = Field(sa_column=Column(String, unique=True, index=True, nullable=False))
    name: str
    description: str
    risk_level: str = Field(default="medium", index=True)
    market: str = Field(default="NSE", index=True)
    instrument_type: str = Field(default="OPTIDX", index=True)
    timeframe: str
    is_active: bool = Field(default=True, index=True)
    is_paper_allowed: bool = Field(default=True)
    is_live_allowed: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utc_now_dt)
    updated_at: datetime = Field(default_factory=utc_now_dt)


class StrategyVersion(SQLModel, table=True):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_strategy_version"),
        CheckConstraint("status IN ('draft', 'active', 'retired')", name="ck_strategy_versions_status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    strategy_id: int = Field(index=True, foreign_key="strategies.id")
    version: int = Field(default=1)
    status: str = Field(default="active", index=True)
    changelog: str | None = None
    signal_schema_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now_dt)
    activated_at: datetime | None = Field(default_factory=utc_now_dt)


class UserStrategySubscription(SQLModel, table=True):
    __tablename__ = "user_strategy_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "strategy_id",
            "strategy_version_id",
            "mode",
            name="uq_user_strategy_subscriptions_scope",
        ),
        CheckConstraint("mode IN ('paper', 'live')", name="ck_user_strategy_subscriptions_mode"),
        CheckConstraint(
            "status IN ('active', 'paused', 'disabled')",
            name="ck_user_strategy_subscriptions_status",
        ),
        CheckConstraint(
            "allowed_option_side IN ('CE', 'PE', 'BOTH')",
            name="ck_user_strategy_subscriptions_option_side",
        ),
        CheckConstraint("max_qty IS NULL OR max_qty > 0", name="ck_user_strategy_subscriptions_max_qty"),
        CheckConstraint(
            "max_daily_loss IS NULL OR max_daily_loss >= 0",
            name="ck_user_strategy_subscriptions_max_daily_loss",
        ),
        CheckConstraint(
            "max_trades_per_day IS NULL OR max_trades_per_day >= 0",
            name="ck_user_strategy_subscriptions_max_trades",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    strategy_id: int = Field(index=True, foreign_key="strategies.id")
    strategy_version_id: int = Field(index=True, foreign_key="strategy_versions.id")
    mode: str = Field(default="paper", index=True)
    status: str = Field(default="active", index=True)
    risk_profile_id: str | None = None
    max_qty: int | None = None
    max_daily_loss: float | None = Field(default=None, sa_column=Column(Float))
    max_trades_per_day: int | None = None
    allowed_option_side: str = Field(default="BOTH")
    created_at: datetime = Field(default_factory=utc_now_dt)
    updated_at: datetime = Field(default_factory=utc_now_dt)


class StrategySignal(SQLModel, table=True):
    __tablename__ = "strategy_signals"
    __table_args__ = (
        UniqueConstraint(
            "strategy_id",
            "strategy_version_id",
            "signal_id",
            name="uq_strategy_signals_strategy_version_signal",
        ),
        CheckConstraint(
            "status IN ("
            "'received', 'fanout_queued', 'fanout_started', 'fanout_completed', "
            "'fanout_failed', 'partial_failed', 'rejected'"
            ")",
            name="ck_strategy_signals_status",
        ),
        CheckConstraint("action IN ('BUY', 'SELL', 'EXIT', 'CLOSE')", name="ck_strategy_signals_action"),
        CheckConstraint(
            "option_type IS NULL OR option_type IN ('CE', 'PE')",
            name="ck_strategy_signals_option_type",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    strategy_id: int = Field(index=True, foreign_key="strategies.id")
    strategy_version_id: int = Field(index=True, foreign_key="strategy_versions.id")
    strategy_code: str = Field(index=True)
    signal_id: str = Field(index=True)
    payload_hash: str = Field(index=True)
    symbol: str = Field(index=True)
    action: str = Field(index=True)
    option_type: str | None = Field(default=None, index=True)
    strike: float | None = Field(default=None, sa_column=Column(Float))
    expiry: str | None = None
    timeframe: str | None = None
    price: float | None = Field(default=None, sa_column=Column(Float))
    raw_payload_redacted_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    received_at: datetime = Field(default_factory=utc_now_dt, index=True)
    source: str = Field(default="internal", index=True)
    status: str = Field(default="received", index=True)


class PaperStrategyOrder(SQLModel, table=True):
    __tablename__ = "paper_strategy_orders"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "subscription_id",
            "strategy_signal_id",
            name="uq_paper_strategy_orders_user_subscription_signal",
        ),
        CheckConstraint("status IN ('filled', 'rejected')", name="ck_paper_strategy_orders_status"),
        CheckConstraint("qty > 0", name="ck_paper_strategy_orders_qty"),
        CheckConstraint("fill_price > 0", name="ck_paper_strategy_orders_fill_price"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    subscription_id: int = Field(index=True, foreign_key="user_strategy_subscriptions.id")
    strategy_signal_id: int = Field(index=True, foreign_key="strategy_signals.id")
    strategy_id: int = Field(index=True, foreign_key="strategies.id")
    strategy_version_id: int = Field(index=True, foreign_key="strategy_versions.id")
    action: str = Field(index=True)
    option_type: str | None = Field(default=None, index=True)
    symbol: str = Field(index=True)
    strike: float | None = Field(default=None, sa_column=Column(Float))
    expiry: str | None = None
    qty: int
    fill_price: float = Field(sa_column=Column(Float, nullable=False))
    status: str = Field(default="filled", index=True)
    realized_pnl: float = Field(default=0.0, sa_column=Column(Float, nullable=False))
    created_at: datetime = Field(default_factory=utc_now_dt, index=True)


class UserSignalJob(SQLModel, table=True):
    __tablename__ = "user_signal_jobs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "strategy_signal_id",
            "subscription_id",
            name="uq_user_signal_jobs_user_signal_subscription",
        ),
        CheckConstraint("mode IN ('paper', 'live')", name="ck_user_signal_jobs_mode"),
        CheckConstraint(
            "status IN ("
            "'queued', 'processing', 'skipped_subscription_paused', 'skipped_risk_not_set', "
            "'skipped_daily_trade_limit', 'skipped_daily_loss_limit', 'skipped_market_closed', "
            "'skipped_duplicate', 'skipped_invalid_instrument', 'paper_filled', "
            "'paper_rejected', 'skipped_live_not_approved', 'skipped_executor_not_assigned', "
            "'skipped_executor_not_verified', 'skipped_broker_not_connected', "
            "'skipped_live_disabled', 'skipped_dry_run_only', "
            "'skipped_strategy_not_live_allowed', 'live_dry_run_verified', "
            "'live_sent', 'live_confirmed', 'live_blocked', 'failed'"
            ")",
            name="ck_user_signal_jobs_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_user_signal_jobs_attempt_count"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    subscription_id: int = Field(index=True, foreign_key="user_strategy_subscriptions.id")
    strategy_signal_id: int = Field(index=True, foreign_key="strategy_signals.id")
    strategy_id: int = Field(index=True, foreign_key="strategies.id")
    strategy_version_id: int = Field(index=True, foreign_key="strategy_versions.id")
    mode: str = Field(default="paper", index=True)
    status: str = Field(default="queued", index=True)
    reason_code: str | None = Field(default=None, index=True)
    reason_message: str | None = None
    paper_order_id: int | None = Field(default=None, index=True, foreign_key="paper_strategy_orders.id")
    created_at: datetime = Field(default_factory=utc_now_dt, index=True)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempt_count: int = 0


class WorkerJob(SQLModel, table=True):
    __tablename__ = "worker_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed', 'dead', 'cancelled')",
            name="ck_worker_jobs_status",
        ),
        CheckConstraint("priority >= 0", name="ck_worker_jobs_priority"),
        CheckConstraint("attempt_count >= 0", name="ck_worker_jobs_attempt_count"),
        CheckConstraint("max_attempts > 0", name="ck_worker_jobs_max_attempts"),
    )

    id: int | None = Field(default=None, primary_key=True)
    job_type: str = Field(index=True)
    dedupe_key: str | None = Field(default=None, sa_column=Column(String, unique=True, index=True))
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    status: str = Field(default="queued", index=True)
    priority: int = Field(default=100, index=True)
    attempt_count: int = 0
    max_attempts: int = 3
    available_at: datetime = Field(default_factory=utc_now_dt, index=True)
    locked_by: str | None = Field(default=None, index=True)
    locked_until: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now_dt, index=True)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_error: str | None = None
    created_by_user_id: str | None = Field(default=None, index=True, foreign_key="users.id")


class WorkerHeartbeat(SQLModel, table=True):
    __tablename__ = "worker_heartbeats"
    __table_args__ = (
        CheckConstraint(
            "status IN ('starting', 'running', 'stopping', 'stopped', 'failed')",
            name="ck_worker_heartbeats_status",
        ),
    )

    worker_id: str = Field(primary_key=True)
    worker_role: str = Field(index=True)
    hostname: str
    pid: int
    started_at: datetime = Field(default_factory=utc_now_dt)
    last_seen_at: datetime = Field(default_factory=utc_now_dt, index=True)
    status: str = Field(default="starting", index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


class PaperPosition(SQLModel, table=True):
    __tablename__ = "paper_positions"
    __table_args__ = (
        Index(
            "ix_paper_positions_open_instrument",
            "user_id",
            "subscription_id",
            "symbol",
            "option_type",
            "strike",
            "expiry",
            "side",
            unique=True,
            sqlite_where=text("status = 'open'"),
            postgresql_where=text("status = 'open'"),
        ),
        CheckConstraint(
            "status IN ('open', 'closed', 'cancelled')",
            name="ck_paper_positions_status",
        ),
        CheckConstraint("side = 'LONG'", name="ck_paper_positions_long_only"),
        CheckConstraint("quantity > 0", name="ck_paper_positions_quantity"),
        CheckConstraint("avg_entry_price > 0", name="ck_paper_positions_entry_price"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    subscription_id: int = Field(index=True, foreign_key="user_strategy_subscriptions.id")
    strategy_id: int = Field(index=True, foreign_key="strategies.id")
    strategy_version_id: int = Field(index=True, foreign_key="strategy_versions.id")
    symbol: str = Field(index=True)
    option_type: str = Field(index=True)
    strike: float = Field(sa_column=Column(Float, nullable=False))
    expiry: str = Field(index=True)
    side: str = Field(default="LONG", index=True)
    quantity: int
    avg_entry_price: float = Field(sa_column=Column(Float, nullable=False))
    status: str = Field(default="open", index=True)
    opened_at: datetime = Field(default_factory=utc_now_dt, index=True)
    closed_at: datetime | None = Field(default=None, index=True)
    realized_pnl: float = Field(default=0.0, sa_column=Column(Float, nullable=False))
    source_signal_id: int = Field(index=True, foreign_key="strategy_signals.id")
    last_signal_job_id: int = Field(index=True, foreign_key="user_signal_jobs.id")
    created_at: datetime = Field(default_factory=utc_now_dt)
    updated_at: datetime = Field(default_factory=utc_now_dt)


class PaperLedgerEntry(SQLModel, table=True):
    __tablename__ = "paper_ledger_entries"
    __table_args__ = (
        UniqueConstraint(
            "user_signal_job_id",
            "entry_type",
            name="uq_paper_ledger_entries_job_type",
        ),
        CheckConstraint(
            "entry_type IN ('order_fill', 'position_close', 'pnl_adjustment', 'risk_block')",
            name="ck_paper_ledger_entries_type",
        ),
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="ck_paper_ledger_entries_quantity"),
        CheckConstraint("price IS NULL OR price >= 0", name="ck_paper_ledger_entries_price"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    subscription_id: int = Field(index=True, foreign_key="user_strategy_subscriptions.id")
    strategy_id: int = Field(index=True, foreign_key="strategies.id")
    strategy_signal_id: int = Field(index=True, foreign_key="strategy_signals.id")
    user_signal_job_id: int = Field(index=True, foreign_key="user_signal_jobs.id")
    paper_position_id: int | None = Field(default=None, index=True, foreign_key="paper_positions.id")
    entry_type: str = Field(index=True)
    amount: float = Field(default=0.0, sa_column=Column(Float, nullable=False))
    quantity: int | None = None
    price: float | None = Field(default=None, sa_column=Column(Float))
    realized_pnl: float = Field(default=0.0, sa_column=Column(Float, nullable=False))
    description: str
    created_at: datetime = Field(default_factory=utc_now_dt, index=True)


class UserNotification(SQLModel, table=True):
    __tablename__ = "user_notifications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'sent', 'failed')",
            name="ck_user_notifications_status",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    event_type: str = Field(index=True)
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    dedupe_key: str | None = Field(default=None, sa_column=Column(String, unique=True, index=True))
    status: str = Field(default="queued", index=True)
    created_at: datetime = Field(default_factory=utc_now_dt, index=True)
    sent_at: datetime | None = None


class ExecutorNode(SQLModel, table=True):
    __tablename__ = "executor_nodes"
    __table_args__ = (
        CheckConstraint("provider IN ('digitalocean', 'manual')", name="ck_executor_nodes_provider"),
        CheckConstraint("status IN ('pending', 'active', 'disabled')", name="ck_executor_nodes_status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    executor_code: str = Field(sa_column=Column(String, unique=True, index=True, nullable=False))
    provider: str = Field(default="manual", index=True)
    droplet_name: str
    region: str = Field(index=True)
    reserved_ip: str = Field(sa_column=Column(String, unique=True, index=True, nullable=False))
    health_url: str
    execute_url: str
    egress_ip_url: str
    status: str = Field(default="pending", index=True)
    last_health_status: str | None = None
    last_egress_ip: str | None = None
    last_seen_at: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now_dt)
    updated_at: datetime = Field(default_factory=utc_now_dt)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


class UserExecutorAssignment(SQLModel, table=True):
    __tablename__ = "user_executor_assignments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_whitelist', 'verified', 'disabled')",
            name="ck_user_executor_assignments_status",
        ),
        Index(
            "ix_user_executor_assignments_active_user",
            "user_id",
            unique=True,
            sqlite_where=text("status != 'disabled'"),
            postgresql_where=text("status != 'disabled'"),
        ),
        Index(
            "ix_user_executor_assignments_active_node",
            "executor_node_id",
            unique=True,
            sqlite_where=text("status != 'disabled'"),
            postgresql_where=text("status != 'disabled'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    executor_node_id: int = Field(index=True, foreign_key="executor_nodes.id")
    broker_client_id_hash: str = Field(index=True)
    status: str = Field(default="pending_whitelist", index=True)
    verified_at: datetime | None = None
    last_verified_egress_ip: str | None = None
    created_at: datetime = Field(default_factory=utc_now_dt)
    updated_at: datetime = Field(default_factory=utc_now_dt)


class UserLivePilotApproval(SQLModel, table=True):
    __tablename__ = "user_live_pilot_approvals"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "strategy_id",
            "strategy_version_id",
            name="uq_user_live_pilot_approval_scope",
        ),
        CheckConstraint("status IN ('approved', 'revoked')", name="ck_user_live_pilot_approvals_status"),
        CheckConstraint("max_qty > 0", name="ck_user_live_pilot_approvals_qty"),
        CheckConstraint("max_trades_per_day > 0", name="ck_user_live_pilot_approvals_trades"),
        CheckConstraint("max_daily_loss >= 0", name="ck_user_live_pilot_approvals_loss"),
        CheckConstraint(
            "allowed_option_side IN ('CE', 'PE', 'BOTH')",
            name="ck_user_live_pilot_approvals_side",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    strategy_id: int = Field(index=True, foreign_key="strategies.id")
    strategy_version_id: int = Field(index=True, foreign_key="strategy_versions.id")
    approved_by_admin_user_id: str = Field(index=True, foreign_key="users.id")
    status: str = Field(default="approved", index=True)
    max_qty: int
    max_trades_per_day: int
    max_daily_loss: float = Field(sa_column=Column(Float, nullable=False))
    allowed_option_side: str = Field(default="BOTH")
    expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now_dt)
    updated_at: datetime = Field(default_factory=utc_now_dt)


class LiveOrderJob(SQLModel, table=True):
    __tablename__ = "live_order_jobs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "subscription_id",
            "strategy_signal_id",
            name="uq_live_order_jobs_user_subscription_signal",
        ),
        CheckConstraint(
            "status IN ('queued', 'processing', 'dry_run_verified', 'sent', 'confirmed', 'blocked', 'failed')",
            name="ck_live_order_jobs_status",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    subscription_id: int = Field(index=True, foreign_key="user_strategy_subscriptions.id")
    strategy_signal_id: int = Field(index=True, foreign_key="strategy_signals.id")
    user_signal_job_id: int = Field(index=True, foreign_key="user_signal_jobs.id")
    executor_node_id: int = Field(index=True, foreign_key="executor_nodes.id")
    status: str = Field(default="queued", index=True)
    reason_code: str | None = Field(default=None, index=True)
    reason_message: str | None = None
    correlation_id: str = Field(sa_column=Column(String, unique=True, index=True, nullable=False))
    dhan_order_id: str | None = Field(default=None, index=True)
    dry_run: bool = Field(default=True, index=True)
    realized_pnl: float = Field(default=0.0, sa_column=Column(Float, nullable=False))
    response_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now_dt, index=True)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ExecutorNonceReceipt(SQLModel, table=True):
    __tablename__ = "executor_nonce_receipts"
    __table_args__ = (
        UniqueConstraint("executor_code", "nonce", name="uq_executor_nonce_receipt_code_nonce"),
    )

    id: int | None = Field(default=None, primary_key=True)
    executor_code: str = Field(index=True)
    nonce: str = Field(index=True)
    request_timestamp: int = Field(sa_column=Column(BigInteger, nullable=False))
    received_at: datetime = Field(default_factory=utc_now_dt, index=True)
