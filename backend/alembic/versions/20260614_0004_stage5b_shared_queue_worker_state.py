"""stage 5b shared queue worker state

Revision ID: 20260614_0004
Revises: 20260614_0003
Create Date: 2026-06-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260614_0004"
down_revision = "20260614_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _replace_strategy_signal_constraints(
        status_values=(
            "'received', 'fanout_queued', 'fanout_started', 'fanout_completed', "
            "'fanout_failed', 'partial_failed', 'rejected'"
        ),
        action_values="'BUY', 'SELL', 'EXIT', 'CLOSE'",
    )

    op.create_table(
        "worker_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("locked_by", sa.String(), nullable=True),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed', 'dead', 'cancelled')",
            name="ck_worker_jobs_status",
        ),
        sa.CheckConstraint("priority >= 0", name="ck_worker_jobs_priority"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_worker_jobs_attempt_count"),
        sa.CheckConstraint("max_attempts > 0", name="ck_worker_jobs_max_attempts"),
    )
    for column in (
        "job_type",
        "dedupe_key",
        "status",
        "priority",
        "available_at",
        "locked_by",
        "locked_until",
        "created_at",
        "created_by_user_id",
    ):
        op.create_index(
            f"ix_worker_jobs_{column}",
            "worker_jobs",
            [column],
            unique=column == "dedupe_key",
        )

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(), primary_key=True),
        sa.Column("worker_role", sa.String(), nullable=False),
        sa.Column("hostname", sa.String(), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="starting"),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "status IN ('starting', 'running', 'stopping', 'stopped', 'failed')",
            name="ck_worker_heartbeats_status",
        ),
    )
    for column in ("worker_role", "last_seen_at", "status"):
        op.create_index(f"ix_worker_heartbeats_{column}", "worker_heartbeats", [column])

    op.create_table(
        "paper_positions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("user_strategy_subscriptions.id"),
            nullable=False,
        ),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("option_type", sa.String(), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("expiry", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False, server_default="LONG"),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("avg_entry_price", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("source_signal_id", sa.Integer(), sa.ForeignKey("strategy_signals.id"), nullable=False),
        sa.Column("last_signal_job_id", sa.Integer(), sa.ForeignKey("user_signal_jobs.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'closed', 'cancelled')",
            name="ck_paper_positions_status",
        ),
        sa.CheckConstraint("side = 'LONG'", name="ck_paper_positions_long_only"),
        sa.CheckConstraint("quantity > 0", name="ck_paper_positions_quantity"),
        sa.CheckConstraint("avg_entry_price > 0", name="ck_paper_positions_entry_price"),
    )
    for column in (
        "user_id",
        "subscription_id",
        "strategy_id",
        "strategy_version_id",
        "symbol",
        "option_type",
        "expiry",
        "side",
        "status",
        "opened_at",
        "closed_at",
        "source_signal_id",
        "last_signal_job_id",
    ):
        op.create_index(f"ix_paper_positions_{column}", "paper_positions", [column])
    op.create_index(
        "ix_paper_positions_open_instrument",
        "paper_positions",
        ["user_id", "subscription_id", "symbol", "option_type", "strike", "expiry", "side"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
        sqlite_where=sa.text("status = 'open'"),
    )

    op.create_table(
        "paper_ledger_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("user_strategy_subscriptions.id"),
            nullable=False,
        ),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("strategy_signal_id", sa.Integer(), sa.ForeignKey("strategy_signals.id"), nullable=False),
        sa.Column("user_signal_job_id", sa.Integer(), sa.ForeignKey("user_signal_jobs.id"), nullable=False),
        sa.Column("paper_position_id", sa.Integer(), sa.ForeignKey("paper_positions.id"), nullable=True),
        sa.Column("entry_type", sa.String(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "entry_type IN ('order_fill', 'position_close', 'pnl_adjustment', 'risk_block')",
            name="ck_paper_ledger_entries_type",
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity >= 0",
            name="ck_paper_ledger_entries_quantity",
        ),
        sa.CheckConstraint("price IS NULL OR price >= 0", name="ck_paper_ledger_entries_price"),
        sa.UniqueConstraint(
            "user_signal_job_id",
            "entry_type",
            name="uq_paper_ledger_entries_job_type",
        ),
    )
    for column in (
        "user_id",
        "subscription_id",
        "strategy_id",
        "strategy_signal_id",
        "user_signal_job_id",
        "paper_position_id",
        "entry_type",
        "created_at",
    ):
        op.create_index(f"ix_paper_ledger_entries_{column}", "paper_ledger_entries", [column])

    op.create_table(
        "user_notifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'failed')",
            name="ck_user_notifications_status",
        ),
    )
    for column in ("user_id", "event_type", "dedupe_key", "status", "created_at"):
        op.create_index(
            f"ix_user_notifications_{column}",
            "user_notifications",
            [column],
            unique=column == "dedupe_key",
        )


def downgrade() -> None:
    op.drop_table("user_notifications")
    op.drop_table("paper_ledger_entries")
    op.drop_table("paper_positions")
    op.drop_table("worker_heartbeats")
    op.drop_table("worker_jobs")
    _replace_strategy_signal_constraints(
        status_values="'received', 'fanout_started', 'fanout_completed', 'rejected'",
        action_values="'BUY', 'SELL', 'EXIT'",
    )


def _replace_strategy_signal_constraints(*, status_values: str, action_values: str) -> None:
    with op.batch_alter_table("strategy_signals") as batch_op:
        batch_op.drop_constraint("ck_strategy_signals_status", type_="check")
        batch_op.drop_constraint("ck_strategy_signals_action", type_="check")
        batch_op.create_check_constraint(
            "ck_strategy_signals_status",
            f"status IN ({status_values})",
        )
        batch_op.create_check_constraint(
            "ck_strategy_signals_action",
            f"action IN ({action_values})",
        )
