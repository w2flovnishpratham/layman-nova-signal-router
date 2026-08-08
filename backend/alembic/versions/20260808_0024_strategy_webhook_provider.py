"""Add webhook_event_provider to strategy_signals.

Revision ID: 0024_strategy_webhook_provider
Revises: 0023_paper_trading_entitlement
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_strategy_webhook_provider"
down_revision = "0023_paper_trading_entitlement"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    if table not in set(sa.inspect(op.get_bind()).get_table_names()):
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # Inspector-guarded, matching 0023: a from-scratch upgrade already has
    # this column via Base.metadata.create_all against current models.py, a
    # stamped production database does not.
    columns = _columns("strategy_signals")
    with op.batch_alter_table("strategy_signals") as batch:
        if "webhook_event_provider" not in columns:
            batch.add_column(sa.Column("webhook_event_provider", sa.String(length=160), nullable=True))


def downgrade() -> None:
    columns = _columns("strategy_signals")
    with op.batch_alter_table("strategy_signals") as batch:
        if "webhook_event_provider" in columns:
            batch.drop_column("webhook_event_provider")
