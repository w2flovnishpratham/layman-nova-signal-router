"""stage 2 trading security persistence

Revision ID: 20260613_0002
Revises: 20260612_0001
Create Date: 2026-06-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260613_0002"
down_revision = "20260612_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_signal_receipts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("signal_id", sa.String(), nullable=False),
        sa.Column("strategy_code", sa.String(), nullable=True),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="received"),
        sa.Column("correlation_id", sa.String(), nullable=True),
        sa.Column("message", sa.String(), nullable=True),
        sa.UniqueConstraint("user_id", "signal_id", name="uq_webhook_signal_receipt_user_signal"),
    )
    for column in (
        "user_id",
        "signal_id",
        "strategy_code",
        "payload_hash",
        "first_seen_at",
        "status",
        "correlation_id",
    ):
        op.create_index(f"ix_webhook_signal_receipts_{column}", "webhook_signal_receipts", [column])

    op.create_table(
        "webhook_nonce_receipts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("request_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "nonce", name="uq_webhook_nonce_receipt_user_nonce"),
    )
    for column in ("user_id", "nonce", "first_seen_at"):
        op.create_index(f"ix_webhook_nonce_receipts_{column}", "webhook_nonce_receipts", [column])

    op.create_table(
        "order_route_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("correlation_id", sa.String(), nullable=False),
        sa.Column("signal_id", sa.String(), nullable=False),
        sa.Column("strategy_code", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("instrument_identity", sa.String(), nullable=False),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("order_id", sa.String(), nullable=True),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "correlation_id", name="uq_order_route_attempt_user_correlation"),
    )
    for column in (
        "user_id",
        "correlation_id",
        "signal_id",
        "strategy_code",
        "action",
        "instrument_identity",
        "payload_hash",
        "status",
        "order_id",
        "created_at",
    ):
        op.create_index(f"ix_order_route_attempts_{column}", "order_route_attempts", [column])


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS order_route_attempts")
    op.execute("DROP TABLE IF EXISTS webhook_nonce_receipts")
    op.execute("DROP TABLE IF EXISTS webhook_signal_receipts")
