"""Add paper_trading_enabled to user_entitlements.

Revision ID: 0023_paper_trading_entitlement
Revises: 0022_versioned_risk
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_paper_trading_entitlement"
down_revision = "0022_versioned_risk"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    if table not in set(sa.inspect(op.get_bind()).get_table_names()):
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # Inspector-guarded, matching migrations 0018/0021/0022: the 0001
    # baseline runs Base.metadata.create_all against the current
    # db/models.py, so a from-scratch upgrade already has this column by
    # the time 0023 runs, while a stamped production database does not.
    columns = _columns("user_entitlements")
    with op.batch_alter_table("user_entitlements") as batch:
        if "paper_trading_enabled" not in columns:
            batch.add_column(
                sa.Column(
                    "paper_trading_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    columns = _columns("user_entitlements")
    with op.batch_alter_table("user_entitlements") as batch:
        if "paper_trading_enabled" in columns:
            batch.drop_column("paper_trading_enabled")
