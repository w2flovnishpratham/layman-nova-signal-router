"""Persist global authoritative NIFTY one-minute candles.

Revision ID: 0025_market_candles_1m
Revises: 0024_strategy_webhook_provider
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_market_candles_1m"
down_revision = "0024_strategy_webhook_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "market_candles_1m" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "market_candles_1m",
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("candle_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(18, 4), nullable=False),
        sa.Column("high", sa.Numeric(18, 4), nullable=False),
        sa.Column("low", sa.Numeric(18, 4), nullable=False),
        sa.Column("close", sa.Numeric(18, 4), nullable=False),
        sa.Column("volume", sa.Numeric(24, 4), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "candle_time", name="pk_market_candles_1m"),
    )
    op.create_index(
        "ix_market_candles_1m_date_time",
        "market_candles_1m",
        ["trading_date", "candle_time"],
    )


def downgrade() -> None:
    if "market_candles_1m" not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.drop_index("ix_market_candles_1m_date_time", table_name="market_candles_1m")
    op.drop_table("market_candles_1m")
