"""Structural ownership consistency for position shadow tables.

Revision ID: 0010_position_ownership
Revises: 0009_position_shadow
Create Date: 2026-07-12 00:00:00 UTC

Adds (0009 keeps its originally reviewed contents):
  * strategy_instances: UNIQUE (id, user_id) — composite FK target
  * strategy_instance_positions: UNIQUE (id, user_id) and composite FK
    (strategy_instance_id, user_id) -> strategy_instances (id, user_id);
    NULL strategy_instance_id (legacy rows) passes via MATCH SIMPLE
  * position_events: composite FK (position_id, user_id) ->
    strategy_instance_positions (id, user_id); drops the redundant
    strategy_instance_id column (instance ownership derives via the position)

Downgrade restores the exact 0009 schema: re-adds position_events.
strategy_instance_id (nullable; original 0009 rows never populated it with
authority), drops the composite FKs and the two unique constraints.

Inspector-guarded: fresh databases get all of this from 0001 create_all()
with current models. On SQLite (test-only) alters run through batch
copy_from definitions; on PostgreSQL they are plain ALTERs.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db import models
from app.db.models import ACTIVE_POSITION_STATES


revision = "0010_position_ownership"
down_revision = "0009_position_shadow"
branch_labels = None
depends_on = None

POSITIONS = "strategy_instance_positions"
EVENTS = "position_events"
_ACTIVE_SQL = "position_state IN ({})".format(", ".join(f"'{s}'" for s in ACTIVE_POSITION_STATES))


def _inspector():
    return sa.inspect(op.get_bind())


def _positions_table(*, with_ownership: bool) -> sa.Table:
    metadata = sa.MetaData()
    constraints = []
    if with_ownership:
        constraints = [
            sa.UniqueConstraint("id", "user_id", name="uq_positions_id_user"),
            sa.ForeignKeyConstraint(
                ["strategy_instance_id", "user_id"],
                ["strategy_instances.id", "strategy_instances.user_id"],
                name="fk_position_instance_owner",
            ),
        ]
    table = sa.Table(
        POSITIONS,
        metadata,
        sa.Column("id", models.GUID(), primary_key=True, nullable=False),
        sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_instance_id", models.GUID(), sa.ForeignKey("strategy_instances.id", ondelete="SET NULL"), nullable=True),
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
        *constraints,
    )
    sa.Index("ix_strategy_instance_positions_user_id", table.c.user_id)
    sa.Index("ix_positions_user_state", table.c.user_id, table.c.position_state)
    sa.Index("ix_positions_instance", table.c.strategy_instance_id)
    sa.Index(
        "uq_active_position_per_user_mode",
        table.c.user_id,
        table.c.execution_mode,
        unique=True,
        postgresql_where=sa.text(_ACTIVE_SQL),
        sqlite_where=sa.text(_ACTIVE_SQL),
    )
    return table


def _events_table(*, with_instance_column: bool, with_owner_fk: bool) -> sa.Table:
    metadata = sa.MetaData()
    columns = [
        sa.Column("id", models.GUID(), primary_key=True, nullable=False),
        sa.Column("position_id", models.GUID(), sa.ForeignKey(f"{POSITIONS}.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    ]
    if with_instance_column:
        columns.append(sa.Column("strategy_instance_id", models.GUID(), nullable=True))
    columns += [
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
    ]
    constraints = []
    if with_owner_fk:
        constraints.append(
            sa.ForeignKeyConstraint(
                ["position_id", "user_id"],
                [f"{POSITIONS}.id", f"{POSITIONS}.user_id"],
                name="fk_event_position_owner",
            )
        )
    table = sa.Table(EVENTS, metadata, *columns, *constraints)
    sa.Index("ix_position_events_position_id", table.c.position_id)
    sa.Index("ix_position_events_user_id", table.c.user_id)
    sa.Index("ix_position_events_event_type", table.c.event_type)
    sa.Index("ix_position_events_position_at", table.c.position_id, table.c.event_at)
    sa.Index("ix_position_events_user_at", table.c.user_id, table.c.event_at)
    return table


def _instances_table(*, with_id_user_unique: bool) -> sa.Table:
    """Hardened (post-0008) strategy_instances shape for SQLite copy_from —
    reflection-free so the composite version FK survives recreates."""
    metadata = sa.MetaData()
    constraints = [
        sa.UniqueConstraint("user_id", "strategy_id", "label", name="uq_strategy_instance_user_label"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id", "strategy_id"],
            ["strategy_versions.id", "strategy_versions.strategy_id"],
            name="fk_strategy_instance_version_strategy",
        ),
    ]
    if with_id_user_unique:
        constraints.append(sa.UniqueConstraint("id", "user_id", name="uq_strategy_instances_id_user"))
    table = sa.Table(
        "strategy_instances",
        metadata,
        sa.Column("id", models.GUID(), primary_key=True, nullable=False),
        sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_id", models.GUID(), sa.ForeignKey("strategy_catalog.id"), nullable=False),
        sa.Column("strategy_version_id", models.GUID(), nullable=False),
        sa.Column("source_journey", sa.String(length=30), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ready"),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("execution_mode", sa.String(length=30), nullable=False, server_default="signal_only"),
        sa.Column("current_lots", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("risk_config", models.JSONType(), nullable=True),
        sa.Column(
            "legacy_subscription_id",
            models.GUID(),
            sa.ForeignKey("strategy_subscriptions.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *constraints,
    )
    sa.Index("ix_strategy_instances_user_id", table.c.user_id)
    sa.Index("ix_strategy_instances_strategy_id", table.c.strategy_id)
    sa.Index("ix_strategy_instances_strategy_version_id", table.c.strategy_version_id)
    sa.Index("ix_strategy_instances_user_status", table.c.user_id, table.c.status)
    sa.Index("ix_strategy_instances_strategy_status", table.c.strategy_id, table.c.status)
    return table


def _uniques(table: str) -> set[tuple[str, ...]]:
    inspector = _inspector()
    return {
        tuple(sorted(c["column_names"])) for c in inspector.get_unique_constraints(table)
    } | {
        tuple(sorted(i["column_names"])) for i in inspector.get_indexes(table) if i.get("unique")
    }


def _has_fk(table: str, columns: list[str]) -> bool:
    return any(sorted(fk["constrained_columns"]) == sorted(columns) for fk in _inspector().get_foreign_keys(table))


def upgrade() -> None:
    tables = set(_inspector().get_table_names())

    if "strategy_instances" in tables and ("id", "user_id") not in _uniques("strategy_instances"):
        with op.batch_alter_table(
            "strategy_instances", copy_from=_instances_table(with_id_user_unique=False)
        ) as batch:
            batch.create_unique_constraint("uq_strategy_instances_id_user", ["id", "user_id"])

    if POSITIONS in tables:
        if ("id", "user_id") not in _uniques(POSITIONS):
            with op.batch_alter_table(POSITIONS, copy_from=_positions_table(with_ownership=False)) as batch:
                batch.create_unique_constraint("uq_positions_id_user", ["id", "user_id"])
                batch.create_foreign_key(
                    "fk_position_instance_owner",
                    "strategy_instances",
                    ["strategy_instance_id", "user_id"],
                    ["id", "user_id"],
                )
        elif not _has_fk(POSITIONS, ["strategy_instance_id", "user_id"]):
            with op.batch_alter_table(POSITIONS, copy_from=_positions_table(with_ownership=False)) as batch:
                batch.create_foreign_key(
                    "fk_position_instance_owner",
                    "strategy_instances",
                    ["strategy_instance_id", "user_id"],
                    ["id", "user_id"],
                )

    if EVENTS in tables:
        columns = {c["name"] for c in _inspector().get_columns(EVENTS)}
        needs_drop = "strategy_instance_id" in columns
        needs_fk = not _has_fk(EVENTS, ["position_id", "user_id"])
        if needs_drop or needs_fk:
            with op.batch_alter_table(
                EVENTS, copy_from=_events_table(with_instance_column=needs_drop, with_owner_fk=False)
            ) as batch:
                if needs_drop:
                    batch.drop_column("strategy_instance_id")
                if needs_fk:
                    batch.create_foreign_key(
                        "fk_event_position_owner",
                        POSITIONS,
                        ["position_id", "user_id"],
                        ["id", "user_id"],
                    )


def downgrade() -> None:
    """Restore the exact 0009 schema. All rows and event history preserved."""
    dialect = op.get_bind().dialect.name
    tables = set(_inspector().get_table_names())

    if EVENTS in tables:
        columns = {c["name"] for c in _inspector().get_columns(EVENTS)}
        has_owner_fk = _has_fk(EVENTS, ["position_id", "user_id"])
        if "strategy_instance_id" not in columns or has_owner_fk:
            if dialect == "postgresql":
                if has_owner_fk:
                    op.drop_constraint("fk_event_position_owner", EVENTS, type_="foreignkey")
                if "strategy_instance_id" not in columns:
                    op.add_column(EVENTS, sa.Column("strategy_instance_id", models.GUID(), nullable=True))
            else:
                with op.batch_alter_table(
                    EVENTS,
                    copy_from=_events_table(with_instance_column=False, with_owner_fk=True),
                    recreate="always",
                ) as batch:
                    batch.drop_constraint("fk_event_position_owner", type_="foreignkey")
                    batch.add_column(sa.Column("strategy_instance_id", models.GUID(), nullable=True))

    if POSITIONS in tables:
        if _has_fk(POSITIONS, ["strategy_instance_id", "user_id"]) or ("id", "user_id") in _uniques(POSITIONS):
            if dialect == "postgresql":
                op.drop_constraint("fk_position_instance_owner", POSITIONS, type_="foreignkey")
                op.drop_constraint("uq_positions_id_user", POSITIONS, type_="unique")
            else:
                with op.batch_alter_table(
                    POSITIONS,
                    copy_from=_positions_table(with_ownership=True),
                    recreate="always",
                ) as batch:
                    batch.drop_constraint("fk_position_instance_owner", type_="foreignkey")
                    batch.drop_constraint("uq_positions_id_user", type_="unique")

    if "strategy_instances" in tables and ("id", "user_id") in _uniques("strategy_instances"):
        with op.batch_alter_table(
            "strategy_instances", copy_from=_instances_table(with_id_user_unique=True)
        ) as batch:
            batch.drop_constraint("uq_strategy_instances_id_user", type_="unique")
