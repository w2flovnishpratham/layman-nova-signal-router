"""Expand strategy_admin_reviews status columns to VARCHAR(64).

Revision ID: 0017_expand_admin_review_status
Revises: 0016_c2_tv_installation

The C1 approval route writes ``ready_for_admin_review`` (22 chars) into
``previous_status`` and ``approved_for_tv_compile`` (23 chars) into
``new_status``. Both columns were created VARCHAR(20) by migration 0011, so
PostgreSQL raises StringDataRightTruncation on a valid approval. SQLite does
not enforce declared length and masked the defect.

This is a width-only migration: nullability, foreign keys, indexes, review
semantics and stored status values are unchanged.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017_expand_admin_review_status"
down_revision = "0016_c2_tv_installation"
branch_labels = None
depends_on = None

_TABLE = "strategy_admin_reviews"
_COLUMNS = ("previous_status", "new_status")
_WIDE = 64
_NARROW = 20


def _batch_options() -> dict:
    return {"recreate": "always"} if op.get_bind().dialect.name == "sqlite" else {}


def upgrade() -> None:
    with op.batch_alter_table(_TABLE, **_batch_options()) as batch:
        for column in _COLUMNS:
            batch.alter_column(
                column,
                existing_type=sa.String(length=_NARROW),
                type_=sa.String(length=_WIDE),
                existing_nullable=True,
            )


def downgrade() -> None:
    # Never silently truncate review evidence. If any stored value would not
    # fit VARCHAR(20), fail with a controlled error instead of narrowing.
    bind = op.get_bind()
    offenders = bind.execute(
        sa.text(
            f"SELECT COUNT(*) FROM {_TABLE} "
            f"WHERE (previous_status IS NOT NULL AND LENGTH(previous_status) > {_NARROW}) "
            f"OR (new_status IS NOT NULL AND LENGTH(new_status) > {_NARROW})"
        )
    ).scalar()
    if offenders:
        raise RuntimeError(
            f"Cannot narrow {_TABLE}.previous_status/new_status to VARCHAR({_NARROW}): "
            f"{offenders} row(s) hold C1/C2 review evidence longer than {_NARROW} "
            "characters. Remove or migrate that evidence explicitly before downgrading."
        )
    with op.batch_alter_table(_TABLE, **_batch_options()) as batch:
        for column in _COLUMNS:
            batch.alter_column(
                column,
                existing_type=sa.String(length=_WIDE),
                type_=sa.String(length=_NARROW),
                existing_nullable=True,
            )
