"""Durable position shadow tables (Phase 2A).

Revision ID: 0009_position_shadow
Revises: 0008_structural_consistency
Create Date: 2026-07-12 00:00:00 UTC

Additive: strategy_instance_positions + position_events. During Phase 2A the
JSON files remain the execution read authority; these tables receive shadow
dual-writes only. The partial unique index enforces at most one active
position row per (user, execution_mode) slot — a violation can only fail a
shadow write (visible + audited), never an execution path.

Downgrade drops only the two shadow tables. Shadow rows are rebuildable from
the authoritative JSON via scripts/import_positions_to_shadow.py, so dropping
them loses no execution authority.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db import models
from app.db.models import ACTIVE_POSITION_STATES


revision = "0009_position_shadow"
down_revision = "0008_structural_consistency"
branch_labels = None
depends_on = None

_ACTIVE_SQL = "position_state IN ({})".format(", ".join(f"'{s}'" for s in ACTIVE_POSITION_STATES))


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("strategy_instance_positions"):
        op.create_table(
            "strategy_instance_positions",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column(
                "strategy_instance_id",
                models.GUID(),
                sa.ForeignKey("strategy_instances.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("execution_mode", sa.String(length=20), nullable=False),
            sa.Column("position_state", sa.String(length=30), nullable=False),
            sa.Column("position_side", sa.String(length=10), nullable=False, server_default="LONG"),
            sa.Column("strategy_code", sa.String(length=120), nullable=True),
            sa.Column("security_id", sa.String(length=60), nullable=True),
            sa.Column("trading_symbol", sa.String(length=120), nullable=True),
            sa.Column("underlying", sa.String(length=20), nullable=False, server_default="NIFTY"),
            sa.Column("option_side", sa.String(length=8), nullable=True),
            sa.Column("strike", sa.String(length=20), nullable=True),
            sa.Column("expiry", sa.String(length=20), nullable=True),
            sa.Column("product_type", sa.String(length=30), nullable=True),
            sa.Column("entry_quantity", sa.Integer(), nullable=True),
            sa.Column("filled_entry_quantity", sa.Integer(), nullable=True),
            sa.Column("open_quantity", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("filled_exit_quantity", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_lots", sa.Integer(), nullable=True),
            sa.Column("avg_entry_price_paise", sa.BigInteger(), nullable=True),
            sa.Column("realized_pnl_paise", sa.BigInteger(), nullable=True),
            sa.Column("entry_order_id", sa.String(length=120), nullable=True),
            sa.Column("exit_order_id", sa.String(length=120), nullable=True),
            sa.Column("broker_correlation_id", sa.String(length=120), nullable=True),
            sa.Column("pending_order_metadata", models.JSONType(), nullable=True),
            sa.Column("reversal_metadata", models.JSONType(), nullable=True),
            sa.Column("super_order_metadata", models.JSONType(), nullable=True),
            sa.Column("reconciliation_status", sa.String(length=40), nullable=True),
            sa.Column("last_broker_reconciled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_reason", sa.Text(), nullable=True),
            sa.Column("raw_snapshot", models.JSONType(), nullable=True),
            sa.Column("snapshot_sha256", sa.String(length=64), nullable=True),
            sa.Column("parity_status", sa.String(length=200), nullable=True),
            sa.Column("last_parity_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("imported_from_json", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )
        op.create_index("ix_strategy_instance_positions_user_id", "strategy_instance_positions", ["user_id"])
        op.create_index("ix_positions_user_state", "strategy_instance_positions", ["user_id", "position_state"])
        op.create_index("ix_positions_instance", "strategy_instance_positions", ["strategy_instance_id"])
        op.create_index(
            "uq_active_position_per_user_mode",
            "strategy_instance_positions",
            ["user_id", "execution_mode"],
            unique=True,
            postgresql_where=sa.text(_ACTIVE_SQL),
            sqlite_where=sa.text(_ACTIVE_SQL),
        )

    if not _has_table("position_events"):
        op.create_table(
            "position_events",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column(
                "position_id",
                models.GUID(),
                sa.ForeignKey("strategy_instance_positions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("strategy_instance_id", models.GUID(), nullable=True),
            sa.Column("event_type", sa.String(length=40), nullable=False),
            sa.Column("source", sa.String(length=30), nullable=False),
            sa.Column("signal_id", sa.String(length=255), nullable=True),
            sa.Column("execution_job_id", models.GUID(), nullable=True),
            sa.Column("order_intent_id", models.GUID(), nullable=True),
            sa.Column("broker_order_id", sa.String(length=120), nullable=True),
            sa.Column("quantity", sa.Integer(), nullable=True),
            sa.Column("price_paise", sa.BigInteger(), nullable=True),
            sa.Column("previous_state", sa.String(length=30), nullable=True),
            sa.Column("new_state", sa.String(length=30), nullable=True),
            sa.Column("metadata", models.JSONType(), nullable=True),
            sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=True, unique=True),
        )
        op.create_index("ix_position_events_position_id", "position_events", ["position_id"])
        op.create_index("ix_position_events_user_id", "position_events", ["user_id"])
        op.create_index("ix_position_events_event_type", "position_events", ["event_type"])
        op.create_index("ix_position_events_position_at", "position_events", ["position_id", "event_at"])
        op.create_index("ix_position_events_user_at", "position_events", ["user_id", "event_at"])


def downgrade() -> None:
    for table in ("position_events", "strategy_instance_positions"):
        if _has_table(table):
            op.drop_table(table)
