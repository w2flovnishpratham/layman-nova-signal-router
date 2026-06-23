"""Durable webhook replay store and strategy risk caps.

Revision ID: 0002_durable_webhook_risk
Revises: 0001_baseline
Create Date: 2026-06-23 00:00:01 UTC
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db import models


revision = "0002_durable_webhook_risk"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _create_webhook_events() -> None:
    if _has_table("webhook_events"):
        return
    op.create_table(
        "webhook_events",
        sa.Column("id", models.GUID(), primary_key=True, nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("raw_body_sha256", sa.String(length=64), nullable=False),
        sa.Column("signature_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("replay_status", sa.String(length=20), nullable=False, server_default="fresh"),
        sa.Column("processed_status", sa.String(length=30), nullable=False, server_default="received"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata", models.JSONType(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "event_id", name="uq_webhook_event_provider_event"),
    )
    op.create_index("ix_webhook_events_provider", "webhook_events", ["provider"])
    op.create_index("ix_webhook_events_event_id", "webhook_events", ["event_id"])
    op.create_index("ix_webhook_events_user_id", "webhook_events", ["user_id"])
    op.create_index("ix_webhook_events_user_received", "webhook_events", ["user_id", "received_at"])


def _create_webhook_nonces() -> None:
    if _has_table("webhook_nonces"):
        return
    op.create_table(
        "webhook_nonces",
        sa.Column("id", models.GUID(), primary_key=True, nullable=False),
        sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nonce", sa.String(length=255), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "nonce", name="uq_webhook_nonce_user_nonce"),
    )
    op.create_index("ix_webhook_nonces_user_id", "webhook_nonces", ["user_id"])
    op.create_index("ix_webhook_nonces_seen_at", "webhook_nonces", ["seen_at"])


def _create_user_risk_controls() -> None:
    if _has_table("user_risk_controls"):
        return
    op.create_table(
        "user_risk_controls",
        sa.Column("id", models.GUID(), primary_key=True, nullable=False),
        sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kill_switch", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_lots_per_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_notional_per_trade_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("max_orders_per_day", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_loss_per_day_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_user_risk_controls_user_id"),
    )
    op.create_index("ix_user_risk_controls_user_id", "user_risk_controls", ["user_id"])


def _create_user_strategy_risk_controls() -> None:
    if _has_table("user_strategy_risk_controls"):
        return
    op.create_table(
        "user_strategy_risk_controls",
        sa.Column("id", models.GUID(), primary_key=True, nullable=False),
        sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_name", sa.String(length=120), nullable=False),
        sa.Column("kill_switch", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_lots_per_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_notional_per_trade_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("max_orders_per_day", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_loss_per_day_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "strategy_name", name="uq_user_strategy_risk_control"),
    )
    op.create_index("ix_user_strategy_risk_controls_user_id", "user_strategy_risk_controls", ["user_id"])
    op.create_index("ix_user_strategy_risk_controls_strategy_name", "user_strategy_risk_controls", ["strategy_name"])


def _create_user_strategy_daily_risk_counters() -> None:
    if _has_table("user_strategy_daily_risk_counters"):
        return
    op.create_table(
        "user_strategy_daily_risk_counters",
        sa.Column("id", models.GUID(), primary_key=True, nullable=False),
        sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_name", sa.String(length=120), nullable=False),
        sa.Column("trade_date_ist", sa.Date(), nullable=False),
        sa.Column("orders_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notional_used_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("realized_pnl_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "strategy_name", "trade_date_ist", name="uq_user_strategy_daily_risk"),
    )
    op.create_index("ix_user_strategy_daily_risk_counters_user_id", "user_strategy_daily_risk_counters", ["user_id"])
    op.create_index("ix_user_strategy_daily_risk_counters_strategy_name", "user_strategy_daily_risk_counters", ["strategy_name"])
    op.create_index(
        "ix_user_strategy_daily_risk_user_date",
        "user_strategy_daily_risk_counters",
        ["user_id", "trade_date_ist"],
    )


def upgrade() -> None:
    _create_webhook_events()
    _create_webhook_nonces()
    _create_user_risk_controls()
    _create_user_strategy_risk_controls()
    _create_user_strategy_daily_risk_counters()


def downgrade() -> None:
    for table_name in (
        "user_strategy_daily_risk_counters",
        "user_strategy_risk_controls",
        "user_risk_controls",
        "webhook_nonces",
        "webhook_events",
    ):
        if _has_table(table_name):
            op.drop_table(table_name)
