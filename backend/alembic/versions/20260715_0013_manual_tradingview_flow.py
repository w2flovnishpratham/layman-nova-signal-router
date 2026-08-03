"""Manual TradingView integration and prompt qualification records.

Revision ID: 0013_manual_tradingview_flow
Revises: 0012_pine_conversion_requests

This revision intentionally branches from Phase 4B. The parked Phase 5A
revision has a different identifier and will require an Alembic merge revision
when both branches are eventually combined.
"""
from alembic import op
import sqlalchemy as sa

from app.db import models


revision = "0013_manual_tradingview_flow"
down_revision = "0012_pine_conversion_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "tradingview_setups" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "pine_prompt_versions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("version_label", sa.String(80), nullable=False, unique=True),
        sa.Column("content_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="QUALIFICATION"),
        sa.Column("change_notes", sa.Text()),
        sa.Column("approved_by_user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "pine_user_acceptances",
        sa.Column("id", models.GUID(), primary_key=True),
        sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_version_id", models.GUID(), sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("candidate_version_id", models.GUID(), sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("prompt_version_id", sa.String(40), sa.ForeignKey("pine_prompt_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("setup_type", sa.String(40), nullable=False),
        sa.Column("validation_report_id", models.GUID(), sa.ForeignKey("strategy_validation_reports.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("validation_report_sha256", sa.String(64), nullable=False),
        sa.Column("assumptions", models.JSONType()),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_version_id", name="uq_pine_acceptance_candidate"),
    )
    op.create_index("ix_pine_user_acceptances_user_id", "pine_user_acceptances", ["user_id"])
    op.create_table(
        "tradingview_setups",
        sa.Column("id", models.GUID(), primary_key=True),
        sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_instance_id", models.GUID(), sa.ForeignKey("strategy_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("approved_version_id", models.GUID(), sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("setup_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="SETUP_PENDING"),
        sa.Column("requested_timeframe", sa.String(20)),
        sa.Column("user_reported_compiled_at", sa.DateTime(timezone=True)),
        sa.Column("installation_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("installation_metadata", models.JSONType()),
        sa.Column("hold_signal_id", sa.String(255)),
        sa.Column("hold_verified_at", sa.DateTime(timezone=True)),
        sa.Column("paper_entry_signal_id", sa.String(255)),
        sa.Column("paper_entry_verified_at", sa.DateTime(timezone=True)),
        sa.Column("paper_exit_signal_id", sa.String(255)),
        sa.Column("paper_exit_verified_at", sa.DateTime(timezone=True)),
        sa.Column("reversal_signal_id", sa.String(255)),
        sa.Column("reversal_verified_at", sa.DateTime(timezone=True)),
        sa.Column("blocking_reason", sa.Text()),
        sa.Column("admin_notes", sa.Text()),
        sa.Column("reset_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("strategy_instance_id", name="uq_tradingview_setup_instance"),
    )
    op.create_index("ix_tradingview_setups_user_id", "tradingview_setups", ["user_id"])
    op.create_index("ix_tradingview_setups_type_status", "tradingview_setups", ["setup_type", "status"])
    op.create_index("ix_tradingview_setups_owner_updated", "tradingview_setups", ["user_id", "updated_at"])
    op.create_table(
        "pine_prompt_qualification_trials",
        sa.Column("id", models.GUID(), primary_key=True),
        sa.Column("prompt_version_id", sa.String(40), sa.ForeignKey("pine_prompt_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("original_version_id", models.GUID(), sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("candidate_version_id", models.GUID(), sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("strategy_type", sa.String(80), nullable=False),
        sa.Column("test_classification", sa.String(50), nullable=False),
        sa.Column("evidence", models.JSONType(), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("tester_user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pine_prompt_trials_prompt_outcome", "pine_prompt_qualification_trials", ["prompt_version_id", "outcome"])


def downgrade() -> None:
    op.drop_index("ix_pine_prompt_trials_prompt_outcome", table_name="pine_prompt_qualification_trials")
    op.drop_table("pine_prompt_qualification_trials")
    op.drop_index("ix_tradingview_setups_owner_updated", table_name="tradingview_setups")
    op.drop_index("ix_tradingview_setups_type_status", table_name="tradingview_setups")
    op.drop_index("ix_tradingview_setups_user_id", table_name="tradingview_setups")
    op.drop_table("tradingview_setups")
    op.drop_index("ix_pine_user_acceptances_user_id", table_name="pine_user_acceptances")
    op.drop_table("pine_user_acceptances")
    op.drop_table("pine_prompt_versions")
