"""Add canonical trading configuration revision history.

Revision ID: 0019_trading_configuration_revisions
Revises: 0018_user_preferences

The existing StrategyInstance.risk_config stores only the current per-mode
snapshot and runtime JSON mirrors risk settings for the execution path. Neither
can retain multiple immutable revisions or identify the exact revision used by
an engine run. This additive table provides that missing owner-scoped history;
the legacy snapshots remain as backward-compatible derived mirrors.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_trading_config_revisions"
down_revision = "0018_user_preferences"
branch_labels = None
depends_on = None

_TABLE = "strategy_configuration_revisions"
_ALERT_ACK_TABLE = "terminal_alert_acknowledgements"


def _json_type():
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _foreign_keys(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {
        str(constraint["name"])
        for constraint in inspector.get_foreign_keys(table)
        if constraint.get("name")
    }


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _ALERT_ACK_TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _ALERT_ACK_TABLE,
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event_key", sa.String(64), nullable=False),
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "event_key", name="uq_terminal_alert_ack_user_event"),
        )
        op.create_index(
            "ix_terminal_alert_ack_user_at",
            _ALERT_ACK_TABLE,
            ["user_id", "acknowledged_at"],
        )
    if _TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column(
                "strategy_instance_id",
                sa.Uuid(),
                sa.ForeignKey("strategy_instances.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "strategy_version_id",
                sa.Uuid(),
                sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("mode", sa.String(20), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("configuration_json", _json_type(), nullable=False),
            sa.Column("risk_json", _json_type(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="committed"),
            sa.Column(
                "supersedes_revision_id",
                sa.Uuid(),
                sa.ForeignKey(f"{_TABLE}.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint(
                "user_id",
                "strategy_instance_id",
                "strategy_version_id",
                "mode",
                "revision",
                name="uq_strategy_configuration_revision_identity",
            ),
        )
        op.create_index(
            "ix_strategy_configuration_user_mode_status",
            _TABLE,
            ["user_id", "mode", "status"],
        )
        op.create_index(
            "ix_strategy_configuration_instance_created",
            _TABLE,
            ["strategy_instance_id", "created_at"],
        )

    engine_columns = _columns("user_engine_configs")
    with op.batch_alter_table("user_engine_configs") as batch:
        if "selected_configuration_revision_id" not in engine_columns:
            batch.add_column(sa.Column("selected_configuration_revision_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_user_engine_selected_configuration",
                _TABLE,
                ["selected_configuration_revision_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "selected_configuration_revision" not in engine_columns:
            batch.add_column(sa.Column("selected_configuration_revision", sa.Integer(), nullable=True))

    run_columns = _columns("user_runs")
    with op.batch_alter_table("user_runs") as batch:
        if "configuration_revision_id" not in run_columns:
            batch.add_column(sa.Column("configuration_revision_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_user_run_configuration_revision",
                _TABLE,
                ["configuration_revision_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "configuration_revision" not in run_columns:
            batch.add_column(sa.Column("configuration_revision", sa.Integer(), nullable=True))
        if "strategy_version_id" not in run_columns:
            batch.add_column(sa.Column("strategy_version_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_user_run_strategy_version",
                "strategy_versions",
                ["strategy_version_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    run_columns = _columns("user_runs")
    run_foreign_keys = _foreign_keys("user_runs")
    with op.batch_alter_table("user_runs") as batch:
        if "strategy_version_id" in run_columns:
            if "fk_user_run_strategy_version" in run_foreign_keys:
                batch.drop_constraint("fk_user_run_strategy_version", type_="foreignkey")
            batch.drop_column("strategy_version_id")
        if "configuration_revision" in run_columns:
            batch.drop_column("configuration_revision")
        if "configuration_revision_id" in run_columns:
            if "fk_user_run_configuration_revision" in run_foreign_keys:
                batch.drop_constraint("fk_user_run_configuration_revision", type_="foreignkey")
            batch.drop_column("configuration_revision_id")

    engine_columns = _columns("user_engine_configs")
    engine_foreign_keys = _foreign_keys("user_engine_configs")
    with op.batch_alter_table("user_engine_configs") as batch:
        if "selected_configuration_revision" in engine_columns:
            batch.drop_column("selected_configuration_revision")
        if "selected_configuration_revision_id" in engine_columns:
            if "fk_user_engine_selected_configuration" in engine_foreign_keys:
                batch.drop_constraint("fk_user_engine_selected_configuration", type_="foreignkey")
            batch.drop_column("selected_configuration_revision_id")

    inspector = sa.inspect(op.get_bind())
    if _TABLE in set(inspector.get_table_names()):
        op.drop_table(_TABLE)
    inspector = sa.inspect(op.get_bind())
    if _ALERT_ACK_TABLE in set(inspector.get_table_names()):
        op.drop_table(_ALERT_ACK_TABLE)
