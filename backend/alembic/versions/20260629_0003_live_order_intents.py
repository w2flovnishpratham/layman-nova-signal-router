"""Durable live order idempotency intents.

Revision ID: 0003_live_order_intents
Revises: 0002_durable_webhook_risk
Create Date: 2026-06-29 00:00:00 UTC
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db import models


revision = "0003_live_order_intents"
down_revision = "0002_durable_webhook_risk"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("live_order_intents"):
        return
    op.create_table(
        "live_order_intents",
        sa.Column("id", models.GUID(), primary_key=True, nullable=False),
        sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(length=60), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("signal_id", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=True),
        sa.Column("side", sa.String(length=20), nullable=True),
        sa.Column("symbol", sa.String(length=120), nullable=True),
        sa.Column("broker_order_id", sa.String(length=120), nullable=True),
        sa.Column("broker_correlation_id", sa.String(length=120), nullable=True),
        sa.Column("result_summary", models.JSONType(), nullable=True),
        sa.Column("metadata", models.JSONType(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "scope", "idempotency_key", name="uq_live_order_intent_user_scope_key"),
    )
    op.create_index("ix_live_order_intents_user_id", "live_order_intents", ["user_id"])
    op.create_index("ix_live_order_intents_scope", "live_order_intents", ["scope"])
    op.create_index("ix_live_order_intents_signal_id", "live_order_intents", ["signal_id"])
    op.create_index("ix_live_order_intents_broker_order_id", "live_order_intents", ["broker_order_id"])
    op.create_index("ix_live_order_intents_user_created", "live_order_intents", ["user_id", "created_at"])
    op.create_index("ix_live_order_intents_status_updated", "live_order_intents", ["status", "updated_at"])


def downgrade() -> None:
    if _has_table("live_order_intents"):
        op.drop_table("live_order_intents")
