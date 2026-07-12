"""Structural-consistency hardening (Phase 1 hardening pass).

Revision ID: 0008_structural_consistency
Revises: 0007_strategy_instances
Create Date: 2026-07-11 00:00:00 UTC

All hardening schema changes live here; 0006/0007 keep their originally
reviewed contents.

Upgrade (0007 schema -> hardened schema):
  * strategy_versions: add unique (id, strategy_id) — composite FK target
  * strategy_instances: drop the single-column strategy_version_id FK and add
    the composite FK (strategy_version_id, strategy_id) ->
    strategy_versions (id, strategy_id), so a version must belong to the
    chosen strategy
  * strategy_instance_webhook_credentials: drop the redundant user_id column
    and its index (ownership derives through the instance)

Downgrade restores the exact 0007 schema: re-adds user_id (backfilled from
strategy_instances.user_id, then NOT NULL + FK + index), restores the
single-column version FK, and drops the composite FK and the supporting
unique constraint. Data, credentials and token history are preserved in both
directions.

Inspector guards exist because 0001_baseline runs create_all() with the
CURRENT models, so fresh databases already carry the hardened shape when this
revision runs — every operation is applied exactly once regardless of path.
On SQLite (test databases only) table changes go through batch_alter_table
with explicit copy_from definitions: reflection-free recreates that keep the
partial unique index and all rows intact. On PostgreSQL batch mode degrades
to plain ALTERs.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db import models


revision = "0008_structural_consistency"
down_revision = "0007_strategy_instances"
branch_labels = None
depends_on = None


CREDENTIALS = "strategy_instance_webhook_credentials"


def _inspector():
    return sa.inspect(op.get_bind())


def _credentials_table(*, with_user_id: bool, user_id_nullable: bool = False) -> sa.Table:
    """Explicit current-shape definition for SQLite batch copy_from."""
    metadata = sa.MetaData()
    columns = [
        sa.Column("id", models.GUID(), primary_key=True, nullable=False),
        sa.Column(
            "strategy_instance_id",
            models.GUID(),
            sa.ForeignKey("strategy_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
    ]
    if with_user_id:
        columns.append(sa.Column("user_id", models.GUID(), nullable=user_id_nullable))
    columns += [
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("token_prefix", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=120), nullable=True),
        sa.Column("replaced_by_id", models.GUID(), nullable=True),
    ]
    table = sa.Table(CREDENTIALS, metadata, *columns)
    sa.Index(
        "uq_instance_webhook_credential_active",
        table.c.strategy_instance_id,
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
        sqlite_where=sa.text("revoked_at IS NULL"),
    )
    sa.Index("ix_instance_webhook_credentials_instance", table.c.strategy_instance_id)
    return table


def _instances_table(*, with_single_version_fk: bool) -> sa.Table:
    """Explicit definition for SQLite batch copy_from. Omitting the single
    version FK from the definition is how the SQLite recreate drops it."""
    metadata = sa.MetaData()
    version_fk_args = (
        [sa.ForeignKey("strategy_versions.id")] if with_single_version_fk else []
    )
    table = sa.Table(
        "strategy_instances",
        metadata,
        sa.Column("id", models.GUID(), primary_key=True, nullable=False),
        sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_id", models.GUID(), sa.ForeignKey("strategy_catalog.id"), nullable=False),
        sa.Column("strategy_version_id", models.GUID(), *version_fk_args, nullable=False),
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
        sa.UniqueConstraint("user_id", "strategy_id", "label", name="uq_strategy_instance_user_label"),
    )
    sa.Index("ix_strategy_instances_user_id", table.c.user_id)
    sa.Index("ix_strategy_instances_strategy_id", table.c.strategy_id)
    sa.Index("ix_strategy_instances_strategy_version_id", table.c.strategy_version_id)
    sa.Index("ix_strategy_instances_user_status", table.c.user_id, table.c.status)
    sa.Index("ix_strategy_instances_strategy_status", table.c.strategy_id, table.c.status)
    return table


def _versions_table(*, with_id_strategy_unique: bool) -> sa.Table:
    metadata = sa.MetaData()
    constraints = [sa.UniqueConstraint("strategy_id", "version", name="uq_strategy_version")]
    if with_id_strategy_unique:
        constraints.append(sa.UniqueConstraint("id", "strategy_id", name="uq_strategy_versions_id_strategy"))
    table = sa.Table(
        "strategy_versions",
        metadata,
        sa.Column("id", models.GUID(), primary_key=True, nullable=False),
        sa.Column("strategy_id", models.GUID(), sa.ForeignKey("strategy_catalog.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("payload_spec_version", sa.String(length=20), nullable=False),
        sa.Column("source_journey", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("execution_kind", sa.String(length=20), nullable=False),
        sa.Column("runtime_definition", models.JSONType(), nullable=True),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by_user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *constraints,
    )
    sa.Index("ix_strategy_versions_strategy_id", table.c.strategy_id)
    sa.Index("ix_strategy_versions_strategy_status", table.c.strategy_id, table.c.status)
    return table


def _has_versions_unique(inspector) -> bool:
    unique_sets = {
        tuple(sorted(c["column_names"])) for c in inspector.get_unique_constraints("strategy_versions")
    } | {
        tuple(sorted(i["column_names"]))
        for i in inspector.get_indexes("strategy_versions")
        if i.get("unique")
    }
    return ("id", "strategy_id") in unique_sets


def _instances_composite_fk(inspector):
    return next(
        (
            fk
            for fk in inspector.get_foreign_keys("strategy_instances")
            if sorted(fk["constrained_columns"]) == ["strategy_id", "strategy_version_id"]
        ),
        None,
    )


def _instances_single_version_fk(inspector):
    return next(
        (
            fk
            for fk in inspector.get_foreign_keys("strategy_instances")
            if fk["constrained_columns"] == ["strategy_version_id"]
        ),
        None,
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = _inspector()
    dialect = bind.dialect.name
    tables = set(inspector.get_table_names())

    # 1. strategy_versions: unique (id, strategy_id) — the composite FK target.
    if "strategy_versions" in tables and not _has_versions_unique(inspector):
        with op.batch_alter_table(
            "strategy_versions", copy_from=_versions_table(with_id_strategy_unique=False)
        ) as batch:
            batch.create_unique_constraint("uq_strategy_versions_id_strategy", ["id", "strategy_id"])

    # 2. strategy_instances: single version FK -> composite version+strategy FK.
    if "strategy_instances" in tables:
        inspector = _inspector()
        if _instances_composite_fk(inspector) is None:
            single_fk = _instances_single_version_fk(inspector)
            if dialect == "postgresql" and single_fk is not None and single_fk.get("name"):
                op.drop_constraint(single_fk["name"], "strategy_instances", type_="foreignkey")
            # SQLite: the copy_from definition simply omits the single FK.
            with op.batch_alter_table(
                "strategy_instances", copy_from=_instances_table(with_single_version_fk=False)
            ) as batch:
                batch.create_foreign_key(
                    "fk_strategy_instance_version_strategy",
                    "strategy_versions",
                    ["strategy_version_id", "strategy_id"],
                    ["id", "strategy_id"],
                )

    # 3. credentials: drop redundant user_id (ownership derives via instance).
    if CREDENTIALS in tables:
        inspector = _inspector()
        columns = {c["name"] for c in inspector.get_columns(CREDENTIALS)}
        if "user_id" in columns:
            index_names = {i["name"] for i in inspector.get_indexes(CREDENTIALS)}
            if "ix_instance_webhook_credentials_user" in index_names:
                op.drop_index("ix_instance_webhook_credentials_user", table_name=CREDENTIALS)
            with op.batch_alter_table(
                CREDENTIALS, copy_from=_credentials_table(with_user_id=True)
            ) as batch:
                batch.drop_column("user_id")


def downgrade() -> None:
    """Restore the exact 0007 schema, preserving all rows and token history."""
    bind = op.get_bind()
    inspector = _inspector()
    dialect = bind.dialect.name
    tables = set(inspector.get_table_names())

    # 1-3. credentials: re-add user_id, backfill from the owning instance,
    # then NOT NULL + FK + index exactly as 0007 defined them.
    if CREDENTIALS in tables:
        columns = {c["name"] for c in inspector.get_columns(CREDENTIALS)}
        if "user_id" not in columns:
            op.add_column(CREDENTIALS, sa.Column("user_id", models.GUID(), nullable=True))
            op.execute(sa.text(
                f"UPDATE {CREDENTIALS} SET user_id = ("
                f"SELECT user_id FROM strategy_instances "
                f"WHERE strategy_instances.id = {CREDENTIALS}.strategy_instance_id)"
            ))
            remaining_nulls = bind.execute(
                sa.text(f"SELECT COUNT(*) FROM {CREDENTIALS} WHERE user_id IS NULL")
            ).scalar()
            if remaining_nulls:
                raise RuntimeError(
                    f"Cannot restore NOT NULL user_id: {remaining_nulls} credential rows "
                    "have no owning strategy instance. Resolve them before downgrading."
                )
            with op.batch_alter_table(
                CREDENTIALS,
                copy_from=_credentials_table(with_user_id=True, user_id_nullable=True),
            ) as batch:
                batch.alter_column("user_id", existing_type=models.GUID(), nullable=False)
                batch.create_foreign_key(
                    "fk_instance_webhook_credentials_user_id",
                    "users",
                    ["user_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
            op.create_index("ix_instance_webhook_credentials_user", CREDENTIALS, ["user_id"])

    # 4. strategy_instances: drop the composite FK, restore the single FK.
    if "strategy_instances" in tables:
        inspector = _inspector()
        composite = _instances_composite_fk(inspector)
        if composite is not None:
            if dialect == "postgresql":
                op.drop_constraint(
                    composite.get("name") or "fk_strategy_instance_version_strategy",
                    "strategy_instances",
                    type_="foreignkey",
                )
                if _instances_single_version_fk(_inspector()) is None:
                    op.create_foreign_key(
                        "strategy_instances_strategy_version_id_fkey",
                        "strategy_instances",
                        "strategy_versions",
                        ["strategy_version_id"],
                        ["id"],
                    )
            else:
                # SQLite recreate from a definition that carries only the
                # single version FK — dropping the composite by omission.
                # recreate="always": there are no batch ops, only the omission.
                with op.batch_alter_table(
                    "strategy_instances",
                    copy_from=_instances_table(with_single_version_fk=True),
                    recreate="always",
                ):
                    pass

    # 5. strategy_versions: drop the supporting unique constraint.
    inspector = _inspector()
    if "strategy_versions" in tables and _has_versions_unique(inspector):
        with op.batch_alter_table(
            "strategy_versions", copy_from=_versions_table(with_id_strategy_unique=True)
        ) as batch:
            batch.drop_constraint("uq_strategy_versions_id_strategy", type_="unique")
