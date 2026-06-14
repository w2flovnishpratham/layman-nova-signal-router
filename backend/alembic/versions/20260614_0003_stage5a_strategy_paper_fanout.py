"""stage 5a strategy subscription and paper signal fanout

Revision ID: 20260614_0003
Revises: 20260613_0002
Create Date: 2026-06-14
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op


revision = "20260614_0003"
down_revision = "20260613_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("strategy_code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("risk_level", sa.String(), nullable=False, server_default="medium"),
        sa.Column("market", sa.String(), nullable=False, server_default="NSE"),
        sa.Column("instrument_type", sa.String(), nullable=False, server_default="OPTIDX"),
        sa.Column("timeframe", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_paper_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_live_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("risk_level IN ('low', 'medium', 'high')", name="ck_strategies_risk_level"),
        sa.UniqueConstraint("strategy_code"),
    )
    for column in ("strategy_code", "risk_level", "market", "instrument_type", "is_active"):
        op.create_index(f"ix_strategies_{column}", "strategies", [column], unique=column == "strategy_code")

    op.create_table(
        "strategy_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("changelog", sa.String(), nullable=True),
        sa.Column("signal_schema_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'active', 'retired')", name="ck_strategy_versions_status"),
        sa.UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_strategy_version"),
    )
    op.create_index("ix_strategy_versions_strategy_id", "strategy_versions", ["strategy_id"])
    op.create_index("ix_strategy_versions_status", "strategy_versions", ["status"])

    op.create_table(
        "user_strategy_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=False),
        sa.Column("mode", sa.String(), nullable=False, server_default="paper"),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("risk_profile_id", sa.String(), nullable=True),
        sa.Column("max_qty", sa.Integer(), nullable=True),
        sa.Column("max_daily_loss", sa.Float(), nullable=True),
        sa.Column("max_trades_per_day", sa.Integer(), nullable=True),
        sa.Column("allowed_option_side", sa.String(), nullable=False, server_default="BOTH"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("mode = 'paper'", name="ck_user_strategy_subscriptions_paper_only"),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'disabled')",
            name="ck_user_strategy_subscriptions_status",
        ),
        sa.CheckConstraint(
            "allowed_option_side IN ('CE', 'PE', 'BOTH')",
            name="ck_user_strategy_subscriptions_option_side",
        ),
        sa.CheckConstraint("max_qty IS NULL OR max_qty > 0", name="ck_user_strategy_subscriptions_max_qty"),
        sa.CheckConstraint(
            "max_daily_loss IS NULL OR max_daily_loss >= 0",
            name="ck_user_strategy_subscriptions_max_daily_loss",
        ),
        sa.CheckConstraint(
            "max_trades_per_day IS NULL OR max_trades_per_day >= 0",
            name="ck_user_strategy_subscriptions_max_trades",
        ),
        sa.UniqueConstraint(
            "user_id",
            "strategy_id",
            "strategy_version_id",
            "mode",
            name="uq_user_strategy_subscriptions_scope",
        ),
    )
    for column in ("user_id", "strategy_id", "strategy_version_id", "mode", "status"):
        op.create_index(
            f"ix_user_strategy_subscriptions_{column}",
            "user_strategy_subscriptions",
            [column],
        )

    op.create_table(
        "strategy_signals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=False),
        sa.Column("strategy_code", sa.String(), nullable=False),
        sa.Column("signal_id", sa.String(), nullable=False),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("option_type", sa.String(), nullable=True),
        sa.Column("strike", sa.Float(), nullable=True),
        sa.Column("expiry", sa.String(), nullable=True),
        sa.Column("timeframe", sa.String(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("raw_payload_redacted_json", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="internal"),
        sa.Column("status", sa.String(), nullable=False, server_default="received"),
        sa.CheckConstraint(
            "status IN ('received', 'fanout_started', 'fanout_completed', 'rejected')",
            name="ck_strategy_signals_status",
        ),
        sa.CheckConstraint("action IN ('BUY', 'SELL', 'EXIT')", name="ck_strategy_signals_action"),
        sa.CheckConstraint(
            "option_type IS NULL OR option_type IN ('CE', 'PE')",
            name="ck_strategy_signals_option_type",
        ),
        sa.UniqueConstraint(
            "strategy_id",
            "strategy_version_id",
            "signal_id",
            name="uq_strategy_signals_strategy_version_signal",
        ),
    )
    for column in (
        "strategy_id",
        "strategy_version_id",
        "strategy_code",
        "signal_id",
        "payload_hash",
        "symbol",
        "action",
        "option_type",
        "received_at",
        "source",
        "status",
    ):
        op.create_index(f"ix_strategy_signals_{column}", "strategy_signals", [column])

    op.create_table(
        "paper_strategy_orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("user_strategy_subscriptions.id"), nullable=False),
        sa.Column("strategy_signal_id", sa.Integer(), sa.ForeignKey("strategy_signals.id"), nullable=False),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("option_type", sa.String(), nullable=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("strike", sa.Float(), nullable=True),
        sa.Column("expiry", sa.String(), nullable=True),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("fill_price", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="filled"),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('filled', 'rejected')", name="ck_paper_strategy_orders_status"),
        sa.CheckConstraint("qty > 0", name="ck_paper_strategy_orders_qty"),
        sa.CheckConstraint("fill_price > 0", name="ck_paper_strategy_orders_fill_price"),
        sa.UniqueConstraint(
            "user_id",
            "subscription_id",
            "strategy_signal_id",
            name="uq_paper_strategy_orders_user_subscription_signal",
        ),
    )
    for column in (
        "user_id",
        "subscription_id",
        "strategy_signal_id",
        "strategy_id",
        "strategy_version_id",
        "action",
        "option_type",
        "symbol",
        "status",
        "created_at",
    ):
        op.create_index(f"ix_paper_strategy_orders_{column}", "paper_strategy_orders", [column])

    op.create_table(
        "user_signal_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("user_strategy_subscriptions.id"), nullable=False),
        sa.Column("strategy_signal_id", sa.Integer(), sa.ForeignKey("strategy_signals.id"), nullable=False),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=False),
        sa.Column("mode", sa.String(), nullable=False, server_default="paper"),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("reason_code", sa.String(), nullable=True),
        sa.Column("reason_message", sa.String(), nullable=True),
        sa.Column("paper_order_id", sa.Integer(), sa.ForeignKey("paper_strategy_orders.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("mode = 'paper'", name="ck_user_signal_jobs_paper_only"),
        sa.CheckConstraint(
            "status IN ("
            "'queued', 'processing', 'skipped_subscription_paused', 'skipped_risk_not_set', "
            "'skipped_daily_trade_limit', 'skipped_daily_loss_limit', 'skipped_market_closed', "
            "'skipped_duplicate', 'skipped_invalid_instrument', 'paper_filled', "
            "'paper_rejected', 'failed'"
            ")",
            name="ck_user_signal_jobs_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_user_signal_jobs_attempt_count"),
        sa.UniqueConstraint(
            "user_id",
            "strategy_signal_id",
            "subscription_id",
            name="uq_user_signal_jobs_user_signal_subscription",
        ),
    )
    for column in (
        "user_id",
        "subscription_id",
        "strategy_signal_id",
        "strategy_id",
        "strategy_version_id",
        "mode",
        "status",
        "reason_code",
        "paper_order_id",
        "created_at",
    ):
        op.create_index(f"ix_user_signal_jobs_{column}", "user_signal_jobs", [column])

    _seed_strategy_catalog()


def downgrade() -> None:
    op.drop_table("user_signal_jobs")
    op.drop_table("paper_strategy_orders")
    op.drop_table("strategy_signals")
    op.drop_table("user_strategy_subscriptions")
    op.drop_table("strategy_versions")
    op.drop_table("strategies")


def _seed_strategy_catalog() -> None:
    now = datetime.now(timezone.utc)
    strategies = sa.table(
        "strategies",
        sa.column("id", sa.Integer()),
        sa.column("strategy_code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("risk_level", sa.String()),
        sa.column("market", sa.String()),
        sa.column("instrument_type", sa.String()),
        sa.column("timeframe", sa.String()),
        sa.column("is_active", sa.Boolean()),
        sa.column("is_paper_allowed", sa.Boolean()),
        sa.column("is_live_allowed", sa.Boolean()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    versions = sa.table(
        "strategy_versions",
        sa.column("strategy_id", sa.Integer()),
        sa.column("version", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("changelog", sa.String()),
        sa.column("signal_schema_json", sa.JSON()),
        sa.column("created_at", sa.DateTime()),
        sa.column("activated_at", sa.DateTime()),
    )
    rows = [
        (1, "ORB_PORTAL", "ORB Portal", "Opening-range breakout signals for NIFTY options.", "medium", "5m"),
        (2, "SUPERTREND_FLIP", "Supertrend Flip", "Trend reversal signals from confirmed Supertrend flips.", "medium", "5m"),
        (
            3,
            "SUPPORT_RESISTANCE_REVERSAL",
            "Support/Resistance Reversal",
            "Reversal signals near validated intraday support and resistance.",
            "medium",
            "5m",
        ),
        (4, "OOPS_GAP_REVERSAL", "OOPS Gap Reversal", "Gap-reversal signals after confirmation.", "high", "5m"),
        (
            5,
            "NIFTY_MOMENTUM_SCALPER",
            "Nifty Momentum Scalper",
            "Short-timeframe NIFTY option momentum signals.",
            "high",
            "1m",
        ),
    ]
    op.bulk_insert(
        strategies,
        [
            {
                "id": strategy_id,
                "strategy_code": code,
                "name": name,
                "description": description,
                "risk_level": risk,
                "market": "NSE",
                "instrument_type": "OPTIDX",
                "timeframe": timeframe,
                "is_active": True,
                "is_paper_allowed": True,
                "is_live_allowed": False,
                "created_at": now,
                "updated_at": now,
            }
            for strategy_id, code, name, description, risk, timeframe in rows
        ],
    )
    signal_schema = {
        "required": ["strategy_code", "signal_id", "symbol", "action"],
        "paper_fill_requires": ["price"],
        "option_types": ["CE", "PE"],
    }
    op.bulk_insert(
        versions,
        [
            {
                "strategy_id": strategy_id,
                "version": 1,
                "status": "active",
                "changelog": "Initial paper-safe catalog version.",
                "signal_schema_json": signal_schema,
                "created_at": now,
                "activated_at": now,
            }
            for strategy_id, *_rest in rows
        ],
    )
