"""Per-user setup profile for resume-with-previous-settings.

Revision ID: 0005_user_setup_profiles
Revises: 0004_entitlements_payment_events
Create Date: 2026-07-01 00:00:00 UTC
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db import models


revision = "0005_user_setup_profiles"
down_revision = "0004_entitlements_payment_events"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("user_setup_profiles"):
        op.create_table(
            "user_setup_profiles",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("engine_mode", sa.String(length=20), nullable=False),
            sa.Column("strategy_name", sa.String(length=120), nullable=True),
            sa.Column("side", sa.String(length=8), nullable=True),
            sa.Column("lots", sa.Integer(), nullable=True),
            sa.Column("max_trades", sa.Integer(), nullable=True),
            sa.Column("max_loss", sa.Integer(), nullable=True),
            sa.Column("exit_mode", sa.String(length=20), nullable=True),
            sa.Column("target_pct", sa.Float(), nullable=True),
            sa.Column("target_profit", sa.Integer(), nullable=True),
            sa.Column("stop_loss_pct", sa.Float(), nullable=True),
            sa.Column("paper_starting_balance", sa.Float(), nullable=True),
            sa.Column("paper_available_balance", sa.Float(), nullable=True),
            sa.Column("paper_realized_pnl", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_user_setup_profiles_user"),
        )
        op.create_index("ix_user_setup_profiles_user_id", "user_setup_profiles", ["user_id"])


def downgrade() -> None:
    if _has_table("user_setup_profiles"):
        op.drop_index("ix_user_setup_profiles_user_id", table_name="user_setup_profiles")
        op.drop_table("user_setup_profiles")
