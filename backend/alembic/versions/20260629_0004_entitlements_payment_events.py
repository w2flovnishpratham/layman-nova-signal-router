"""Durable user entitlements and payment events.

Revision ID: 0004_entitlements_payment_events
Revises: 0003_live_order_intents
Create Date: 2026-06-29 00:00:00 UTC
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db import models


revision = "0004_entitlements_payment_events"
down_revision = "0003_live_order_intents"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("user_entitlements"):
        op.create_table(
            "user_entitlements",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("plan_code", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("source", sa.String(length=40), nullable=False),
            sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("live_orders_enabled", sa.Boolean(), nullable=False),
            sa.Column("static_ip_enabled", sa.Boolean(), nullable=False),
            sa.Column("strategy_access_enabled", sa.Boolean(), nullable=False),
            sa.Column("max_strategy_count", sa.Integer(), nullable=True),
            sa.Column("metadata", models.JSONType(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_user_entitlements_user_id", "user_entitlements", ["user_id"])
        op.create_index("ix_user_entitlements_status", "user_entitlements", ["status"])
        op.create_index("ix_user_entitlements_user_status", "user_entitlements", ["user_id", "status"])
        op.create_index("ix_user_entitlements_user_expires", "user_entitlements", ["user_id", "expires_at"])

    if not _has_table("payment_events"):
        op.create_table(
            "payment_events",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("provider", sa.String(length=80), nullable=False),
            sa.Column("provider_event_id", sa.String(length=255), nullable=False),
            sa.Column("event_type", sa.String(length=120), nullable=False),
            sa.Column("event_status", sa.String(length=40), nullable=False),
            sa.Column("amount", sa.BigInteger(), nullable=True),
            sa.Column("currency", sa.String(length=12), nullable=True),
            sa.Column("payload_hash", sa.String(length=64), nullable=False),
            sa.Column("processed", sa.Boolean(), nullable=False),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("metadata", models.JSONType(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("provider", "provider_event_id", name="uq_payment_event_provider_event"),
        )
        op.create_index("ix_payment_events_user_id", "payment_events", ["user_id"])
        op.create_index("ix_payment_events_received_at", "payment_events", ["received_at"])


def downgrade() -> None:
    if _has_table("payment_events"):
        op.drop_table("payment_events")
    if _has_table("user_entitlements"):
        op.drop_table("user_entitlements")
