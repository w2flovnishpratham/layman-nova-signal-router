"""Add durable engine starts and bind entry jobs to configuration revisions.

Revision ID: 0020_engine_start_entry
Revises: 0019_trading_config_revisions
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0020_engine_start_entry"
down_revision = "0019_trading_config_revisions"
branch_labels = None
depends_on = None

_START_TABLE = "engine_start_operations"
_REVISION_TABLE = "strategy_configuration_revisions"


def _json_type():
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _foreign_keys(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {
        str(constraint["name"])
        for constraint in sa.inspect(op.get_bind()).get_foreign_keys(table)
        if constraint.get("name")
    }


def upgrade() -> None:
    if _START_TABLE not in _tables():
        op.create_table(
            _START_TABLE,
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("idempotency_key", sa.String(255), nullable=False),
            sa.Column("payload_hash", sa.String(64), nullable=False),
            sa.Column("mode", sa.String(20), nullable=False),
            sa.Column(
                "strategy_instance_id",
                sa.Uuid(),
                sa.ForeignKey("strategy_instances.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "strategy_version_id",
                sa.Uuid(),
                sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "configuration_revision_id",
                sa.Uuid(),
                sa.ForeignKey(f"{_REVISION_TABLE}.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("configuration_revision", sa.Integer(), nullable=False),
            sa.Column(
                "live_acknowledged",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
            sa.Column(
                "started_run_id",
                sa.Uuid(),
                sa.ForeignKey("user_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("result_summary", _json_type(), nullable=True),
            sa.Column("error_code", sa.String(80), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "user_id",
                "idempotency_key",
                name="uq_engine_start_operation_user_key",
            ),
        )
        op.create_index(
            "ix_engine_start_operations_user_created",
            _START_TABLE,
            ["user_id", "created_at"],
        )
        op.create_index(
            "ix_engine_start_operations_status_updated",
            _START_TABLE,
            ["status", "updated_at"],
        )

    job_columns = _columns("strategy_execution_jobs")
    with op.batch_alter_table("strategy_execution_jobs") as batch:
        if "configuration_revision_id" not in job_columns:
            batch.add_column(sa.Column("configuration_revision_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_strategy_job_configuration_revision",
                _REVISION_TABLE,
                ["configuration_revision_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_index(
                "ix_strategy_execution_jobs_configuration_revision_id",
                ["configuration_revision_id"],
            )
        if "configuration_revision" not in job_columns:
            batch.add_column(sa.Column("configuration_revision", sa.Integer(), nullable=True))

    trade_columns = _columns("portfolio_trades")
    with op.batch_alter_table("portfolio_trades") as batch:
        if "configuration_revision_id" not in trade_columns:
            batch.add_column(sa.Column("configuration_revision_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_portfolio_trade_configuration_revision",
                _REVISION_TABLE,
                ["configuration_revision_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_index(
                "ix_portfolio_trades_configuration_revision_id",
                ["configuration_revision_id"],
            )
        if "configuration_revision" not in trade_columns:
            batch.add_column(sa.Column("configuration_revision", sa.Integer(), nullable=True))


def downgrade() -> None:
    trade_columns = _columns("portfolio_trades")
    trade_foreign_keys = _foreign_keys("portfolio_trades")
    with op.batch_alter_table("portfolio_trades") as batch:
        if "configuration_revision" in trade_columns:
            batch.drop_column("configuration_revision")
        if "configuration_revision_id" in trade_columns:
            batch.drop_index("ix_portfolio_trades_configuration_revision_id")
            if "fk_portfolio_trade_configuration_revision" in trade_foreign_keys:
                batch.drop_constraint(
                    "fk_portfolio_trade_configuration_revision",
                    type_="foreignkey",
                )
            batch.drop_column("configuration_revision_id")

    job_columns = _columns("strategy_execution_jobs")
    job_foreign_keys = _foreign_keys("strategy_execution_jobs")
    with op.batch_alter_table("strategy_execution_jobs") as batch:
        if "configuration_revision" in job_columns:
            batch.drop_column("configuration_revision")
        if "configuration_revision_id" in job_columns:
            batch.drop_index("ix_strategy_execution_jobs_configuration_revision_id")
            if "fk_strategy_job_configuration_revision" in job_foreign_keys:
                batch.drop_constraint(
                    "fk_strategy_job_configuration_revision",
                    type_="foreignkey",
                )
            batch.drop_column("configuration_revision_id")

    if _START_TABLE in _tables():
        op.drop_table(_START_TABLE)
