"""stage 6a signing relay and manual executor routing

Revision ID: 20260615_0005
Revises: 20260614_0004
Create Date: 2026-06-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260615_0005"
down_revision = "20260614_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_strategy_subscriptions") as batch:
        batch.drop_constraint("ck_user_strategy_subscriptions_paper_only", type_="check")
        batch.create_check_constraint("ck_user_strategy_subscriptions_mode", "mode IN ('paper', 'live')")
    with op.batch_alter_table("user_signal_jobs") as batch:
        batch.drop_constraint("ck_user_signal_jobs_paper_only", type_="check")
        batch.drop_constraint("ck_user_signal_jobs_status", type_="check")
        batch.create_check_constraint("ck_user_signal_jobs_mode", "mode IN ('paper', 'live')")
        batch.create_check_constraint(
            "ck_user_signal_jobs_status",
            "status IN ("
            "'queued', 'processing', 'skipped_subscription_paused', 'skipped_risk_not_set', "
            "'skipped_daily_trade_limit', 'skipped_daily_loss_limit', 'skipped_market_closed', "
            "'skipped_duplicate', 'skipped_invalid_instrument', 'paper_filled', 'paper_rejected', "
            "'skipped_live_not_approved', 'skipped_executor_not_assigned', "
            "'skipped_executor_not_verified', 'skipped_broker_not_connected', "
            "'skipped_live_disabled', 'skipped_dry_run_only', "
            "'skipped_strategy_not_live_allowed', 'live_dry_run_verified', "
            "'live_sent', 'live_confirmed', 'live_blocked', 'failed'"
            ")",
        )

    op.create_table(
        "executor_nodes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("executor_code", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False, server_default="manual"),
        sa.Column("droplet_name", sa.String(), nullable=False),
        sa.Column("region", sa.String(), nullable=False),
        sa.Column("reserved_ip", sa.String(), nullable=False),
        sa.Column("health_url", sa.String(), nullable=False),
        sa.Column("execute_url", sa.String(), nullable=False),
        sa.Column("egress_ip_url", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("last_health_status", sa.String(), nullable=True),
        sa.Column("last_egress_ip", sa.String(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("provider IN ('digitalocean', 'manual')", name="ck_executor_nodes_provider"),
        sa.CheckConstraint("status IN ('pending', 'active', 'disabled')", name="ck_executor_nodes_status"),
    )
    for column in ("executor_code", "provider", "region", "reserved_ip", "status", "last_seen_at"):
        op.create_index(
            f"ix_executor_nodes_{column}",
            "executor_nodes",
            [column],
            unique=column in {"executor_code", "reserved_ip"},
        )

    op.create_table(
        "user_executor_assignments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("executor_node_id", sa.Integer(), sa.ForeignKey("executor_nodes.id"), nullable=False),
        sa.Column("broker_client_id_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending_whitelist"),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("last_verified_egress_ip", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending_whitelist', 'verified', 'disabled')",
            name="ck_user_executor_assignments_status",
        ),
    )
    for column in ("user_id", "executor_node_id", "broker_client_id_hash", "status"):
        op.create_index(f"ix_user_executor_assignments_{column}", "user_executor_assignments", [column])
    op.create_index(
        "ix_user_executor_assignments_active_user",
        "user_executor_assignments",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status != 'disabled'"),
        sqlite_where=sa.text("status != 'disabled'"),
    )
    op.create_index(
        "ix_user_executor_assignments_active_node",
        "user_executor_assignments",
        ["executor_node_id"],
        unique=True,
        postgresql_where=sa.text("status != 'disabled'"),
        sqlite_where=sa.text("status != 'disabled'"),
    )

    op.create_table(
        "user_live_pilot_approvals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=False),
        sa.Column("approved_by_admin_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="approved"),
        sa.Column("max_qty", sa.Integer(), nullable=False),
        sa.Column("max_trades_per_day", sa.Integer(), nullable=False),
        sa.Column("max_daily_loss", sa.Float(), nullable=False),
        sa.Column("allowed_option_side", sa.String(), nullable=False, server_default="BOTH"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('approved', 'revoked')", name="ck_user_live_pilot_approvals_status"),
        sa.CheckConstraint("max_qty > 0", name="ck_user_live_pilot_approvals_qty"),
        sa.CheckConstraint("max_trades_per_day > 0", name="ck_user_live_pilot_approvals_trades"),
        sa.CheckConstraint("max_daily_loss >= 0", name="ck_user_live_pilot_approvals_loss"),
        sa.CheckConstraint(
            "allowed_option_side IN ('CE', 'PE', 'BOTH')",
            name="ck_user_live_pilot_approvals_side",
        ),
        sa.UniqueConstraint(
            "user_id", "strategy_id", "strategy_version_id",
            name="uq_user_live_pilot_approval_scope",
        ),
    )
    for column in (
        "user_id", "strategy_id", "strategy_version_id", "approved_by_admin_user_id",
        "status", "expires_at",
    ):
        op.create_index(f"ix_user_live_pilot_approvals_{column}", "user_live_pilot_approvals", [column])

    op.create_table(
        "live_order_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("user_strategy_subscriptions.id"), nullable=False),
        sa.Column("strategy_signal_id", sa.Integer(), sa.ForeignKey("strategy_signals.id"), nullable=False),
        sa.Column("user_signal_job_id", sa.Integer(), sa.ForeignKey("user_signal_jobs.id"), nullable=False),
        sa.Column("executor_node_id", sa.Integer(), sa.ForeignKey("executor_nodes.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("reason_code", sa.String(), nullable=True),
        sa.Column("reason_message", sa.String(), nullable=True),
        sa.Column("correlation_id", sa.String(), nullable=False),
        sa.Column("dhan_order_id", sa.String(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'dry_run_verified', 'sent', 'confirmed', 'blocked', 'failed')",
            name="ck_live_order_jobs_status",
        ),
        sa.UniqueConstraint(
            "user_id", "subscription_id", "strategy_signal_id",
            name="uq_live_order_jobs_user_subscription_signal",
        ),
    )
    for column in (
        "user_id", "subscription_id", "strategy_signal_id", "user_signal_job_id",
        "executor_node_id", "status", "reason_code", "correlation_id", "dhan_order_id",
        "dry_run", "created_at",
    ):
        op.create_index(
            f"ix_live_order_jobs_{column}",
            "live_order_jobs",
            [column],
            unique=column == "correlation_id",
        )

    op.create_table(
        "executor_nonce_receipts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("executor_code", sa.String(), nullable=False),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("request_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("executor_code", "nonce", name="uq_executor_nonce_receipt_code_nonce"),
    )
    for column in ("executor_code", "nonce", "received_at"):
        op.create_index(f"ix_executor_nonce_receipts_{column}", "executor_nonce_receipts", [column])

    op.execute(
        sa.text(
            "UPDATE strategies SET is_live_allowed = :enabled, updated_at = CURRENT_TIMESTAMP "
            "WHERE strategy_code = 'SUPERTREND_FLIP'"
        ).bindparams(enabled=True)
    )


def downgrade() -> None:
    op.execute("UPDATE strategies SET is_live_allowed = false WHERE strategy_code = 'SUPERTREND_FLIP'")
    op.drop_table("executor_nonce_receipts")
    op.drop_table("live_order_jobs")
    op.drop_table("user_live_pilot_approvals")
    op.drop_table("user_executor_assignments")
    op.drop_table("executor_nodes")
    with op.batch_alter_table("user_signal_jobs") as batch:
        batch.drop_constraint("ck_user_signal_jobs_mode", type_="check")
        batch.drop_constraint("ck_user_signal_jobs_status", type_="check")
        batch.create_check_constraint("ck_user_signal_jobs_paper_only", "mode = 'paper'")
        batch.create_check_constraint(
            "ck_user_signal_jobs_status",
            "status IN ("
            "'queued', 'processing', 'skipped_subscription_paused', 'skipped_risk_not_set', "
            "'skipped_daily_trade_limit', 'skipped_daily_loss_limit', 'skipped_market_closed', "
            "'skipped_duplicate', 'skipped_invalid_instrument', 'paper_filled', "
            "'paper_rejected', 'failed'"
            ")",
        )
    with op.batch_alter_table("user_strategy_subscriptions") as batch:
        batch.drop_constraint("ck_user_strategy_subscriptions_mode", type_="check")
        batch.create_check_constraint("ck_user_strategy_subscriptions_paper_only", "mode = 'paper'")
