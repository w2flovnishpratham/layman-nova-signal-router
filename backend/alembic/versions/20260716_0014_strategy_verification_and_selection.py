"""Controlled paper verification mode and persisted engine strategy selection.

Revision ID: 0014_strategy_verification_and_selection
Revises: 0013_manual_tradingview_flow

Adds the fields that break the readiness deadlock (a personal strategy can run
paper-only verification signals before it is generally activatable) and persist
the engine picker's selected strategy instance. Branches from the Phase 4B
lineage, not Phase 5A's parked hosted-runtime revision.
"""
from alembic import op
import sqlalchemy as sa

from app.db import models


revision = "0014_strategy_verification_and_selection"
down_revision = "0013_manual_tradingview_flow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns("strategy_instances")}
    if "verification_mode" not in columns:
        op.add_column(
            "strategy_instances",
            sa.Column("verification_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.add_column("strategy_instances", sa.Column("verification_started_at", sa.DateTime(timezone=True)))
        op.add_column("strategy_instances", sa.Column("verification_completed_at", sa.DateTime(timezone=True)))

    if "user_engine_configs" not in inspector.get_table_names():
        op.create_table(
            "user_engine_configs",
            sa.Column("id", models.GUID(), primary_key=True),
            sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column(
                "selected_strategy_instance_id",
                models.GUID(),
                sa.ForeignKey("strategy_instances.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_user_engine_config_user"),
        )


def downgrade() -> None:
    op.drop_table("user_engine_configs")
    op.drop_column("strategy_instances", "verification_completed_at")
    op.drop_column("strategy_instances", "verification_started_at")
    op.drop_column("strategy_instances", "verification_mode")
