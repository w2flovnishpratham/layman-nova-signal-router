"""Multi-strategy data model foundation.

Revision ID: 0006_multistrategy_data_model
Revises: 0005_user_setup_profiles
Create Date: 2026-07-03 00:00:00 UTC
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db import models


revision = "0006_multistrategy_data_model"
down_revision = "0005_user_setup_profiles"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _columns(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {column["name"] for column in _inspector().get_columns(table_name)}


def _has_column(table_name: str, column_name: str) -> bool:
    return column_name in _columns(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def _create_index_if_missing(
    name: str,
    table_name: str,
    columns: list[str],
    *,
    unique: bool = False,
    **kwargs,
) -> None:
    if _has_table(table_name) and not _has_index(table_name, name):
        op.create_index(name, table_name, columns, unique=unique, **kwargs)


def _drop_index_if_exists(name: str, table_name: str) -> None:
    if _has_index(table_name, name):
        op.drop_index(name, table_name=table_name)


def _drop_columns(table_name: str, column_names: tuple[str, ...]) -> None:
    existing = [column for column in column_names if _has_column(table_name, column)]
    if not existing:
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table_name, recreate="always") as batch_op:
            for column in existing:
                batch_op.drop_column(column)
        return
    for column in existing:
        op.drop_column(table_name, column)


def _add_columns(table_name: str, additions: dict[str, sa.Column]) -> None:
    missing = [(name, column) for name, column in additions.items() if not _has_column(table_name, name)]
    if not missing:
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table_name, recreate="always") as batch_op:
            for _, column in missing:
                batch_op.add_column(column)
        return
    for _, column in missing:
        op.add_column(table_name, column)


def _active_real_order_conflicts() -> bool:
    if not _has_table("user_strategy_instances"):
        return False
    result = op.get_bind().execute(
        sa.text(
            """
            SELECT user_id
            FROM user_strategy_instances
            WHERE execution_mode = 'real_orders' AND status = 'active'
            GROUP BY user_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    return result is not None


def _create_active_real_orders_index_if_safe() -> None:
    name = "uq_user_strategy_instances_active_real_orders_per_user"
    if _has_index("user_strategy_instances", name) or _active_real_order_conflicts():
        return
    op.create_index(
        name,
        "user_strategy_instances",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("execution_mode = 'real_orders' AND status = 'active'"),
        sqlite_where=sa.text("execution_mode = 'real_orders' AND status = 'active'"),
    )


def _create_strategy_catalog() -> None:
    if not _has_table("strategy_catalog"):
        op.create_table(
            "strategy_catalog",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column("code", sa.String(length=80), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("source_type", sa.String(length=50), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("metadata", models.JSONType(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("code", name="uq_strategy_catalog_code"),
        )
    _create_index_if_missing("ix_strategy_catalog_status", "strategy_catalog", ["status"])
    _create_index_if_missing("ix_strategy_catalog_source_type", "strategy_catalog", ["source_type"])


def _create_strategy_versions() -> None:
    if not _has_table("strategy_versions"):
        op.create_table(
            "strategy_versions",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column(
                "strategy_id",
                models.GUID(),
                sa.ForeignKey("strategy_catalog.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("payload_version", sa.String(length=40), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("config_schema", models.JSONType(), nullable=True),
            sa.Column("metadata", models.JSONType(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),
        )
    _create_index_if_missing("ix_strategy_versions_strategy_id", "strategy_versions", ["strategy_id"])
    _create_index_if_missing("ix_strategy_versions_strategy_status", "strategy_versions", ["strategy_id", "status"])


def _create_strategy_backtest_reports() -> None:
    if not _has_table("strategy_backtest_reports"):
        op.create_table(
            "strategy_backtest_reports",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column(
                "strategy_version_id",
                models.GUID(),
                sa.ForeignKey("strategy_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("report_uri", sa.Text(), nullable=True),
            sa.Column("metrics", models.JSONType(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    _create_index_if_missing(
        "ix_strategy_backtest_reports_strategy_version_id",
        "strategy_backtest_reports",
        ["strategy_version_id"],
    )
    _create_index_if_missing(
        "ix_strategy_backtest_reports_version_created",
        "strategy_backtest_reports",
        ["strategy_version_id", "created_at"],
    )


def _create_user_strategy_instances() -> None:
    if not _has_table("user_strategy_instances"):
        op.create_table(
            "user_strategy_instances",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column(
                "strategy_id",
                models.GUID(),
                sa.ForeignKey("strategy_catalog.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "strategy_version_id",
                models.GUID(),
                sa.ForeignKey("strategy_versions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "legacy_subscription_id",
                models.GUID(),
                sa.ForeignKey("strategy_subscriptions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("instance_label", sa.String(length=120), nullable=True),
            sa.Column("source_type", sa.String(length=50), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("execution_mode", sa.String(length=30), nullable=False),
            sa.Column("lots", sa.Integer(), nullable=False),
            sa.Column("side_preference", sa.String(length=8), nullable=True),
            sa.Column("config", models.JSONType(), nullable=True),
            sa.Column("risk_config", models.JSONType(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("legacy_subscription_id", name="uq_user_strategy_instances_legacy_subscription"),
        )
    _create_index_if_missing("ix_user_strategy_instances_user_id", "user_strategy_instances", ["user_id"])
    _create_index_if_missing("ix_user_strategy_instances_strategy_id", "user_strategy_instances", ["strategy_id"])
    _create_index_if_missing(
        "ix_user_strategy_instances_strategy_version_id",
        "user_strategy_instances",
        ["strategy_version_id"],
    )
    _create_index_if_missing("ix_user_strategy_instances_user_status", "user_strategy_instances", ["user_id", "status"])
    _create_index_if_missing(
        "ix_user_strategy_instances_strategy_status",
        "user_strategy_instances",
        ["strategy_id", "status"],
    )
    _create_active_real_orders_index_if_safe()


def _create_user_strategy_webhook_credentials() -> None:
    if not _has_table("user_strategy_webhook_credentials"):
        op.create_table(
            "user_strategy_webhook_credentials",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column(
                "instance_id",
                models.GUID(),
                sa.ForeignKey("user_strategy_instances.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("webhook_key_hash", sa.String(length=128), nullable=True),
            sa.Column("message_secret_hash", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("webhook_key_hash", name="uq_user_strategy_webhook_key_hash"),
        )
    _create_index_if_missing(
        "ix_user_strategy_webhook_credentials_instance",
        "user_strategy_webhook_credentials",
        ["instance_id"],
    )
    _create_index_if_missing(
        "ix_user_strategy_webhook_credentials_active",
        "user_strategy_webhook_credentials",
        ["instance_id", "is_active"],
    )


def _extend_strategy_signals() -> None:
    additions = {
        "instance_id": sa.Column(
            "instance_id",
            models.GUID(),
            sa.ForeignKey(
                "user_strategy_instances.id",
                ondelete="SET NULL",
                name="fk_strategy_signals_instance_id",
            ),
            nullable=True,
        ),
        "strategy_version_id": sa.Column(
            "strategy_version_id",
            models.GUID(),
            sa.ForeignKey(
                "strategy_versions.id",
                ondelete="SET NULL",
                name="fk_strategy_signals_strategy_version_id",
            ),
            nullable=True,
        ),
        "payload_version": sa.Column("payload_version", sa.String(length=40), nullable=True),
        "source_type": sa.Column("source_type", sa.String(length=50), nullable=True),
        "received_ip": sa.Column("received_ip", sa.String(length=64), nullable=True),
        "dedupe_key": sa.Column("dedupe_key", sa.String(length=255), nullable=True),
    }
    _add_columns("strategy_signals", additions)
    _create_index_if_missing("ix_strategy_signals_instance_id", "strategy_signals", ["instance_id"])
    _create_index_if_missing("ix_strategy_signals_strategy_version_id", "strategy_signals", ["strategy_version_id"])
    _create_index_if_missing("ix_strategy_signals_dedupe_key", "strategy_signals", ["dedupe_key"])


def _create_normalized_option_signals() -> None:
    if not _has_table("normalized_option_signals"):
        op.create_table(
            "normalized_option_signals",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column(
                "strategy_signal_id",
                models.GUID(),
                sa.ForeignKey("strategy_signals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "instance_id",
                models.GUID(),
                sa.ForeignKey("user_strategy_instances.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("action", sa.String(length=20), nullable=False),
            sa.Column("intent", sa.String(length=20), nullable=False),
            sa.Column("symbol", sa.String(length=40), nullable=False),
            sa.Column("instrument_type", sa.String(length=40), nullable=False),
            sa.Column("option_side", sa.String(length=8), nullable=False),
            sa.Column("strike_mode", sa.String(length=20), nullable=False),
            sa.Column("resolved_strike", sa.Float(), nullable=True),
            sa.Column("expiry_mode", sa.String(length=20), nullable=False),
            sa.Column("resolved_expiry", sa.Date(), nullable=True),
            sa.Column("qty_mode", sa.String(length=20), nullable=False),
            sa.Column("lots", sa.Integer(), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=True),
            sa.Column("order_type", sa.String(length=20), nullable=False),
            sa.Column("product_type", sa.String(length=30), nullable=False),
            sa.Column("raw_mapping_details", models.JSONType(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    _create_index_if_missing(
        "ix_normalized_option_signals_strategy_signal",
        "normalized_option_signals",
        ["strategy_signal_id"],
    )
    _create_index_if_missing(
        "ix_normalized_option_signals_instance_created",
        "normalized_option_signals",
        ["instance_id", "created_at"],
    )


def _extend_strategy_execution_jobs() -> None:
    additions = {
        "instance_id": sa.Column(
            "instance_id",
            models.GUID(),
            sa.ForeignKey(
                "user_strategy_instances.id",
                ondelete="SET NULL",
                name="fk_strategy_execution_jobs_instance_id",
            ),
            nullable=True,
        ),
        "strategy_version_id": sa.Column(
            "strategy_version_id",
            models.GUID(),
            sa.ForeignKey(
                "strategy_versions.id",
                ondelete="SET NULL",
                name="fk_strategy_execution_jobs_strategy_version_id",
            ),
            nullable=True,
        ),
        "normalized_signal_id": sa.Column(
            "normalized_signal_id",
            models.GUID(),
            sa.ForeignKey(
                "normalized_option_signals.id",
                ondelete="SET NULL",
                name="fk_strategy_execution_jobs_normalized_signal_id",
            ),
            nullable=True,
        ),
        "locked_by": sa.Column("locked_by", sa.String(length=120), nullable=True),
        "dead_letter_reason": sa.Column("dead_letter_reason", sa.Text(), nullable=True),
    }
    _add_columns("strategy_execution_jobs", additions)
    _create_index_if_missing("ix_strategy_execution_jobs_instance_id", "strategy_execution_jobs", ["instance_id"])
    _create_index_if_missing(
        "ix_strategy_execution_jobs_strategy_version_id",
        "strategy_execution_jobs",
        ["strategy_version_id"],
    )
    _create_index_if_missing(
        "ix_strategy_execution_jobs_normalized_signal_id",
        "strategy_execution_jobs",
        ["normalized_signal_id"],
    )


def upgrade() -> None:
    _create_strategy_catalog()
    _create_strategy_versions()
    _create_strategy_backtest_reports()
    _create_user_strategy_instances()
    _create_user_strategy_webhook_credentials()
    _extend_strategy_signals()
    _create_normalized_option_signals()
    _extend_strategy_execution_jobs()


def downgrade() -> None:
    for name in (
        "ix_strategy_execution_jobs_normalized_signal_id",
        "ix_strategy_execution_jobs_strategy_version_id",
        "ix_strategy_execution_jobs_instance_id",
    ):
        _drop_index_if_exists(name, "strategy_execution_jobs")
    _drop_columns(
        "strategy_execution_jobs",
        ("dead_letter_reason", "locked_by", "normalized_signal_id", "strategy_version_id", "instance_id"),
    )

    if _has_table("normalized_option_signals"):
        op.drop_table("normalized_option_signals")

    for name in (
        "ix_strategy_signals_dedupe_key",
        "ix_strategy_signals_strategy_version_id",
        "ix_strategy_signals_instance_id",
    ):
        _drop_index_if_exists(name, "strategy_signals")
    _drop_columns(
        "strategy_signals",
        ("dedupe_key", "received_ip", "source_type", "payload_version", "strategy_version_id", "instance_id"),
    )

    if _has_table("user_strategy_webhook_credentials"):
        op.drop_table("user_strategy_webhook_credentials")
    if _has_table("user_strategy_instances"):
        op.drop_table("user_strategy_instances")
    if _has_table("strategy_backtest_reports"):
        op.drop_table("strategy_backtest_reports")
    if _has_table("strategy_versions"):
        op.drop_table("strategy_versions")
    if _has_table("strategy_catalog"):
        op.drop_table("strategy_catalog")
