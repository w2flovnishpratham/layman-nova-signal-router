"""Strategy registry foundation: catalog, versions, artifacts, validation, reviews.

Revision ID: 0006_strategy_registry
Revises: 0005_user_setup_profiles
Create Date: 2026-07-11 00:00:00 UTC

Purely additive. Also backfills the existing hardcoded "supertrend" strategy
as the first approved Nova-owned catalog entry (idempotent).

Rollback: downgrade drops only the five new tables; no existing table or
column is touched in either direction.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

from app.db import models


revision = "0006_strategy_registry"
down_revision = "0005_user_setup_profiles"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("strategy_catalog"):
        op.create_table(
            "strategy_catalog",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column("code", sa.String(length=80), nullable=False),
            sa.Column("display_name", sa.String(length=160), nullable=False),
            sa.Column("owner_type", sa.String(length=20), nullable=False, server_default="nova"),
            sa.Column(
                "owner_user_id",
                models.GUID(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("visibility", sa.String(length=20), nullable=False, server_default="private"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="coming_soon"),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("owner_user_id", "code", name="uq_strategy_catalog_owner_code"),
        )
        op.create_index("ix_strategy_catalog_code", "strategy_catalog", ["code"])
        op.create_index("ix_strategy_catalog_owner_user_id", "strategy_catalog", ["owner_user_id"])
        op.create_index("ix_strategy_catalog_owner_status", "strategy_catalog", ["owner_user_id", "status"])
        op.create_index(
            "uq_strategy_catalog_nova_code",
            "strategy_catalog",
            ["code"],
            unique=True,
            postgresql_where=sa.text("owner_user_id IS NULL"),
            sqlite_where=sa.text("owner_user_id IS NULL"),
        )

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
            sa.Column("version", sa.String(length=20), nullable=False),
            sa.Column("payload_spec_version", sa.String(length=20), nullable=False),
            sa.Column("source_journey", sa.String(length=30), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("execution_kind", sa.String(length=20), nullable=False),
            sa.Column("runtime_definition", models.JSONType(), nullable=True),
            sa.Column("changelog", sa.Text(), nullable=True),
            sa.Column(
                "created_by_user_id",
                models.GUID(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "approved_by_user_id",
                models.GUID(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),
        )
        op.create_index("ix_strategy_versions_strategy_id", "strategy_versions", ["strategy_id"])
        op.create_index("ix_strategy_versions_strategy_status", "strategy_versions", ["strategy_id", "status"])

    if not _has_table("strategy_source_artifacts"):
        op.create_table(
            "strategy_source_artifacts",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column(
                "strategy_version_id",
                models.GUID(),
                sa.ForeignKey("strategy_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("artifact_type", sa.String(length=30), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("content_sha256", sa.String(length=64), nullable=False),
            sa.Column(
                "submitted_by_user_id",
                models.GUID(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("conversion_method", sa.String(length=30), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_strategy_source_artifacts_strategy_version_id",
            "strategy_source_artifacts",
            ["strategy_version_id"],
        )
        op.create_index(
            "ix_strategy_source_artifacts_version_type",
            "strategy_source_artifacts",
            ["strategy_version_id", "artifact_type"],
        )

    if not _has_table("strategy_validation_reports"):
        op.create_table(
            "strategy_validation_reports",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column(
                "strategy_version_id",
                models.GUID(),
                sa.ForeignKey("strategy_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("stage", sa.String(length=30), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("findings", models.JSONType(), nullable=False),
            sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
        )
        op.create_index(
            "ix_strategy_validation_reports_strategy_version_id",
            "strategy_validation_reports",
            ["strategy_version_id"],
        )
        op.create_index(
            "ix_strategy_validation_reports_version_stage",
            "strategy_validation_reports",
            ["strategy_version_id", "stage", "executed_at"],
        )

    if not _has_table("strategy_admin_reviews"):
        op.create_table(
            "strategy_admin_reviews",
            sa.Column("id", models.GUID(), primary_key=True, nullable=False),
            sa.Column(
                "strategy_version_id",
                models.GUID(),
                sa.ForeignKey("strategy_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "reviewer_user_id",
                models.GUID(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("decision", sa.String(length=20), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_strategy_admin_reviews_strategy_version_id",
            "strategy_admin_reviews",
            ["strategy_version_id"],
        )
        op.create_index(
            "ix_strategy_admin_reviews_version_reviewed",
            "strategy_admin_reviews",
            ["strategy_version_id", "reviewed_at"],
        )

    _backfill_supertrend()


# Lightweight table handles so the GUID/JSON TypeDecorators bind correctly on
# both PostgreSQL and SQLite without depending on current ORM model state.
_catalog = sa.table(
    "strategy_catalog",
    sa.column("id", models.GUID()),
    sa.column("code", sa.String()),
    sa.column("display_name", sa.String()),
    sa.column("owner_type", sa.String()),
    sa.column("owner_user_id", models.GUID()),
    sa.column("visibility", sa.String()),
    sa.column("status", sa.String()),
    sa.column("description", sa.Text()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

_versions = sa.table(
    "strategy_versions",
    sa.column("id", models.GUID()),
    sa.column("strategy_id", models.GUID()),
    sa.column("version", sa.String()),
    sa.column("payload_spec_version", sa.String()),
    sa.column("source_journey", sa.String()),
    sa.column("status", sa.String()),
    sa.column("execution_kind", sa.String()),
    sa.column("changelog", sa.Text()),
    sa.column("approved_at", sa.DateTime(timezone=True)),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _backfill_supertrend() -> None:
    """Register the pre-registry hardcoded supertrend strategy as the first
    approved Nova-owned entry. Idempotent across re-runs."""
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    strategy_id = bind.execute(
        sa.select(_catalog.c.id).where(
            _catalog.c.code == "supertrend", _catalog.c.owner_user_id.is_(None)
        )
    ).scalar()
    if strategy_id is None:
        strategy_id = uuid.uuid4()
        bind.execute(
            _catalog.insert().values(
                id=strategy_id,
                code="supertrend",
                display_name="Supertrend (NIFTY)",
                owner_type="nova",
                owner_user_id=None,
                visibility="nova_shared",
                status="active",
                description=(
                    "Nova-operated NIFTY Supertrend strategy (backfilled from the "
                    "pre-registry hardcoded strategy)."
                ),
                created_at=now,
                updated_at=now,
            )
        )

    version_exists = bind.execute(
        sa.select(_versions.c.id).where(
            _versions.c.strategy_id == strategy_id, _versions.c.version == "1.0.0"
        )
    ).scalar()
    if version_exists is None:
        bind.execute(
            _versions.insert().values(
                id=uuid.uuid4(),
                strategy_id=strategy_id,
                version="1.0.0",
                payload_spec_version="nova.v1",
                source_journey="nova_shared",
                status="approved",
                execution_kind="external_webhook",
                changelog="Backfilled from the pre-registry hardcoded supertrend strategy.",
                approved_at=now,
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    for table in (
        "strategy_admin_reviews",
        "strategy_validation_reports",
        "strategy_source_artifacts",
        "strategy_versions",
        "strategy_catalog",
    ):
        if _has_table(table):
            op.drop_table(table)
