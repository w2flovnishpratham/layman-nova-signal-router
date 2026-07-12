"""Strategy instances and per-instance private webhook credentials.

Revision ID: 0007_strategy_instances
Revises: 0006_strategy_registry
Create Date: 2026-07-11 00:00:00 UTC

Purely additive. Backfills one NOVA_SHARED strategy instance per existing
strategy subscription whose strategy resolves in the catalog (idempotent via
the unique legacy_subscription_id link).

Rollback: downgrade drops only the two new tables.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

from app.db import models


revision = "0007_strategy_instances"
down_revision = "0006_strategy_registry"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("strategy_instances"):
        op.create_table(
            "strategy_instances",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("strategy_id", models.GUID(), sa.ForeignKey("strategy_catalog.id"), nullable=False),
            sa.Column("strategy_version_id", models.GUID(), sa.ForeignKey("strategy_versions.id"), nullable=False),
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
        op.create_index("ix_strategy_instances_user_id", "strategy_instances", ["user_id"])
        op.create_index("ix_strategy_instances_strategy_id", "strategy_instances", ["strategy_id"])
        op.create_index("ix_strategy_instances_strategy_version_id", "strategy_instances", ["strategy_version_id"])
        op.create_index("ix_strategy_instances_user_status", "strategy_instances", ["user_id", "status"])
        op.create_index("ix_strategy_instances_strategy_status", "strategy_instances", ["strategy_id", "status"])

    if not _has_table("strategy_instance_webhook_credentials"):
        op.create_table(
            "strategy_instance_webhook_credentials",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column(
                "strategy_instance_id",
                models.GUID(),
                sa.ForeignKey("strategy_instances.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("token_prefix", sa.String(length=12), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_reason", sa.String(length=120), nullable=True),
            sa.Column("replaced_by_id", models.GUID(), nullable=True),
        )
        op.create_index(
            "ix_instance_webhook_credentials_instance",
            "strategy_instance_webhook_credentials",
            ["strategy_instance_id"],
        )
        op.create_index(
            "ix_instance_webhook_credentials_user",
            "strategy_instance_webhook_credentials",
            ["user_id"],
        )
        op.create_index(
            "uq_instance_webhook_credential_active",
            "strategy_instance_webhook_credentials",
            ["strategy_instance_id"],
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
            sqlite_where=sa.text("revoked_at IS NULL"),
        )

    _backfill_instances()


_subscriptions = sa.table(
    "strategy_subscriptions",
    sa.column("id", models.GUID()),
    sa.column("user_id", models.GUID()),
    sa.column("strategy_name", sa.String()),
    sa.column("active", sa.Boolean()),
    sa.column("lots", sa.Integer()),
    sa.column("execution_mode", sa.String()),
)

_catalog = sa.table(
    "strategy_catalog",
    sa.column("id", models.GUID()),
    sa.column("code", sa.String()),
    sa.column("display_name", sa.String()),
    sa.column("owner_user_id", models.GUID()),
)

_versions = sa.table(
    "strategy_versions",
    sa.column("id", models.GUID()),
    sa.column("strategy_id", models.GUID()),
    sa.column("status", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)

_instances = sa.table(
    "strategy_instances",
    sa.column("id", models.GUID()),
    sa.column("user_id", models.GUID()),
    sa.column("strategy_id", models.GUID()),
    sa.column("strategy_version_id", models.GUID()),
    sa.column("source_journey", sa.String()),
    sa.column("label", sa.String()),
    sa.column("status", sa.String()),
    sa.column("execution_mode", sa.String()),
    sa.column("current_lots", sa.Integer()),
    sa.column("legacy_subscription_id", models.GUID()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _backfill_instances() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    already_linked = {
        row[0]
        for row in bind.execute(
            sa.select(_instances.c.legacy_subscription_id).where(
                _instances.c.legacy_subscription_id.is_not(None)
            )
        )
    }
    for sub in bind.execute(sa.select(_subscriptions)).fetchall():
        if sub.id in already_linked:
            continue
        strategy = bind.execute(
            sa.select(_catalog.c.id, _catalog.c.display_name).where(
                _catalog.c.code == sub.strategy_name, _catalog.c.owner_user_id.is_(None)
            )
        ).first()
        if strategy is None:
            continue
        version_id = bind.execute(
            sa.select(_versions.c.id)
            .where(_versions.c.strategy_id == strategy.id, _versions.c.status == "approved")
            .order_by(_versions.c.created_at.desc())
        ).scalar()
        if version_id is None:
            continue
        bind.execute(
            _instances.insert().values(
                id=uuid.uuid4(),
                user_id=sub.user_id,
                strategy_id=strategy.id,
                strategy_version_id=version_id,
                source_journey="NOVA_SHARED",
                label=strategy.display_name,
                status="active" if sub.active else "paused",
                execution_mode=sub.execution_mode,
                current_lots=sub.lots,
                legacy_subscription_id=sub.id,
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    for table in ("strategy_instance_webhook_credentials", "strategy_instances"):
        if _has_table(table):
            op.drop_table(table)
