"""Add owner/mode risk configurations, immutable versions, and entry snapshots.

Revision ID: 0022_versioned_risk
Revises: 0021_cred_verify_wallet_origin
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0022_versioned_risk"
down_revision = "0021_cred_verify_wallet_origin"
branch_labels = None
depends_on = None


def _json_type():
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _index_names(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def _fk_names(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {fk["name"] for fk in sa.inspect(op.get_bind()).get_foreign_keys(table)}


def upgrade() -> None:
    # Inspector-guarded throughout, matching migrations 0018/0021: the 0001
    # baseline runs Base.metadata.create_all against the current db/models.py,
    # so a from-scratch upgrade already has these tables/columns by the time
    # 0022 runs, while a stamped production database does not.
    existing = _tables()

    if "user_risk_configurations" not in existing:
        op.create_table(
            "user_risk_configurations",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("execution_mode", sa.String(20), nullable=False),
            sa.Column("active_version_id", sa.Uuid(), nullable=True),
            sa.Column("based_on_preset", sa.String(30), nullable=False, server_default="BALANCED"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "execution_mode", name="uq_user_risk_configuration_mode"),
        )
    if "ix_user_risk_configurations_user_id" not in _index_names("user_risk_configurations"):
        op.create_index(
            "ix_user_risk_configurations_user_id",
            "user_risk_configurations",
            ["user_id"],
        )

    if "user_risk_configuration_versions" not in existing:
        op.create_table(
            "user_risk_configuration_versions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("risk_configuration_id", sa.Uuid(), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("preset_key", sa.String(30), nullable=False),
            sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("daily_loss_cap_paise", sa.BigInteger(), nullable=False),
            sa.Column("max_loss_per_trade_paise", sa.BigInteger(), nullable=False),
            sa.Column("max_trades_per_day", sa.Integer(), nullable=False),
            sa.Column("max_open_positions", sa.Integer(), nullable=False),
            sa.Column("lots_per_trade_min", sa.Integer(), nullable=False),
            sa.Column("lots_per_trade_max", sa.Integer(), nullable=False),
            sa.Column("cooldown_minutes", sa.Integer(), nullable=False),
            sa.Column("exit_mode", sa.String(30), nullable=False),
            sa.Column("stop_loss_value", sa.Float(), nullable=True),
            sa.Column("take_profit_value", sa.Float(), nullable=True),
            sa.Column("stop_loss_basis", sa.String(20), nullable=False),
            sa.Column("take_profit_basis", sa.String(20), nullable=False),
            sa.Column("margin_exposure_cap_paise", sa.BigInteger(), nullable=True),
            sa.Column("change_source", sa.String(30), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["risk_configuration_id"],
                ["user_risk_configurations.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "risk_configuration_id",
                "version_number",
                name="uq_user_risk_version_number",
            ),
        )
    version_indexes = _index_names("user_risk_configuration_versions")
    if "ix_user_risk_configuration_versions_risk_configuration_id" not in version_indexes:
        op.create_index(
            "ix_user_risk_configuration_versions_risk_configuration_id",
            "user_risk_configuration_versions",
            ["risk_configuration_id"],
        )
    if "ix_user_risk_versions_configuration_created" not in version_indexes:
        op.create_index(
            "ix_user_risk_versions_configuration_created",
            "user_risk_configuration_versions",
            ["risk_configuration_id", "created_at"],
        )

    if "fk_user_risk_configuration_active_version" not in _fk_names("user_risk_configurations"):
        with op.batch_alter_table("user_risk_configurations") as batch:
            batch.create_foreign_key(
                "fk_user_risk_configuration_active_version",
                "user_risk_configuration_versions",
                ["active_version_id"],
                ["id"],
                ondelete="SET NULL",
            )

    live_order_columns = _columns("live_order_intents")
    live_order_fks = _fk_names("live_order_intents")
    live_order_indexes = _index_names("live_order_intents")
    with op.batch_alter_table("live_order_intents") as batch:
        if "risk_configuration_version_id" not in live_order_columns:
            batch.add_column(sa.Column("risk_configuration_version_id", sa.Uuid(), nullable=True))
        if "fk_live_order_intent_risk_version" not in live_order_fks:
            batch.create_foreign_key(
                "fk_live_order_intent_risk_version",
                "user_risk_configuration_versions",
                ["risk_configuration_version_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "ix_live_order_intents_risk_configuration_version_id" not in live_order_indexes:
            batch.create_index(
                "ix_live_order_intents_risk_configuration_version_id",
                ["risk_configuration_version_id"],
            )

    position_columns = _columns("strategy_instance_positions")
    position_fks = _fk_names("strategy_instance_positions")
    position_indexes = _index_names("strategy_instance_positions")
    with op.batch_alter_table("strategy_instance_positions") as batch:
        if "risk_configuration_version_id" not in position_columns:
            batch.add_column(sa.Column("risk_configuration_version_id", sa.Uuid(), nullable=True))
        if "entry_stop_rule" not in position_columns:
            batch.add_column(sa.Column("entry_stop_rule", _json_type(), nullable=True))
        if "entry_target_rule" not in position_columns:
            batch.add_column(sa.Column("entry_target_rule", _json_type(), nullable=True))
        if "position_sizing_rule" not in position_columns:
            batch.add_column(sa.Column("position_sizing_rule", _json_type(), nullable=True))
        if "maximum_loss_rule" not in position_columns:
            batch.add_column(sa.Column("maximum_loss_rule", _json_type(), nullable=True))
        if "fk_position_risk_version" not in position_fks:
            batch.create_foreign_key(
                "fk_position_risk_version",
                "user_risk_configuration_versions",
                ["risk_configuration_version_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "ix_strategy_instance_positions_risk_configuration_version_id" not in position_indexes:
            batch.create_index(
                "ix_strategy_instance_positions_risk_configuration_version_id",
                ["risk_configuration_version_id"],
            )


def downgrade() -> None:
    position_columns = _columns("strategy_instance_positions")
    position_fks = _fk_names("strategy_instance_positions")
    position_indexes = _index_names("strategy_instance_positions")
    with op.batch_alter_table("strategy_instance_positions") as batch:
        if "ix_strategy_instance_positions_risk_configuration_version_id" in position_indexes:
            batch.drop_index("ix_strategy_instance_positions_risk_configuration_version_id")
        if "fk_position_risk_version" in position_fks:
            batch.drop_constraint("fk_position_risk_version", type_="foreignkey")
        for column in ("maximum_loss_rule", "position_sizing_rule", "entry_target_rule", "entry_stop_rule", "risk_configuration_version_id"):
            if column in position_columns:
                batch.drop_column(column)

    live_order_columns = _columns("live_order_intents")
    live_order_fks = _fk_names("live_order_intents")
    live_order_indexes = _index_names("live_order_intents")
    with op.batch_alter_table("live_order_intents") as batch:
        if "ix_live_order_intents_risk_configuration_version_id" in live_order_indexes:
            batch.drop_index("ix_live_order_intents_risk_configuration_version_id")
        if "fk_live_order_intent_risk_version" in live_order_fks:
            batch.drop_constraint("fk_live_order_intent_risk_version", type_="foreignkey")
        if "risk_configuration_version_id" in live_order_columns:
            batch.drop_column("risk_configuration_version_id")

    if "fk_user_risk_configuration_active_version" in _fk_names("user_risk_configurations"):
        with op.batch_alter_table("user_risk_configurations") as batch:
            batch.drop_constraint("fk_user_risk_configuration_active_version", type_="foreignkey")

    version_indexes = _index_names("user_risk_configuration_versions")
    if "ix_user_risk_versions_configuration_created" in version_indexes:
        op.drop_index(
            "ix_user_risk_versions_configuration_created",
            table_name="user_risk_configuration_versions",
        )
    if "ix_user_risk_configuration_versions_risk_configuration_id" in version_indexes:
        op.drop_index(
            "ix_user_risk_configuration_versions_risk_configuration_id",
            table_name="user_risk_configuration_versions",
        )
    if "user_risk_configuration_versions" in _tables():
        op.drop_table("user_risk_configuration_versions")

    if "ix_user_risk_configurations_user_id" in _index_names("user_risk_configurations"):
        op.drop_index(
            "ix_user_risk_configurations_user_id",
            table_name="user_risk_configurations",
        )
    if "user_risk_configurations" in _tables():
        op.drop_table("user_risk_configurations")
